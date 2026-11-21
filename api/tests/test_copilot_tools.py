"""Unit tests for the copilot's risk-tiered tool layer."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from api.services.copilot.tools import (
    RISK_READ,
    RISK_WRITE,
    TOOLS,
    CopilotContext,
    SearchLeadsArgs,
    TagLeadsArgs,
    _search_leads,
    _tag_leads,
)

GATED_TOOLS = {
    "tag_leads",
    "start_lead_search",
    "create_email_campaign_draft",
    "launch_email_campaign",
    "send_whatsapp_message",
    "start_voice_campaign",
    "pause_voice_campaign",
    "resume_voice_campaign",
}


def test_every_tool_documents_itself_and_has_a_risk():
    assert {
        "business_overview",
        "search_leads",
        "tag_leads",
        "start_lead_search",
        "create_email_campaign_draft",
        "launch_email_campaign",
        "list_whatsapp_accounts",
        "list_whatsapp_templates",
        "preview_whatsapp_message",
        "send_whatsapp_message",
        "list_voice_campaigns",
        "voice_campaign_status",
        "start_voice_campaign",
        "pause_voice_campaign",
        "resume_voice_campaign",
    } <= set(TOOLS)
    for tool in TOOLS.values():
        assert tool.description.strip()
        assert tool.risk in {RISK_READ, RISK_WRITE}
        assert issubclass(tool.args_schema, object)


def test_only_writes_require_approval():
    gated = {name for name, tool in TOOLS.items() if tool.requires_approval}
    assert gated == GATED_TOOLS


def test_tag_leads_bounds_are_enforced_by_the_schema():
    with pytest.raises(ValidationError):
        TagLeadsArgs(lead_ids=list(range(101)), tags=["hot"])
    with pytest.raises(ValidationError):
        TagLeadsArgs(lead_ids=[1], tags=["a", "b", "c", "d", "e", "f"])
    with pytest.raises(ValidationError):
        TagLeadsArgs(lead_ids=[], tags=["hot"])


def test_search_leads_limit_is_capped():
    with pytest.raises(ValidationError):
        SearchLeadsArgs(limit=51)


@pytest.mark.asyncio
async def test_search_leads_rejects_unknown_crm_status():
    with pytest.raises(ValueError, match="Unknown crm_status"):
        await _search_leads(
            CopilotContext(organization_id=1, user_id=1, conversation_id=1),
            SearchLeadsArgs(crm_status="hot"),
        )


@pytest.mark.asyncio
async def test_search_leads_is_organization_scoped_and_compact():
    lead = SimpleNamespace(
        id=7,
        title="Acme",
        category="Roofing",
        city="Austin",
        country="US",
        phone_e164="+15125550100",
        email="a@example.com",
        website="https://acme.example",
        rating=4.6,
        crm_status="new",
        tags=["hot"],
        created_at=None,
    )
    with patch("api.services.copilot.tools.db_client") as db:
        db.list_leads = AsyncMock(return_value=([lead], 1))
        result = await _search_leads(
            CopilotContext(organization_id=42, user_id=1, conversation_id=1),
            SearchLeadsArgs(limit=5),
        )

    assert db.list_leads.await_args.args == (42,)
    assert result["total"] == 1
    assert result["leads"][0]["id"] == 7
    assert result["leads"][0]["title"] == "Acme"


@pytest.mark.asyncio
async def test_tag_leads_uses_the_authenticated_organization():
    with patch("api.services.copilot.tools.db_client") as db:
        db.add_tags_to_leads = AsyncMock(return_value=1)
        result = await _tag_leads(
            CopilotContext(organization_id=42, user_id=1, conversation_id=1),
            TagLeadsArgs(lead_ids=[7, 8], tags=["follow-up"]),
        )

    db.add_tags_to_leads.assert_awaited_once_with(42, [7, 8], ["follow-up"])
    assert result == {
        "requested": 2,
        "updated": 1,
        "note": "Leads not found in this workspace were left unchanged.",
    }
