"""Copilot tools: the only surface the agent uses to read or change the workspace.

Every handler is organization-scoped because the context is built from the
authenticated user, never from model output. Risk decides whether a call runs
immediately (``read``) or waits for the user's approval (``write``).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field

from api.db import db_client
from api.schemas.leads import LEAD_CRM_STATUSES

RISK_READ = "read"
RISK_WRITE = "write"

MAX_LEAD_ROWS = 50
MAX_TAGGED_LEADS = 100
MAX_TAGS = 5


@dataclass(frozen=True)
class CopilotContext:
    organization_id: int
    user_id: int
    conversation_id: int


@dataclass(frozen=True)
class CopilotTool:
    name: str
    description: str
    args_schema: type[BaseModel]
    risk: str
    handler: Callable[[CopilotContext, Any], Awaitable[Any]]

    @property
    def requires_approval(self) -> bool:
        return self.risk == RISK_WRITE


def _iso(value) -> str | None:
    return value.isoformat() if value else None


class OverviewArgs(BaseModel):
    project_id: int | None = Field(
        default=None,
        description="Restrict lead counts to one saved lead project (list).",
    )


async def _business_overview(ctx: CopilotContext, args: OverviewArgs) -> dict:
    stats = await db_client.lead_stats(ctx.organization_id, project_id=args.project_id)
    voice_campaigns = await db_client.get_campaigns(ctx.organization_id)
    email_campaigns = await db_client.list_email_campaigns(ctx.organization_id)
    recent_calls = await db_client.list_recent_voice_runs(ctx.organization_id, limit=5)
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "leads": stats,
        "voice_campaigns": {
            "total": len(voice_campaigns),
            "by_state": dict(Counter(c.state for c in voice_campaigns)),
        },
        "email_campaigns": {
            "total": len(email_campaigns),
            "by_status": dict(Counter(c.status for c in email_campaigns)),
            "sent": sum(c.sent_count or 0 for c in email_campaigns),
            "failed": sum(c.failed_count or 0 for c in email_campaigns),
        },
        "recent_calls": recent_calls,
    }


class SearchLeadsArgs(BaseModel):
    query: str | None = Field(
        default=None,
        max_length=200,
        description="Free-text match against business name, city or category.",
    )
    crm_status: str | None = Field(
        default=None,
        description=f"One of: {', '.join(LEAD_CRM_STATUSES)}.",
    )
    tag: str | None = Field(default=None, max_length=40)
    project_id: int | None = Field(
        default=None, description="Only leads in this saved lead project."
    )
    has_email: bool | None = None
    has_phone: bool | None = None
    sort: str = Field(
        default="created_at",
        description="One of: created_at, rating, title, status.",
    )
    limit: int = Field(default=20, ge=1, le=MAX_LEAD_ROWS)


async def _search_leads(ctx: CopilotContext, args: SearchLeadsArgs) -> dict:
    if args.crm_status and args.crm_status not in LEAD_CRM_STATUSES:
        raise ValueError(
            f"Unknown crm_status. Use one of: {', '.join(LEAD_CRM_STATUSES)}."
        )
    if args.sort not in {"created_at", "rating", "title", "status"}:
        raise ValueError("sort must be one of: created_at, rating, title, status.")
    leads, total = await db_client.list_leads(
        ctx.organization_id,
        query=args.query,
        crm_status=args.crm_status,
        tag=args.tag,
        project_id=args.project_id,
        has_email=args.has_email,
        has_phone=args.has_phone,
        sort=args.sort,
        limit=args.limit,
    )
    return {
        "total": total,
        "returned": len(leads),
        "leads": [
            {
                "id": lead.id,
                "title": lead.title,
                "category": lead.category,
                "city": lead.city,
                "country": lead.country,
                "phone": lead.phone_e164,
                "email": lead.email,
                "website": lead.website,
                "rating": lead.rating,
                "crm_status": lead.crm_status,
                "tags": list(lead.tags or []),
                "created_at": _iso(lead.created_at),
            }
            for lead in leads
        ],
    }


class ListCampaignsArgs(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)


async def _list_voice_campaigns(ctx: CopilotContext, args: ListCampaignsArgs) -> dict:
    campaigns = await db_client.get_campaigns(ctx.organization_id)
    return {
        "total": len(campaigns),
        "campaigns": [
            {
                "id": campaign.id,
                "name": campaign.name,
                "state": campaign.state,
                "workflow_id": campaign.workflow_id,
                "total_rows": campaign.total_rows,
                "processed_rows": campaign.processed_rows,
                "failed_rows": campaign.failed_rows,
                "created_at": _iso(campaign.created_at),
            }
            for campaign in campaigns[: args.limit]
        ],
    }


async def _list_email_campaigns(ctx: CopilotContext, args: ListCampaignsArgs) -> dict:
    campaigns = await db_client.list_email_campaigns(ctx.organization_id)
    return {
        "total": len(campaigns),
        "campaigns": [
            {
                "id": campaign.campaign_uuid,
                "name": campaign.name,
                "status": campaign.status,
                "recipients": campaign.recipient_count,
                "eligible": campaign.eligible_count,
                "sent": campaign.sent_count,
                "failed": campaign.failed_count,
                "created_at": _iso(campaign.created_at),
            }
            for campaign in campaigns[: args.limit]
        ],
    }


class ListCallsArgs(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)


async def _list_recent_calls(ctx: CopilotContext, args: ListCallsArgs) -> dict:
    calls = await db_client.list_recent_voice_runs(ctx.organization_id, args.limit)
    return {"returned": len(calls), "calls": calls}


class TagLeadsArgs(BaseModel):
    lead_ids: list[int] = Field(
        min_length=1,
        max_length=MAX_TAGGED_LEADS,
        description="Lead ids returned by search_leads.",
    )
    tags: list[str] = Field(
        min_length=1,
        max_length=MAX_TAGS,
        description="Tags to append; existing tags are preserved.",
    )


async def _tag_leads(ctx: CopilotContext, args: TagLeadsArgs) -> dict:
    updated = await db_client.add_tags_to_leads(
        ctx.organization_id, args.lead_ids, args.tags
    )
    return {
        "requested": len(args.lead_ids),
        "updated": updated,
        "note": (
            "Leads not found in this workspace were left unchanged."
            if updated < len(args.lead_ids)
            else None
        ),
    }


TOOLS: dict[str, CopilotTool] = {
    tool.name: tool
    for tool in (
        CopilotTool(
            name="business_overview",
            description=(
                "Workspace snapshot: lead CRM counts, voice and email campaign "
                "totals by state, and the latest calls. Use this first for "
                "questions like 'how are we doing' or 'what should I focus on'."
            ),
            args_schema=OverviewArgs,
            risk=RISK_READ,
            handler=_business_overview,
        ),
        CopilotTool(
            name="search_leads",
            description=(
                "Search this workspace's leads by text, CRM status, tag, or "
                "saved project. Returns ids you can use with tag_leads. "
                "Use it before claiming any lead count beyond business_overview."
            ),
            args_schema=SearchLeadsArgs,
            risk=RISK_READ,
            handler=_search_leads,
        ),
        CopilotTool(
            name="list_voice_campaigns",
            description=(
                "List outbound voice calling campaigns with progress. Use for "
                "questions about calling campaigns or outbound call results."
            ),
            args_schema=ListCampaignsArgs,
            risk=RISK_READ,
            handler=_list_voice_campaigns,
        ),
        CopilotTool(
            name="list_email_campaigns",
            description=(
                "List email marketing campaigns with delivery counters. Use "
                "for questions about email performance or send status."
            ),
            args_schema=ListCampaignsArgs,
            risk=RISK_READ,
            handler=_list_email_campaigns,
        ),
        CopilotTool(
            name="list_recent_calls",
            description=(
                "List the most recent completed voice calls across all agents, "
                "with duration and disposition. Use for call activity questions."
            ),
            args_schema=ListCallsArgs,
            risk=RISK_READ,
            handler=_list_recent_calls,
        ),
        CopilotTool(
            name="tag_leads",
            description=(
                "Append tags to specific leads. Changes CRM data, so the user "
                "must approve it in the chat before it runs. Prefer tags over "
                "any status change unless the user explicitly asks for status."
            ),
            args_schema=TagLeadsArgs,
            risk=RISK_WRITE,
            handler=_tag_leads,
        ),
    )
}


def tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "args_schema": tool.args_schema.model_json_schema(),
            "risk": tool.risk,
        }
        for tool in TOOLS.values()
    ]
