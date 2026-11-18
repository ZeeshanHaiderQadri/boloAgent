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
6. ``eligible`` — consent is ``opted_in`` (or ``unknown`` under the override).

A lead is judged on its *current* address and current consent, because consent
is evidence about the address the person agreed to.

Duplicates are counted separately: once a lead is eligible, its normalized
address identifies the recipient. When several eligible leads share one
address, the representative is the opted-in lead before an unknown one, then
the newest record. The extra leads are reported as
``duplicates_collapsed_count`` and are not recipients.

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
from api.utils.email_validation import classify_email

MAX_EXPLICIT_LEAD_IDS = 1000
MASKED_SAMPLE_SIZE = 5

# Documented for the docs page and tests. Keep in sync with the resolver body.
EXCLUSION_ORDER: tuple[str, ...] = (
    "no_email",
    "invalid_email",
    "suppressed",
    "opted_out",
    "unknown",
    "eligible",
)


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
    duplicates_collapsed_count: int = 0
    # Lead-level count of unknown-consent leads, independent of the policy.
    unknown_lead_count: int = 0
    masked_sample: list[str] = field(default_factory=list)

    def counters(self) -> dict[str, int]:
        """Column names on ``email_campaigns`` for the stored counters."""
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
        return
    if audience_type == EmailCampaignAudienceType.PROJECT.value:
        if rule.project_id is None:
            raise AudienceError("Provide a lead project.")
        return
    if audience_type == EmailCampaignAudienceType.TAG.value:
        if not (rule.tag or "").strip():
            raise AudienceError("Provide a tag.")
        return
    if audience_type == EmailCampaignAudienceType.FILTER.value:
        if not rule.filters:
            raise AudienceError("Provide a lead filter.")
        if rule.filters.get("all_matching") is False:
            raise AudienceError(
                "A campaign always addresses every lead matching the filter; "
                "a saved project can capture a page instead."
            )
        return
    raise AudienceError(f"Unknown audience type: {audience_type!r}.")


def validate_audience_rule(rule: AudienceRule) -> None:
    """Raise :class:`AudienceError` when the rule shape is unusable."""
    _validate_rule(rule)


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


def _iter_lead_rows(
    organization_id: int,
    *,
    lead_ids: Iterable[int] | None,
    filter_kwargs: Mapping[str, Any],
):
    """Return the streaming select for the audience.

    ``stream`` keeps one batch of rows on the server side; a preview over a
    large filter never loads every lead into memory.
    """
    return db_client.iter_audience_leads(
        organization_id,
        lead_ids=lead_ids,
        filter_kwargs=filter_kwargs,
    )


async def resolve_audience(
    organization_id: int,
    rule: AudienceRule,
    *,
    consent_policy: str,
    sample_size: int = MASKED_SAMPLE_SIZE,
) -> AudienceResolution:
    """Count the audience and return a masked sample of eligible addresses."""
    _validate_rule(rule)
    if consent_policy not in {policy.value for policy in EmailCampaignConsentPolicy}:
        raise AudienceError(f"Unknown consent policy: {consent_policy!r}")

    include_unknown = consent_policy == EmailCampaignConsentPolicy.INCLUDE_UNKNOWN.value

    explicit_ids: list[int] | None = None
    if rule.audience_type == EmailCampaignAudienceType.LEADS.value:
        explicit_ids = await _validated_explicit_ids(organization_id, rule)

    suppressed = await _suppressed_addresses(organization_id)

    recipient_count = 0
    no_email = 0
    invalid = 0
    suppressed_count = 0
    opted_out = 0
    unknown_excluded = 0
    unknown_leads = 0
    eligible_leads = 0
    # normalized address -> (priority, updated_at timestamp, lead id)
    # Lower priority wins; then newest timestamp; then highest id.
    recipients: dict[str, tuple[int, float, int]] = {}

    async for lead_id, raw_email, consent_value, updated_at in _iter_lead_rows(
        organization_id,
        lead_ids=explicit_ids,
        filter_kwargs=_filter_kwargs(rule),
    ):
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

        eligible_leads += 1
        stamp = updated_at.timestamp() if updated_at is not None else 0.0
        current = recipients.get(address)
        if current is None or (priority, -stamp, -lead_id) < (
            current[0],
            -current[1],
            -current[2],
        ):
            recipients[address] = (priority, stamp, lead_id)

    sample = [
        mask_address(address) for address in sorted(recipients)[: max(0, sample_size)]
    ]
    return AudienceResolution(
        recipient_count=recipient_count,
        eligible_count=len(recipients),
        excluded_no_email_count=no_email,
        excluded_invalid_email_count=invalid,
        excluded_suppressed_count=suppressed_count,
        excluded_opted_out_count=opted_out,
        excluded_unknown_count=unknown_excluded,
        duplicates_collapsed_count=eligible_leads - len(recipients),
        unknown_lead_count=unknown_leads,
        masked_sample=sample,
    )
