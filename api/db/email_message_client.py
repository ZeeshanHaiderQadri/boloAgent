"""Database operations for campaign recipient rows and outbound email queue.

Campaign launch writes one ``email_messages`` row per eligible address in the
``held`` state; the same transaction releases them to ``queued`` for the send
worker. This module owns every SQL statement for those rows, always
organization-scoped where a caller could supply the id.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Iterable, List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from api.db.base_client import BaseDBClient
from api.db.models import (
    EmailCampaignModel,
    EmailMessageModel,
    EmailSuppressionModel,
    LeadModel,
)
from api.enums import EmailCampaignStatus, EmailMessageStatus


class EmailMessageClient(BaseDBClient):
    async def create_held_messages(
        self,
        organization_id: int,
        campaign_id: int,
        rows: list[dict[str, Any]],
    ) -> int:
        """Insert frozen recipient rows. Conflicts are ignored, not retried."""
        if not rows:
            return 0
        payload = [
            {
                **row,
                "organization_id": organization_id,
                "campaign_id": campaign_id,
                "status": EmailMessageStatus.HELD.value,
            }
            for row in rows
        ]
        async with self.async_session() as session:
            result = await session.execute(
                insert(EmailMessageModel).values(payload).on_conflict_do_nothing()
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def release_held_messages(
        self, organization_id: int, campaign_id: int
    ) -> int:
        async with self.async_session() as session:
            result = await session.execute(
                update(EmailMessageModel)
                .where(
                    EmailMessageModel.organization_id == organization_id,
                    EmailMessageModel.campaign_id == campaign_id,
                    EmailMessageModel.status == EmailMessageStatus.HELD.value,
                )
                .values(status=EmailMessageStatus.QUEUED.value)
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def claim_queued_messages(
        self, campaign_id: int, *, limit: int
    ) -> List[SimpleNamespace]:
        """Move up to ``limit`` due rows to ``sending`` and return snapshots.

        ``FOR UPDATE SKIP LOCKED`` keeps two workers from claiming the same
        row; snapshots are taken before commit so callers never touch expired
        ORM attributes.
        """
        now = datetime.now(UTC)
        async with self.async_session() as session:
            result = await session.execute(
                select(EmailMessageModel)
                .where(
                    EmailMessageModel.campaign_id == campaign_id,
                    EmailMessageModel.status == EmailMessageStatus.QUEUED.value,
                    (EmailMessageModel.scheduled_at.is_(None))
                    | (EmailMessageModel.scheduled_at <= now),
                )
                .order_by(EmailMessageModel.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            messages = list(result.scalars().all())
            snapshots = []
            for message in messages:
                message.status = EmailMessageStatus.SENDING.value
                message.attempts = (message.attempts or 0) + 1
                snapshots.append(
                    SimpleNamespace(
                        id=message.id,
                        message_uuid=message.message_uuid,
                        organization_id=message.organization_id,
                        campaign_id=message.campaign_id,
                        lead_id=message.lead_id,
                        sender_id=message.sender_id,
                        to_email=message.to_email,
                        to_name=message.to_name,
                        subject=message.subject,
                        html_body=message.html_body,
                        text_body=message.text_body,
                        headers=dict(message.headers or {}),
                        attempts=message.attempts,
                    )
                )
            await session.commit()
            return snapshots

    async def mark_message_sent(
        self, message_id: int, provider_message_id: Optional[str]
    ) -> None:
        async with self.async_session() as session:
            await session.execute(
                update(EmailMessageModel)
                .where(EmailMessageModel.id == message_id)
                .values(
                    status=EmailMessageStatus.SENT.value,
                    provider_message_id=provider_message_id,
                    error_message=None,
                    sent_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def mark_message_failed(self, message_id: int, error: str) -> None:
        async with self.async_session() as session:
            await session.execute(
                update(EmailMessageModel)
                .where(EmailMessageModel.id == message_id)
                .values(
                    status=EmailMessageStatus.FAILED.value,
                    error_message=error[:2000],
                )
            )
            await session.commit()

    async def mark_message_cancelled(self, message_id: int, reason: str) -> None:
        async with self.async_session() as session:
            await session.execute(
                update(EmailMessageModel)
                .where(EmailMessageModel.id == message_id)
                .values(
                    status=EmailMessageStatus.CANCELLED.value,
                    error_message=reason[:2000],
                )
            )
            await session.commit()

    async def fail_stale_sending(
        self, campaign_id: int, *, older_than: datetime
    ) -> int:
        """Fail rows a crashed worker left in ``sending``."""
        async with self.async_session() as session:
            result = await session.execute(
                update(EmailMessageModel)
                .where(
                    EmailMessageModel.campaign_id == campaign_id,
                    EmailMessageModel.status == EmailMessageStatus.SENDING.value,
                    EmailMessageModel.updated_at < older_than,
                )
                .values(
                    status=EmailMessageStatus.FAILED.value,
                    error_message="Send interrupted before the worker recorded a result.",
                )
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def cancel_pending_messages(
        self, organization_id: int, campaign_id: int, *, reason: str
    ) -> int:
        """Cancel every row not yet handed to the provider."""
        async with self.async_session() as session:
            result = await session.execute(
                update(EmailMessageModel)
                .where(
                    EmailMessageModel.organization_id == organization_id,
                    EmailMessageModel.campaign_id == campaign_id,
                    EmailMessageModel.status.in_(
                        [
                            EmailMessageStatus.HELD.value,
                            EmailMessageStatus.QUEUED.value,
                        ]
                    ),
                )
                .values(
                    status=EmailMessageStatus.CANCELLED.value,
                    error_message=reason[:2000],
                )
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def requeue_claimed_messages(
        self, organization_id: int, message_ids: list[int]
    ) -> int:
        """Return claimed-but-unsent rows to ``queued`` after a pause."""
        if not message_ids:
            return 0
        async with self.async_session() as session:
            result = await session.execute(
                update(EmailMessageModel)
                .where(
                    EmailMessageModel.organization_id == organization_id,
                    EmailMessageModel.id.in_(message_ids),
                    EmailMessageModel.status == EmailMessageStatus.SENDING.value,
                )
                .values(status=EmailMessageStatus.QUEUED.value)
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def requeue_cancelled_messages(
        self, organization_id: int, campaign_id: int
    ) -> int:
        """Return a stopped campaign's undelivered rows to the queue.

        Delivered, bounced and complained rows are the audit record and stay
        terminal; only rows a stop or a failure cancelled are retried.
        """
        async with self.async_session() as session:
            result = await session.execute(
                update(EmailMessageModel)
                .where(
                    EmailMessageModel.organization_id == organization_id,
                    EmailMessageModel.campaign_id == campaign_id,
                    EmailMessageModel.status == EmailMessageStatus.CANCELLED.value,
                )
                .values(
                    status=EmailMessageStatus.QUEUED.value, error_message=None
                )
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def cancel_messages_by_ids(
        self, organization_id: int, message_ids: list[int], *, reason: str
    ) -> int:
        """Cancel specific claimed rows (pause/stop between two sends)."""
        if not message_ids:
            return 0
        async with self.async_session() as session:
            result = await session.execute(
                update(EmailMessageModel)
                .where(
                    EmailMessageModel.organization_id == organization_id,
                    EmailMessageModel.id.in_(message_ids),
                    EmailMessageModel.status.in_(
                        [
                            EmailMessageStatus.HELD.value,
                            EmailMessageStatus.QUEUED.value,
                            EmailMessageStatus.SENDING.value,
                        ]
                    ),
                )
                .values(
                    status=EmailMessageStatus.CANCELLED.value,
                    error_message=reason[:2000],
                )
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def list_campaign_messages(
        self,
        organization_id: int,
        campaign_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Recent recipient rows for the live campaign progress screen."""
        async with self.async_session() as session:
            total = await session.scalar(
                select(func.count(EmailMessageModel.id)).where(
                    EmailMessageModel.organization_id == organization_id,
                    EmailMessageModel.campaign_id == campaign_id,
                )
            )
            rows = await session.execute(
                select(
                    EmailMessageModel.to_email,
                    EmailMessageModel.status,
                    EmailMessageModel.error_message,
                    EmailMessageModel.sent_at,
                    EmailMessageModel.updated_at,
                )
                .where(
                    EmailMessageModel.organization_id == organization_id,
                    EmailMessageModel.campaign_id == campaign_id,
                )
                .order_by(
                    EmailMessageModel.updated_at.desc(),
                    EmailMessageModel.id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
            items = [
                {
                    "to_email": to_email,
                    "status": str(status),
                    "error_message": error_message,
                    "sent_at": sent_at,
                    "updated_at": updated_at,
                }
                for to_email, status, error_message, sent_at, updated_at in rows.all()
            ]
            return items, int(total or 0)

    async def count_campaign_messages(
        self, organization_id: int, campaign_id: int
    ) -> dict[str, int]:
        async with self.async_session() as session:
            rows = await session.execute(
                select(EmailMessageModel.status, func.count(EmailMessageModel.id))
                .where(
                    EmailMessageModel.organization_id == organization_id,
                    EmailMessageModel.campaign_id == campaign_id,
                )
                .group_by(EmailMessageModel.status)
            )
            return {str(status): int(count) for status, count in rows.all()}

    async def add_campaign_counters(
        self,
        organization_id: int,
        campaign_id: int,
        *,
        sent: int = 0,
        failed: int = 0,
        unsubscribed: int = 0,
    ) -> None:
        if not (sent or failed or unsubscribed):
            return
        values: dict[str, Any] = {}
        if sent:
            values["sent_count"] = EmailCampaignModel.sent_count + sent
        if failed:
            values["failed_count"] = EmailCampaignModel.failed_count + failed
        if unsubscribed:
            values["unsubscribed_count"] = (
                EmailCampaignModel.unsubscribed_count + unsubscribed
            )
        async with self.async_session() as session:
            await session.execute(
                update(EmailCampaignModel)
                .where(
                    EmailCampaignModel.organization_id == organization_id,
                    EmailCampaignModel.id == campaign_id,
                )
                .values(**values)
            )
            await session.commit()

    async def lead_consent_map(
        self, organization_id: int, lead_ids: Iterable[int]
    ) -> dict[int, str]:
        ids = [int(lead_id) for lead_id in lead_ids if lead_id is not None]
        if not ids:
            return {}
        async with self.async_session() as session:
            rows = await session.execute(
                select(LeadModel.id, LeadModel.email_consent).where(
                    LeadModel.organization_id == organization_id,
                    LeadModel.id.in_(ids),
                )
            )
            return {int(row.id): str(row.email_consent or "unknown") for row in rows}

    async def suppress_address(
        self,
        organization_id: int,
        email: str,
        *,
        reason: str,
        campaign_id: Optional[int] = None,
    ) -> bool:
        """Suppress an address, opt its leads out, and bump the counter.

        Returns ``True`` when this call created the suppression row (so the
        caller can render "unsubscribed" rather than "already unsubscribed").
        """
        address = email.strip().lower()
        now = datetime.now(UTC)
        async with self.async_session() as session:
            inserted = await session.execute(
                insert(EmailSuppressionModel)
                .values(
                    organization_id=organization_id,
                    email=address,
                    reason=reason,
                )
                .on_conflict_do_nothing()
            )
            created = bool(inserted.rowcount)
            await session.execute(
                update(LeadModel)
                .where(
                    LeadModel.organization_id == organization_id,
                    func.lower(LeadModel.email) == address,
                )
                .values(
                    email_consent="opted_out",
                    email_consent_source="unsubscribe",
                    email_consent_at=now,
                )
            )
            if campaign_id is not None and created:
                await session.execute(
                    update(EmailCampaignModel)
                    .where(
                        EmailCampaignModel.organization_id == organization_id,
                        EmailCampaignModel.id == campaign_id,
                    )
                    .values(
                        unsubscribed_count=EmailCampaignModel.unsubscribed_count + 1
                    )
                )
            await session.commit()
            return created

    async def set_campaign_launch_fields(
        self,
        organization_id: int,
        campaign_id: int,
        **fields: Any,
    ) -> None:
        """Write freeze/status fields on a campaign, organization-scoped."""
        async with self.async_session() as session:
            await session.execute(
                update(EmailCampaignModel)
                .where(
                    EmailCampaignModel.organization_id == organization_id,
                    EmailCampaignModel.id == campaign_id,
                )
                .values(**fields)
            )
            await session.commit()

    async def mark_campaign_completed(
        self, organization_id: int, campaign_id: int
    ) -> bool:
        """Complete a campaign when no queued or sending rows remain.

        Only a launching or sending campaign can complete: a paused campaign
        keeps its queued rows, and a cancelled or failed one must never be
        rewritten by a worker that was still mid-batch when the operator
        stopped it.
        """
        async with self.async_session() as session:
            pending = await session.execute(
                select(func.count(EmailMessageModel.id)).where(
                    EmailMessageModel.organization_id == organization_id,
                    EmailMessageModel.campaign_id == campaign_id,
                    EmailMessageModel.status.in_(
                        [
                            EmailMessageStatus.QUEUED.value,
                            EmailMessageStatus.SENDING.value,
                        ]
                    ),
                )
            )
            if pending.scalar_one():
                return False
            result = await session.execute(
                update(EmailCampaignModel)
                .where(
                    EmailCampaignModel.organization_id == organization_id,
                    EmailCampaignModel.id == campaign_id,
                    EmailCampaignModel.status.in_(
                        [
                            EmailCampaignStatus.LAUNCHING.value,
                            EmailCampaignStatus.SENDING.value,
                        ]
                    ),
                )
                .values(status=EmailCampaignStatus.COMPLETED.value)
            )
            await session.commit()
            return bool(result.rowcount)
