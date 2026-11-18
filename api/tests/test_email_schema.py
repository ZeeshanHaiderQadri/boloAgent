"""Email schema behaviour: limits, lead consent, and credential separation."""

import pytest

from api.db.models import EmailSenderModel, LeadModel
from api.enums import EmailProvider
from api.services.email.limits import (
    PROVIDER_DEFAULTS,
    SenderLimits,
    effective_limits,
    provider_limits,
)


async def _make_user_and_org(db_session, slug: str):
    user, _ = await db_session.get_or_create_user_by_provider_id(f"{slug}_user")
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        f"{slug}_org", user.id
    )
    await db_session.update_user_selected_organization(user.id, org.id)
    return await db_session.get_user_by_id(user.id), org


async def _make_sender(db_session, user, org, name: str, **overrides):
    credential = await db_session.create_credential(
        organization_id=org.id,
        user_id=user.id,
        name=f"{name} credential",
        credential_type="basic_auth",
        credential_data={"username": "u", "password": "p"},
        purpose="email_sender",
    )
    async with db_session.async_session() as session:
        sender = EmailSenderModel(
            organization_id=org.id,
            name=name,
            provider=overrides.pop("provider", EmailProvider.SMTP.value),
            from_name=overrides.pop("from_name", "Bolo"),
            from_email=overrides.pop("from_email", "hello@example.test"),
            credential_id=credential.id,
            **overrides,
        )
        session.add(sender)
        await session.flush()
        return sender


def test_every_provider_has_conservative_defaults():
    assert set(PROVIDER_DEFAULTS) == set(EmailProvider)

    # A freshly connected account must not be able to blast at provider quota.
    smtp = provider_limits(EmailProvider.SMTP)
    assert smtp.daily_cap <= 100
    assert smtp.rate_per_second <= 1.0
    for provider in EmailProvider:
        limits = provider_limits(provider.value)  # values arrive as strings
        assert limits.daily_cap >= 1
        assert limits.rate_per_second > 0


def test_effective_limits_use_defaults_until_an_override_is_set():
    class Row:
        provider = "smtp"
        daily_cap = None
        rate_per_second = None

    assert effective_limits(Row()) == provider_limits("smtp")

    Row.daily_cap = 25
    Row.rate_per_second = 0.5
    assert effective_limits(Row()) == SenderLimits(daily_cap=25, rate_per_second=0.5)


@pytest.mark.asyncio
async def test_sender_rows_without_limits_resolve_to_provider_defaults(
    db_session,
):
    user, org = await _make_user_and_org(db_session, "email_limits")
    sender = await _make_sender(db_session, user, org, "Hostinger")

    assert sender.daily_cap is None
    assert sender.rate_per_second is None
    assert effective_limits(sender) == provider_limits(EmailProvider.SMTP)


@pytest.mark.asyncio
async def test_new_leads_start_with_unknown_email_consent(db_session):
    _, org = await _make_user_and_org(db_session, "email_consent")
    async with db_session.async_session() as session:
        lead = LeadModel(
            organization_id=org.id,
            dedupe_key="consent-lead",
            title="Consent Lead",
        )
        session.add(lead)
        await session.flush()

    assert lead.email_consent == "unknown"
    assert lead.email_consent_source is None
    assert lead.email_consent_at is None


@pytest.mark.asyncio
async def test_webhook_credential_list_hides_email_sender_credentials(
    db_session, test_client_factory
):
    user, org = await _make_user_and_org(db_session, "email_credentials")
    await db_session.create_credential(
        organization_id=org.id,
        user_id=user.id,
        name="Webhook secret",
        credential_type="bearer_token",
        credential_data={"token": "t"},
    )
    await db_session.create_credential(
        organization_id=org.id,
        user_id=user.id,
        name="SMTP password",
        credential_type="basic_auth",
        credential_data={"username": "u", "password": "p"},
        purpose="email_sender",
    )

    async with test_client_factory(user) as client:
        response = await client.get("/api/v1/credentials/")

    assert response.status_code == 200
    names = [item["name"] for item in response.json()]
    assert names == ["Webhook secret"]

    everything = await db_session.get_credentials_for_organization(org.id, purpose=None)
    assert sorted(item.name for item in everything) == [
        "SMTP password",
        "Webhook secret",
    ]
