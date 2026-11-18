"""API behaviour for sending domains: normalization, scoping, SES verification."""

import pytest

from api.enums import EmailDomainStatus
from api.services.email.ses import SesEmailAdapter

SES_SENDER = {
    "name": "SES",
    "provider": "ses",
    "from_name": "Bolo",
    "from_email": "hello@example.com",
    "settings": {"region": "eu-west-1"},
    "credentials": {
        "access_key_id": "AKIA_EXAMPLE",
        "secret_access_key": "secret",
    },
}


async def _make_user_and_org(db_session, slug: str):
    user, _ = await db_session.get_or_create_user_by_provider_id(f"{slug}_user")
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        f"{slug}_org", user.id
    )
    await db_session.update_user_selected_organization(user.id, org.id)
    return await db_session.get_user_by_id(user.id), org


async def _sender(client, payload: dict):
    response = await client.post("/api/v1/email/senders", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _domain(client, sender_uuid: str, domain: str = "example.com"):
    return await client.post(
        "/api/v1/email/domains",
        json={"domain": domain, "sender_uuid": sender_uuid},
    )


@pytest.mark.asyncio
async def test_create_domain_normalizes_and_inherits_the_provider(
    db_session, test_client_factory
):
    user, _ = await _make_user_and_org(db_session, "domain_create")
    async with test_client_factory(user) as client:
        sender = await _sender(client, SES_SENDER)
        created = await _domain(client, sender["uuid"], "Example.COM.")

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["domain"] == "example.com"
    assert body["provider"] == "ses"
    assert body["sender_uuid"] == sender["uuid"]
    assert body["status"] == "pending"
    assert body["verification"] == {}


@pytest.mark.asyncio
async def test_create_domain_rejects_urls_addresses_and_duplicates(
    db_session, test_client_factory
):
    user, _ = await _make_user_and_org(db_session, "domain_invalid")
    async with test_client_factory(user) as client:
        sender = await _sender(client, SES_SENDER)
        url = await _domain(client, sender["uuid"], "https://example.com")
        address = await _domain(client, sender["uuid"], "hello@example.com")
        malformed = await _domain(client, sender["uuid"], "example")
        first = await _domain(client, sender["uuid"], "example.com")
        duplicate = await _domain(client, sender["uuid"], "EXAMPLE.com")

    assert url.status_code == 400
    assert address.status_code == 400
    assert malformed.status_code == 400
    assert first.status_code == 201
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_domains_are_organization_scoped(db_session, test_client_factory):
    owner, _ = await _make_user_and_org(db_session, "domain_owner")
    outsider, _ = await _make_user_and_org(db_session, "domain_outsider")

    async with test_client_factory(owner) as client:
        sender = await _sender(client, SES_SENDER)
        created = await _domain(client, sender["uuid"])

    async with test_client_factory(outsider) as client:
        listing = await client.get("/api/v1/email/domains")
        direct = await client.get(f"/api/v1/email/domains/{created.json()['uuid']}")
        delete = await client.delete(f"/api/v1/email/domains/{created.json()['uuid']}")

    assert listing.json() == []
    assert direct.status_code == 404
    assert delete.status_code == 404


@pytest.mark.asyncio
async def test_renaming_a_domain_resets_its_verification(
    db_session, test_client_factory
):
    user, org = await _make_user_and_org(db_session, "domain_rename")
    async with test_client_factory(user) as client:
        sender = await _sender(client, SES_SENDER)
        created = await _domain(client, sender["uuid"])
        await db_session.record_email_domain_verification(
            organization_id=org.id,
            domain_uuid=created.json()["uuid"],
            status=EmailDomainStatus.VERIFIED,
            verification={"dkim_tokens": ["token-1"]},
        )
        renamed = await client.patch(
            f"/api/v1/email/domains/{created.json()['uuid']}",
            json={"domain": "mail.example.com"},
        )

    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["domain"] == "mail.example.com"
    assert renamed.json()["status"] == "pending"
    assert renamed.json()["verification"] == {}
    assert renamed.json()["last_checked_at"] is None


@pytest.mark.asyncio
async def test_delete_removes_the_domain(db_session, test_client_factory):
    user, _ = await _make_user_and_org(db_session, "domain_delete")
    async with test_client_factory(user) as client:
        sender = await _sender(client, SES_SENDER)
        created = await _domain(client, sender["uuid"])
        deleted = await client.delete(f"/api/v1/email/domains/{created.json()['uuid']}")
        listing = await client.get("/api/v1/email/domains")

    assert deleted.status_code == 200
    assert listing.json() == []


@pytest.mark.asyncio
async def test_ses_verification_records_dkim_tokens(
    monkeypatch, db_session, test_client_factory
):
    async def fake_verification(self, domain):
        assert domain == "example.com"
        return {
            "verified": True,
            "verification_status": "Success",
            "details": {
                "verification_status": "Success",
                "dkim_tokens": ["dkim-1", "dkim-2"],
            },
        }

    monkeypatch.setattr(SesEmailAdapter, "get_domain_verification", fake_verification)
    user, _ = await _make_user_and_org(db_session, "domain_verify")
    async with test_client_factory(user) as client:
        sender = await _sender(client, SES_SENDER)
        created = await _domain(client, sender["uuid"])
        verified = await client.post(
            f"/api/v1/email/domains/{created.json()['uuid']}/verify"
        )

    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "verified"
    assert verified.json()["verification"]["dkim_tokens"] == ["dkim-1", "dkim-2"]
    assert verified.json()["last_checked_at"] is not None


@pytest.mark.asyncio
async def test_ses_failed_verification_is_recorded(
    monkeypatch, db_session, test_client_factory
):
    async def fake_verification(self, domain):
        return {
            "verified": False,
            "verification_status": "Failed",
            "details": {"verification_status": "Failed", "dkim_tokens": []},
        }

    monkeypatch.setattr(SesEmailAdapter, "get_domain_verification", fake_verification)
    user, _ = await _make_user_and_org(db_session, "domain_failed")
    async with test_client_factory(user) as client:
        sender = await _sender(client, SES_SENDER)
        created = await _domain(client, sender["uuid"])
        checked = await client.post(
            f"/api/v1/email/domains/{created.json()['uuid']}/verify"
        )

    assert checked.status_code == 200
    assert checked.json()["status"] == "failed"


@pytest.mark.asyncio
async def test_smtp_domains_cannot_be_verified_through_the_api(
    db_session, test_client_factory
):
    user, _ = await _make_user_and_org(db_session, "domain_smtp")
    async with test_client_factory(user) as client:
        sender = await _sender(
            client,
            {
                "name": "SMTP",
                "provider": "smtp",
                "from_name": "Bolo",
                "from_email": "hello@example.com",
                "settings": {"preset": "hostinger"},
                "credentials": {
                    "username": "hello@example.com",
                    "password": "secret",
                },
            },
        )
        created = await _domain(client, sender["uuid"])
        checked = await client.post(
            f"/api/v1/email/domains/{created.json()['uuid']}/verify"
        )

    assert created.status_code == 201
    assert checked.status_code == 400
    assert "SMTP" in checked.json()["detail"]
