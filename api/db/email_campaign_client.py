"""Database operations for email campaigns, always organization-scoped.

The route layer resolves an audience and checks launch preconditions; this
module only stores and returns campaign rows and translates the unique name
constraint into a conflict error. Nothing here sends email.
"""

from datetime import UTC, datetime
from typing import Any, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.db.base_client import BaseDBClient
from api.db.models import EmailCampaignModel


class EmailCampaignNameConflictError(Exception):
    """Another campaign in this organization already uses the name."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"A campaign named {name!r} already exists.")


class EmailCampaignClient(BaseDBClient):
    """Organization-scoped email campaign operations."""

    async def list_email_campaigns(
        self, organization_id: int
    ) -> List[EmailCampaignModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(EmailCampaignModel)
                .where(EmailCampaignModel.organization_id == organization_id)
                .order_by(
                    EmailCampaignModel.updated_at.desc(),
                    EmailCampaignModel.name,
                )
            )
            return list(result.scalars().all())

    async def get_email_campaign(
        self, organization_id: int, campaign_uuid: str
    ) -> Optional[EmailCampaignModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(EmailCampaignModel).where(
                    EmailCampaignModel.organization_id == organization_id,
                    EmailCampaignModel.campaign_uuid == campaign_uuid,
                )
            )
            return result.scalar_one_or_none()

    async def get_email_campaign_by_id(
        self, organization_id: int, campaign_id: int
    ) -> Optional[EmailCampaignModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(EmailCampaignModel).where(
                    EmailCampaignModel.organization_id == organization_id,
                    EmailCampaignModel.id == campaign_id,
                )
            )
            return result.scalar_one_or_none()

    async def create_email_campaign(
        self,
        *,
        organization_id: int,
        user_id: int,
        name: str,
        audience_type: str,
        audience: dict[str, Any],
        consent_policy: str,
        sender_id: Optional[int] = None,
        template_id: Optional[int] = None,
        consent_override_reason: Optional[str] = None,
        consent_override_warning: Optional[str] = None,
    ) -> EmailCampaignModel:
        async with self.async_session() as session:
            duplicate = await session.execute(
                select(EmailCampaignModel.id).where(
                    EmailCampaignModel.organization_id == organization_id,
                    EmailCampaignModel.name == name,
                )
            )
            if duplicate.first() is not None:
                raise EmailCampaignNameConflictError(name)

            now = datetime.now(UTC)
            override = bool(consent_override_reason)
            campaign = EmailCampaignModel(
                organization_id=organization_id,
                created_by=user_id,
                name=name,
                sender_id=sender_id,
                template_id=template_id,
                audience_type=audience_type,
                audience=audience,
                consent_policy=consent_policy,
                consent_override_reason=consent_override_reason,
                consent_override_user_id=user_id if override else None,
                consent_override_at=now if override else None,
                consent_override_warning=(
                    consent_override_warning if override else None
                ),
            )
            session.add(campaign)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise EmailCampaignNameConflictError(name) from exc
            await session.refresh(campaign)
            return campaign

    async def update_email_campaign(
        self,
        organization_id: int,
        campaign_uuid: str,
        *,
        name: Optional[str] = None,
        sender_id: Any = ...,
        template_id: Any = ...,
        audience_type: Optional[str] = None,
        audience: Optional[dict[str, Any]] = None,
        consent_policy: Optional[str] = None,
        consent_override_reason: Any = ...,
        consent_override_warning: Any = ...,
        consent_override_user_id: Any = ...,
    ) -> Optional[EmailCampaignModel]:
        """Update the editable draft fields.

        ``...`` means "leave unchanged"; an explicit ``None`` clears a field
        (used for detaching the sender and for clearing the consent override).
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(EmailCampaignModel).where(
                    EmailCampaignModel.organization_id == organization_id,
                    EmailCampaignModel.campaign_uuid == campaign_uuid,
                )
            )
            campaign = result.scalar_one_or_none()
            if campaign is None:
                return None

            if name is not None:
                duplicate = await session.execute(
                    select(EmailCampaignModel.id).where(
                        EmailCampaignModel.organization_id == organization_id,
                        EmailCampaignModel.name == name,
                        EmailCampaignModel.campaign_uuid != campaign_uuid,
                    )
                )
                if duplicate.first() is not None:
                    raise EmailCampaignNameConflictError(name)
                campaign.name = name

            if sender_id is not ...:
                campaign.sender_id = sender_id
            if template_id is not ...:
                campaign.template_id = template_id
            if audience_type is not None:
                campaign.audience_type = audience_type
            if audience is not None:
                campaign.audience = audience
            if consent_policy is not None:
                campaign.consent_policy = consent_policy
            if consent_override_reason is not ...:
                campaign.consent_override_reason = consent_override_reason
            if consent_override_warning is not ...:
                campaign.consent_override_warning = consent_override_warning
            if consent_override_user_id is not ...:
                campaign.consent_override_user_id = consent_override_user_id
            if consent_override_reason is not ...:
                campaign.consent_override_at = (
                    datetime.now(UTC) if consent_override_reason else None
                )

            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise EmailCampaignNameConflictError(campaign.name) from exc
            await session.refresh(campaign)
            return campaign

    async def update_email_campaign_counts(
        self,
        organization_id: int,
        campaign_uuid: str,
        *,
        counts: dict[str, int],
    ) -> Optional[EmailCampaignModel]:
        """Store the last preview/launch counts on the campaign."""
        async with self.async_session() as session:
            result = await session.execute(
                select(EmailCampaignModel).where(
                    EmailCampaignModel.organization_id == organization_id,
                    EmailCampaignModel.campaign_uuid == campaign_uuid,
                )
            )
            campaign = result.scalar_one_or_none()
            if campaign is None:
                return None
            for field, value in counts.items():
                setattr(campaign, field, value)
            await session.commit()
            await session.refresh(campaign)
            return campaign

    async def delete_email_campaign(
        self, organization_id: int, campaign_uuid: str
    ) -> Optional[EmailCampaignModel]:
        """Delete a campaign row; the caller decides whether its state allows it."""
        async with self.async_session() as session:
            result = await session.execute(
                select(EmailCampaignModel).where(
                    EmailCampaignModel.organization_id == organization_id,
                    EmailCampaignModel.campaign_uuid == campaign_uuid,
                )
            )
            campaign = result.scalar_one_or_none()
            if campaign is None:
                return None
            await session.delete(campaign)
            await session.commit()
            return campaign
