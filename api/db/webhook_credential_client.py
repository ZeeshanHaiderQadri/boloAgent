"""Database client for managing webhook credentials."""

from datetime import UTC, datetime
from typing import List, Optional

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.orm.attributes import set_committed_value

from api.db.base_client import BaseDBClient
from api.db.models import EmailSenderModel, ExternalCredentialModel
from api.enums import CredentialPurpose
from api.services.credentials.cipher import (
    decrypt_credential_data,
    encrypt_credential_data,
)


class CredentialInUseError(Exception):
    """Raised when a credential still backs one or more email senders."""

    def __init__(self, sender_names: List[str]):
        self.sender_names = list(sender_names)
        super().__init__(
            "Credential is still used by email sender(s): "
            + ", ".join(self.sender_names)
        )


class WebhookCredentialClient(BaseDBClient):
    """Client for managing webhook credentials (organization-scoped, UUID-referenced)."""

    @staticmethod
    def _decrypt_for_read(
        credential: Optional[ExternalCredentialModel],
    ) -> Optional[ExternalCredentialModel]:
        """Replace the stored envelope with plaintext without dirtying the row.

        ``set_committed_value`` is important here: assigning directly would mark
        the JSON column as modified and could write plaintext back on flush.
        """
        if credential is not None:
            set_committed_value(
                credential,
                "credential_data",
                decrypt_credential_data(credential.credential_data),
            )
        return credential

    async def verify_credential_encryption_readable(self) -> None:
        """Decrypt one stored row so a wrong key fails at startup, not at call time.

        An empty table has nothing to prove. Decryption failures raise
        CredentialEncryptionError; database errors are left to the caller.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(ExternalCredentialModel)
                .order_by(ExternalCredentialModel.id)
                .limit(1)
            )
            credential = result.scalar_one_or_none()
        if credential is not None:
            decrypt_credential_data(credential.credential_data)

    async def create_credential(
        self,
        organization_id: int,
        user_id: int,
        name: str,
        credential_type: str,
        credential_data: dict,
        description: Optional[str] = None,
        purpose: str = CredentialPurpose.WEBHOOK.value,
    ) -> ExternalCredentialModel:
        """Create a new webhook credential.

        Args:
            organization_id: ID of the organization
            user_id: ID of the user creating the credential
            name: Display name for the credential
            credential_type: Type of credential (none, api_key, bearer_token, basic_auth, custom_header)
            credential_data: JSON data containing the credential details
            description: Optional description
            purpose: Owning feature (webhook or email_sender)

        Returns:
            The created ExternalCredentialModel with auto-generated UUID
        """
        async with self.async_session() as session:
            credential = ExternalCredentialModel(
                organization_id=organization_id,
                created_by=user_id,
                name=name,
                description=description,
                credential_type=credential_type,
                credential_data=encrypt_credential_data(credential_data),
                purpose=purpose,
            )

            session.add(credential)
            await session.commit()
            await session.refresh(credential)

            logger.info(
                f"Created webhook credential '{name}' ({credential.credential_uuid}) "
                f"for organization {organization_id}"
            )
            return self._decrypt_for_read(credential)

    async def get_credentials_for_organization(
        self,
        organization_id: int,
        active_only: bool = True,
        purpose: Optional[str] = CredentialPurpose.WEBHOOK.value,
    ) -> List[ExternalCredentialModel]:
        """Get the credentials of one purpose for an organization.

        Email sender credentials are stored in the same table but are never
        listed as webhook credentials; pass ``purpose=None`` to see all.

        Args:
            organization_id: ID of the organization
            active_only: If True, only return active (non-deleted) credentials
            purpose: Owning feature to filter by, or None for every purpose

        Returns:
            List of ExternalCredentialModel instances
        """
        async with self.async_session() as session:
            query = select(ExternalCredentialModel).where(
                ExternalCredentialModel.organization_id == organization_id
            )

            if active_only:
                query = query.where(ExternalCredentialModel.is_active.is_(True))

            if purpose is not None:
                query = query.where(ExternalCredentialModel.purpose == purpose)

            query = query.order_by(ExternalCredentialModel.name)

            result = await session.execute(query)
            return [
                self._decrypt_for_read(credential)
                for credential in result.scalars().all()
            ]

    async def get_credential_by_uuid(
        self,
        credential_uuid: str,
        organization_id: int,
        active_only: bool = True,
        purpose: Optional[str] = CredentialPurpose.WEBHOOK.value,
    ) -> Optional[ExternalCredentialModel]:
        """Get a credential by its UUID, scoped to organization and purpose.

        The purpose default is ``webhook`` so the webhook routes and every
        runtime consumer keep seeing only webhook credentials. Email sender
        credentials must be fetched with an explicit ``purpose``.

        Args:
            credential_uuid: The unique credential UUID
            organization_id: ID of the organization (for authorization)
            active_only: If True, only return if active
            purpose: Owning feature to filter by, or None for every purpose

        Returns:
            ExternalCredentialModel if found and authorized, None otherwise
        """
        async with self.async_session() as session:
            query = select(ExternalCredentialModel).where(
                ExternalCredentialModel.credential_uuid == credential_uuid,
                ExternalCredentialModel.organization_id == organization_id,
            )

            if active_only:
                query = query.where(ExternalCredentialModel.is_active.is_(True))

            if purpose is not None:
                query = query.where(ExternalCredentialModel.purpose == purpose)

            result = await session.execute(query)
            return self._decrypt_for_read(result.scalar_one_or_none())

    async def update_credential(
        self,
        credential_uuid: str,
        organization_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        credential_type: Optional[str] = None,
        credential_data: Optional[dict] = None,
        purpose: Optional[str] = CredentialPurpose.WEBHOOK.value,
    ) -> Optional[ExternalCredentialModel]:
        """Update a credential by UUID.

        Args:
            credential_uuid: The unique credential UUID
            organization_id: ID of the organization (for authorization)
            name: New name (if provided)
            description: New description (if provided)
            credential_type: New credential type (if provided)
            credential_data: New credential data (if provided)
            purpose: Owning feature to filter by, or None for every purpose

        Returns:
            Updated ExternalCredentialModel if found, None otherwise
        """
        async with self.async_session() as session:
            # First check if credential exists and belongs to organization
            credential = await self.get_credential_by_uuid(
                credential_uuid, organization_id, purpose=purpose
            )
            if not credential:
                return None

            # Build update values
            update_values = {"updated_at": datetime.now(UTC)}
            if name is not None:
                update_values["name"] = name
            if description is not None:
                update_values["description"] = description
            if credential_type is not None:
                update_values["credential_type"] = credential_type
            if credential_data is not None:
                update_values["credential_data"] = encrypt_credential_data(
                    credential_data
                )

            statement = update(ExternalCredentialModel).where(
                ExternalCredentialModel.credential_uuid == credential_uuid,
                ExternalCredentialModel.organization_id == organization_id,
            )
            if purpose is not None:
                statement = statement.where(ExternalCredentialModel.purpose == purpose)
            await session.execute(statement.values(**update_values))
            await session.commit()

            # Fetch updated credential, keeping the organization scope on the
            # re-select so a row that changed hands mid-update cannot be
            # returned to the wrong organization.
            re_select = select(ExternalCredentialModel).where(
                ExternalCredentialModel.credential_uuid == credential_uuid,
                ExternalCredentialModel.organization_id == organization_id,
            )
            if purpose is not None:
                re_select = re_select.where(ExternalCredentialModel.purpose == purpose)
            updated_credential = (await session.execute(re_select)).scalar_one_or_none()
            if updated_credential is None:
                return None

            logger.info(
                f"Updated webhook credential {credential_uuid} "
                f"for organization {organization_id}"
            )
            return self._decrypt_for_read(updated_credential)

    async def delete_credential(
        self,
        credential_uuid: str,
        organization_id: int,
        purpose: Optional[str] = CredentialPurpose.WEBHOOK.value,
    ) -> bool:
        """Soft delete a credential by UUID.

        Deletes are soft, so the ``ondelete=RESTRICT`` foreign key on
        ``email_senders.credential_id`` never fires. The referencing senders are
        checked explicitly instead: the call fails with ``CredentialInUseError``
        rather than leaving a sender pointing at an inactive credential.

        Args:
            credential_uuid: The unique credential UUID
            organization_id: ID of the organization (for authorization)
            purpose: Owning feature to filter by, or None for every purpose

        Returns:
            True if credential was deleted, False if not found

        Raises:
            CredentialInUseError: If an email sender still references it
        """
        async with self.async_session() as session:
            query = select(ExternalCredentialModel).where(
                ExternalCredentialModel.credential_uuid == credential_uuid,
                ExternalCredentialModel.organization_id == organization_id,
                ExternalCredentialModel.is_active.is_(True),
            )
            if purpose is not None:
                query = query.where(ExternalCredentialModel.purpose == purpose)

            credential = (await session.execute(query)).scalar_one_or_none()
            if credential is None:
                return False

            sender_names = list(
                (
                    await session.execute(
                        select(EmailSenderModel.name).where(
                            EmailSenderModel.credential_id == credential.id,
                            EmailSenderModel.organization_id == organization_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            if sender_names:
                raise CredentialInUseError(sender_names)

            credential.is_active = False
            credential.updated_at = datetime.now(UTC)
            await session.commit()

            logger.info(
                f"Soft deleted webhook credential {credential_uuid} "
                f"for organization {organization_id}"
            )
            return True

    async def validate_credential_uuid(
        self,
        credential_uuid: str,
        organization_id: int,
        purpose: Optional[str] = CredentialPurpose.WEBHOOK.value,
    ) -> bool:
        """Check if a credential UUID exists and belongs to the organization.

        This is useful for workflow validation to ensure referenced credentials exist.

        Args:
            credential_uuid: The credential UUID to validate
            organization_id: ID of the organization
            purpose: Owning feature to filter by, or None for every purpose

        Returns:
            True if valid, False otherwise
        """
        credential = await self.get_credential_by_uuid(
            credential_uuid, organization_id, purpose=purpose
        )
        return credential is not None
