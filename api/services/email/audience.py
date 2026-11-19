"""Resolve a campaign audience rule into recipient counts.

Nothing here sends email or writes recipient rows. The resolver is the single
place that decides which leads a campaign may address, so the preview endpoint
and the launch precondition check can never disagree.

Exclusion order (one fixed order, one bucket per lead)
------------------------------------------------------

Every lead that matches the audience rule lands in exactly one bucket, checked
in this order:

1. ``no_email`` — the lead has no address at all.
2. ``invalid_email`` — the shared validator rejects the address (syntax,
   placeholder domain, image extension, too long, ...).
3. ``suppressed`` — the normalized address is on the organization's
   suppression list.
4. ``opted_out`` — the lead's marketing-email consent is ``opted_out``.
5. ``unknown`` — consent is ``unknown``. Excluded under the default
   ``opted_in_only`` policy; admitted only with the ``include_unknown``
   override, which requires a written reason.
6. ``manually_excluded`` — the operator unchecked the lead in the recipients
   table. The id lives in ``excluded_lead_ids`` inside the audience JSON, so
   no migration is needed. The resolver re-applies the list on every call.
7. ``eligible`` — consent is ``opted_in`` (or ``unknown`` under the override)
   and the lead was not manually excluded.

A lead is judged on its *current* address and current consent, because consent
is evidence about the address the person agreed to.

Manual addresses
----------------

``manual_emails`` (operator-typed addresses, typically the sender's own inbox
for a test send) are unioned in after the leads. They have no lead record and
no consent history, so the consent policy does not apply; the suppression list
still does. An address already covered by an eligible lead is not added twice —
the lead record wins because it carries merge values and a real audit link.
An invalid manual address fails the whole rule with a 422 rather than being
silently dropped.

Extra leads
-----------

``extra_lead_ids`` lets the operator append individually selected leads to a
campaign whose audience is a filter, project or tag. Their rows are streamed
with no filter so they are not excluded just because they fall outside the
saved rule. They go through the same consent and exclusion gates as any other
lead, and a lead already matched by the base rule is counted once.

Duplicates are counted separately: once a lead is eligible, its normalized
address identifies the recipient. When several eligible leads share one
address, the representative is the opted-in lead before an unknown one, then
the newest record. The extra leads are reported as
``duplicates_collapsed_count`` and are not recipients.

.. note:: Launch must persist the winner.

    The launch step must call :func:`resolve_audience` again rather than
    reading the stored counts, and it must write one ``email_messages`` row
    per *winning* lead id — the one this resolver picked for each address.
    The opted-in-over-unknown / newest preference only becomes observable and
    enforceable when the actual lead id is recorded in the outgoing message
    row. Reusing the stored counts skips the de-duplication and breaks the
    consent guarantee.

Performance
-----------

Leads are streamed from the database in batches and never loaded into memory as
rows; only compact per-address bookkeeping is kept for de-duplication. The
suppression list is loaded once as a set of normalized addresses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from api.db import db_client
from api.enums import EmailCampaignAudienceType, EmailCampaignConsentPolicy
from api.utils.email_buckets import EXCLUSION_ORDER as EXCLUSION_ORDER
from api.utils.email_validation import classify_email

MAX_EXPLICIT_LEAD_IDS = 1000
MASKED_SAMPLE_SIZE = 5

MAX_MANUAL_EXCLUSIONS = 5000
MAX_MANUAL_EMAILS = 100


class AudienceError(ValueError):
    """An audience rule the API must reject (mapped to 422)."""


@dataclass(frozen=True)
class AudienceRule:
    """The stored audience rule, independent of the route schema."""

    audience_type: str
    lead_ids: tuple[int, ...] = ()
    project_id: int | None = None
    tag: str | None = None
    filters: Mapping[str, Any] = field(default_factory=dict)
    excluded_lead_ids: tuple[int, ...] = ()
    manual_emails: tuple[str, ...] = ()
    extra_lead_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class AudienceResolution:
    """Counts plus a masked sample; never a full address."""

    recipient_count: int = 0
    eligible_count: int = 0
    excluded_no_email_count: int = 0
    excluded_invalid_email_count: int = 0
    excluded_suppressed_count: int = 0
    excluded_opted_out_count: int = 0
    excluded_unknown_count: int = 0
    excluded_manual_count: int = 0
    duplicates_collapsed_count: int = 0
    # Lead-level count of unknown-consent leads, independent of the policy.
    unknown_lead_count: int = 0
    masked_sample: list[str] = field(default_factory=list)

    def counters(self) -> dict[str, int]:
        """Column names on ``email_campaigns`` for the stored counters.

        ``excluded_manual_count`` is intentionally absent: it is derived from
        ``len(excluded_lead_ids)`` in the audience JSON at response time, so
        no migration is needed to add the manual-exclusion bucket.
        """
        return {
            "recipient_count": self.recipient_count,
            "eligible_count": self.eligible_count,
            "excluded_no_email_count": self.excluded_no_email_count,
            "excluded_invalid_email_count": self.excluded_invalid_email_count,
            "excluded_suppressed_count": self.excluded_suppressed_count,
            "excluded_opted_out_count": self.excluded_opted_out_count,
            "excluded_unknown_count": self.excluded_unknown_count,
            "duplicates_collapsed_count": self.duplicates_collapsed_count,
        }


def mask_address(address: str) -> str:
    """Mask the local part, keeping the domain so the operator can sanity-check.

    ``owner@apex.example`` becomes ``o***@apex.example``. A preview never
    returns a full address.
    """
    local, separator, domain = address.partition("@")
    if not separator:
        return "***"
    first = local[:1] if local else ""
    return f"{first}***@{domain}"


def normalized_manual_addresses(raw_addresses: tuple[str, ...]) -> list[str]:
    """Validate, normalize and de-duplicate operator-typed addresses.

    Rejecting the whole rule on one bad entry beats silently dropping it: the
    operator typed that address because they expect the campaign to mail it.
    """
    seen: dict[str, None] = {}
    for raw in raw_addresses:
        address, reason = classify_email(raw)
        if reason is not None or address is None:
            raise AudienceError(
                f"Manual address {mask_address(raw.strip())} is not valid ({reason})."
            )
        seen.setdefault(address, None)
    return list(seen)


def _validate_rule(rule: AudienceRule) -> None:
    audience_type = rule.audience_type
    if audience_type == EmailCampaignAudienceType.LEADS.value:
        if not rule.lead_ids:
            raise AudienceError("Provide at least one lead id.")
        if len(set(rule.lead_ids)) > MAX_EXPLICIT_LEAD_IDS:
            raise AudienceError(
                f"A campaign may address at most {MAX_EXPLICIT_LEAD_IDS} "
                "explicitly selected leads."
            )
    if (
        rule.excluded_lead_ids
        and len(set(rule.excluded_lead_ids)) > MAX_MANUAL_EXCLUSIONS
    ):
        raise AudienceError(
            f"At most {MAX_MANUAL_EXCLUSIONS} leads may be manually excluded."
        )
    if rule.extra_lead_ids and len(set(rule.extra_lead_ids)) > MAX_EXPLICIT_LEAD_IDS:
        raise AudienceError(
            f"At most {MAX_EXPLICIT_LEAD_IDS} leads may be added individually."
        )
    if rule.manual_emails:
        addresses = normalized_manual_addresses(rule.manual_emails)
        if len(addresses) > MAX_MANUAL_EMAILS:
            raise AudienceError(
                f"At most {MAX_MANUAL_EMAILS} manually typed addresses are allowed."
            )
    if audience_type == EmailCampaignAudienceType.PROJECT.value:
        if rule.project_id is None:
            raise AudienceError("Provide a lead project.")
    if audience_type == EmailCampaignAudienceType.TAG.value:
        if not (rule.tag or "").strip():
            raise AudienceError("Provide a tag.")
    if audience_type == EmailCampaignAudienceType.FILTER.value:
        if not rule.filters:
            raise AudienceError("Provide a lead filter.")
        if rule.filters.get("all_matching") is False:
            raise AudienceError(
                "A campaign always addresses every lead matching the filter; "
                "a saved project can capture a page instead."
            )
    if audience_type not in {t.value for t in EmailCampaignAudienceType}:
        raise AudienceError(f"Unknown audience type: {audience_type!r}.")


def validate_audience_rule(rule: AudienceRule) -> None:
    """Raise :class:`AudienceError` when the rule shape is unusable."""
    _validate_rule(rule)


def filter_kwargs(rule: AudienceRule) -> dict[str, Any]:
    """Translate a rule into ``lead_filter_clauses`` keyword arguments.

    Public wrapper so routes do not import the private helper.
    """
    return _filter_kwargs(rule)


def _filter_kwargs(rule: AudienceRule) -> dict[str, Any]:
    if rule.audience_type == EmailCampaignAudienceType.PROJECT.value:
        return {"project_id": rule.project_id}
    if rule.audience_type == EmailCampaignAudienceType.TAG.value:
        return {"tag": rule.tag}
    if rule.audience_type == EmailCampaignAudienceType.FILTER.value:
        allowed = {
            "search_id",
            "status",
            "q",
            "has_phone",
            "has_website",
            "has_email",
            "country",
            "state",
            "city",
            "category",
            "tag",
            "email_consent",
        }
        unknown = set(rule.filters) - allowed - {"all_matching"}
        if unknown:
            raise AudienceError(
                "Unknown filter field(s): " + ", ".join(sorted(unknown))
            )
        kwargs = {key: value for key, value in rule.filters.items() if key in allowed}
        if "status" in kwargs:
            kwargs["crm_status"] = kwargs.pop("status")
        if "q" in kwargs:
            kwargs["query"] = kwargs.pop("q")
        return kwargs
    return {}


async def _validated_explicit_ids(
    organization_id: int, rule: AudienceRule
) -> list[int]:
    ids = sorted({int(lead_id) for lead_id in rule.lead_ids})
    if not ids:
        return []
    found = await db_client.audience_lead_ids(organization_id, ids)
    missing = [lead_id for lead_id in ids if lead_id not in found]
    if missing:
        # Do not say whether the ids exist elsewhere: another organization's
        # lead is indistinguishable from an id that never existed.
        raise AudienceError(
            f"{len(missing)} selected lead(s) do not belong to this "
            "organization; no email was prepared."
        )
    return ids


async def _suppressed_addresses(organization_id: int) -> set[str]:
    return await db_client.suppressed_audience_addresses(organization_id)


@dataclass(frozen=True)
class AudienceRecipient:
    """One winning lead for a normalized address, with merge-field values.

    ``lead_id`` is ``None`` for an address the operator typed by hand; those
    rows are still recorded in ``email_messages`` but link to no CRM record.
    """

    lead_id: int | None
    email: str
    consent: str
    values: Mapping[str, str]


def _manual_values(address: str) -> dict[str, str]:
    """Merge values for a manual address: only the address itself is known."""
    return {
        "business_name": "",
        "category": "",
        "address": "",
        "city": "",
        "state": "",
        "country": "",
        "postal_code": "",
        "phone": "",
        "email": address,
        "website": "",
        "rating": "",
        "review_count": "",
    }


def _merge_values(row, address: str) -> dict[str, str]:
    """Merge values for one lead. Missing fields fall back per template."""

    def text(value: Any) -> str:
        return "" if value is None else str(value)

    rating = (
        f"{row.rating:.1f}".rstrip("0").rstrip(".") if row.rating is not None else ""
    )
    return {
        "business_name": text(row.title),
        "category": text(row.category),
        "address": text(row.address),
        "city": text(row.city),
        "state": text(row.state),
        "country": text(row.country),
        "postal_code": text(row.postal_code),
        "phone": text(row.phone),
        "email": address,
        "website": text(row.website),
        "rating": rating,
        "review_count": text(row.review_count),
    }


async def _resolve(
    organization_id: int,
    rule: AudienceRule,
    *,
    consent_policy: str,
    sample_size: int,
    collect_recipients: bool,
) -> tuple[AudienceResolution, list[AudienceRecipient]]:
    _validate_rule(rule)
    if consent_policy not in {policy.value for policy in EmailCampaignConsentPolicy}:
        raise AudienceError(f"Unknown consent policy: {consent_policy!r}")

    include_unknown = consent_policy == EmailCampaignConsentPolicy.INCLUDE_UNKNOWN.value
    excluded_ids = set(rule.excluded_lead_ids or ())

    explicit_ids: list[int] | None = None
    if rule.audience_type == EmailCampaignAudienceType.LEADS.value:
        explicit_ids = await _validated_explicit_ids(organization_id, rule)
    extra_ids: list[int] = []
    if rule.extra_lead_ids:
        extra_ids = await _validated_explicit_ids(
            organization_id,
            AudienceRule(
                audience_type=EmailCampaignAudienceType.LEADS.value,
                lead_ids=rule.extra_lead_ids,
            ),
        )
    manual_addresses = (
        normalized_manual_addresses(rule.manual_emails) if rule.manual_emails else []
    )
    suppressed = await _suppressed_addresses(organization_id)

    recipient_count = 0
    no_email = 0
    invalid = 0
    suppressed_count = 0
    opted_out = 0
    unknown_excluded = 0
    unknown_leads = 0
    manual_excluded = 0
    eligible_leads = 0
    manual_winners = 0
    # normalized address -> (priority, updated_at timestamp, lead id)
    # Lower priority wins; then newest timestamp; then highest id.
    winners: dict[str, tuple[int, float, int]] = {}
    collected: dict[str, AudienceRecipient] = {}
    seen_lead_ids: set[int] = set()

    async def lead_rows():
        async for row in db_client.iter_audience_leads(
            organization_id,
            lead_ids=explicit_ids,
            filter_kwargs=_filter_kwargs(rule),
        ):
            yield row
        if extra_ids:
            async for row in db_client.iter_audience_leads(
                organization_id,
                lead_ids=extra_ids,
                filter_kwargs={},
            ):
                yield row

    async for row in lead_rows():
        lead_id = row.id
        if lead_id in seen_lead_ids:
            continue
        seen_lead_ids.add(lead_id)
        raw_email = row.email
        consent_value = row.email_consent
        updated_at = row.updated_at
        recipient_count += 1
        if not (raw_email or "").strip():
            no_email += 1
            continue
        address, reason = classify_email(raw_email)
        if reason is not None or address is None:
            invalid += 1
            continue
        if address in suppressed:
            suppressed_count += 1
            continue
        consent = consent_value or "unknown"
        if consent == "opted_out":
            opted_out += 1
            continue
        if consent == "unknown":
            unknown_leads += 1
            if not include_unknown:
                unknown_excluded += 1
                continue
            priority = 1
        else:  # opted_in
            priority = 0

        # Manual exclusion is the last check before eligibility — the operator
        # explicitly chose to remove this lead after it passed every other gate.
        if lead_id in excluded_ids:
            manual_excluded += 1
            continue

        eligible_leads += 1
        stamp = updated_at.timestamp() if updated_at is not None else 0.0
        current = winners.get(address)
        if current is None or (priority, -stamp, -lead_id) < (
            current[0],
            -current[1],
            -current[2],
        ):
            winners[address] = (priority, stamp, lead_id)
            if collect_recipients:
                collected[address] = AudienceRecipient(
                    lead_id=lead_id,
                    email=address,
                    consent=consent,
                    values=_merge_values(row, address),
                )

    # Manual addresses: no consent record and no lead row, so only the
    # suppression list can veto them. A lead already representing the address
    # wins — it has merge values and an audit link.
    for address in manual_addresses:
        if address in winners:
            continue
        recipient_count += 1
        if address in suppressed:
            suppressed_count += 1
            continue
        winners[address] = (-1, 0.0, 0)
        manual_winners += 1
        if collect_recipients:
            collected[address] = AudienceRecipient(
                lead_id=None,
                email=address,
                consent="manual",
                values=_manual_values(address),
            )

    sample = [
        mask_address(address) for address in sorted(winners)[: max(0, sample_size)]
    ]
    resolution = AudienceResolution(
        recipient_count=recipient_count,
        eligible_count=len(winners),
        excluded_no_email_count=no_email,
        excluded_invalid_email_count=invalid,
        excluded_suppressed_count=suppressed_count,
        excluded_opted_out_count=opted_out,
        excluded_unknown_count=unknown_excluded,
        excluded_manual_count=manual_excluded,
        duplicates_collapsed_count=eligible_leads - (len(winners) - manual_winners),
        unknown_lead_count=unknown_leads,
        masked_sample=sample,
    )
    recipients = (
        sorted(collected.values(), key=lambda item: item.email)
        if collect_recipients
        else []
    )
    return resolution, recipients


async def resolve_audience(
    organization_id: int,
    rule: AudienceRule,
    *,
    consent_policy: str,
    sample_size: int = MASKED_SAMPLE_SIZE,
) -> AudienceResolution:
    """Count the audience and return a masked sample of eligible addresses."""
    resolution, _ = await _resolve(
        organization_id,
        rule,
        consent_policy=consent_policy,
        sample_size=sample_size,
        collect_recipients=False,
    )
    return resolution


async def resolve_audience_recipients(
    organization_id: int,
    rule: AudienceRule,
    *,
    consent_policy: str,
) -> tuple[AudienceResolution, list[AudienceRecipient]]:
    """Resolve counts and the winning lead for every eligible address.

    The returned list is what launch freezes into ``email_messages``; its
    length always equals ``resolution.eligible_count``.
    """
    return await _resolve(
        organization_id,
        rule,
        consent_policy=consent_policy,
        sample_size=0,
        collect_recipients=True,
    )
