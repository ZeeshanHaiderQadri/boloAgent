"""API behaviour for email senders: secrets, scoping, limits, verify, test send."""

import json

import pytest
from sqlalchemy import select

from api.db.models import (
    EmailMessageModel,
    EmailSenderModel,
    ExternalCredentialModel,
)
from api.services.credentials.cipher import decrypt_credential_data
from api.services.email.adapter import (
    EmailConfigurationError,
    ProviderSendResult,
)
from api.services.email.smtp import SmtpEmailAdapter

SMTP_PAYLOAD = {
    "name": "Support",
    "provider": "smtp",
    "from_name": "Bolo Support",
    "from_email": "support@example.com",
    "settings": {"preset": "hostinger"},
    "credentials": {
        "username": "support@example.com",
        "password": "hunter2-smtp",
    },
}


async def _make_user_and_org(db_session, slug: str):
    user, _ = await db_session.get_or_create_user_by_provider_id(f"{slug}_user")
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        f"{slug}_org", user.id
    )
    await db_session.update_user_selected_organization(user.id, org.id)
    return await db_session.get_user_by_id(user.id), org


async def _create_sender(client, payload: dict | None = None):
    return await client.post("/api/v1/email/senders", json=payload or SMTP_PAYLOAD)


@pytest.mark.asyncio
async def test_create_sender_encrypts_credentials_and_expands_the_preset(
    db_session, test_client_factory
):
    user, org = await _make_user_and_org(db_session, "sender_create")
    async with test_client_factory(user) as client:
        response = await _create_sender(client)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["provider"] == "smtp"
    assert body["status"] == "pending"
    assert body["settings"] == {
        "host": "smtp.hostinger.com",
        "port": 465,
        "security": "ssl",
    }
    assert body["limits"] == {"daily_cap": 100, "rate_per_second": 1.0}
    assert body["credentials_configured"] is True
    assert body["credential_hint"] == {"username": "support@example.com"}
    assert "hunter2-smtp" not in json.dumps(body)

    async with db_session.async_session() as session:
        sender = (
            await session.execute(
                select(EmailSenderModel).where(
                    EmailSenderModel.organization_id == org.id
                )
            )
        ).scalar_one()
        credential = await session.get(ExternalCredentialModel, sender.credential_id)
        assert credential.purpose == "email_sender"
        assert credential.credential_type == "basic_auth"
        # The stored envelope is not the plaintext payload.
        assert credential.credential_data != SMTP_PAYLOAD["credentials"]
        assert decrypt_credential_data(credential.credential_data) == {
            "username": "support@example.com",
            "password": "hunter2-smtp",
        }


@pytest.mark.asyncio
async def test_create_sender_rejects_unsupported_or_incomplete_input(
    db_session, test_client_factory
):
    user, _ = await _make_user_and_org(db_session, "sender_invalid")
    async with test_client_factory(user) as client:
        mailgun = await _create_sender(
            client,
            {
                **SMTP_PAYLOAD,
                "provider": "mailgun",
                "settings": {"domain": "example.com"},
                "credentials": {"api_key": "key"},
            },
        )
        missing_password = await _create_sender(
            client,
            {
                **SMTP_PAYLOAD,
                "credentials": {"username": "support@example.com"},
            },
        )
        bad_limit = await _create_sender(client, {**SMTP_PAYLOAD, "daily_cap": 0})
        bad_address = await _create_sender(
            client, {**SMTP_PAYLOAD, "from_email": "not-an-address"}
        )

    assert mailgun.status_code == 400
    assert "not available yet" in mailgun.json()["detail"]
    assert missing_password.status_code == 400
    assert "password" in missing_password.json()["detail"]
    assert bad_limit.status_code == 400
    assert bad_address.status_code == 400


@pytest.mark.asyncio
async def test_senders_are_organization_scoped(db_session, test_client_factory):
    owner, _ = await _make_user_and_org(db_session, "sender_owner")
    outsider, _ = await _make_user_and_org(db_session, "sender_outsider")

    async with test_client_factory(owner) as client:
        created = await _create_sender(client)
    sender_uuid = created.json()["uuid"]

    async with test_client_factory(outsider) as client:
        listing = await client.get("/api/v1/email/senders")
        direct = await client.get(f"/api/v1/email/senders/{sender_uuid}")
        update = await client.patch(
            f"/api/v1/email/senders/{sender_uuid}", json={"name": "Hijacked"}
        )

    assert listing.json() == []
    assert direct.status_code == 404
    assert update.status_code == 404


@pytest.mark.asyncio
async def test_only_one_sender_is_default(db_session, test_client_factory):
    user, _ = await _make_user_and_org(db_session, "sender_default")
    async with test_client_factory(user) as client:
        first = await _create_sender(client, {**SMTP_PAYLOAD, "is_default": True})
        second = await _create_sender(
            client,
            {**SMTP_PAYLOAD, "name": "Sales", "from_email": "sales@example.com"},
        )
        switched = await client.patch(
            f"/api/v1/email/senders/{second.json()['uuid']}",
            json={"is_default": True},
        )
        listing = await client.get("/api/v1/email/senders")

    assert first.json()["is_default"] is True
    assert switched.status_code == 200
    assert switched.json()["is_default"] is True
    flags = {item["name"]: item["is_default"] for item in listing.json()}
    assert flags == {"Support": False, "Sales": True}


@pytest.mark.asyncio
async def test_update_rotates_a_secret_and_requires_reverification(
    db_session, test_client_factory
):
    user, _ = await _make_user_and_org(db_session, "sender_update")
    async with test_client_factory(user) as client:
        created = await _create_sender(client)
        sender_uuid = created.json()["uuid"]
        await db_session.mark_email_sender_verified(
            user.selected_organization_id, sender_uuid
        )
        rotated = await client.patch(
            f"/api/v1/email/senders/{sender_uuid}",
            json={"credentials": {"password": "a-new-password"}},
        )

    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["status"] == "pending"
    assert rotated.json()["last_verified_at"] is None
    assert "a-new-password" not in rotated.text

    assert await db_session.get_sender_credential_data(
        user.selected_organization_id,
        await db_session.get_email_sender(user.selected_organization_id, sender_uuid),
    ) == {
        "username": "support@example.com",
        "password": "a-new-password",
    }


@pytest.mark.asyncio
async def test_update_can_clear_reply_to_and_return_to_provider_limits(
    db_session, test_client_factory
):
    user, _ = await _make_user_and_org(db_session, "sender_clear")
    async with test_client_factory(user) as client:
        created = await _create_sender(
            client,
            {
                **SMTP_PAYLOAD,
                "reply_to_email": "replies@example.com",
                "daily_cap": 25,
                "rate_per_second": 0.5,
            },
        )
        assert created.json()["limits"] == {"daily_cap": 25, "rate_per_second": 0.5}

        cleared = await client.patch(
            f"/api/v1/email/senders/{created.json()['uuid']}",
            json={
                "reply_to_email": None,
                "daily_cap": None,
                "rate_per_second": None,
            },
        )

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["reply_to_email"] is None
    assert cleared.json()["daily_cap_override"] is None
    assert cleared.json()["rate_per_second_override"] is None
    assert cleared.json()["limits"] == {"daily_cap": 100, "rate_per_second": 1.0}


@pytest.mark.asyncio
async def test_delete_sender_deactivates_its_credential(
    db_session, test_client_factory
):
    user, org = await _make_user_and_org(db_session, "sender_delete")
    async with test_client_factory(user) as client:
        created = await _create_sender(client)
        sender_uuid = created.json()["uuid"]
        deleted = await client.delete(f"/api/v1/email/senders/{sender_uuid}")
        missing = await client.get(f"/api/v1/email/senders/{sender_uuid}")

    assert deleted.status_code == 200
    assert missing.status_code == 404

    async with db_session.async_session() as session:
        credential = (
            await session.execute(
                select(ExternalCredentialModel).where(
                    ExternalCredentialModel.organization_id == org.id
                )
            )
        ).scalar_one()
        assert credential.is_active is False


@pytest.mark.asyncio
async def test_sender_with_message_history_cannot_be_deleted(
    db_session, test_client_factory
):
    user, org = await _make_user_and_org(db_session, "sender_history")
    async with test_client_factory(user) as client:
        created = await _create_sender(client)
        sender_uuid = created.json()["uuid"]

        sender = await db_session.get_email_sender(org.id, sender_uuid)
        async with db_session.async_session() as session:
            session.add(
                EmailMessageModel(
                    organization_id=org.id,
                    sender_id=sender.id,
                    to_email="customer@example.net",
                    subject="History",
                    html_body="<p>History</p>",
                )
            )
            await session.flush()

        response = await client.delete(f"/api/v1/email/senders/{sender_uuid}")

    assert response.status_code == 409
    assert "message(s)" in response.json()["detail"]


@pytest.mark.asyncio
async def test_verify_marks_the_sender_verified(
    monkeypatch, db_session, test_client_factory
):
    async def fake_verify(self):
        return None

    monkeypatch.setattr(SmtpEmailAdapter, "verify", fake_verify)
    user, _ = await _make_user_and_org(db_session, "sender_verify")
    async with test_client_factory(user) as client:
        created = await _create_sender(client)
        verified = await client.post(
            f"/api/v1/email/senders/{created.json()['uuid']}/verify"
        )

    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "verified"
    assert verified.json()["last_verified_at"] is not None


@pytest.mark.asyncio
async def test_verify_maps_configuration_errors_to_400(
    monkeypatch, db_session, test_client_factory
):
    async def fake_verify(self):
        raise EmailConfigurationError("SMTP authentication failed")

    monkeypatch.setattr(SmtpEmailAdapter, "verify", fake_verify)
    user, _ = await _make_user_and_org(db_session, "sender_verify_fail")
    async with test_client_factory(user) as client:
        created = await _create_sender(client)
        failed = await client.post(
            f"/api/v1/email/senders/{created.json()['uuid']}/verify"
        )

    assert failed.status_code == 400
    assert "authentication failed" in failed.json()["detail"]


@pytest.mark.asyncio
async def test_test_send_returns_the_provider_message_id(
    monkeypatch, db_session, test_client_factory
):
    sent = {}

    async def fake_send(self, message):
        sent["to"] = message.to_email
        return ProviderSendResult(provider_message_id="smtp-message-1")

    monkeypatch.setattr(SmtpEmailAdapter, "send", fake_send)
    user, _ = await _make_user_and_org(db_session, "sender_test_send")
    async with test_client_factory(user) as client:
        created = await _create_sender(client)
        response = await client.post(
            f"/api/v1/email/senders/{created.json()['uuid']}/test-send",
            json={"to_email": "reviewer@example.net"},
        )
        invalid = await client.post(
            f"/api/v1/email/senders/{created.json()['uuid']}/test-send",
            json={"to_email": "not-an-address"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["provider_message_id"] == "smtp-message-1"
    assert sent["to"] == "reviewer@example.net"
    assert invalid.status_code == 400
