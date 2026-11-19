"""Database operations for email templates, always organization-scoped.

A template name is unique inside one organization. The route layer compiles and
sanitizes a template before calling these methods; this module only stores what
it is given and translates the unique constraint into a conflict error.
"""

from datetime import UTC, datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.db.base_client import BaseDBClient
from api.db.models import EmailTemplateModel


class EmailTemplateNameConflictError(Exception):
    """Another template in this organization already uses the name."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"A template named {name!r} already exists.")


class EmailTemplateClient(BaseDBClient):
    """Organization-scoped email template operations."""

    async def list_email_templates(
        self, organization_id: int
    ) -> List[EmailTemplateModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(EmailTemplateModel)
                .where(EmailTemplateModel.organization_id == organization_id)
                .order_by(
                    EmailTemplateModel.updated_at.desc(),
                    EmailTemplateModel.name,
                )
            )
            return list(result.scalars().all())

    async def get_email_template(
        self, organization_id: int, template_uuid: str
    ) -> Optional[EmailTemplateModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(EmailTemplateModel).where(
                    EmailTemplateModel.organization_id == organization_id,
                    EmailTemplateModel.template_uuid == template_uuid,
                )
            )
            return result.scalar_one_or_none()

    async def get_email_template_by_id(
        self, organization_id: int, template_id: int
    ) -> Optional[EmailTemplateModel]:
        """Load a template by primary key for campaign response rendering."""
        async with self.async_session() as session:
            result = await session.execute(
                select(EmailTemplateModel).where(
                    EmailTemplateModel.organization_id == organization_id,
                    EmailTemplateModel.id == template_id,
                )
            )
            return result.scalar_one_or_none()

    async def create_email_template(
        self,
        *,
        organization_id: int,
        user_id: int,
        name: str,
        subject: str,
        source_format: str,
        source_body: str,
        html_body: str,
        text_body: Optional[str],
        has_unsubscribe_placeholder: bool,
    ) -> EmailTemplateModel:
        async with self.async_session() as session:
            # Check first so the common duplicate is a clean 409 without a
            # failed transaction. The IntegrityError handler still covers a race.
            duplicate = await session.execute(
                select(EmailTemplateModel.id).where(
                    EmailTemplateModel.organization_id == organization_id,
                    EmailTemplateModel.name == name,
                )
            )
            if duplicate.first() is not None:
                raise EmailTemplateNameConflictError(name)

            template = EmailTemplateModel(
                organization_id=organization_id,
                created_by=user_id,
                name=name,
                subject=subject,
                source_format=source_format,
                source_body=source_body,
                html_body=html_body,
                text_body=text_body,
                has_unsubscribe_placeholder=has_unsubscribe_placeholder,
            )
            session.add(template)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise EmailTemplateNameConflictError(name) from exc
            await session.refresh(template)
            return template

    async def update_email_template(
        self,
        organization_id: int,
        template_uuid: str,
        *,
        name: str,
        subject: str,
        source_format: str,
        source_body: str,
        html_body: str,
        text_body: Optional[str],
        has_unsubscribe_placeholder: bool,
    ) -> Optional[EmailTemplateModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(EmailTemplateModel).where(
                    EmailTemplateModel.organization_id == organization_id,
                    EmailTemplateModel.template_uuid == template_uuid,
                )
            )
            template = result.scalar_one_or_none()
            if template is None:
                return None

            duplicate = await session.execute(
                select(EmailTemplateModel.id).where(
                    EmailTemplateModel.organization_id == organization_id,
                    EmailTemplateModel.name == name,
                    EmailTemplateModel.template_uuid != template_uuid,
                )
            )
            if duplicate.first() is not None:
                raise EmailTemplateNameConflictError(name)

            template.name = name
            template.subject = subject
            template.source_format = source_format
            template.source_body = source_body
            template.html_body = html_body
            template.text_body = text_body
            template.has_unsubscribe_placeholder = has_unsubscribe_placeholder
            template.updated_at = datetime.now(UTC)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise EmailTemplateNameConflictError(name) from exc
            await session.refresh(template)
            return template

    async def delete_email_template(
        self, organization_id: int, template_uuid: str
    ) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                select(EmailTemplateModel).where(
                    EmailTemplateModel.organization_id == organization_id,
                    EmailTemplateModel.template_uuid == template_uuid,
                )
            )
            template = result.scalar_one_or_none()
            if template is None:
                return False
            await session.delete(template)
            await session.commit()
            return True
