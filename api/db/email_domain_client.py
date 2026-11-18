"""Database client for sending domains.

A domain belongs to one organization and points at the sender whose provider
credentials check it. Rows are organization-scoped at the query level so a
domain from another organization is indistinguishable from one that does not
exist.
"""

from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from api.db.base_client import BaseDBClient
from api.db.email_sender_client import EmailResourceConflictError
from api.db.models import EmailDomainModel
from api.enums import EmailDomainStatus, EmailProvider


class EmailDomainClient(BaseDBClient):
    """Organization-scoped sending-domain operations."""

    async def list_email_domains(self, organization_id: int) -> List[EmailDomainModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(EmailDomainModel)
                .options(selectinload(EmailDomainModel.sender))
                .where(EmailDomainModel.organization_id == organization_id)
                .order_by(EmailDomainModel.domain)
            )
            return list(result.scalars().all())

    async def get_email_domain(
        self, organization_id: int, domain_uuid: str
    ) -> Optional[EmailDomainModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(EmailDomainModel)
                .options(selectinload(EmailDomainModel.sender))
                .where(
                    EmailDomainModel.organization_id == organization_id,
                    EmailDomainModel.domain_uuid == domain_uuid,
                )
            )
            return result.scalar_one_or_none()

    async def create_email_domain(
        self,
        *,
        organization_id: int,
        user_id: int,
        domain: str,
        provider: EmailProvider,
        sender_id: Optional[int],
    ) -> EmailDomainModel:
        async with self.async_session() as session:
            row = EmailDomainModel(
                organization_id=organization_id,
                domain=domain,
                provider=provider.value,
                sender_id=sender_id,
                created_by=user_id,
            )
            session.add(row)
            try:
                await session.flush()
                domain_uuid = row.domain_uuid
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise EmailResourceConflictError(
                    f"A sending domain named '{domain}' already exists"
                ) from exc

            logger.info(
                f"Created email domain '{domain}' ({domain_uuid}) "
                f"for organization {organization_id}"
            )
            return (
                await session.execute(
                    select(EmailDomainModel)
                    .options(selectinload(EmailDomainModel.sender))
                    .where(
                        EmailDomainModel.organization_id == organization_id,
                        EmailDomainModel.domain_uuid == domain_uuid,
                    )
                )
            ).scalar_one()

    async def update_email_domain(
        self,
        *,
        organization_id: int,
        domain_uuid: str,
        domain: Optional[str] = None,
        provider: Optional[EmailProvider] = None,
        sender_id: Optional[int] = None,
        clear_sender: bool = False,
    ) -> Optional[EmailDomainModel]:
        """Re-point or rename a domain.

        Changing the name or the sender invalidates any earlier check, so the
        row returns to ``pending`` with an empty verification record.
        """
        async with self.async_session() as session:
            row = await self._get_domain_in_session(
                session, organization_id, domain_uuid
            )
            if row is None:
                return None

            values: Dict[str, Any] = {"updated_at": datetime.now(UTC)}
            if domain is not None:
                values["domain"] = domain
            if provider is not None:
                values["provider"] = provider.value
            if clear_sender:
                values["sender_id"] = None
            elif sender_id is not None:
                values["sender_id"] = sender_id
            if len(values) > 1:
                values.update(
                    status=EmailDomainStatus.PENDING.value,
                    verification={},
                    last_checked_at=None,
                )

            try:
                await session.execute(
                    update(EmailDomainModel)
                    .where(
                        EmailDomainModel.organization_id == organization_id,
                        EmailDomainModel.domain_uuid == domain_uuid,
                    )
                    .values(**values)
                )
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise EmailResourceConflictError(
                    f"A sending domain named '{domain or row.domain}' already exists"
                ) from exc

            return await self._reload_domain(session, organization_id, domain_uuid)

    async def delete_email_domain(self, organization_id: int, domain_uuid: str) -> bool:
        async with self.async_session() as session:
            row = await self._get_domain_in_session(
                session, organization_id, domain_uuid
            )
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            logger.info(
                f"Deleted email domain {domain_uuid} for organization {organization_id}"
            )
            return True

    async def record_email_domain_verification(
        self,
        *,
        organization_id: int,
        domain_uuid: str,
        status: EmailDomainStatus,
        verification: Dict[str, Any],
    ) -> Optional[EmailDomainModel]:
        async with self.async_session() as session:
            now = datetime.now(UTC)
            result = await session.execute(
                update(EmailDomainModel)
                .where(
                    EmailDomainModel.organization_id == organization_id,
                    EmailDomainModel.domain_uuid == domain_uuid,
                )
                .values(
                    status=status.value,
                    verification=verification,
                    last_checked_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
            if result.rowcount == 0:
                return None
            return await self._reload_domain(session, organization_id, domain_uuid)

    @staticmethod
    async def _get_domain_in_session(session, organization_id, domain_uuid):
        return (
            await session.execute(
                select(EmailDomainModel)
                .options(selectinload(EmailDomainModel.sender))
                .where(
                    EmailDomainModel.organization_id == organization_id,
                    EmailDomainModel.domain_uuid == domain_uuid,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def _reload_domain(session, organization_id, domain_uuid):
        return (
            await session.execute(
                select(EmailDomainModel)
                .options(selectinload(EmailDomainModel.sender))
                .where(
                    EmailDomainModel.organization_id == organization_id,
                    EmailDomainModel.domain_uuid == domain_uuid,
                )
            )
        ).scalar_one()
