"""Organization-scoped, read-only audience queries for email campaigns.

The campaign resolver lives in ``api/services/email/audience.py`` and decides
which lead falls in which bucket. This module only streams the rows it needs
and loads the suppression list, so all SQL stays inside ``api/db``.
"""

from datetime import datetime
from typing import Any, AsyncIterator, Iterable, Mapping

from sqlalchemy import select

from api.db.base_client import BaseDBClient
from api.db.lead_client import lead_filter_clauses
from api.db.models import EmailSuppressionModel, LeadModel


def _classify_bucket(
    lead_id: int,
    raw_email: str | None,
    consent_value: str | None,
    suppressed: set[str],
    excluded_ids: set[int],
    include_unknown: bool,
) -> str:
    """Classify a lead into an exclusion bucket.

    Uses the same order as the campaign audience resolver:
    no_email → invalid_email → suppressed → opted_out → unknown →
    manually_excluded → eligible.

    Importable from ``api.utils`` (not from ``api.services``) so the DB
    layer boundary is not crossed.
    """
    if not (raw_email or "").strip():
        return "no_email"
    from api.utils.email_validation import classify_email  # noqa: PLC0415

    address, reason = classify_email(raw_email)
    if reason is not None or address is None:
        return "invalid_email"
    if address in suppressed:
        return "suppressed"
    consent = consent_value or "unknown"
    if consent == "opted_out":
        return "opted_out"
    if consent == "unknown":
        if not include_unknown:
            return "unknown"
    if lead_id in excluded_ids:
        return "manually_excluded"
    return "eligible"


def _mask_email(raw_email: str) -> str:
    """Mask the local part of an email address, keeping the domain."""
    if "@" not in raw_email:
        return "***"
    local, domain = raw_email.split("@", 1)
    first = local[:1] if local else ""
    return f"{first}***@{domain}"


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
    ) -> tuple[list[dict], int, dict[str, int]]:
        """Stream all leads, classify into buckets, paginate, and return counts.

        Returns ``(items, total_matching_filter, bucket_counts)`` where:

        - ``items`` are dicts with keys lead_id, title, masked_email,
          email_consent, bucket (paged by offset/limit).
        - ``total_matching_filter`` is the count of rows matching
          ``bucket_filter`` (or all rows when None).
        - ``bucket_counts`` maps every bucket name to its count across
          the whole audience (not just the page).

        Never returns a full email address.
        """
        from api.services.email.audience import EXCLUSION_ORDER  # noqa: PLC0415

        bucket_counts: dict[str, int] = {b: 0 for b in EXCLUSION_ORDER}
        matching: list[dict] = []

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
                bucket = _classify_bucket(
                    row.id,
                    row.email,
                    row.email_consent,
                    suppressed,
                    excluded_ids,
                    include_unknown,
                )
                bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
                if bucket_filter is None or bucket == bucket_filter:
                    masked = (
                        _mask_email(row.email)
                        if row.email and row.email.strip()
                        else ""
                    )
                    matching.append(
                        {
                            "lead_id": row.id,
                            "title": row.title or "",
                            "masked_email": masked,
                            "email_consent": row.email_consent or "unknown",
                            "bucket": bucket,
                        }
                    )

        total = len(matching)
        page_items = matching[offset : offset + limit]
        return page_items, total, bucket_counts

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
    ) -> AsyncIterator[tuple[int, str | None, str | None, datetime | None]]:
        """Stream ``(id, email, email_consent, updated_at)`` for the audience.

        ``stream`` keeps one batch of rows on the server side; a preview over a
        large filter never builds a list of every lead.
        """
        clauses = lead_filter_clauses(organization_id, **filter_kwargs)
        statement = select(
            LeadModel.id,
            LeadModel.email,
            LeadModel.email_consent,
            LeadModel.updated_at,
        ).where(*clauses)
        if lead_ids is not None:
            statement = statement.where(LeadModel.id.in_(list(lead_ids)))
        statement = statement.execution_options(yield_per=500)

        async with self.async_session() as session:
            result = await session.stream(statement)
            async for row in result:
                yield row.id, row.email, row.email_consent, row.updated_at
