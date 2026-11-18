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


class EmailAudienceClient(BaseDBClient):
    """Read-only lead and suppression queries for one organization."""

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
