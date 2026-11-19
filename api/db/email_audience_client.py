"""Organization-scoped, read-only audience queries for email campaigns.

The campaign resolver lives in ``api/services/email/audience.py`` and decides
which lead falls in which bucket. This module only streams the rows it needs
and loads the suppression list, so all SQL stays inside ``api/db``.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator, Iterable, Mapping

from sqlalchemy import select

from api.db.base_client import BaseDBClient
from api.db.lead_client import lead_filter_clauses
from api.db.models import EmailSuppressionModel, LeadModel


@dataclass(frozen=True)
class AudienceLeadRow:
    """One lead matching an audience rule, with the fields a send needs.

    Full addresses stay inside the DB/service layer; the recipients endpoint
    masks them before returning.
    """

    id: int
    title: str | None
    email: str | None
    email_consent: str | None
    updated_at: datetime | None
    category: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    website: str | None = None
    rating: float | None = None
    review_count: int | None = None


def _classify_bucket(
    lead_id: int,
    raw_email: str | None,
    consent_value: str | None,
    suppressed: set[str],
    excluded_ids: set[int],
    include_unknown: bool,
) -> tuple[str, str | None, str]:
    """Classify a lead into a bucket and explain the decision.

    Returns ``(bucket, normalized_address, reason)``. The reason is written
    for the campaign owner, so it names the check that decided the outcome
    (validator, suppression list, consent consent policy, manual exclusion).

    Uses the same order as the campaign audience resolver:
    no_email → invalid_email → suppressed → opted_out → unknown →
    manually_excluded → eligible.
    """
    if not (raw_email or "").strip():
        return "no_email", None, "No email address on record."
    from api.utils.email_validation import (  # noqa: PLC0415
        classify_email,
        email_rejection_message,
    )

    address, rejection = classify_email(raw_email)
    if rejection is not None or address is None:
        detail = email_rejection_message(rejection or "invalid_syntax")
        return "invalid_email", None, f"Rejected by the email validator: {detail}."
    if address in suppressed:
        return (
            "suppressed",
            address,
            "On the suppression list — a previous email bounced or was "
            "reported as spam.",
        )
    consent = consent_value or "unknown"
    if consent == "opted_out":
        return "opted_out", address, "Lead opted out of marketing email."
    if consent == "unknown":
        if not include_unknown:
            return (
                "unknown",
                address,
                "No consent recorded — this campaign sends to opted-in leads "
                "only. Switch Step 3 to include unknown consent to send.",
            )
    if lead_id in excluded_ids:
        return (
            "manually_excluded",
            address,
            "Removed from this campaign. The lead stays in your CRM.",
        )
    if consent == "unknown":
        return (
            "eligible",
            address,
            "No consent recorded — included by the unknown-consent override.",
        )
    return "eligible", address, "Consent recorded — will receive this campaign."


class EmailAudienceClient(BaseDBClient):
    """Read-only lead and suppression queries for one organization."""

    async def stream_recipient_rows(
        self,
        organization_id: int,
        *,
        lead_ids: Iterable[int] | None,
        filter_kwargs: Mapping[str, Any],
        suppressed: set[str],
        excluded_ids: set[int],
        include_unknown: bool,
        bucket_filter: str | None,
        offset: int,
        limit: int,
        manual_addresses: Iterable[str] = (),
    ) -> tuple[list[dict], int, dict[str, int]]:
        """Stream all leads, classify into buckets, paginate, and return counts.

        Returns ``(items, total_matching_filter, bucket_counts)`` where:

        - ``items`` are dicts with keys lead_id, title, email, email_consent,
          bucket, reason (paged by offset/limit).
        - ``total_matching_filter`` is the count of rows matching
          ``bucket_filter`` (or all rows when None).
        - ``bucket_counts`` maps every bucket name to its count across
          the whole audience (not just the page).

        Eligible addresses are deduplicated the way the resolver does it, so a
        second lead with the same address is not offered twice.
        """
        from api.utils.email_buckets import EXCLUSION_ORDER  # noqa: PLC0415

        bucket_counts: dict[str, int] = {b: 0 for b in EXCLUSION_ORDER}
        eligible_addresses: set[str] = set()
        # Only the requested window is retained; the rest of the audience is
        # counted and discarded so a large filter never builds a full list.
        page_items: list[dict] = []
        matching_total = 0

        def emit(item: dict) -> None:
            nonlocal matching_total
            if matching_total >= offset and len(page_items) < limit:
                page_items.append(item)
            matching_total += 1

        clauses = lead_filter_clauses(organization_id, **filter_kwargs)
        statement = select(
            LeadModel.id,
            LeadModel.title,
            LeadModel.email,
            LeadModel.email_consent,
        ).where(*clauses)
        if lead_ids is not None:
            statement = statement.where(LeadModel.id.in_(list(lead_ids)))
        statement = statement.execution_options(yield_per=500)

        async with self.async_session() as session:
            result = await session.stream(statement)
            async for row in result:
                bucket, address, reason = _classify_bucket(
                    row.id,
                    row.email,
                    row.email_consent,
                    suppressed,
                    excluded_ids,
                    include_unknown,
                )
                if bucket == "eligible":
                    if address in eligible_addresses:
                        continue
                    eligible_addresses.add(address)
                bucket_counts[bucket] += 1
                if bucket_filter is None or bucket == bucket_filter:
                    emit(
                        {
                            "lead_id": row.id,
                            "title": row.title or "",
                            "email": (row.email or "").strip(),
                            "email_consent": row.email_consent or "unknown",
                            "bucket": bucket,
                            "reason": reason,
                        }
                    )

        # Manual addresses have no lead row; a lead that already represents the
        # same address is kept and the duplicate entry is dropped.
        for address in dict.fromkeys(manual_addresses):
            normalized = address.strip().lower()
            if not normalized or normalized in eligible_addresses:
                continue
            bucket = "suppressed" if normalized in suppressed else "eligible"
            if bucket == "eligible":
                eligible_addresses.add(normalized)
            if bucket == "suppressed":
                reason = (
                    "On the suppression list — a previous email bounced or was "
                    "reported as spam."
                )
            else:
                reason = "Added manually for this campaign."
            bucket_counts[bucket] += 1
            if bucket_filter is None or bucket == bucket_filter:
                emit(
                    {
                        "lead_id": None,
                        "title": "",
                        "email": normalized,
                        "email_consent": "manual",
                        "bucket": bucket,
                        "reason": reason,
                    }
                )

        return page_items, matching_total, bucket_counts

    async def audience_lead_ids(
        self, organization_id: int, lead_ids: Iterable[int]
    ) -> set[int]:
        """Return which of the given ids exist in this organization."""
        ids = list(lead_ids)
        if not ids:
            return set()
        async with self.async_session() as session:
            result = await session.execute(
                select(LeadModel.id).where(
                    LeadModel.organization_id == organization_id,
                    LeadModel.id.in_(ids),
                )
            )
            return set(result.scalars().all())

    async def suppressed_audience_addresses(self, organization_id: int) -> set[str]:
        async with self.async_session() as session:
            rows = await session.execute(
                select(EmailSuppressionModel.email).where(
                    EmailSuppressionModel.organization_id == organization_id
                )
            )
            return {str(value).strip().lower() for value in rows.scalars().all()}

    async def iter_audience_leads(
        self,
        organization_id: int,
        *,
        lead_ids: Iterable[int] | None,
        filter_kwargs: Mapping[str, Any],
    ) -> AsyncIterator[AudienceLeadRow]:
        """Stream leads matching the audience, with send-time fields.

        ``stream`` keeps one batch of rows on the server side; a preview over a
        large filter never builds a list of every lead.
        """
        clauses = lead_filter_clauses(organization_id, **filter_kwargs)
        statement = select(
            LeadModel.id,
            LeadModel.title,
            LeadModel.email,
            LeadModel.email_consent,
            LeadModel.updated_at,
            LeadModel.category,
            LeadModel.address,
            LeadModel.city,
            LeadModel.state,
            LeadModel.country,
            LeadModel.postal_code,
            LeadModel.phone,
            LeadModel.website,
            LeadModel.rating,
            LeadModel.review_count,
        ).where(*clauses)
        if lead_ids is not None:
            statement = statement.where(LeadModel.id.in_(list(lead_ids)))
        statement = statement.execution_options(yield_per=500)

        async with self.async_session() as session:
            result = await session.stream(statement)
            async for row in result:
                yield AudienceLeadRow(
                    id=row.id,
                    title=row.title,
                    email=row.email,
                    email_consent=row.email_consent,
                    updated_at=row.updated_at,
                    category=row.category,
                    address=row.address,
                    city=row.city,
                    state=row.state,
                    country=row.country,
                    postal_code=row.postal_code,
                    phone=row.phone,
                    website=row.website,
                    rating=row.rating,
                    review_count=row.review_count,
                )
