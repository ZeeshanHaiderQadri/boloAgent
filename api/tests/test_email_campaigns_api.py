"""Draft campaign CRUD: consent storage, scoping, name conflicts, deletion."""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from api.constants import EMAIL_CONSENT_WARNING
from api.db.models import (
    EmailCampaignModel,
    EmailSenderModel,
    EmailTemplateModel,
    ExternalCredentialModel,
    LeadModel,
)
from api.enums import CredentialPurpose
from api.services.credentials.cipher import encrypt_credential_data

UNSUBSCRIBE = '<p><a href="{{unsubscribe_link}}">Unsubscribe</a></p>'


@pytest.fixture(autouse=True)
def _allow_preview_limits(monkeypatch):
    """Keep Redis out of these tests; limits have their own coverage."""

    async def allow(**kwargs):
        return True

    monkeypatch.setattr("api.routes.email_campaigns.allow_campaign_preview", allow)


async def _user_and_org(db_session, slug: str):
    user, _ = await db_session.get_or_create_user_by_provider_id(f"{slug}_user")
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        f"{slug}_org", user.id
    )
    await db_session.update_user_selected_organization(user.id, org.id)
    snapshot = SimpleNamespace(id=user.id, selected_organization_id=org.id, email=None)
    return snapshot, org


async def _lead(session, org_id, key, *, email, consent="opted_in"):
    lead = LeadModel(
        organization_id=org_id,
        dedupe_key=key,
        title=f"Lead {key}",
        email=email,
        email_consent=consent,
    )
    session.add(lead)
    await session.flush()
    return lead


async def _sender(
    db_session,
    org_id,
    user_id,
    *,
    name="Campaign sender",
    status="verified",
    daily_cap=100,
):
    async with db_session.async_session() as session:
        credential = ExternalCredentialModel(
            organization_id=org_id,
            created_by=user_id,
            name=name,
            credential_type="basic_auth",
            credential_data=encrypt_credential_data(
                {"username": "demo", "password": "fake-password"}
            ),
            purpose=CredentialPurpose.EMAIL_SENDER.value,
        )
        session.add(credential)
        await session.flush()
        sender = EmailSenderModel(
            organization_id=org_id,
            name=name,
            provider="smtp",
            from_name="Demo Sender",
            from_email="demo@apex-software-demo.com",
            settings={
                "host": "smtp.hostinger.com",
                "port": 465,
                "security": "ssl",
            },
            credential_id=credential.id,
            status=status,
            daily_cap=daily_cap,
            rate_per_second=1.0,
        )
        session.add(sender)
        await session.commit()
        return SimpleNamespace(id=sender.id, uuid=sender.sender_uuid)


async def _template(
    db_session,
    org_id,
    user_id,
    *,
    name="Campaign template",
    has_unsubscribe=True,
):
    async with db_session.async_session() as session:
        template = EmailTemplateModel(
            organization_id=org_id,
            created_by=user_id,
            name=name,
            subject="Hello {{business_name}}",
            source_format="html",
            source_body=f"<p>Hi</p>{UNSUBSCRIBE}" if has_unsubscribe else "<p>Hi</p>",
            html_body=f"<p>Hi</p>{UNSUBSCRIBE}" if has_unsubscribe else "<p>Hi</p>",
            text_body="Hi",
            has_unsubscribe_placeholder=has_unsubscribe,
        )
        session.add(template)
        await session.commit()
        return SimpleNamespace(id=template.id, uuid=template.template_uuid)


async def _leads(db_session, org_id, count=2):
    async with db_session.async_session() as session:
        leads = [
            await _lead(
                session,
                org_id,
                f"campaign-lead-{index}",
                email=f"owner{index}@acme-plumbing.com",
            )
            for index in range(count)
        ]
        await session.commit()
        return [lead.id for lead in leads]


def _audience(*lead_ids: int) -> dict:
    return {"audience_type": "leads", "lead_ids": list(lead_ids)}


def _create_payload(lead_ids, **overrides) -> dict:
    payload = {
        "name": "Spring outreach",
        "audience": _audience(*lead_ids),
    }
    payload.update(overrides)
    return payload


async def _create(client, lead_ids, **overrides):
    return await client.post(
        "/api/v1/email/campaigns", json=_create_payload(lead_ids, **overrides)
    )


@pytest.mark.asyncio
async def test_create_list_get_update_and_delete_a_campaign(
    db_session, test_client_factory
):
    user, org = await _user_and_org(db_session, "campaign_crud")
    ids = await _leads(db_session, org.id, count=2)
    sender = await _sender(db_session, org.id, user.id, daily_cap=10)
    template = await _template(db_session, org.id, user.id)

    async with test_client_factory(user) as client:
        created = await _create(
            client,
            ids,
            sender_uuid=sender.uuid,
            template_uuid=template.uuid,
        )
        assert created.status_code == 201, created.text
        body = created.json()
        campaign_uuid = body["uuid"]
        assert body["status"] == "draft"
        assert body["name"] == "Spring outreach"
        assert body["audience"] == {"lead_ids": ids}
        assert body["consent_policy"] == "opted_in_only"
        assert body["consent_override_reason"] is None
        assert body["counts"]["recipient_count"] == 0
        assert body["sender"]["daily_cap"] == 10
        assert body["sender"]["estimated_days"] is None
        assert body["sender"]["guidance"].startswith("10 per day")
        assert body["template"]["has_unsubscribe_placeholder"] is True
        assert body["template"]["unsubscribe_warning"] is None

        listed = await client.get("/api/v1/email/campaigns")
        assert listed.status_code == 200
        assert [item["uuid"] for item in listed.json()] == [campaign_uuid]

        duplicate = await _create(client, ids)
        assert duplicate.status_code == 409

        preview = await client.post(
            "/api/v1/email/campaigns/preview",
            json={
                "audience": _audience(*ids),
                "campaign_uuid": campaign_uuid,
            },
        )
        assert preview.status_code == 200, preview.text

        fetched = await client.get(f"/api/v1/email/campaigns/{campaign_uuid}")
        assert fetched.status_code == 200
        assert fetched.json()["counts"]["eligible_count"] == 2
        assert fetched.json()["sender"]["estimated_days"] == 1
        assert (
            fetched.json()["sender"]["guidance"]
            == "10 per day, this campaign will take 1 day."
        )

        updated = await client.patch(
            f"/api/v1/email/campaigns/{campaign_uuid}",
            json={"name": "Spring outreach v2", "sender_uuid": None},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["name"] == "Spring outreach v2"
        assert updated.json()["sender"] is None

        deleted = await client.delete(f"/api/v1/email/campaigns/{campaign_uuid}")
        assert deleted.status_code == 200
        assert deleted.json() == {"status": "deleted", "uuid": campaign_uuid}

        missing = await client.get(f"/api/v1/email/campaigns/{campaign_uuid}")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_include_unknown_create_stores_reason_user_and_warning(
    db_session, test_client_factory
):
    user, org = await _user_and_org(db_session, "campaign_consent_store")
    async with db_session.async_session() as session:
        lead = await _lead(
            session, org.id, "consent-store", email="a@x.com", consent="unknown"
        )
        await session.commit()
        lead_id = lead.id

    async with test_client_factory(user) as client:
        missing_reason = await _create(
            client, [lead_id], consent_policy="include_unknown"
        )
        created = await _create(
            client,
            [lead_id],
            consent_policy="include_unknown",
            consent_override_reason="Customers asked to be contacted.",
        )

    assert missing_reason.status_code == 422
    assert created.status_code == 201, created.text
    async with db_session.async_session() as session:
        campaign = (
            await session.execute(
                select(EmailCampaignModel).where(
                    EmailCampaignModel.campaign_uuid == created.json()["uuid"]
                )
            )
        ).scalar_one()
        assert campaign.consent_override_reason == "Customers asked to be contacted."
        assert campaign.consent_override_user_id == user.id
        assert campaign.consent_override_at is not None
        assert campaign.consent_override_warning == EMAIL_CONSENT_WARNING


@pytest.mark.asyncio
async def test_campaigns_are_organization_scoped(db_session, test_client_factory):
    user, org = await _user_and_org(db_session, "campaign_scope")
    other_user, other_org = await _user_and_org(db_session, "campaign_scope_other")
    ids = await _leads(db_session, org.id, count=1)
    other_sender = await _sender(db_session, other_org.id, other_user.id)

    async with test_client_factory(user) as client:
        created = await _create(client, ids)
        campaign_uuid = created.json()["uuid"]
        foreign_sender = await _create(
            client, ids, name="Other sender", sender_uuid=other_sender.uuid
        )
        no_such_campaign = await client.get(
            "/api/v1/email/campaigns/00000000-0000-0000-0000-000000000000"
        )

    assert foreign_sender.status_code == 404
    assert no_such_campaign.status_code == 404

    async with test_client_factory(other_user) as other_client:
        other_view = await other_client.get(f"/api/v1/email/campaigns/{campaign_uuid}")
    assert other_view.status_code == 404


@pytest.mark.asyncio
async def test_only_draft_or_ready_campaigns_can_be_edited_or_deleted(
    db_session, test_client_factory
):
    user, org = await _user_and_org(db_session, "campaign_delete_guard")
    ids = await _leads(db_session, org.id, count=1)

    async with test_client_factory(user) as client:
        created = await _create(client, ids, name="Guarded")
        campaign_uuid = created.json()["uuid"]
        async with db_session.async_session() as session:
            row = (
                await session.execute(
                    select(EmailCampaignModel).where(
                        EmailCampaignModel.campaign_uuid == campaign_uuid
                    )
                )
            ).scalar_one()
            row.status = "launching"
            await session.commit()

        edited = await client.patch(
            f"/api/v1/email/campaigns/{campaign_uuid}", json={"name": "Nope"}
        )
        deleted = await client.delete(f"/api/v1/email/campaigns/{campaign_uuid}")
        preview = await client.post(
            "/api/v1/email/campaigns/preview",
            json={"audience": _audience(*ids), "campaign_uuid": campaign_uuid},
        )

    assert edited.status_code == 409
    assert deleted.status_code == 409
    assert preview.status_code == 409


@pytest.mark.asyncio
async def test_template_without_unsubscribe_link_reports_a_warning(
    db_session, test_client_factory
):
    user, org = await _user_and_org(db_session, "campaign_template_warning")
    ids = await _leads(db_session, org.id, count=1)
    template = await _template(db_session, org.id, user.id, has_unsubscribe=False)

    async with test_client_factory(user) as client:
        created = await _create(client, ids, template_uuid=template.uuid)

    assert created.status_code == 201, created.text
    summary = created.json()["template"]
    assert summary["has_unsubscribe_placeholder"] is False
    assert "unsubscribe" in summary["unsubscribe_warning"].lower()
