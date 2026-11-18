"""Organization-scoped database access for lead generation and CRM records."""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import String, and_, cast, delete, func, or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.models import (
    CampaignModel,
    LeadActivityModel,
    LeadCampaignDispatchModel,
    LeadModel,
    LeadProjectModel,
    LeadSearchModel,
    LeadSearchResultModel,
    QueuedRunModel,
    WorkflowRunModel,
)
from api.services.leads.consent import (
    consent_reset_values,
    email_address_changed,
    email_change_activity,
)

LEAD_CRM_STATUSES = (
    "new",
    "contacted",
    "interested",
    "not_interested",
    "won",
    "lost",
    "do_not_call",
)

# Business fields refreshed when a search sees an existing place again. CRM
# fields (status, owner, tags, notes) are deliberately never overwritten.
_LEAD_REFRESH_FIELDS = (
    "title",
    "category",
    "address",
    "city",
    "state",
    "country",
    "postal_code",
    "phone",
    "phone_e164",
    "email",
    "website",
    "place_id",
    "cid",
    "rating",
    "review_count",
    "latitude",
    "longitude",
    "open_hours",
    "raw_data",
    "source",
)


def _lead_filters(
    organization_id: int,
    *,
    search_id: int | None = None,
    project_id: int | None = None,
    crm_status: str | None = None,
    query: str | None = None,
    has_phone: bool | None = None,
    has_website: bool | None = None,
    has_email: bool | None = None,
    country: str | None = None,
    state: str | None = None,
    city: str | None = None,
    category: str | None = None,
    tag: str | None = None,
    email_consent: str | None = None,
) -> list:
    """Shared CRM filter clauses for listing, exporting and bulk assignment.

    Keeping this in one place guarantees "save these results as a project"
    selects exactly the same rows the operator was looking at.
    """
    filters = [LeadModel.organization_id == organization_id]
    if search_id is not None:
        filters.append(
            LeadModel.id.in_(
                select(LeadSearchResultModel.lead_id).where(
                    LeadSearchResultModel.search_id == search_id
                )
            )
        )
    if project_id is not None:
        filters.append(LeadModel.project_id == project_id)
    if crm_status:
        filters.append(LeadModel.crm_status == crm_status)
    if has_phone is True:
        filters.append(LeadModel.phone_e164.is_not(None))
    elif has_phone is False:
        filters.append(LeadModel.phone_e164.is_(None))
    if has_website is True:
        filters.append(and_(LeadModel.website.is_not(None), LeadModel.website != ""))
    elif has_website is False:
        filters.append(or_(LeadModel.website.is_(None), LeadModel.website == ""))
    if has_email is True:
        filters.append(and_(LeadModel.email.is_not(None), LeadModel.email != ""))
    elif has_email is False:
        filters.append(or_(LeadModel.email.is_(None), LeadModel.email == ""))
    if country:
        filters.append(func.lower(LeadModel.country) == country.strip().lower())
    if state:
        filters.append(func.lower(LeadModel.state) == state.strip().lower())
    if city:
        filters.append(func.lower(LeadModel.city) == city.strip().lower())
    if category:
        filters.append(func.lower(LeadModel.category) == category.strip().lower())
    if query:
        like = f"%{query.strip()}%"
        filters.append(
            or_(
                LeadModel.title.ilike(like),
                LeadModel.category.ilike(like),
                LeadModel.address.ilike(like),
                LeadModel.phone.ilike(like),
                LeadModel.email.ilike(like),
                LeadModel.website.ilike(like),
            )
        )
    if tag:
        filters.append(cast(LeadModel.tags, String).ilike(f'%"{tag}"%'))
    if email_consent:
        filters.append(LeadModel.email_consent == email_consent)
    return filters


class LeadProjectNameConflictError(Exception):
    """Raised when a lead project name already exists in the organization."""


class LeadClient(BaseDBClient):
    # ------------------------------------------------------------------
    # Searches
    # ------------------------------------------------------------------
    async def create_lead_search(
        self,
        *,
        organization_id: int,
        created_by: int | None,
        name: str,
        category: str | None,
        location: str | None,
        country_code: str | None,
        language: str,
        provider: str,
        params: dict[str, Any],
        target_results: int | None = None,
        status: str = "queued",
    ) -> LeadSearchModel:
        async with self.async_session() as session:
            search = LeadSearchModel(
                organization_id=organization_id,
                created_by=created_by,
                name=name,
                category=category,
                location=location,
                country_code=country_code,
                language=language,
                provider=provider,
                params=params,
                target_results=target_results,
                status=status,
                results_count=0,
                new_leads_count=0,
            )
            session.add(search)
            await session.commit()
            await session.refresh(search)
            return search

    async def get_lead_search(
        self, search_id: int, organization_id: int
    ) -> LeadSearchModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(LeadSearchModel).where(
                    LeadSearchModel.id == search_id,
                    LeadSearchModel.organization_id == organization_id,
                )
            )
            return result.scalar_one_or_none()

    async def get_lead_search_unscoped(self, search_id: int) -> LeadSearchModel | None:
        """Worker-only lookup. Callers must derive tenancy from the row itself."""
        async with self.async_session() as session:
            result = await session.execute(
                select(LeadSearchModel).where(LeadSearchModel.id == search_id)
            )
            return result.scalar_one_or_none()

    async def list_lead_searches(
        self, organization_id: int, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[LeadSearchModel], int]:
        async with self.async_session() as session:
            total = await session.scalar(
                select(func.count(LeadSearchModel.id)).where(
                    LeadSearchModel.organization_id == organization_id
                )
            )
            result = await session.execute(
                select(LeadSearchModel)
                .where(LeadSearchModel.organization_id == organization_id)
                .order_by(LeadSearchModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return list(result.scalars().all()), int(total or 0)

    async def update_lead_search(
        self, search_id: int, organization_id: int, **values: Any
    ) -> LeadSearchModel | None:
        values["updated_at"] = datetime.now(UTC)
        async with self.async_session() as session:
            result = await session.execute(
                update(LeadSearchModel)
                .where(
                    LeadSearchModel.id == search_id,
                    LeadSearchModel.organization_id == organization_id,
                )
                .values(**values)
                .returning(LeadSearchModel)
            )
            row = result.scalar_one_or_none()
            await session.commit()
            if row is not None:
                await session.refresh(row)
            return row

    async def cancel_lead_search(
        self, search_id: int, organization_id: int
    ) -> LeadSearchModel | None:
        return await self.update_lead_search(
            search_id,
            organization_id,
            status="cancelled",
            completed_at=datetime.now(UTC),
        )

    # ------------------------------------------------------------------
    # Leads / result ingestion
    # ------------------------------------------------------------------
    async def upsert_leads_for_search(
        self,
        *,
        search_id: int,
        organization_id: int,
        leads: Iterable[dict[str, Any]],
    ) -> dict[str, int]:
        """Upsert provider rows into org-wide leads and link them to the search.

        Deduplication is on ``(organization_id, dedupe_key)``. Existing business
        fields are refreshed, CRM fields are preserved.
        """
        rows = list(leads)
        seen: set[str] = set()
        results_count = 0
        new_leads = 0
        updated_leads = 0

        async with self.async_session() as session:
            existing_keys_result = await session.execute(
                select(
                    LeadModel.id,
                    LeadModel.dedupe_key,
                    LeadModel.email,
                    LeadModel.email_consent,
                ).where(
                    LeadModel.organization_id == organization_id,
                    LeadModel.dedupe_key.in_(
                        [row["dedupe_key"] for row in rows if row.get("dedupe_key")]
                    ),
                )
            )
            existing = {row.dedupe_key: row for row in existing_keys_result.all()}

            for row in rows:
                dedupe_key = row.get("dedupe_key")
                if not dedupe_key or dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                results_count += 1

                insert_values = {"organization_id": organization_id, **row}
                insert_stmt = pg_insert(LeadModel).values(**insert_values)
                refresh = {
                    field: getattr(insert_stmt.excluded, field)
                    for field in _LEAD_REFRESH_FIELDS
                    if field in row
                }
                # A later scrape often cannot recover an address the first page
                # exposed (different page layout, lazy-loaded contact block).
                # Never replace a known address with an empty value.
                if not row.get("email"):
                    refresh.pop("email", None)
                refresh["updated_at"] = datetime.now(UTC)

                previous = existing.get(dedupe_key)
                new_email = row.get("email")
                if (
                    previous is not None
                    and new_email
                    and email_address_changed(previous.email, new_email)
                ):
                    # The consent decision was made about the old mailbox, so it
                    # cannot carry over to the new one.
                    refresh.update(consent_reset_values())
                    session.add(
                        LeadActivityModel(
                            **email_change_activity(
                                organization_id=organization_id,
                                lead_id=previous.id,
                                user_id=None,
                                previous_email=previous.email,
                                new_email=new_email,
                                previous_consent=previous.email_consent,
                                source="scraper",
                            )
                        )
                    )
                statement = insert_stmt.on_conflict_do_update(
                    constraint="uq_leads_organization_dedupe_key",
                    set_=refresh,
                ).returning(LeadModel.id)
                lead_id = await session.scalar(statement)
                if lead_id is None:
                    continue

                link_stmt = (
                    pg_insert(LeadSearchResultModel)
                    .values(search_id=search_id, lead_id=lead_id)
                    .on_conflict_do_nothing(constraint="uq_lead_search_results_pair")
                )
                await session.execute(link_stmt)

                if previous is not None:
                    updated_leads += 1
                else:
                    new_leads += 1

            await session.commit()

        return {
            "results_count": results_count,
            "new_leads_count": new_leads,
            "updated_leads_count": updated_leads,
        }

    async def clear_search_results(self, search_id: int, organization_id: int) -> None:
        """Unlink every business from a search, keeping the org-wide leads.

        Used when an operator edits a search enough that the previously
        collected rows no longer match the query.
        """
        async with self.async_session() as session:
            owned = await session.scalar(
                select(LeadSearchModel.id).where(
                    LeadSearchModel.id == search_id,
                    LeadSearchModel.organization_id == organization_id,
                )
            )
            if owned is None:
                return
            await session.execute(
                delete(LeadSearchResultModel).where(
                    LeadSearchResultModel.search_id == search_id
                )
            )
            await session.commit()

    async def count_search_results(self, search_id: int) -> int:
        """Total businesses linked to a search (across every ingest batch)."""
        async with self.async_session() as session:
            total = await session.scalar(
                select(func.count(LeadSearchResultModel.id)).where(
                    LeadSearchResultModel.search_id == search_id
                )
            )
            return int(total or 0)

    async def list_leads(
        self,
        organization_id: int,
        *,
        search_id: int | None = None,
        project_id: int | None = None,
        crm_status: str | None = None,
        query: str | None = None,
        has_phone: bool | None = None,
        has_website: bool | None = None,
        has_email: bool | None = None,
        country: str | None = None,
        state: str | None = None,
        city: str | None = None,
        category: str | None = None,
        tag: str | None = None,
        email_consent: str | None = None,
        sort: str = "created_at",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[LeadModel], int]:
        filters = _lead_filters(
            organization_id,
            search_id=search_id,
            project_id=project_id,
            crm_status=crm_status,
            query=query,
            has_phone=has_phone,
            has_website=has_website,
            has_email=has_email,
            country=country,
            state=state,
            city=city,
            category=category,
            tag=tag,
            email_consent=email_consent,
        )

        order_clause = {
            "created_at": LeadModel.created_at.desc(),
            "rating": LeadModel.rating.desc().nullslast(),
            "title": LeadModel.title.asc(),
            "status": LeadModel.crm_status.asc(),
        }.get(sort, LeadModel.created_at.desc())

        async with self.async_session() as session:
            total = await session.scalar(
                select(func.count(LeadModel.id)).where(*filters)
            )
            result = await session.execute(
                select(LeadModel)
                .where(*filters)
                .order_by(order_clause, LeadModel.id.desc())
                .limit(limit)
                .offset(offset)
            )
            return list(result.scalars().all()), int(total or 0)

    async def get_lead(self, lead_id: int, organization_id: int) -> LeadModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(LeadModel).where(
                    LeadModel.id == lead_id,
                    LeadModel.organization_id == organization_id,
                )
            )
            return result.scalar_one_or_none()

    async def get_leads_by_ids(
        self, lead_ids: list[int], organization_id: int
    ) -> list[LeadModel]:
        if not lead_ids:
            return []
        async with self.async_session() as session:
            result = await session.execute(
                select(LeadModel).where(
                    LeadModel.organization_id == organization_id,
                    LeadModel.id.in_(lead_ids),
                )
            )
            return list(result.scalars().all())

    async def update_lead(
        self,
        lead_id: int,
        organization_id: int,
        *,
        activities: list[dict[str, Any]] | None = None,
        **values: Any,
    ) -> LeadModel | None:
        """Update one lead and optionally append audit rows atomically.

        ``activities`` are committed in the same transaction as the update, so
        a consent change can never be persisted without its audit record.
        """
        values["updated_at"] = datetime.now(UTC)
        async with self.async_session() as session:
            result = await session.execute(
                update(LeadModel)
                .where(
                    LeadModel.id == lead_id,
                    LeadModel.organization_id == organization_id,
                )
                .values(**values)
                .returning(LeadModel)
            )
            row = result.scalar_one_or_none()
            if row is not None and activities:
                session.add_all([LeadActivityModel(**item) for item in activities])
            await session.commit()
            if row is not None:
                await session.refresh(row)
            return row

    async def bulk_update_leads(
        self,
        organization_id: int,
        lead_ids: list[int],
        *,
        activities: list[dict[str, Any]] | None = None,
        **values: Any,
    ) -> int:
        if not lead_ids:
            return 0
        values["updated_at"] = datetime.now(UTC)
        async with self.async_session() as session:
            result = await session.execute(
                update(LeadModel)
                .where(
                    LeadModel.organization_id == organization_id,
                    LeadModel.id.in_(lead_ids),
                )
                .values(**values)
                .returning(LeadModel.id)
            )
            ids = list(result.scalars().all())
            if ids and activities:
                session.add_all([LeadActivityModel(**item) for item in activities])
            await session.commit()
            return len(ids)

    async def delete_lead(self, lead_id: int, organization_id: int) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                delete(LeadModel).where(
                    LeadModel.id == lead_id,
                    LeadModel.organization_id == organization_id,
                )
            )
            await session.commit()
            return bool(result.rowcount)

    # ------------------------------------------------------------------
    # Saved projects (lists)
    # ------------------------------------------------------------------
    async def list_lead_projects(
        self, organization_id: int
    ) -> list[tuple[LeadProjectModel, int, int]]:
        """Projects with their lead and callable counts, newest first."""
        async with self.async_session() as session:
            counts = (
                select(
                    LeadModel.project_id.label("project_id"),
                    func.count(LeadModel.id).label("lead_count"),
                    func.count(LeadModel.phone_e164).label("callable_count"),
                )
                .where(
                    LeadModel.organization_id == organization_id,
                    LeadModel.project_id.is_not(None),
                )
                .group_by(LeadModel.project_id)
                .subquery()
            )
            result = await session.execute(
                select(
                    LeadProjectModel,
                    func.coalesce(counts.c.lead_count, 0),
                    func.coalesce(counts.c.callable_count, 0),
                )
                .outerjoin(counts, counts.c.project_id == LeadProjectModel.id)
                .where(LeadProjectModel.organization_id == organization_id)
                .order_by(
                    LeadProjectModel.created_at.desc(), LeadProjectModel.id.desc()
                )
            )
            return [
                (project, int(lead_count or 0), int(callable_count or 0))
                for project, lead_count, callable_count in result.all()
            ]

    async def get_lead_project(
        self, project_id: int, organization_id: int
    ) -> tuple[LeadProjectModel, int, int] | None:
        async with self.async_session() as session:
            project = await session.scalar(
                select(LeadProjectModel).where(
                    LeadProjectModel.id == project_id,
                    LeadProjectModel.organization_id == organization_id,
                )
            )
            if project is None:
                return None
            counts = (
                await session.execute(
                    select(
                        func.count(LeadModel.id),
                        func.count(LeadModel.phone_e164),
                    ).where(
                        LeadModel.organization_id == organization_id,
                        LeadModel.project_id == project_id,
                    )
                )
            ).one()
            return project, int(counts[0] or 0), int(counts[1] or 0)

    async def get_lead_project_by_name(
        self, organization_id: int, name: str
    ) -> LeadProjectModel | None:
        async with self.async_session() as session:
            return await session.scalar(
                select(LeadProjectModel).where(
                    LeadProjectModel.organization_id == organization_id,
                    func.lower(LeadProjectModel.name) == name.strip().lower(),
                )
            )

    async def create_lead_project(
        self,
        organization_id: int,
        name: str,
        created_by: int | None = None,
    ) -> LeadProjectModel:
        async with self.async_session() as session:
            project = LeadProjectModel(
                organization_id=organization_id,
                name=name.strip(),
                created_by=created_by,
            )
            session.add(project)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise LeadProjectNameConflictError(
                    f"A project named '{name.strip()}' already exists."
                )
            await session.refresh(project)
            return project

    async def rename_lead_project(
        self, project_id: int, organization_id: int, name: str
    ) -> LeadProjectModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                update(LeadProjectModel)
                .where(
                    LeadProjectModel.id == project_id,
                    LeadProjectModel.organization_id == organization_id,
                )
                .values(name=name.strip(), updated_at=datetime.now(UTC))
                .returning(LeadProjectModel)
            )
            project = result.scalar_one_or_none()
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise LeadProjectNameConflictError(
                    f"A project named '{name.strip()}' already exists."
                )
            if project is not None:
                await session.refresh(project)
            return project

    async def delete_lead_project(
        self, project_id: int, organization_id: int
    ) -> int | None:
        """Delete a project and un-file its leads without deleting them."""
        async with self.async_session() as session:
            project = await session.scalar(
                select(LeadProjectModel).where(
                    LeadProjectModel.id == project_id,
                    LeadProjectModel.organization_id == organization_id,
                )
            )
            if project is None:
                return None
            result = await session.execute(
                update(LeadModel)
                .where(
                    LeadModel.organization_id == organization_id,
                    LeadModel.project_id == project_id,
                )
                .values(project_id=None, updated_at=datetime.now(UTC))
            )
            unassigned = int(result.rowcount or 0)
            await session.delete(project)
            await session.commit()
            return unassigned

    async def assign_leads_to_project(
        self, organization_id: int, project_id: int, lead_ids: list[int]
    ) -> int:
        """Move leads into a project. One project per lead, so this also
        detaches them from any previous project; records are never duplicated."""
        if not lead_ids:
            return 0
        async with self.async_session() as session:
            result = await session.execute(
                update(LeadModel)
                .where(
                    LeadModel.organization_id == organization_id,
                    LeadModel.id.in_(lead_ids),
                )
                .values(project_id=project_id, updated_at=datetime.now(UTC))
                .returning(LeadModel.id)
            )
            ids = list(result.scalars().all())
            await session.commit()
            return len(ids)

    async def assign_leads_to_project_by_filters(
        self, organization_id: int, project_id: int, **filters: Any
    ) -> int:
        """Move every lead matching the CRM filters into a project."""
        clauses = _lead_filters(organization_id, **filters)
        async with self.async_session() as session:
            result = await session.execute(
                update(LeadModel)
                .where(*clauses)
                .values(project_id=project_id, updated_at=datetime.now(UTC))
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def unassign_leads_from_project(
        self, organization_id: int, project_id: int, lead_ids: list[int]
    ) -> int:
        if not lead_ids:
            return 0
        async with self.async_session() as session:
            result = await session.execute(
                update(LeadModel)
                .where(
                    LeadModel.organization_id == organization_id,
                    LeadModel.project_id == project_id,
                    LeadModel.id.in_(lead_ids),
                )
                .values(project_id=None, updated_at=datetime.now(UTC))
                .returning(LeadModel.id)
            )
            ids = list(result.scalars().all())
            await session.commit()
            return len(ids)

    async def add_tags_to_leads(
        self, organization_id: int, lead_ids: list[int], tags: list[str]
    ) -> int:
        """Append tags to each lead, preserving existing labels; idempotent."""
        if not lead_ids or not tags:
            return 0
        cleaned: list[str] = []
        seen_tags: set[str] = set()
        for tag in tags:
            tag = str(tag).strip()[:40]
            if tag and tag.lower() not in seen_tags:
                seen_tags.add(tag.lower())
                cleaned.append(tag)
        if not cleaned:
            return 0
        updated = 0
        async with self.async_session() as session:
            result = await session.execute(
                select(LeadModel).where(
                    LeadModel.organization_id == organization_id,
                    LeadModel.id.in_(lead_ids),
                )
            )
            for lead in result.scalars().all():
                existing = list(lead.tags or [])
                seen = {str(tag).lower() for tag in existing}
                changed = False
                for tag in cleaned:
                    if tag.lower() not in seen and len(existing) < 20:
                        existing.append(tag)
                        seen.add(tag.lower())
                        changed = True
                if changed:
                    lead.tags = existing[:20]
                    lead.updated_at = datetime.now(UTC)
                    updated += 1
            await session.commit()
        return updated

    async def lead_stats(
        self,
        organization_id: int,
        *,
        search_id: int | None = None,
        project_id: int | None = None,
    ) -> dict[str, int]:
        filters = [LeadModel.organization_id == organization_id]
        if search_id is not None:
            filters.append(
                LeadModel.id.in_(
                    select(LeadSearchResultModel.lead_id).where(
                        LeadSearchResultModel.search_id == search_id
                    )
                )
            )
        if project_id is not None:
            filters.append(LeadModel.project_id == project_id)
        async with self.async_session() as session:
            row = (
                await session.execute(
                    select(
                        func.count(LeadModel.id).label("total"),
                        func.count(LeadModel.phone_e164).label("with_phone"),
                        func.count(LeadModel.email).label("with_email"),
                        func.count(LeadModel.id)
                        .filter(LeadModel.crm_status == "new")
                        .label("new"),
                        func.count(LeadModel.id)
                        .filter(LeadModel.crm_status == "contacted")
                        .label("contacted"),
                        func.count(LeadModel.id)
                        .filter(LeadModel.crm_status == "interested")
                        .label("interested"),
                        func.count(LeadModel.id)
                        .filter(LeadModel.crm_status == "won")
                        .label("won"),
                        func.count(LeadModel.id)
                        .filter(LeadModel.crm_status == "do_not_call")
                        .label("do_not_call"),
                    ).where(*filters)
                )
            ).one()
            searches_total = await session.scalar(
                select(func.count(LeadSearchModel.id)).where(
                    LeadSearchModel.organization_id == organization_id
                )
            )
        return {
            "total": int(row.total or 0),
            "with_phone": int(row.with_phone or 0),
            "with_email": int(row.with_email or 0),
            "new": int(row.new or 0),
            "contacted": int(row.contacted or 0),
            "interested": int(row.interested or 0),
            "won": int(row.won or 0),
            "do_not_call": int(row.do_not_call or 0),
            "searches": int(searches_total or 0),
        }

    async def lead_facets(
        self,
        organization_id: int,
        *,
        country: str | None = None,
        state: str | None = None,
    ) -> dict[str, list[str]]:
        """Distinct geography values for CRM filter dropdowns."""
        base = [LeadModel.organization_id == organization_id]
        country_scope = (
            [func.lower(LeadModel.country) == country.strip().lower()]
            if country
            else []
        )
        state_scope = (
            [func.lower(LeadModel.state) == state.strip().lower()] if state else []
        )

        def _values(column, scopes):
            return (
                select(column)
                .where(
                    *base,
                    *scopes,
                    column.is_not(None),
                    column != "",
                )
                .group_by(column)
                .order_by(column.asc())
            )

        async with self.async_session() as session:
            countries = (
                (await session.execute(_values(LeadModel.country, []))).scalars().all()
            )
            # States are scoped by country, cities by both country and state.
            states = (
                (await session.execute(_values(LeadModel.state, country_scope)))
                .scalars()
                .all()
            )
            cities = (
                (
                    await session.execute(
                        _values(LeadModel.city, [*country_scope, *state_scope])
                    )
                )
                .scalars()
                .all()
            )
            categories = (
                (await session.execute(_values(LeadModel.category, []))).scalars().all()
            )

        return {
            "countries": [str(value) for value in countries],
            "states": [str(value) for value in states],
            "cities": [str(value) for value in cities],
            "categories": [str(value) for value in categories],
        }

    async def list_lead_search_links(
        self, lead_id: int, organization_id: int
    ) -> list[LeadSearchModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(LeadSearchModel)
                .join(
                    LeadSearchResultModel,
                    LeadSearchResultModel.search_id == LeadSearchModel.id,
                )
                .where(
                    LeadSearchResultModel.lead_id == lead_id,
                    LeadSearchModel.organization_id == organization_id,
                )
                .order_by(LeadSearchModel.created_at.desc())
            )
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    # CRM timeline
    # ------------------------------------------------------------------
    async def add_lead_activity(
        self,
        *,
        organization_id: int,
        lead_id: int,
        activity_type: str,
        user_id: int | None = None,
        title: str | None = None,
        body: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LeadActivityModel:
        async with self.async_session() as session:
            activity = LeadActivityModel(
                organization_id=organization_id,
                lead_id=lead_id,
                user_id=user_id,
                activity_type=activity_type,
                title=title,
                body=body,
                extra_metadata=metadata or {},
            )
            session.add(activity)
            await session.commit()
            await session.refresh(activity)
            return activity

    async def add_lead_activities_bulk(self, activities: list[dict[str, Any]]) -> None:
        if not activities:
            return
        async with self.async_session() as session:
            session.add_all([LeadActivityModel(**item) for item in activities])
            await session.commit()

    async def list_lead_activities(
        self, lead_id: int, organization_id: int, *, limit: int = 100
    ) -> list[LeadActivityModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(LeadActivityModel)
                .where(
                    LeadActivityModel.lead_id == lead_id,
                    LeadActivityModel.organization_id == organization_id,
                )
                .order_by(LeadActivityModel.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Outbound campaign dispatches / call history
    # ------------------------------------------------------------------
    async def create_lead_dispatches(self, dispatches: list[dict[str, Any]]) -> None:
        if not dispatches:
            return
        async with self.async_session() as session:
            await session.execute(
                pg_insert(LeadCampaignDispatchModel)
                .values(dispatches)
                .on_conflict_do_nothing(constraint="uq_lead_dispatch_campaign_source")
            )
            await session.commit()

    async def list_lead_calls(
        self, lead_id: int, organization_id: int, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Join a lead's dispatches to queued/workflow runs for call history."""
        async with self.async_session() as session:
            result = await session.execute(
                select(
                    WorkflowRunModel.id,
                    WorkflowRunModel.state,
                    WorkflowRunModel.created_at,
                    WorkflowRunModel.recording_url,
                    WorkflowRunModel.transcript_url,
                    WorkflowRunModel.gathered_context,
                    CampaignModel.id.label("campaign_id"),
                    CampaignModel.name.label("campaign_name"),
                    QueuedRunModel.retry_count,
                )
                .select_from(LeadCampaignDispatchModel)
                .join(
                    QueuedRunModel,
                    and_(
                        QueuedRunModel.campaign_id
                        == LeadCampaignDispatchModel.campaign_id,
                        QueuedRunModel.source_uuid
                        == LeadCampaignDispatchModel.source_uuid,
                    ),
                )
                .join(
                    WorkflowRunModel,
                    WorkflowRunModel.queued_run_id == QueuedRunModel.id,
                )
                .join(
                    CampaignModel,
                    CampaignModel.id == LeadCampaignDispatchModel.campaign_id,
                )
                .where(
                    LeadCampaignDispatchModel.lead_id == lead_id,
                    LeadCampaignDispatchModel.organization_id == organization_id,
                )
                .order_by(WorkflowRunModel.created_at.desc())
                .limit(limit)
            )
            calls = []
            for row in result:
                gathered = row.gathered_context or {}
                tags = gathered.get("call_tags") or []
                if not isinstance(tags, list):
                    tags = []
                extracted = gathered.get("extracted_variables")
                calls.append(
                    {
                        "workflow_run_id": row.id,
                        "state": row.state,
                        "created_at": row.created_at,
                        "campaign_id": row.campaign_id,
                        "campaign_name": row.campaign_name,
                        "retry_count": row.retry_count,
                        "recording_url": row.recording_url,
                        "transcript_url": row.transcript_url,
                        "call_id": gathered.get("call_id"),
                        "call_status": gathered.get("call_status"),
                        "call_disposition": gathered.get("call_disposition"),
                        "mapped_call_disposition": gathered.get(
                            "mapped_call_disposition"
                        ),
                        "call_tags": [str(tag) for tag in tags],
                        "extracted_variables": (
                            extracted if isinstance(extracted, dict) else {}
                        ),
                    }
                )
            return calls

    async def list_pending_lead_call_outcomes(
        self, *, limit: int = 200, since_days: int = 14
    ) -> list[dict[str, Any]]:
        """Completed calls for dispatched leads that have no timeline entry yet."""
        cutoff = datetime.now(UTC) - timedelta(days=since_days)
        already_recorded = (
            select(LeadActivityModel.id)
            .where(
                LeadActivityModel.lead_id == LeadCampaignDispatchModel.lead_id,
                LeadActivityModel.organization_id
                == LeadCampaignDispatchModel.organization_id,
                LeadActivityModel.activity_type == "call",
                LeadActivityModel.extra_metadata.op("->>")("workflow_run_id")
                == cast(WorkflowRunModel.id, String),
            )
            .exists()
        )
        async with self.async_session() as session:
            result = await session.execute(
                select(
                    LeadCampaignDispatchModel.lead_id,
                    LeadCampaignDispatchModel.organization_id,
                    LeadCampaignDispatchModel.campaign_id,
                    LeadModel.title,
                    LeadModel.crm_status,
                    WorkflowRunModel.id.label("workflow_run_id"),
                    WorkflowRunModel.state,
                    WorkflowRunModel.created_at,
                    WorkflowRunModel.gathered_context,
                )
                .select_from(LeadCampaignDispatchModel)
                .join(
                    QueuedRunModel,
                    and_(
                        QueuedRunModel.campaign_id
                        == LeadCampaignDispatchModel.campaign_id,
                        QueuedRunModel.source_uuid
                        == LeadCampaignDispatchModel.source_uuid,
                    ),
                )
                .join(
                    WorkflowRunModel,
                    WorkflowRunModel.queued_run_id == QueuedRunModel.id,
                )
                .join(
                    LeadModel,
                    LeadModel.id == LeadCampaignDispatchModel.lead_id,
                )
                .where(
                    WorkflowRunModel.is_completed.is_(True),
                    WorkflowRunModel.created_at >= cutoff,
                    ~already_recorded,
                )
                .order_by(WorkflowRunModel.created_at.desc())
                .limit(limit)
            )
            return [
                {
                    "lead_id": row.lead_id,
                    "organization_id": row.organization_id,
                    "campaign_id": row.campaign_id,
                    "lead_title": row.title,
                    "crm_status": row.crm_status,
                    "workflow_run_id": row.workflow_run_id,
                    "state": row.state,
                    "created_at": row.created_at,
                    "gathered_context": row.gathered_context or {},
                }
                for row in result
            ]

    async def list_dispatch_lead_ids(
        self, campaign_id: int, organization_id: int
    ) -> list[int]:
        async with self.async_session() as session:
            result = await session.execute(
                select(LeadCampaignDispatchModel.lead_id).where(
                    LeadCampaignDispatchModel.campaign_id == campaign_id,
                    LeadCampaignDispatchModel.organization_id == organization_id,
                )
            )
            return list(result.scalars().all())
