"""Database operations for WhatsApp templates and senders.

Everything is organization-scoped. These rows are click-to-chat metadata only:
nothing here sends, retries or records delivery.
"""

from datetime import UTC, datetime
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from api.db.base_client import BaseDBClient
from api.db.models import WhatsAppSenderModel, WhatsAppTemplateModel


class WhatsAppConflictError(Exception):
    """A unique constraint in this organization would be violated."""

    def __init__(self, message: str):
        super().__init__(message)


class WhatsAppClient(BaseDBClient):
    """Organization-scoped WhatsApp template and sender operations."""

    async def list_whatsapp_templates(
        self, organization_id: int
    ) -> List[WhatsAppTemplateModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(WhatsAppTemplateModel)
                .where(WhatsAppTemplateModel.organization_id == organization_id)
                .order_by(
                    WhatsAppTemplateModel.updated_at.desc(),
                    WhatsAppTemplateModel.name,
                )
            )
            return list(result.scalars().all())

    async def get_whatsapp_template(
        self, organization_id: int, template_uuid: str
    ) -> Optional[WhatsAppTemplateModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(WhatsAppTemplateModel).where(
                    WhatsAppTemplateModel.organization_id == organization_id,
                    WhatsAppTemplateModel.template_uuid == template_uuid,
                )
            )
            return result.scalar_one_or_none()

    async def create_whatsapp_template(
        self,
        *,
        organization_id: int,
        user_id: int,
        name: str,
        language: str,
        category: str,
        body: str,
        variables: List[str],
    ) -> WhatsAppTemplateModel:
        async with self.async_session() as session:
            duplicate = await session.execute(
                select(WhatsAppTemplateModel.id).where(
                    WhatsAppTemplateModel.organization_id == organization_id,
                    WhatsAppTemplateModel.name == name,
                )
            )
            if duplicate.first() is not None:
                raise WhatsAppConflictError(
                    f"A WhatsApp template named {name!r} already exists."
                )
            template = WhatsAppTemplateModel(
                organization_id=organization_id,
                created_by=user_id,
                name=name,
                language=language,
                category=category,
                body=body,
                variables=variables,
            )
            session.add(template)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise WhatsAppConflictError(
                    f"A WhatsApp template named {name!r} already exists."
                ) from exc
            await session.refresh(template)
            return template

    async def update_whatsapp_template(
        self,
        organization_id: int,
        template_uuid: str,
        *,
        name: str,
        language: str,
        category: str,
        body: str,
        variables: List[str],
    ) -> Optional[WhatsAppTemplateModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(WhatsAppTemplateModel).where(
                    WhatsAppTemplateModel.organization_id == organization_id,
                    WhatsAppTemplateModel.template_uuid == template_uuid,
                )
            )
            template = result.scalar_one_or_none()
            if template is None:
                return None
            duplicate = await session.execute(
                select(WhatsAppTemplateModel.id).where(
                    WhatsAppTemplateModel.organization_id == organization_id,
                    WhatsAppTemplateModel.name == name,
                    WhatsAppTemplateModel.template_uuid != template_uuid,
                )
            )
            if duplicate.first() is not None:
                raise WhatsAppConflictError(
                    f"A WhatsApp template named {name!r} already exists."
                )
            template.name = name
            template.language = language
            template.category = category
            template.body = body
            template.variables = variables
            template.updated_at = datetime.now(UTC)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise WhatsAppConflictError(
                    f"A WhatsApp template named {name!r} already exists."
                ) from exc
            await session.refresh(template)
            return template

    async def delete_whatsapp_template(
        self, organization_id: int, template_uuid: str
    ) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                select(WhatsAppTemplateModel).where(
                    WhatsAppTemplateModel.organization_id == organization_id,
                    WhatsAppTemplateModel.template_uuid == template_uuid,
                )
            )
            template = result.scalar_one_or_none()
            if template is None:
                return False
            await session.delete(template)
            await session.commit()
            return True

    async def list_whatsapp_senders(
        self, organization_id: int
    ) -> List[WhatsAppSenderModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(WhatsAppSenderModel)
                .where(WhatsAppSenderModel.organization_id == organization_id)
                .order_by(
                    WhatsAppSenderModel.is_default.desc(),
                    WhatsAppSenderModel.label,
                )
            )
            return list(result.scalars().all())

    async def get_whatsapp_sender(
        self, organization_id: int, sender_uuid: str
    ) -> Optional[WhatsAppSenderModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(WhatsAppSenderModel).where(
                    WhatsAppSenderModel.organization_id == organization_id,
                    WhatsAppSenderModel.sender_uuid == sender_uuid,
                )
            )
            return result.scalar_one_or_none()

    async def get_default_whatsapp_sender(
        self, organization_id: int
    ) -> Optional[WhatsAppSenderModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(WhatsAppSenderModel).where(
                    WhatsAppSenderModel.organization_id == organization_id,
                    WhatsAppSenderModel.is_default.is_(True),
                )
            )
            return result.scalar_one_or_none()

    async def create_whatsapp_sender(
        self,
        *,
        organization_id: int,
        user_id: int,
        label: str,
        phone_e164: str,
        country: str,
        is_default: bool,
    ) -> WhatsAppSenderModel:
        async with self.async_session() as session:
            duplicate = await session.execute(
                select(WhatsAppSenderModel.id).where(
                    WhatsAppSenderModel.organization_id == organization_id,
                    WhatsAppSenderModel.phone_e164 == phone_e164,
                )
            )
            if duplicate.first() is not None:
                raise WhatsAppConflictError(
                    "This phone number is already saved as a WhatsApp sender."
                )
            existing = await session.execute(
                select(WhatsAppSenderModel.id).where(
                    WhatsAppSenderModel.organization_id == organization_id
                )
            )
            make_default = is_default or existing.first() is None
            if make_default:
                await session.execute(
                    update(WhatsAppSenderModel)
                    .where(WhatsAppSenderModel.organization_id == organization_id)
                    .values(is_default=False)
                )
            sender = WhatsAppSenderModel(
                organization_id=organization_id,
                created_by=user_id,
                label=label,
                phone_e164=phone_e164,
                country=country,
                is_default=make_default,
            )
            session.add(sender)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise WhatsAppConflictError(
                    "This phone number is already saved as a WhatsApp sender."
                ) from exc
            await session.refresh(sender)
            return sender

    async def update_whatsapp_sender(
        self,
        organization_id: int,
        sender_uuid: str,
        *,
        label: str,
        phone_e164: str,
        country: str,
        is_default: bool,
    ) -> Optional[WhatsAppSenderModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(WhatsAppSenderModel).where(
                    WhatsAppSenderModel.organization_id == organization_id,
                    WhatsAppSenderModel.sender_uuid == sender_uuid,
                )
            )
            sender = result.scalar_one_or_none()
            if sender is None:
                return None
            duplicate = await session.execute(
                select(WhatsAppSenderModel.id).where(
                    WhatsAppSenderModel.organization_id == organization_id,
                    WhatsAppSenderModel.phone_e164 == phone_e164,
                    WhatsAppSenderModel.sender_uuid != sender_uuid,
                )
            )
            if duplicate.first() is not None:
                raise WhatsAppConflictError(
                    "This phone number is already saved as a WhatsApp sender."
                )
            if is_default:
                await session.execute(
                    update(WhatsAppSenderModel)
                    .where(
                        WhatsAppSenderModel.organization_id == organization_id,
                        WhatsAppSenderModel.sender_uuid != sender_uuid,
                    )
                    .values(is_default=False)
                )
            sender.label = label
            sender.phone_e164 = phone_e164
            sender.country = country
            sender.is_default = is_default
            sender.updated_at = datetime.now(UTC)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise WhatsAppConflictError(
                    "This phone number is already saved as a WhatsApp sender."
                ) from exc
            await session.refresh(sender)
            return sender

    async def delete_whatsapp_sender(
        self, organization_id: int, sender_uuid: str
    ) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                select(WhatsAppSenderModel).where(
                    WhatsAppSenderModel.organization_id == organization_id,
                    WhatsAppSenderModel.sender_uuid == sender_uuid,
                )
            )
            sender = result.scalar_one_or_none()
            if sender is None:
                return False
            was_default = bool(sender.is_default)
            await session.delete(sender)
            await session.flush()
            if was_default:
                remaining = await session.execute(
                    select(WhatsAppSenderModel)
                    .where(WhatsAppSenderModel.organization_id == organization_id)
                    .order_by(WhatsAppSenderModel.id)
                    .limit(1)
                )
                successor = remaining.scalar_one_or_none()
                if successor is not None:
                    successor.is_default = True
            await session.commit()
            return True
