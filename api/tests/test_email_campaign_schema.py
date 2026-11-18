"""Email campaign table and recipient-row constraints."""

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError

from api.db.models import (
    EmailCampaignModel,
    EmailMessageModel,
    EmailSenderModel,
    LeadModel,
    OrganizationModel,
    UserModel,
)
from api.enums import EmailProvider


async def _make_user_and_org(db_session, slug: str):
    user, _ = await db_session.get_or_create_user_by_provider_id(f"{slug}_user")
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        f"{slug}_org", user.id
    )
    await db_session.update_user_selected_organization(user.id, org.id)
    return await db_session.get_user_by_id(user.id), org


async def _make_sender(db_session, user, org, name: str):
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
            provider=EmailProvider.SMTP.value,
            from_name="Bolo",
            from_email="hello@apex-software-demo.com",
            credential_id=credential.id,
        )
        session.add(sender)
        await session.flush()
        return sender


async def _make_campaign(session, org, name: str, **overrides):
    campaign = EmailCampaignModel(
        organization_id=org.id,
        name=name,
        **overrides,
    )
    session.add(campaign)
    await session.flush()
    return campaign


async def _make_message(session, org, sender, campaign, to_email, **overrides):
    message = EmailMessageModel(
        organization_id=org.id,
        sender_id=sender.id,
        campaign_id=campaign.id if campaign is not None else None,
        to_email=to_email,
        subject="Hi",
        html_body="<p>Hi</p>",
        **overrides,
    )
    session.add(message)
    await session.flush()
    return message


@pytest.mark.asyncio
async def test_a_draft_campaign_has_safe_defaults(db_session):
    user, org = await _make_user_and_org(db_session, "campaign_defaults")
    async with db_session.async_session() as session:
        campaign = await _make_campaign(session, org, "Spring promo")

    assert campaign.status == "draft"
    assert campaign.consent_policy == "opted_in_only"
    assert campaign.audience_type == "leads"
    assert campaign.audience == {}
    assert campaign.eligible_count == 0
    assert campaign.recipient_count == 0
    assert campaign.frozen_at is None
    assert campaign.launched_at is None
    assert campaign.consent_override_at is None
    assert campaign.failure_reason is None
    assert campaign.cancelled_by is None
    assert campaign.cancelled_at is None
    assert campaign.sent_count == 0
    assert campaign.failed_count == 0
    assert campaign.bounced_count == 0
    assert campaign.complained_count == 0
    assert campaign.unsubscribed_count == 0


@pytest.mark.asyncio
async def test_campaign_names_are_unique_per_organization(db_session):
    _, org = await _make_user_and_org(db_session, "campaign_unique")
    _, other_org = await _make_user_and_org(db_session, "campaign_unique_other")

    async with db_session.async_session() as session:
        await _make_campaign(session, org, "Same name")
        await _make_campaign(session, other_org, "Same name")
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await _make_campaign(session, org, "Same name")


@pytest.mark.asyncio
async def test_held_recipient_rows_are_lowercase_and_unique_per_campaign(
    db_session,
):
    user, org = await _make_user_and_org(db_session, "campaign_recipients")
    sender = await _make_sender(db_session, user, org, "Campaign sender")

    async with db_session.async_session() as session:
        campaign = await _make_campaign(session, org, "Recipients")
        message = await _make_message(
            session,
            org,
            sender,
            campaign,
            "owner@apex-software-demo.com",
            status="held",
        )
        assert message.status == "held"

        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await _make_message(
                    session,
                    org,
                    sender,
                    campaign,
                    "owner@apex-software-demo.com",
                    status="held",
                )


@pytest.mark.asyncio
async def test_campaign_recipients_must_be_lowercase(db_session):
    user, org = await _make_user_and_org(db_session, "campaign_lowercase")
    sender = await _make_sender(db_session, user, org, "Lowercase sender")

    async with db_session.async_session() as session:
        campaign = await _make_campaign(session, org, "Lowercase")
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await _make_message(
                    session,
                    org,
                    sender,
                    campaign,
                    "Owner@Apex-Software-Demo.com",
                    status="held",
                )

    # The rule only applies to campaign rows; ordinary messages are unaffected.
    async with db_session.async_session() as session:
        await _make_message(
            session,
            org,
            sender,
            None,
            "Owner@Apex-Software-Demo.com",
            status="queued",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "padded_address",
    [
        " owner@apex-software-demo.com",
        "owner@apex-software-demo.com ",
        "  OWNER@Apex-Software-Demo.com  ",
    ],
)
async def test_campaign_recipients_must_not_be_padded(db_session, padded_address):
    """A padded address would bypass the per-campaign uniqueness index, so the
    database rejects it; the resolver must trim and lowercase."""
    user, org = await _make_user_and_org(db_session, "campaign_padded")
    sender = await _make_sender(db_session, user, org, "Padded sender")

    async with db_session.async_session() as session:
        campaign = await _make_campaign(session, org, "Padded")
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await _make_message(
                    session,
                    org,
                    sender,
                    campaign,
                    padded_address,
                    status="held",
                )


@pytest.mark.asyncio
async def test_the_same_address_may_appear_in_two_campaigns(db_session):
    user, org = await _make_user_and_org(db_session, "campaign_two_lists")
    sender = await _make_sender(db_session, user, org, "Two lists sender")

    async with db_session.async_session() as session:
        first = await _make_campaign(session, org, "First")
        second = await _make_campaign(session, org, "Second")
        await _make_message(
            session,
            org,
            sender,
            first,
            "owner@apex-software-demo.com",
            status="held",
        )
        await _make_message(
            session,
            org,
            sender,
            second,
            "owner@apex-software-demo.com",
            status="held",
        )


@pytest.mark.asyncio
async def test_deleting_a_lead_keeps_the_campaign_audit_row(db_session):
    user, org = await _make_user_and_org(db_session, "campaign_lead_delete")
    sender = await _make_sender(db_session, user, org, "Lead delete sender")

    async with db_session.async_session() as session:
        lead = LeadModel(
            organization_id=org.id,
            dedupe_key="campaign-audit-lead",
            title="Audit Lead",
            email="audit@apex-software-demo.com",
        )
        session.add(lead)
        await session.flush()
        campaign = await _make_campaign(session, org, "Audit")
        message = await _make_message(
            session,
            org,
            sender,
            campaign,
            "audit@apex-software-demo.com",
            status="held",
            lead_id=lead.id,
        )
        message_id = message.id
        lead_id = lead.id

    async with db_session.async_session() as session:
        await session.execute(delete(LeadModel).where(LeadModel.id == lead_id))
        kept = await session.get(EmailMessageModel, message_id, populate_existing=True)

    assert kept is not None
    assert kept.lead_id is None
    assert kept.to_email == "audit@apex-software-demo.com"


@pytest.mark.asyncio
async def test_deleting_a_campaign_removes_its_recipient_rows(db_session):
    user, org = await _make_user_and_org(db_session, "campaign_delete")
    sender = await _make_sender(db_session, user, org, "Delete sender")

    async with db_session.async_session() as session:
        campaign = await _make_campaign(session, org, "Delete me")
        message = await _make_message(
            session,
            org,
            sender,
            campaign,
            "owner@apex-software-demo.com",
            status="held",
        )
        campaign_id = campaign.id
        message_id = message.id

    async with db_session.async_session() as session:
        await session.execute(
            delete(EmailCampaignModel).where(EmailCampaignModel.id == campaign_id)
        )
        remaining = await session.scalar(
            select(EmailMessageModel.id).where(EmailMessageModel.id == message_id)
        )

    assert remaining is None


@pytest.mark.asyncio
async def test_a_sender_used_by_a_campaign_cannot_be_deleted(db_session):
    user, org = await _make_user_and_org(db_session, "campaign_sender_guard")
    sender = await _make_sender(db_session, user, org, "Guarded sender")

    async with db_session.async_session() as session:
        campaign = await _make_campaign(session, org, "Guarded", sender_id=sender.id)

    async with db_session.async_session() as session:
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    delete(EmailSenderModel).where(EmailSenderModel.id == sender.id)
                )


@pytest.mark.asyncio
async def test_a_finished_campaign_can_detach_its_sender_before_deletion(db_session):
    """Decision: a sender is deletable once only finished campaigns use it.

    The sender endpoint (PR b) nulls ``sender_id`` on those campaigns in the
    same transaction, then deletes. The snapshot columns remain the record.
    """
    user, org = await _make_user_and_org(db_session, "campaign_sender_detach")
    sender = await _make_sender(db_session, user, org, "Finished sender")

    async with db_session.async_session() as session:
        campaign = await _make_campaign(
            session,
            org,
            "Finished",
            sender_id=sender.id,
            status="completed",
            sender_from_name="Bolo",
            sender_from_email="hello@apex-software-demo.com",
        )
        campaign_id = campaign.id

    async with db_session.async_session() as session:
        await session.execute(
            update(EmailCampaignModel)
            .where(EmailCampaignModel.id == campaign_id)
            .values(sender_id=None)
        )
        await session.execute(
            delete(EmailSenderModel).where(EmailSenderModel.id == sender.id)
        )
        kept = await session.get(
            EmailCampaignModel, campaign_id, populate_existing=True
        )

    assert kept is not None
    assert kept.sender_id is None
    assert kept.sender_from_email == "hello@apex-software-demo.com"


@pytest.mark.asyncio
async def test_deleting_an_organization_cascades_campaigns_and_their_sender(
    db_session,
):
    """NO ACTION (not RESTRICT) lets organization teardown cascade through both
    email_campaigns and email_senders in one statement."""
    user, org = await _make_user_and_org(db_session, "campaign_org_teardown")
    sender = await _make_sender(db_session, user, org, "Teardown sender")

    async with db_session.async_session() as session:
        campaign = await _make_campaign(session, org, "Teardown", sender_id=sender.id)
        message = await _make_message(
            session,
            org,
            sender,
            campaign,
            "owner@apex-software-demo.com",
            status="held",
        )
        campaign_id = campaign.id
        message_id = message.id
        sender_id = sender.id

    async with db_session.async_session() as session:
        await session.execute(
            update(UserModel)
            .where(UserModel.id == user.id)
            .values(selected_organization_id=None)
        )
        await session.execute(
            text("DELETE FROM organization_users WHERE organization_id = :org_id"),
            {"org_id": org.id},
        )
        await session.execute(
            delete(OrganizationModel).where(OrganizationModel.id == org.id)
        )

    async with db_session.async_session() as session:
        assert (
            await session.get(EmailCampaignModel, campaign_id, populate_existing=True)
            is None
        )
        assert (
            await session.get(EmailMessageModel, message_id, populate_existing=True)
            is None
        )
        assert (
            await session.get(EmailSenderModel, sender_id, populate_existing=True)
            is None
        )
