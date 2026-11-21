"""Unit tests for the copilot's Phase-2 action tools."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.db.email_campaign_client import EmailCampaignNameConflictError
from api.services.copilot import tools
from api.services.copilot.tools import (
    CopilotContext,
    CreateEmailDraftArgs,
    LaunchEmailCampaignArgs,
    LeadSearchStatusArgs,
    ListSearchesArgs,
    PreviewAudienceArgs,
    StartLeadSearchArgs,
    _create_email_campaign_draft,
    _launch_email_campaign,
    _lead_search_status,
    _list_lead_searches,
    _preview_email_audience,
    _start_lead_search,
)
from api.services.email.launch import (
    CampaignLaunchResult,
    CampaignNotLaunchableError,
    SendingDisabledError,
)
from api.services.leads.searches import SearchQueueUnavailableError

CTX = CopilotContext(organization_id=42, user_id=9, conversation_id=1)


@pytest.mark.asyncio
async def test_start_lead_search_is_scoped_and_enqueues():
    search = SimpleNamespace(id=5, name="dentists in Austin")
    with (
        patch.object(tools, "db_client") as db,
        patch.object(tools, "enqueue_search", AsyncMock()) as enqueue,
    ):
        db.create_lead_search = AsyncMock(return_value=search)
        result = await _start_lead_search(
            CTX, StartLeadSearchArgs(category="dentists", location="Austin")
        )

    kwargs = db.create_lead_search.await_args.kwargs
    assert kwargs["organization_id"] == 42
    assert kwargs["created_by"] == 9
    assert kwargs["country_code"] == "US"
    assert kwargs["target_results"] == 25
    enqueue.assert_awaited_once_with(5, 42)
    assert result["search_id"] == 5
    assert result["status"] == "queued"


@pytest.mark.asyncio
async def test_start_lead_search_rejects_unavailable_provider():
    with patch.object(
        tools,
        "resolve_provider",
        side_effect=tools.ProviderUnavailableError("Provider 'x' is not available"),
    ):
        with pytest.raises(ValueError, match="not available"):
            await _start_lead_search(
                CTX,
                StartLeadSearchArgs(
                    category="dentists", location="Austin", provider="x"
                ),
            )


@pytest.mark.asyncio
async def test_start_lead_search_reports_queue_outage():
    search = SimpleNamespace(id=5, name="dentists in Austin")
    with (
        patch.object(tools, "db_client") as db,
        patch.object(
            tools,
            "enqueue_search",
            AsyncMock(side_effect=SearchQueueUnavailableError("queue down")),
        ),
    ):
        db.create_lead_search = AsyncMock(return_value=search)
        with pytest.raises(ValueError, match="queue down"):
            await _start_lead_search(
                CTX, StartLeadSearchArgs(category="dentists", location="Austin")
            )


@pytest.mark.asyncio
async def test_lead_search_status_is_org_scoped():
    search = SimpleNamespace(
        id=5,
        name="dentists",
        status="running",
        results_count=12,
        new_leads_count=8,
        error_message=None,
        created_at=None,
        completed_at=None,
    )
    with patch.object(tools, "db_client") as db:
        db.get_lead_search = AsyncMock(return_value=search)
        result = await _lead_search_status(CTX, LeadSearchStatusArgs(search_id=5))

    db.get_lead_search.assert_awaited_once_with(5, 42)
    assert result["status"] == "running"
    assert result["new_leads_count"] == 8


@pytest.mark.asyncio
async def test_lead_search_status_missing_is_an_error():
    with patch.object(tools, "db_client") as db:
        db.get_lead_search = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="not found"):
            await _lead_search_status(CTX, LeadSearchStatusArgs(search_id=5))


@pytest.mark.asyncio
async def test_list_lead_searches_caps_and_scopes():
    with patch.object(tools, "db_client") as db:
        db.list_lead_searches = AsyncMock(return_value=([], 0))
        await _list_lead_searches(CTX, ListSearchesArgs(limit=3))

    db.list_lead_searches.assert_awaited_once_with(42, limit=3)


@pytest.mark.asyncio
async def test_preview_counts_without_writing():
    resolution = SimpleNamespace(
        counters=lambda: {"recipient_count": 10, "eligible_count": 7},
        masked_sample=["a***@acme.example"],
        unknown_lead_count=0,
        eligible_count=7,
    )
    with (
        patch.object(tools, "allow_campaign_preview", AsyncMock(return_value=True)),
        patch.object(
            tools, "resolve_audience", AsyncMock(return_value=resolution)
        ) as resolve,
        patch.object(tools, "db_client") as db,
    ):
        result = await _preview_email_audience(
            CTX, PreviewAudienceArgs(audience_type="tag", tag="vip")
        )

    assert resolve.await_args.args[0] == 42
    assert resolve.await_args.kwargs["consent_policy"] == "opted_in_only"
    assert result["counts"]["eligible_count"] == 7
    assert result["masked_sample"] == ["a***@acme.example"]
    assert db.mock_calls == []


@pytest.mark.asyncio
async def test_preview_is_rate_limited():
    with patch.object(tools, "allow_campaign_preview", AsyncMock(return_value=False)):
        with pytest.raises(ValueError, match="Too many"):
            await _preview_email_audience(
                CTX, PreviewAudienceArgs(audience_type="tag", tag="vip")
            )


@pytest.mark.asyncio
async def test_preview_requires_reason_for_unknown_consent():
    with pytest.raises(ValueError, match="written reason"):
        await _preview_email_audience(
            CTX,
            PreviewAudienceArgs(
                audience_type="tag",
                tag="vip",
                consent_policy="include_unknown",
            ),
        )


@pytest.mark.asyncio
async def test_audience_tag_requires_a_tag():
    with pytest.raises(ValueError, match="Provide a tag"):
        await _preview_email_audience(CTX, PreviewAudienceArgs(audience_type="tag"))


@pytest.mark.asyncio
async def test_create_draft_resolves_sender_and_template_in_org():
    campaign = SimpleNamespace(
        campaign_uuid="uuid-1", name="Spring promo", status="draft"
    )
    with patch.object(tools, "db_client") as db:
        db.get_email_sender = AsyncMock(
            return_value=SimpleNamespace(id=3, from_email="owner@acme.example")
        )
        db.get_email_template = AsyncMock(
            return_value=SimpleNamespace(id=4, name="Welcome")
        )
        db.create_email_campaign = AsyncMock(return_value=campaign)
        result = await _create_email_campaign_draft(
            CTX,
            CreateEmailDraftArgs(
                name="Spring promo",
                audience_type="tag",
                tag="vip",
                sender_uuid="sender-1",
                template_uuid="template-1",
            ),
        )

    db.get_email_sender.assert_awaited_once_with(42, "sender-1")
    db.get_email_template.assert_awaited_once_with(42, "template-1")
    kwargs = db.create_email_campaign.await_args.kwargs
    assert kwargs["organization_id"] == 42
    assert kwargs["user_id"] == 9
    assert kwargs["sender_id"] == 3
    assert kwargs["template_id"] == 4
    assert kwargs["audience"] == {"tag": "vip"}
    assert kwargs["consent_policy"] == "opted_in_only"
    assert result["campaign_uuid"] == "uuid-1"


@pytest.mark.asyncio
async def test_create_draft_rejects_unknown_sender():
    with patch.object(tools, "db_client") as db:
        db.get_email_sender = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Sender not found"):
            await _create_email_campaign_draft(
                CTX,
                CreateEmailDraftArgs(
                    name="Spring",
                    audience_type="tag",
                    tag="vip",
                    sender_uuid="nope",
                ),
            )


@pytest.mark.asyncio
async def test_create_draft_maps_name_conflict():
    with patch.object(tools, "db_client") as db:
        db.create_email_campaign = AsyncMock(
            side_effect=EmailCampaignNameConflictError("Spring")
        )
        with pytest.raises(ValueError, match="already exists"):
            await _create_email_campaign_draft(
                CTX,
                CreateEmailDraftArgs(name="Spring", audience_type="tag", tag="vip"),
            )


@pytest.mark.asyncio
async def test_create_draft_requires_reason_for_unknown_consent():
    with patch.object(tools, "db_client") as db:
        db.create_email_campaign = AsyncMock()
        with pytest.raises(ValueError, match="written reason"):
            await _create_email_campaign_draft(
                CTX,
                CreateEmailDraftArgs(
                    name="Spring",
                    audience_type="tag",
                    tag="vip",
                    consent_policy="include_unknown",
                ),
            )

    db.create_email_campaign.assert_not_awaited()


@pytest.mark.asyncio
async def test_launch_runs_through_the_shared_service():
    result = CampaignLaunchResult(
        status="sending",
        ready=True,
        message="Campaign launched: 7 recipient(s) queued for delivery.",
        queued_count=7,
    )
    with patch.object(
        tools, "launch_campaign", AsyncMock(return_value=result)
    ) as launch:
        output = await _launch_email_campaign(
            CTX, LaunchEmailCampaignArgs(campaign_uuid="uuid-1")
        )

    launch.assert_awaited_once_with(
        organization_id=42, user_id=9, campaign_uuid="uuid-1"
    )
    assert output["launched"] is True
    assert output["queued_count"] == 7
    assert output["preconditions"] == []


@pytest.mark.asyncio
async def test_launch_reports_blocked_preconditions_without_raising():
    result = CampaignLaunchResult(
        status="draft",
        ready=False,
        preconditions=["The sender is not verified."],
        message="Launch is blocked.",
    )
    with patch.object(tools, "launch_campaign", AsyncMock(return_value=result)):
        output = await _launch_email_campaign(
            CTX, LaunchEmailCampaignArgs(campaign_uuid="uuid-1")
        )

    assert output["launched"] is False
    assert output["preconditions"] == ["The sender is not verified."]


@pytest.mark.asyncio
async def test_launch_explains_disabled_sending():
    with patch.object(
        tools,
        "launch_campaign",
        AsyncMock(side_effect=SendingDisabledError("EMAIL_CAMPAIGN_SENDING_ENABLED")),
    ):
        with pytest.raises(ValueError, match="disabled"):
            await _launch_email_campaign(
                CTX, LaunchEmailCampaignArgs(campaign_uuid="uuid-1")
            )


@pytest.mark.asyncio
async def test_launch_surfaces_service_refusals():
    with patch.object(
        tools,
        "launch_campaign",
        AsyncMock(side_effect=CampaignNotLaunchableError("already sending")),
    ):
        with pytest.raises(ValueError, match="already sending"):
            await _launch_email_campaign(
                CTX, LaunchEmailCampaignArgs(campaign_uuid="uuid-1")
            )
