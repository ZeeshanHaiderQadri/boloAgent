"""Database client for email senders and their encrypted credentials.

Every query is organization-scoped. Secret material never leaves this module in
plaintext for storage: credentials are encrypted with the same envelope as
webhook credentials and decrypted only for an explicit verify or test send.
"""

from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from api.db.base_client import BaseDBClient
from api.db.models import (
    EmailCampaignModel,
    EmailMessageModel,
    EmailSenderModel,
    ExternalCredentialModel,
)
from api.enums import CredentialPurpose, EmailProvider
from api.services.credentials.cipher import (
    decrypt_credential_data,
    encrypt_credential_data,
)
from api.services.email.campaign_state import SENDER_IN_USE_CAMPAIGN_STATUSES

# Distinguishes "leave this field alone" from "set it to NULL" in updates.
UNSET: Any = object()


class EmailResourceConflictError(Exception):
    """A unique constraint rejected the write; the caller should return 409."""

    def __init__(self, message: str):
        super().__init__(message)


class EmailSenderInUseError(Exception):
    """A sender still backs message history and cannot be hard-deleted."""

    def __init__(self, message_count: int):
        self.message_count = message_count
        super().__init__(
            f"This sender is referenced by {message_count} message(s); "
            "disable it instead of deleting it."
        )


class EmailSenderCampaignInUseError(Exception):
    """A draft, ready, launching or sending campaign still uses the sender."""

    def __init__(self, campaign_count: int):
        self.campaign_count = campaign_count
        super().__init__(
            f"This sender is used by {campaign_count} campaign(s) that are "
            "still draft, ready, launching or sending. Cancel or finish them "
            "before deleting the sender."
        )


class EmailSenderClient(BaseDBClient):
    """Organization-scoped email sender operations."""

    async def list_email_senders(self, organization_id: int) -> List[EmailSenderModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(EmailSenderModel)
                .where(EmailSenderModel.organization_id == organization_id)
                .order_by(EmailSenderModel.is_default.desc(), EmailSenderModel.name)
            )
            return list(result.scalars().all())

    async def get_email_sender(
        self, organization_id: int, sender_uuid: str
    ) -> Optional[EmailSenderModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(EmailSenderModel).where(
                    EmailSenderModel.organization_id == organization_id,
                    EmailSenderModel.sender_uuid == sender_uuid,
                )
            )
            return result.scalar_one_or_none()

    async def get_email_sender_by_id(
        self, organization_id: int, sender_id: int
    ) -> Optional[EmailSenderModel]:
        """Load a sender by primary key for campaign response rendering."""
        async with self.async_session() as session:
            result = await session.execute(
                select(EmailSenderModel).where(
                    EmailSenderModel.organization_id == organization_id,
                    EmailSenderModel.id == sender_id,
                )
            )
            return result.scalar_one_or_none()

    async def create_email_sender(
        self,
        *,
        organization_id: int,
        user_id: int,
        name: str,
        provider: EmailProvider,
        from_name: str,
        from_email: str,
        reply_to_email: Optional[str],
        settings: Dict[str, Any],
        credential_type: str,
        credential_data: Dict[str, Any],
        is_default: bool,
        daily_cap: Optional[int],
        rate_per_second: Optional[float],
    ) -> EmailSenderModel:
        """Create the encrypted credential and the sender in one transaction.

        A failure while creating the sender rolls the credential back too, so a
        half-configured sender can never appear in a list.
        """
        async with self.async_session() as session:
            credential = ExternalCredentialModel(
                organization_id=organization_id,
                created_by=user_id,
                name=name,
                credential_type=credential_type,
                credential_data=encrypt_credential_data(credential_data),
                purpose=CredentialPurpose.EMAIL_SENDER.value,
            )
            session.add(credential)
            try:
                await session.flush()

                if is_default:
                    await self._clear_default_sender(session, organization_id)

                sender = EmailSenderModel(
                    organization_id=organization_id,
                    name=name,
                    provider=provider.value,
                    from_name=from_name,
                    from_email=from_email,
                    reply_to_email=reply_to_email,
                    settings=settings,
                    credential_id=credential.id,
                    is_default=is_default,
                    daily_cap=daily_cap,
                    rate_per_second=rate_per_second,
                    created_by=user_id,
                )
                session.add(sender)
                await session.flush()
                sender_uuid = sender.sender_uuid
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise self._conflict_from(exc, name=name) from exc
            except Exception:
                await session.rollback()
                raise

            logger.info(
                f"Created email sender '{name}' ({sender_uuid}) "
                f"for organization {organization_id}"
            )
            return await self._reload_sender(session, organization_id, sender_uuid)

    async def update_email_sender(
        self,
        *,
        organization_id: int,
        sender_uuid: str,
        name: Optional[str] = None,
        from_name: Optional[str] = None,
        from_email: Optional[str] = None,
        reply_to_email: Any = UNSET,
        settings: Optional[Dict[str, Any]] = None,
        is_default: Optional[bool] = None,
        daily_cap: Any = UNSET,
        rate_per_second: Any = UNSET,
        credential_type: Optional[str] = None,
        credential_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[EmailSenderModel]:
        """Update a sender, optionally rotating its stored credential.

        The credential keeps the sender's name so the two lists never drift.
        Changing any connection setting resets ``last_verified_at`` and moves
        the sender back to ``pending``: the old verification proved the old
        configuration, not this one.
        """
        async with self.async_session() as session:
            sender = await self._get_sender_in_session(
                session, organization_id, sender_uuid
            )
            if sender is None:
                return None

            values: Dict[str, Any] = {"updated_at": datetime.now(UTC)}
            if name is not None:
                values["name"] = name
            if from_name is not None:
                values["from_name"] = from_name
            if from_email is not None:
                values["from_email"] = from_email
            if reply_to_email is not UNSET:
                values["reply_to_email"] = reply_to_email
            if settings is not None:
                values["settings"] = settings
            if daily_cap is not UNSET:
                values["daily_cap"] = daily_cap
            if rate_per_second is not UNSET:
                values["rate_per_second"] = rate_per_second
            if credential_data is not None:
                values["last_verified_at"] = None
            if from_email is not None or settings is not None:
                values["last_verified_at"] = None
            if (
                from_email is not None
                or settings is not None
                or credential_data is not None
            ):
                # A verification proves the configuration that was checked, not
                # the new one, so the sender has to be verified again.
                values["status"] = "pending"

            if is_default:
                await self._clear_default_sender(
                    session, organization_id, except_sender_id=sender.id
                )
            if is_default is not None:
                values["is_default"] = is_default

            credential = await session.get(
                ExternalCredentialModel, sender.credential_id
            )
            if credential is None or credential.organization_id != organization_id:
                raise EmailResourceConflictError(
                    "The sender's credential no longer exists"
                )
            credential_updates: Dict[str, Any] = {"updated_at": datetime.now(UTC)}
            if name is not None:
                credential_updates["name"] = name
            if credential_type is not None:
                credential_updates["credential_type"] = credential_type
            if credential_data is not None:
                credential_updates["credential_data"] = encrypt_credential_data(
                    credential_data
                )
            if len(credential_updates) > 1:
                for key, value in credential_updates.items():
                    setattr(credential, key, value)

            try:
                await session.execute(
                    update(EmailSenderModel)
                    .where(
                        EmailSenderModel.organization_id == organization_id,
                        EmailSenderModel.sender_uuid == sender_uuid,
                    )
                    .values(**values)
                )
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise self._conflict_from(exc, name=name or sender.name) from exc

            logger.info(
                f"Updated email sender {sender_uuid} for organization {organization_id}"
            )
            return await self._reload_sender(session, organization_id, sender_uuid)

    async def mark_email_sender_verified(
        self, organization_id: int, sender_uuid: str
    ) -> Optional[EmailSenderModel]:
        async with self.async_session() as session:
            now = datetime.now(UTC)
            result = await session.execute(
                update(EmailSenderModel)
                .where(
                    EmailSenderModel.organization_id == organization_id,
                    EmailSenderModel.sender_uuid == sender_uuid,
                )
                .values(
                    status="verified",
                    last_verified_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
            if result.rowcount == 0:
                return None
            return await self._reload_sender(session, organization_id, sender_uuid)

    async def set_email_sender_status(
        self, organization_id: int, sender_uuid: str, status: str
    ) -> Optional[EmailSenderModel]:
        async with self.async_session() as session:
            result = await session.execute(
                update(EmailSenderModel)
                .where(
                    EmailSenderModel.organization_id == organization_id,
                    EmailSenderModel.sender_uuid == sender_uuid,
                )
                .values(status=status, updated_at=datetime.now(UTC))
            )
            await session.commit()
            if result.rowcount == 0:
                return None
            return await self._reload_sender(session, organization_id, sender_uuid)

    async def delete_email_sender(self, organization_id: int, sender_uuid: str) -> bool:
        """Delete a sender that no active campaign and no message history uses.

        Messages keep a RESTRICT foreign key to their sender so delivery
        history can never lose its origin. A sender with history is therefore
        rejected here with :class:`EmailSenderInUseError`; the UI should offer
        "disable" instead.

        A sender referenced by a ``draft``, ``ready``, ``launching`` or
        ``sending`` campaign is rejected with :class:`EmailSenderCampaignInUseError`.
        Otherwise the finished campaigns are detached (``sender_id`` becomes
        NULL) and the sender is deleted in the same transaction, so the
        campaign keeps its identity snapshot columns and no other code path can
        leave a dangling reference behind.
        """
        async with self.async_session() as session:
            sender = await self._get_sender_in_session(
                session, organization_id, sender_uuid
            )
            if sender is None:
                return False

            message_count = (
                await session.execute(
                    select(func.count(EmailMessageModel.id)).where(
                        EmailMessageModel.sender_id == sender.id
                    )
                )
            ).scalar_one()
            if message_count:
                raise EmailSenderInUseError(int(message_count))

            active_campaign_count = (
                await session.execute(
                    select(func.count(EmailCampaignModel.id)).where(
                        EmailCampaignModel.organization_id == organization_id,
                        EmailCampaignModel.sender_id == sender.id,
                        EmailCampaignModel.status.in_(
                            list(SENDER_IN_USE_CAMPAIGN_STATUSES)
                        ),
                    )
                )
            ).scalar_one()
            if active_campaign_count:
                raise EmailSenderCampaignInUseError(int(active_campaign_count))

            # Detach finished campaigns first; NO ACTION would otherwise refuse
            # the delete. Snapshot columns keep the identity the campaign used.
            await session.execute(
                update(EmailCampaignModel)
                .where(
                    EmailCampaignModel.organization_id == organization_id,
                    EmailCampaignModel.sender_id == sender.id,
                )
                .values(sender_id=None)
            )

            credential = await session.get(
                ExternalCredentialModel, sender.credential_id
            )
            await session.delete(sender)
            if credential is not None and credential.organization_id == organization_id:
                credential.is_active = False
                credential.updated_at = datetime.now(UTC)
            await session.commit()

            logger.info(
                f"Deleted email sender {sender_uuid} for organization {organization_id}"
            )
            return True

    async def get_email_sender_credentials(
        self, organization_id: int
    ) -> Dict[int, Dict[str, Any]]:
        """Decrypt every email-sender credential, keyed by credential id.

        One query serves a whole sender list, and the result is used only to
        build masked hints for the UI — never returned as-is.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(ExternalCredentialModel).where(
                    ExternalCredentialModel.organization_id == organization_id,
                    ExternalCredentialModel.purpose
                    == CredentialPurpose.EMAIL_SENDER.value,
                    ExternalCredentialModel.is_active.is_(True),
                )
            )
            credentials = result.scalars().all()
        return {
            credential.id: decrypt_credential_data(credential.credential_data)
            for credential in credentials
        }

    async def get_sender_credential_data(
        self, organization_id: int, sender: EmailSenderModel
    ) -> Dict[str, Any]:
        """Decrypt the credential of one sender, scoped by organization."""
        async with self.async_session() as session:
            credential = await session.get(
                ExternalCredentialModel, sender.credential_id
            )
        if (
            credential is None
            or credential.organization_id != organization_id
            or not credential.is_active
            or credential.purpose != CredentialPurpose.EMAIL_SENDER.value
        ):
            raise EmailResourceConflictError(
                "The sender's credential is missing or inactive"
            )
        return decrypt_credential_data(credential.credential_data)

    @staticmethod
    async def _get_sender_in_session(session, organization_id, sender_uuid):
        return (
            await session.execute(
                select(EmailSenderModel).where(
                    EmailSenderModel.organization_id == organization_id,
                    EmailSenderModel.sender_uuid == sender_uuid,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def _reload_sender(session, organization_id, sender_uuid):
        return (
            await session.execute(
                select(EmailSenderModel).where(
                    EmailSenderModel.organization_id == organization_id,
                    EmailSenderModel.sender_uuid == sender_uuid,
                )
            )
        ).scalar_one()

    @staticmethod
    async def _clear_default_sender(session, organization_id, except_sender_id=None):
        statement = (
            update(EmailSenderModel)
            .where(
                EmailSenderModel.organization_id == organization_id,
                EmailSenderModel.is_default.is_(True),
            )
            .values(is_default=False)
        )
        if except_sender_id is not None:
            statement = statement.where(EmailSenderModel.id != except_sender_id)
        await session.execute(statement)

    @staticmethod
    def _conflict_from(exc: IntegrityError, *, name: str) -> EmailResourceConflictError:
        detail = str(exc.orig)
        if "uq_email_senders_org_name" in detail:
            return EmailResourceConflictError(
                f"An email sender named '{name}' already exists"
            )
        if "unique_org_credential_name" in detail:
            return EmailResourceConflictError(
                f"A credential named '{name}' already exists; "
                "choose a different sender name"
            )
        if "ix_email_senders_one_default" in detail:
            return EmailResourceConflictError(
                "Another sender became the default at the same time; retry"
            )
        return EmailResourceConflictError("The email sender could not be saved")
