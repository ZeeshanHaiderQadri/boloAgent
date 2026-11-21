"""Launch preconditions, freeze/queue behaviour, and the disabled-by-default gate.

A blocked campaign is left untouched. A ready campaign freezes its audience
into ``email_messages`` rows released as ``queued`` and enqueues exactly one
send job; the SMTP delivery itself belongs to the worker tests, never here.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from api.db.models import (
    EmailCampaignModel,
    EmailMessageModel,
    EmailSenderModel,
    EmailTemplateModel,
    ExternalCredentialModel,
    LeadModel,
)
from api.enums import CredentialPurpose, EmailMessageStatus, EmailSenderStatus
from api.services.call_concurrency.rate_limiter import RateLimiterUnavailable
from api.services.credentials.cipher import encrypt_credential_data
from api.services.email.campaign_launch import check_launch_preconditions
from api.tasks.function_names import FunctionNames

UNSUBSCRIBE = '<p><a href="{{unsubscribe_link}}">Unsubscribe</a></p>'
PUBLIC_URL = "https://app.apex-software-demo.com"


# ----------------------------------------------------------------------
# Unit tests for the single precondition check
# ----------------------------------------------------------------------


def _unit_campaign(**overrides):
    values = {
        "frozen_html_body": None,
        "consent_policy": "opted_in_only",
        "consent_override_reason": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _unit_sender(**overrides):
    values = {"status": EmailSenderStatus.VERIFIED.value}
    values.update(overrides)
    return SimpleNamespace(**values)


def _unit_template(**overrides):
    values = {
        "html_body": f"<p>Hi</p>{UNSUBSCRIBE}",
        "has_unsubscribe_placeholder": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _resolution(**overrides):
    values = {"eligible_count": 2, "unknown_lead_count": 0}
    values.update(overrides)
    return SimpleNamespace(**values)


def _check(**overrides):
    kwargs = {
        "campaign": _unit_campaign(),
        "sender": _unit_sender(),
        "template": _unit_template(),
        "resolution": _resolution(),
        "public_base_url": PUBLIC_URL,
    }
    kwargs.update(overrides)
    return check_launch_preconditions(**kwargs)


def test_a_fully_configured_campaign_is_ready():
    readiness = _check()
    assert readiness.ready is True
    assert readiness.failures == []


def test_every_missing_piece_is_reported_in_one_pass():
    readiness = _check(
        sender=None,
        template=None,
        resolution=_resolution(eligible_count=0),
        public_base_url=None,
    )
    assert readiness.ready is False
    joined = " ".join(readiness.failures)
    assert "sender" in joined
    assert "template" in joined
    assert "PUBLIC_BASE_URL" in joined
    assert "no eligible recipients" in joined


def test_unverified_sender_blocks_launch():
    readiness = _check(sender=_unit_sender(status=EmailSenderStatus.PENDING.value))
    assert readiness.ready is False
    assert any("not verified" in failure for failure in readiness.failures)


def test_template_flag_and_sanitized_html_are_both_checked():
    stale_flag = _check(template=_unit_template(has_unsubscribe_placeholder=False))
    assert any("no unsubscribe link" in failure for failure in stale_flag.failures)

    stale_html = _check(template=_unit_template(html_body="<p>No link</p>"))
    assert any("sanitized HTML" in failure for failure in stale_html.failures)


def test_frozen_html_is_authoritative_after_a_launch():
    readiness = _check(
        campaign=_unit_campaign(frozen_html_body="<p>Frozen, no link</p>"),
        template=_unit_template(),
    )
    assert any("sanitized HTML" in failure for failure in readiness.failures)


@pytest.mark.parametrize(
    "url",
    [None, "", "app.example.com", "ftp://app.example.com", "http://localhost:8000"],
)
def test_public_base_url_must_be_a_public_http_origin(url):
    readiness = _check(public_base_url=url)
    assert readiness.ready is False
    assert any("PUBLIC_BASE_URL" in failure for failure in readiness.failures)


def test_include_unknown_without_a_reason_blocks_launch():
    readiness = _check(
        campaign=_unit_campaign(consent_policy="include_unknown"),
        resolution=_resolution(unknown_lead_count=3),
    )
    assert any("no written reason" in failure for failure in readiness.failures)

    with_reason = _check(
        campaign=_unit_campaign(
            consent_policy="include_unknown",
            consent_override_reason="All leads asked to be contacted.",
        ),
        resolution=_resolution(unknown_lead_count=3),
    )
    assert with_reason.ready is True


# ----------------------------------------------------------------------
# Endpoint tests
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _allow_launch_limits(monkeypatch):
    async def allow(**kwargs):
        return True

    monkeypatch.setattr("api.routes.email_campaigns.allow_campaign_preview", allow)
    monkeypatch.setattr(
        "api.routes.email_campaigns.allow_campaign_launch_attempt", allow
    )
    monkeypatch.setattr(
        "api.services.email.launch.allow_campaign_launch_attempt", allow
    )


async def _user_and_org(db_session, slug: str):
    user, _ = await db_session.get_or_create_user_by_provider_id(f"{slug}_user")
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        f"{slug}_org", user.id
    )
    await db_session.update_user_selected_organization(user.id, org.id)
    snapshot = SimpleNamespace(id=user.id, selected_organization_id=org.id, email=None)
    return snapshot, org


async def _sender(
    db_session,
    org_id,
    user_id,
    *,
    status=EmailSenderStatus.VERIFIED.value,
):
    async with db_session.async_session() as session:
        credential = ExternalCredentialModel(
            organization_id=org_id,
            created_by=user_id,
            name="Launch sender",
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
            name="Launch sender",
            provider="smtp",
            from_name="Demo Sender",
            from_email="demo@apex-software-demo.com",
            settings={"host": "smtp.hostinger.com", "port": 465, "security": "ssl"},
            credential_id=credential.id,
            status=status,
            daily_cap=100,
            rate_per_second=1.0,
        )
        session.add(sender)
        await session.commit()
        return SimpleNamespace(id=sender.id, uuid=sender.sender_uuid)


async def _template(db_session, org_id, user_id, *, has_unsubscribe=True):
    async with db_session.async_session() as session:
        template = EmailTemplateModel(
            organization_id=org_id,
            created_by=user_id,
            name="Launch template",
            subject="Hello",
            source_format="html",
            source_body=f"<p>Hi</p>{UNSUBSCRIBE}" if has_unsubscribe else "<p>Hi</p>",
            html_body=f"<p>Hi</p>{UNSUBSCRIBE}" if has_unsubscribe else "<p>Hi</p>",
            text_body="Hi",
            has_unsubscribe_placeholder=has_unsubscribe,
        )
        session.add(template)
        await session.commit()
        return SimpleNamespace(id=template.id, uuid=template.template_uuid)


async def _lead(db_session, org_id):
    async with db_session.async_session() as session:
        lead = LeadModel(
            organization_id=org_id,
            dedupe_key="launch-lead",
            title="Launch lead",
            email="owner@acme-plumbing.com",
            email_consent="opted_in",
        )
        session.add(lead)
        await session.commit()
        return lead.id


async def _campaign(client, lead_id, **overrides):
    payload = {
        "name": "Launch campaign",
        "audience": {"audience_type": "leads", "lead_ids": [lead_id]},
    }
    payload.update(overrides)
    return await client.post("/api/v1/email/campaigns", json=payload)


async def _message_count(db_session, organization_id):
    async with db_session.async_session() as session:
        return (
            await session.execute(
                select(func.count(EmailMessageModel.id)).where(
                    EmailMessageModel.organization_id == organization_id
                )
            )
        ).scalar_one()


@pytest.mark.asyncio
async def test_launch_is_403_while_sending_is_disabled(
    db_session, test_client_factory, monkeypatch
):
    monkeypatch.setattr("api.constants.EMAIL_CAMPAIGN_SENDING_ENABLED", False)
    user, org = await _user_and_org(db_session, "launch_disabled")
    lead_id = await _lead(db_session, org.id)

    async with test_client_factory(user) as client:
        created = await _campaign(client, lead_id)
        response = await client.post(
            f"/api/v1/email/campaigns/{created.json()['uuid']}/launch"
        )

    assert response.status_code == 403
    assert "EMAIL_CAMPAIGN_SENDING_ENABLED" in response.json()["detail"]
    assert await _message_count(db_session, org.id) == 0


@pytest.mark.asyncio
async def test_launch_freezes_and_queues_when_enabled(
    db_session, test_client_factory, monkeypatch
):
    monkeypatch.setattr("api.constants.EMAIL_CAMPAIGN_SENDING_ENABLED", True)
    monkeypatch.setattr("api.constants.PUBLIC_BASE_URL", PUBLIC_URL)

    enqueued: list[tuple] = []

    async def record_enqueue(function_name, *args, **kwargs):
        enqueued.append((function_name, args, kwargs))

    monkeypatch.setattr("api.routes.email_campaigns.enqueue_job", record_enqueue)
    monkeypatch.setattr("api.services.email.launch.enqueue_job", record_enqueue)

    user, org = await _user_and_org(db_session, "launch_ready")
    lead_id = await _lead(db_session, org.id)
    sender = await _sender(db_session, org.id, user.id)
    template = await _template(db_session, org.id, user.id)

    async with test_client_factory(user) as client:
        created = await _campaign(
            client, lead_id, sender_uuid=sender.uuid, template_uuid=template.uuid
        )
        campaign_uuid = created.json()["uuid"]
        response = await client.post(f"/api/v1/email/campaigns/{campaign_uuid}/launch")
        fetched = await client.get(f"/api/v1/email/campaigns/{campaign_uuid}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is True
    assert body["preconditions"] == []
    assert body["status"] == "sending"
    assert body["queued_count"] == 1
    assert fetched.json()["status"] == "sending"

    async with db_session.async_session() as session:
        messages = (
            (
                await session.execute(
                    select(EmailMessageModel).where(
                        EmailMessageModel.organization_id == org.id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(messages) == 1
    message = messages[0]
    assert message.status == EmailMessageStatus.QUEUED.value
    assert message.to_email == "owner@acme-plumbing.com"
    assert message.campaign_id is not None
    assert "unsubscribe" in message.html_body
    assert message.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    assert len(enqueued) == 1
    function_name, args, _ = enqueued[0]
    assert function_name == FunctionNames.SEND_EMAIL_CAMPAIGN
    assert args == (message.campaign_id, org.id)


@pytest.mark.asyncio
async def test_launch_lists_preconditions_and_still_sends_nothing(
    db_session, test_client_factory, monkeypatch
):
    monkeypatch.setattr("api.constants.EMAIL_CAMPAIGN_SENDING_ENABLED", True)
    monkeypatch.setattr("api.constants.PUBLIC_BASE_URL", PUBLIC_URL)
    user, org = await _user_and_org(db_session, "launch_blocked")
    lead_id = await _lead(db_session, org.id)
    sender = await _sender(
        db_session, org.id, user.id, status=EmailSenderStatus.PENDING.value
    )
    template = await _template(db_session, org.id, user.id, has_unsubscribe=False)

    async with test_client_factory(user) as client:
        created = await _campaign(
            client, lead_id, sender_uuid=sender.uuid, template_uuid=template.uuid
        )
        response = await client.post(
            f"/api/v1/email/campaigns/{created.json()['uuid']}/launch"
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is False
    joined = " ".join(body["preconditions"])
    assert "not verified" in joined
    assert "unsubscribe" in joined
    assert "nothing was queued" in body["message"]
    assert await _message_count(db_session, org.id) == 0


@pytest.mark.asyncio
async def test_launch_rejects_a_campaign_that_already_started(
    db_session, test_client_factory, monkeypatch
):
    monkeypatch.setattr("api.constants.EMAIL_CAMPAIGN_SENDING_ENABLED", True)
    monkeypatch.setattr("api.constants.PUBLIC_BASE_URL", PUBLIC_URL)
    user, org = await _user_and_org(db_session, "launch_started")
    lead_id = await _lead(db_session, org.id)

    async with test_client_factory(user) as client:
        created = await _campaign(client, lead_id)
        campaign_uuid = created.json()["uuid"]
        async with db_session.async_session() as session:
            row = (
                await session.execute(
                    select(EmailCampaignModel).where(
                        EmailCampaignModel.campaign_uuid == campaign_uuid
                    )
                )
            ).scalar_one()
            row.status = "sending"
            await session.commit()
        response = await client.post(f"/api/v1/email/campaigns/{campaign_uuid}/launch")

    assert response.status_code == 409
    assert await _message_count(db_session, org.id) == 0


@pytest.mark.asyncio
async def test_launch_attempts_are_rate_limited_and_fail_closed(
    db_session, test_client_factory, monkeypatch
):
    monkeypatch.setattr("api.constants.EMAIL_CAMPAIGN_SENDING_ENABLED", True)
    user, org = await _user_and_org(db_session, "launch_limits")
    lead_id = await _lead(db_session, org.id)

    async def deny(**kwargs):
        return False

    async def unavailable(**kwargs):
        raise RateLimiterUnavailable("redis is down")

    async with test_client_factory(user) as client:
        created = await _campaign(client, lead_id)
        url = f"/api/v1/email/campaigns/{created.json()['uuid']}/launch"

        monkeypatch.setattr(
            "api.services.email.launch.allow_campaign_launch_attempt", deny
        )
        denied = await client.post(url)
        monkeypatch.setattr(
            "api.services.email.launch.allow_campaign_launch_attempt", unavailable
        )
        down = await client.post(url)

    assert denied.status_code == 429
    assert down.status_code == 503
    assert await _message_count(db_session, org.id) == 0


# ----------------------------------------------------------------------
# Restart (cancelled / failed campaigns)
# ----------------------------------------------------------------------


async def _set_status(db_session, campaign_uuid, status):
    async with db_session.async_session() as session:
        row = (
            await session.execute(
                select(EmailCampaignModel).where(
                    EmailCampaignModel.campaign_uuid == campaign_uuid
                )
            )
        ).scalar_one()
        row.status = status
        await session.commit()
        return row.id


async def _set_message_status(db_session, organization_id, status):
    async with db_session.async_session() as session:
        messages = (
            (
                await session.execute(
                    select(EmailMessageModel).where(
                        EmailMessageModel.organization_id == organization_id
                    )
                )
            )
            .scalars()
            .all()
        )
        for message in messages:
            message.status = status
            message.error_message = "Stopped by the operator."
        await session.commit()


@asynccontextmanager
async def _launched_campaign(db_session, test_client_factory, monkeypatch, slug):
    enqueued: list[tuple] = []

    async def record_enqueue(function_name, *args, **kwargs):
        enqueued.append((function_name, args, kwargs))

    monkeypatch.setattr("api.constants.EMAIL_CAMPAIGN_SENDING_ENABLED", True)
    monkeypatch.setattr("api.constants.PUBLIC_BASE_URL", PUBLIC_URL)
    monkeypatch.setattr("api.routes.email_campaigns.enqueue_job", record_enqueue)
    monkeypatch.setattr("api.services.email.launch.enqueue_job", record_enqueue)

    user, org = await _user_and_org(db_session, slug)
    lead_id = await _lead(db_session, org.id)
    sender = await _sender(db_session, org.id, user.id)
    template = await _template(db_session, org.id, user.id)
    async with test_client_factory(user) as client:
        created = await _campaign(
            client, lead_id, sender_uuid=sender.uuid, template_uuid=template.uuid
        )
        campaign_uuid = created.json()["uuid"]
        launched = await client.post(f"/api/v1/email/campaigns/{campaign_uuid}/launch")
        assert launched.status_code == 200, launched.text
        yield client, user, org, campaign_uuid, enqueued


@pytest.mark.asyncio
async def test_restart_is_403_while_sending_is_disabled(
    db_session, test_client_factory, monkeypatch
):
    user, org = await _user_and_org(db_session, "restart_disabled")
    lead_id = await _lead(db_session, org.id)

    async with test_client_factory(user) as client:
        created = await _campaign(client, lead_id)
        campaign_uuid = created.json()["uuid"]
        await _set_status(db_session, campaign_uuid, "cancelled")
        monkeypatch.setattr("api.constants.EMAIL_CAMPAIGN_SENDING_ENABLED", False)
        response = await client.post(f"/api/v1/email/campaigns/{campaign_uuid}/restart")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_restart_requeues_cancelled_rows_and_skips_delivered(
    db_session, test_client_factory, monkeypatch
):
    async with _launched_campaign(
        db_session, test_client_factory, monkeypatch, "restart_requeue"
    ) as (client, user, org, campaign_uuid, enqueued):
        await _set_status(db_session, campaign_uuid, "cancelled")
        await _set_message_status(
            db_session, org.id, EmailMessageStatus.CANCELLED.value
        )
        enqueued.clear()

        response = await client.post(f"/api/v1/email/campaigns/{campaign_uuid}/restart")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "sending"

        async with db_session.async_session() as session:
            messages = (
                (
                    await session.execute(
                        select(EmailMessageModel).where(
                            EmailMessageModel.organization_id == org.id
                        )
                    )
                )
                .scalars()
                .all()
            )

    assert [m.status for m in messages] == [EmailMessageStatus.QUEUED.value]
    assert all(m.error_message is None for m in messages)
    assert len(enqueued) == 1
    function_name, args, _ = enqueued[0]
    assert function_name == FunctionNames.SEND_EMAIL_CAMPAIGN
    assert args == (messages[0].campaign_id, org.id)


@pytest.mark.asyncio
async def test_restart_rejects_a_running_campaign(
    db_session, test_client_factory, monkeypatch
):
    monkeypatch.setattr("api.constants.EMAIL_CAMPAIGN_SENDING_ENABLED", True)
    user, org = await _user_and_org(db_session, "restart_running")
    lead_id = await _lead(db_session, org.id)

    async with test_client_factory(user) as client:
        created = await _campaign(client, lead_id)
        campaign_uuid = created.json()["uuid"]
        await _set_status(db_session, campaign_uuid, "sending")
        response = await client.post(f"/api/v1/email/campaigns/{campaign_uuid}/restart")

    assert response.status_code == 409
    assert "cancelled or failed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_restart_without_remaining_rows_is_409(
    db_session, test_client_factory, monkeypatch
):
    async with _launched_campaign(
        db_session, test_client_factory, monkeypatch, "restart_nothing_left"
    ) as (client, user, org, campaign_uuid, enqueued):
        await _set_status(db_session, campaign_uuid, "cancelled")
        await _set_message_status(db_session, org.id, EmailMessageStatus.SENT.value)
        enqueued.clear()

        response = await client.post(f"/api/v1/email/campaigns/{campaign_uuid}/restart")
        fetched = await client.get(f"/api/v1/email/campaigns/{campaign_uuid}")

    assert response.status_code == 409
    assert "nothing left to send" in response.json()["detail"]
    assert fetched.json()["status"] == "cancelled"
    assert enqueued == []
