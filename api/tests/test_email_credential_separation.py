"""Email sender credentials stay outside the webhook credential API."""

import pytest

from api.db.models import EmailSenderModel
from api.db.webhook_credential_client import CredentialInUseError
from api.enums import EmailProvider


async def _make_user_and_org(db_session, slug: str):
    user, _ = await db_session.get_or_create_user_by_provider_id(f"{slug}_user")
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        f"{slug}_org", user.id
    )
    await db_session.update_user_selected_organization(user.id, org.id)
    return await db_session.get_user_by_id(user.id), org


@pytest.mark.asyncio
async def test_webhook_api_cannot_read_update_or_delete_email_credentials(
    db_session, test_client_factory
):
    user, org = await _make_user_and_org(db_session, "email_credential_scope")
    email_credential = await db_session.create_credential(
        organization_id=org.id,
        user_id=user.id,
        name="SMTP password",
        credential_type="basic_auth",
        credential_data={"username": "u", "password": "original"},
        purpose="email_sender",
    )
    url = f"/api/v1/credentials/{email_credential.credential_uuid}"

    async with test_client_factory(user) as client:
        fetched = await client.get(url)
        updated = await client.put(
            url, json={"credential_data": {"username": "x", "password": "pwned"}}
        )
        deleted = await client.delete(url)

    assert [fetched.status_code, updated.status_code, deleted.status_code] == [
        404,
        404,
        404,
    ]
    assert not await db_session.validate_credential_uuid(
        email_credential.credential_uuid, org.id
    )

    stored = await db_session.get_credential_by_uuid(
        email_credential.credential_uuid, org.id, purpose="email_sender"
    )
    assert stored.credential_data == {"username": "u", "password": "original"}


@pytest.mark.asyncio
async def test_deleting_a_credential_used_by_a_sender_is_blocked(db_session):
    user, org = await _make_user_and_org(db_session, "email_credential_in_use")
    credential = await db_session.create_credential(
        organization_id=org.id,
        user_id=user.id,
        name="Support password",
        credential_type="basic_auth",
        credential_data={"username": "u", "password": "p"},
        purpose="email_sender",
    )
    async with db_session.async_session() as session:
        session.add(
            EmailSenderModel(
                organization_id=org.id,
                name="Support",
                provider=EmailProvider.SMTP.value,
                from_name="Bolo",
                from_email="hello@example.test",
                credential_id=credential.id,
            )
        )
        await session.commit()

    with pytest.raises(CredentialInUseError, match="Support"):
        await db_session.delete_credential(
            credential.credential_uuid, org.id, purpose="email_sender"
        )

    still_active = await db_session.get_credential_by_uuid(
        credential.credential_uuid, org.id, purpose="email_sender"
    )
    assert still_active is not None


@pytest.mark.asyncio
async def test_delete_guard_ignores_other_organizations_senders(db_session):
    user, org = await _make_user_and_org(db_session, "delete_guard_a")
    other_user, other_org = await _make_user_and_org(db_session, "delete_guard_b")
    credential = await db_session.create_credential(
        organization_id=org.id,
        user_id=user.id,
        name="Shared password",
        credential_type="basic_auth",
        credential_data={"username": "u", "password": "p"},
        purpose="email_sender",
    )

    async with db_session.async_session() as session:
        for owner_org, sender_name in (
            (org, "Own sender"),
            (other_org, "Foreign sender"),
        ):
            session.add(
                EmailSenderModel(
                    organization_id=owner_org.id,
                    name=sender_name,
                    provider=EmailProvider.SMTP.value,
                    from_name="Bolo",
                    from_email="hello@example.test",
                    credential_id=credential.id,
                )
            )
        await session.commit()

    with pytest.raises(CredentialInUseError) as exc_info:
        await db_session.delete_credential(
            credential.credential_uuid, org.id, purpose="email_sender"
        )

    assert exc_info.value.sender_names == ["Own sender"]
    assert "Foreign sender" not in str(exc_info.value)
