"""Unit tests for the copilot's risk-tiered tool layer."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from api.services.copilot import agent as copilot_agent
from api.services.copilot.tools import (
    RISK_READ,
    RISK_WRITE,
    TOOLS,
    CopilotContext,
    ListWhatsAppSendersArgs,
    ListWhatsAppTemplatesArgs,
    ListWorkspaceFilesArgs,
    ReadDocumentArgs,
    SearchKnowledgeBaseArgs,
    SearchLeadsArgs,
    TagLeadsArgs,
    WhatsAppClickTemplatesArgs,
    WhatsAppLeadLinksArgs,
    _list_whatsapp_click_templates,
    _list_whatsapp_senders,
    _list_whatsapp_templates,
    _list_workspace_files,
    _read_document,
    _search_knowledge_base,
    _search_leads,
    _tag_leads,
    _whatsapp_lead_links,
)
from api.services.whatsapp.lead_links import (
    LeadLink,
    LeadLinks,
    LeadLinksDisclaimerRequiredError,
    LeadLinksLeadNotFoundError,
)
from api.services.whatsapp.messaging import WhatsAppConfigNotFoundError

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
        "list_whatsapp_click_templates",
        "list_whatsapp_senders",
        "whatsapp_lead_links",
        "preview_whatsapp_message",
        "send_whatsapp_message",
        "read_document",
        "list_workspace_files",
        "search_knowledge_base",
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
async def test_search_leads_passes_geography_filters():
    with patch("api.services.copilot.tools.db_client") as db:
        db.list_leads = AsyncMock(return_value=([], 0))
        await _search_leads(
            CopilotContext(organization_id=42, user_id=1, conversation_id=1),
            SearchLeadsArgs(country="gb", city="London", limit=5),
        )

    kwargs = db.list_leads.await_args.kwargs
    assert kwargs["country"] == "GB"
    assert kwargs["city"] == "London"


@pytest.mark.asyncio
async def test_search_leads_notes_when_nothing_matches():
    with patch("api.services.copilot.tools.db_client") as db:
        db.list_leads = AsyncMock(return_value=([], 0))
        result = await _search_leads(
            CopilotContext(organization_id=42, user_id=1, conversation_id=1),
            SearchLeadsArgs(query="Cyprus", limit=5),
        )

    assert result["total"] == 0
    assert "note" in result


@pytest.mark.asyncio
async def test_run_tool_reports_unknown_arguments_instead_of_ignoring_them():
    tool = TOOLS["search_leads"]
    call = {
        "id": "call-1",
        "name": "search_leads",
        "args": {"filters": '{"country": "Cyprus"}', "limit": 5},
    }
    with (
        patch.object(copilot_agent, "_record_tool_activity", new=AsyncMock()),
        patch.object(copilot_agent, "audit_tool_call", new=AsyncMock()),
        patch("api.services.copilot.tools.db_client") as db,
    ):
        db.list_leads = AsyncMock()
        message = await copilot_agent._run_tool(
            CopilotContext(organization_id=42, user_id=1, conversation_id=1),
            tool,
            call,
        )

    db.list_leads.assert_not_awaited()
    assert "filters" in message.content
    assert "country" in message.content


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


@pytest.mark.asyncio
async def test_read_document_returns_full_text_inside_the_workspace():
    document = SimpleNamespace(
        id=3,
        filename="pricing.md",
        retrieval_mode="full_document",
        processing_status="completed",
        full_text="Starter plan: $19/mo",
    )
    with patch("api.services.copilot.tools.db_client") as db:
        db.get_document_by_uuid = AsyncMock(return_value=document)
        result = await _read_document(
            CopilotContext(organization_id=42, user_id=1, conversation_id=1),
            ReadDocumentArgs(document_uuid="abc"),
        )

    db.get_document_by_uuid.assert_awaited_once_with("abc", 42)
    assert result["document"] == "pricing.md"
    assert result["text"] == "Starter plan: $19/mo"
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_read_document_falls_back_to_chunks_and_hides_other_orgs():
    chunk = SimpleNamespace(chunk_text="First policy line")
    document = SimpleNamespace(
        id=9,
        filename="policy.pdf",
        retrieval_mode="chunked",
        processing_status="completed",
        full_text=None,
    )
    with patch("api.services.copilot.tools.db_client") as db:
        db.get_document_by_uuid = AsyncMock(return_value=document)
        db.get_chunks_for_document = AsyncMock(return_value=[chunk])
        result = await _read_document(
            CopilotContext(organization_id=42, user_id=1, conversation_id=1),
            ReadDocumentArgs(document_uuid="abc"),
        )
        db.get_chunks_for_document.assert_awaited_once_with(9, 42)
        assert result["source"] == "chunks"
        assert result["text"] == "First policy line"

        db.get_document_by_uuid = AsyncMock(return_value=None)
        missing = await _read_document(
            CopilotContext(organization_id=42, user_id=1, conversation_id=1),
            ReadDocumentArgs(document_uuid="elsewhere"),
        )

    assert missing == {"error": "No file with that id in this workspace."}


@pytest.mark.asyncio
async def test_list_workspace_files_is_organization_scoped():
    document = SimpleNamespace(
        document_uuid="u1",
        filename="handbook.pdf",
        retrieval_mode="chunked",
        processing_status="completed",
        total_chunks=12,
        file_size_bytes=2048,
        created_at=None,
    )
    with patch("api.services.copilot.tools.db_client") as db:
        db.get_documents_for_organization = AsyncMock(return_value=[document])
        result = await _list_workspace_files(
            CopilotContext(organization_id=42, user_id=1, conversation_id=1),
            ListWorkspaceFilesArgs(limit=10),
        )

    db.get_documents_for_organization.assert_awaited_once_with(42, limit=10)
    assert result["total"] == 1
    assert result["files"][0]["document_uuid"] == "u1"
    assert result["files"][0]["retrieval_mode"] == "chunked"


@pytest.mark.asyncio
async def test_search_knowledge_base_is_scoped_and_returns_excerpts():
    embeddings = SimpleNamespace(
        provider="openai",
        api_key="key",
        model="text-embedding-3-small",
        base_url=None,
        endpoint=None,
        api_version=None,
    )
    resolved = SimpleNamespace(effective=SimpleNamespace(embeddings=embeddings))
    search = AsyncMock(
        return_value=[
            {
                "filename": "handbook.pdf",
                "document_uuid": "u1",
                "chunk_index": 2,
                "similarity": 0.91,
                "chunk_text": "Refunds are issued within 30 days.",
                "contextualized_text": None,
            }
        ]
    )
    embedding_service = SimpleNamespace(search_similar_chunks=search)
    with (
        patch(
            "api.services.configuration.ai_model_configuration."
            "get_resolved_ai_model_configuration",
            AsyncMock(return_value=resolved),
        ),
        patch(
            "api.services.gen_ai.build_embedding_service",
            AsyncMock(return_value=embedding_service),
        ),
    ):
        result = await _search_knowledge_base(
            CopilotContext(organization_id=42, user_id=1, conversation_id=1),
            SearchKnowledgeBaseArgs(query="refund policy"),
        )

    search.assert_awaited_once_with(
        query="refund policy",
        organization_id=42,
        limit=5,
        document_uuids=None,
    )
    assert result["results"][0]["filename"] == "handbook.pdf"
    assert result["results"][0]["text"] == "Refunds are issued within 30 days."


@pytest.mark.asyncio
async def test_search_knowledge_base_explains_a_missing_embedding_model():
    resolved = SimpleNamespace(effective=SimpleNamespace(embeddings=None))
    with (
        patch(
            "api.services.configuration.ai_model_configuration."
            "get_resolved_ai_model_configuration",
            AsyncMock(return_value=resolved),
        ),
        pytest.raises(ValueError, match="no embedding model"),
    ):
        await _search_knowledge_base(
            CopilotContext(organization_id=42, user_id=1, conversation_id=1),
            SearchKnowledgeBaseArgs(query="refund policy"),
        )


@pytest.mark.asyncio
async def test_click_to_chat_templates_are_organization_scoped():
    template = SimpleNamespace(
        name="Intro",
        language="en",
        category="marketing",
        body="Hi {{business_name}}",
        variables=["business_name"],
    )
    with patch("api.services.copilot.tools.db_client") as db:
        db.list_whatsapp_templates = AsyncMock(return_value=[template])
        db.list_whatsapp_senders = AsyncMock(return_value=[])
        templates = await _list_whatsapp_click_templates(
            CopilotContext(organization_id=42, user_id=1, conversation_id=1),
            WhatsAppClickTemplatesArgs(),
        )
        senders = await _list_whatsapp_senders(
            CopilotContext(organization_id=42, user_id=1, conversation_id=1),
            ListWhatsAppSendersArgs(),
        )

    db.list_whatsapp_templates.assert_awaited_once_with(42)
    db.list_whatsapp_senders.assert_awaited_once_with(42)
    assert templates["mode"] == "click_to_chat"
    assert templates["templates"][0]["placeholders"] == ["business_name"]
    assert senders["senders"] == []


@pytest.mark.asyncio
async def test_meta_template_tool_falls_back_to_click_to_chat_templates():
    template = SimpleNamespace(
        name="Hi",
        language="en",
        category="marketing",
        body="Hi {{business_name}}",
        variables=["business_name"],
    )
    with (
        patch(
            "api.services.copilot.tools.whatsapp_messaging.list_templates",
            AsyncMock(side_effect=WhatsAppConfigNotFoundError("no WABA")),
        ),
        patch("api.services.copilot.tools.db_client") as db,
    ):
        db.list_whatsapp_templates = AsyncMock(return_value=[template])
        result = await _list_whatsapp_templates(
            CopilotContext(organization_id=42, user_id=1, conversation_id=1),
            ListWhatsAppTemplatesArgs(),
        )

    assert result["mode"] == "click_to_chat"
    assert result["templates"][0]["name"] == "Hi"
    assert "click-to-chat" in result["note"]

    with (
        patch(
            "api.services.copilot.tools.whatsapp_messaging.list_templates",
            AsyncMock(side_effect=WhatsAppConfigNotFoundError("no WABA")),
        ),
        patch("api.services.copilot.tools.db_client") as db,
        pytest.raises(ValueError, match="no click-to-chat templates"),
    ):
        db.list_whatsapp_templates = AsyncMock(return_value=[])
        await _list_whatsapp_templates(
            CopilotContext(organization_id=42, user_id=1, conversation_id=1),
            ListWhatsAppTemplatesArgs(),
        )


@pytest.mark.asyncio
async def test_whatsapp_lead_links_returns_rendered_links():
    links = LeadLinks(
        available=True,
        reason=None,
        phone_e164="+15125550100",
        country="US",
        links=[
            LeadLink(
                template_uuid="t1",
                name="Intro",
                language="en",
                body="Hi Acme",
                url="https://wa.me/15125550100?text=Hi%20Acme",
            )
        ],
    )
    with patch(
        "api.services.copilot.tools.build_lead_links",
        AsyncMock(return_value=links),
    ) as builder:
        result = await _whatsapp_lead_links(
            CopilotContext(organization_id=42, user_id=1, conversation_id=1),
            WhatsAppLeadLinksArgs(lead_id=7),
        )

    builder.assert_awaited_once_with(organization_id=42, user_id=1, lead_id=7)
    assert result["available"] is True
    assert result["links"][0]["url"].startswith("https://wa.me/")
    assert result["phone_e164"] == "+15125550100"


@pytest.mark.asyncio
async def test_whatsapp_lead_links_explains_disclaimer_and_missing_lead():
    with patch(
        "api.services.copilot.tools.build_lead_links",
        AsyncMock(side_effect=LeadLinksDisclaimerRequiredError()),
    ):
        result = await _whatsapp_lead_links(
            CopilotContext(organization_id=42, user_id=1, conversation_id=1),
            WhatsAppLeadLinksArgs(lead_id=7),
        )
    assert result["reason"] == "disclaimer_required"

    with (
        patch(
            "api.services.copilot.tools.build_lead_links",
            AsyncMock(side_effect=LeadLinksLeadNotFoundError()),
        ),
        pytest.raises(ValueError, match="Lead not found"),
    ):
        await _whatsapp_lead_links(
            CopilotContext(organization_id=42, user_id=1, conversation_id=1),
            WhatsAppLeadLinksArgs(lead_id=7),
        )
