"""Copilot tools: the only surface the agent uses to read or change the workspace.

Every handler is organization-scoped because the context is built from the
authenticated user, never from model output. Risk decides whether a call runs
immediately (``read``) or waits for the user's approval (``write``).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.constants import EMAIL_CONSENT_WARNING, LEADS_MAX_TARGET_RESULTS
from api.db import db_client
from api.db.email_campaign_client import EmailCampaignNameConflictError
from api.schemas.leads import (
    LEAD_CRM_STATUSES,
    LeadProjectFilters,
    LeadSearchParams,
)
from api.services.call_concurrency.rate_limiter import RateLimiterUnavailable
from api.services.campaign import launch as voice_campaigns
from api.services.email.audience import (
    AudienceError,
    AudienceRule,
    resolve_audience,
    stored_audience,
    validate_audience_rule,
)
from api.services.email.campaign_rate_limits import allow_campaign_preview
from api.services.email.launch import (
    CampaignNotFoundError,
    EmailLaunchError,
    SendingDisabledError,
    launch_campaign,
)
from api.services.leads.searches import (
    ProviderUnavailableError,
    SearchQueueUnavailableError,
    enqueue_search,
    resolve_provider,
    search_name,
    search_params,
)
from api.services.whatsapp import messaging as whatsapp_messaging
from api.services.whatsapp.lead_links import (
    LeadLinksDisclaimerRequiredError,
    LeadLinksLeadNotFoundError,
    build_lead_links,
)
from api.services.whatsapp.messaging import (
    MAX_BODY_PARAMS,
    WhatsAppConfigNotFoundError,
    WhatsAppMessagingError,
)

RISK_READ = "read"
RISK_WRITE = "write"

MAX_LEAD_ROWS = 50
MAX_TAGGED_LEADS = 100
MAX_TAGS = 5
MAX_SEARCH_ROWS = 10


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


class VoiceCampaignArgs(BaseModel):
    campaign_id: int = Field(ge=1, description="Campaign id from list_voice_campaigns.")


def _voice_campaign_payload(progress: dict) -> dict:
    source_sync = progress.get("source_sync") or {}
    return {
        "campaign_id": progress["campaign_id"],
        "name": progress.get("name"),
        "state": progress["state"],
        "total_rows": progress["total_rows"],
        "processed_rows": progress["processed_rows"],
        "failed_calls": progress["failed_calls"],
        "progress_percentage": round(progress["progress_percentage"], 1),
        "source_sync_status": source_sync.get("status"),
        "source_sync_error": source_sync.get("error"),
        "rate_limit_per_second": progress["rate_limit"],
        "started_at": _iso(progress["started_at"]),
        "completed_at": _iso(progress["completed_at"]),
    }


async def _voice_campaign_status(ctx: CopilotContext, args: VoiceCampaignArgs) -> dict:
    try:
        progress = await voice_campaigns.campaign_progress(
            organization_id=ctx.organization_id,
            campaign_id=args.campaign_id,
        )
    except voice_campaigns.CampaignNotFoundError as exc:
        raise ValueError(
            "Voice campaign not found in this workspace. Use list_voice_campaigns."
        ) from exc
    except voice_campaigns.CampaignActionError as exc:
        raise ValueError(str(exc)) from exc
    return _voice_campaign_payload(progress)


async def _change_voice_campaign_state(
    ctx: CopilotContext, args: VoiceCampaignArgs, action: str
) -> dict:
    action_fn = getattr(voice_campaigns, f"{action}_campaign")
    try:
        await action_fn(
            organization_id=ctx.organization_id,
            campaign_id=args.campaign_id,
            actor_user=SimpleNamespace(id=ctx.user_id),
        )
    except voice_campaigns.TelephonyNotConfiguredError as exc:
        raise ValueError(
            "Outbound calling is not set up in this workspace, so no call was "
            "placed. Connect a telephony provider first."
        ) from exc
    except voice_campaigns.CampaignNotFoundError as exc:
        raise ValueError(
            "Voice campaign not found in this workspace. Use list_voice_campaigns."
        ) from exc
    except voice_campaigns.CampaignActionError as exc:
        raise ValueError(str(exc)) from exc
    return await _voice_campaign_status(ctx, args)


async def _start_voice_campaign(ctx: CopilotContext, args: VoiceCampaignArgs) -> dict:
    return await _change_voice_campaign_state(ctx, args, "start")


async def _resume_voice_campaign(ctx: CopilotContext, args: VoiceCampaignArgs) -> dict:
    return await _change_voice_campaign_state(ctx, args, "resume")


async def _pause_voice_campaign(ctx: CopilotContext, args: VoiceCampaignArgs) -> dict:
    try:
        await voice_campaigns.pause_campaign(
            organization_id=ctx.organization_id,
            campaign_id=args.campaign_id,
        )
    except voice_campaigns.CampaignNotFoundError as exc:
        raise ValueError(
            "Voice campaign not found in this workspace. Use list_voice_campaigns."
        ) from exc
    except voice_campaigns.CampaignActionError as exc:
        raise ValueError(str(exc)) from exc
    return await _voice_campaign_status(ctx, args)


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


class StartLeadSearchArgs(BaseModel):
    category: str = Field(
        min_length=1,
        max_length=200,
        description="Business category or niche, e.g. 'dentists' or 'coffee shops'.",
    )
    location: str = Field(
        min_length=1,
        max_length=200,
        description="City or region, e.g. 'Austin, TX' or 'Berlin'.",
    )
    country_code: str = Field(
        default="US", min_length=2, max_length=2, description="ISO country code."
    )
    language: str = Field(default="en", min_length=2, max_length=2)
    target_results: int = Field(
        default=25,
        ge=1,
        le=LEADS_MAX_TARGET_RESULTS,
        description="Stop collecting after this many unique businesses.",
    )
    radius_km: int = Field(default=10, ge=1, le=100)
    email: bool = Field(
        default=False, description="Ask the scraper to capture public emails."
    )
    website_filter: Literal["any", "has_website", "no_website"] = "any"
    provider: str | None = Field(
        default=None,
        max_length=32,
        description="Lead source; omit for the workspace default.",
    )
    name: str | None = Field(
        default=None, max_length=255, description="Optional label for the search."
    )


async def _start_lead_search(ctx: CopilotContext, args: StartLeadSearchArgs) -> dict:
    try:
        params = LeadSearchParams(
            name=args.name,
            category=args.category,
            location=args.location,
            country_code=args.country_code,
            language=args.language,
            provider=args.provider,
            radius_km=args.radius_km,
            email=args.email,
            website_filter=args.website_filter,
            target_results=args.target_results,
        )
        provider = resolve_provider(args.provider)
    except ValidationError as exc:
        raise ValueError(
            f"Invalid search settings: {exc.error_count()} problem(s)."
        ) from exc
    except ProviderUnavailableError as exc:
        raise ValueError(str(exc)) from exc

    search = await db_client.create_lead_search(
        organization_id=ctx.organization_id,
        created_by=ctx.user_id,
        name=search_name(params),
        category=params.category,
        location=params.location,
        country_code=params.country_code,
        language=params.language,
        provider=provider,
        params=search_params(params),
        target_results=params.target_results,
    )
    try:
        await enqueue_search(search.id, ctx.organization_id)
    except SearchQueueUnavailableError as exc:
        raise ValueError(str(exc)) from exc
    return {
        "search_id": search.id,
        "name": search.name,
        "status": "queued",
        "target_results": params.target_results,
        "note": (
            "The search runs in the background. Check it with lead_search_status; "
            "new leads appear in the CRM when the run finishes."
        ),
    }


class LeadSearchStatusArgs(BaseModel):
    search_id: int = Field(
        description="Search id from start_lead_search or list_lead_searches."
    )


async def _lead_search_status(ctx: CopilotContext, args: LeadSearchStatusArgs) -> dict:
    search = await db_client.get_lead_search(args.search_id, ctx.organization_id)
    if search is None:
        raise ValueError("Search not found in this workspace.")
    return {
        "search_id": search.id,
        "name": search.name,
        "status": search.status,
        "results_count": search.results_count,
        "new_leads_count": search.new_leads_count,
        "error_message": search.error_message,
        "created_at": _iso(search.created_at),
        "completed_at": _iso(search.completed_at),
    }


class ListSearchesArgs(BaseModel):
    limit: int = Field(default=5, ge=1, le=MAX_SEARCH_ROWS)


async def _list_lead_searches(ctx: CopilotContext, args: ListSearchesArgs) -> dict:
    searches, total = await db_client.list_lead_searches(
        ctx.organization_id, limit=args.limit
    )
    return {
        "total": total,
        "searches": [
            {
                "search_id": search.id,
                "name": search.name,
                "status": search.status,
                "results_count": search.results_count,
                "new_leads_count": search.new_leads_count,
                "created_at": _iso(search.created_at),
            }
            for search in searches
        ],
    }


class ListEmailAccountsArgs(BaseModel):
    limit: int = Field(default=10, ge=1, le=25)


async def _list_email_senders(ctx: CopilotContext, args: ListEmailAccountsArgs) -> dict:
    senders = await db_client.list_email_senders(ctx.organization_id)
    return {
        "total": len(senders),
        "senders": [
            {
                "sender_uuid": sender.sender_uuid,
                "name": sender.name,
                "from_email": sender.from_email,
                "status": sender.status,
            }
            for sender in senders[: args.limit]
        ],
    }


async def _list_email_templates(
    ctx: CopilotContext, args: ListEmailAccountsArgs
) -> dict:
    templates = await db_client.list_email_templates(ctx.organization_id)
    return {
        "total": len(templates),
        "templates": [
            {
                "template_uuid": template.template_uuid,
                "name": template.name,
                "subject": template.subject,
                "has_unsubscribe_placeholder": template.has_unsubscribe_placeholder,
            }
            for template in templates[: args.limit]
        ],
    }


class CampaignAudienceArgs(BaseModel):
    audience_type: Literal["leads", "project", "tag", "filter"]
    lead_ids: list[int] = Field(
        default_factory=list,
        max_length=1000,
        description="For audience_type=leads: explicit lead ids from search_leads.",
    )
    project_id: int | None = Field(
        default=None, description="For audience_type=project: saved lead project."
    )
    tag: str | None = Field(
        default=None, max_length=40, description="For audience_type=tag."
    )
    filters: dict[str, Any] | None = Field(
        default=None,
        description=(
            "For audience_type=filter: leads-list filters such as category, city, "
            "country, state, status, q, has_email, has_phone or tag."
        ),
    )
    excluded_lead_ids: list[int] = Field(
        default_factory=list, max_length=5000, description="Leads to skip."
    )


class _StrictCampaignFilters(LeadProjectFilters):
    model_config = ConfigDict(extra="forbid")


def _audience_rule(args: CampaignAudienceArgs) -> AudienceRule:
    filters: dict[str, Any] = {}
    if args.audience_type == "filter":
        if not args.filters:
            raise ValueError("audience_type 'filter' needs a filters object.")
        try:
            parsed = _StrictCampaignFilters.model_validate(args.filters)
        except ValidationError as exc:
            raise ValueError(
                f"Invalid audience filters: {exc.error_count()} problem(s)."
            ) from exc
        filters = parsed.model_dump(exclude_none=True)
        filters["all_matching"] = True
    rule = AudienceRule(
        audience_type=args.audience_type,
        lead_ids=tuple(args.lead_ids),
        project_id=args.project_id,
        tag=args.tag,
        filters=filters,
        excluded_lead_ids=tuple(args.excluded_lead_ids),
    )
    try:
        validate_audience_rule(rule)
    except AudienceError as exc:
        raise ValueError(str(exc)) from exc
    return rule


class PreviewAudienceArgs(CampaignAudienceArgs):
    consent_policy: Literal["opted_in_only", "include_unknown"] = "opted_in_only"
    consent_override_reason: str | None = Field(
        default=None,
        max_length=500,
        description="Required with include_unknown; stored if a draft is created.",
    )


async def _preview_email_audience(
    ctx: CopilotContext, args: PreviewAudienceArgs
) -> dict:
    rule = _audience_rule(args)
    if (
        args.consent_policy == "include_unknown"
        and not (args.consent_override_reason or "").strip()
    ):
        raise ValueError("Including unknown-consent leads needs a written reason.")

    try:
        allowed = await allow_campaign_preview(
            user_id=ctx.user_id, organization_id=ctx.organization_id
        )
    except RateLimiterUnavailable as exc:
        raise ValueError("Audience preview is temporarily unavailable.") from exc
    if not allowed:
        raise ValueError("Too many audience previews; try again later.")

    policy = args.consent_policy
    try:
        resolution = await resolve_audience(
            ctx.organization_id, rule, consent_policy=policy
        )
    except AudienceError as exc:
        raise ValueError(str(exc)) from exc

    warnings: list[str] = []
    if policy == "include_unknown" and resolution.unknown_lead_count:
        warnings.append(
            "Unknown-consent leads are included; the written reason and the "
            "consent warning are stored with the campaign."
        )
    if resolution.eligible_count == 0:
        warnings.append("No eligible recipients; launch would be blocked.")
    return {
        "counts": resolution.counters(),
        "masked_sample": resolution.masked_sample,
        "unknown_lead_count": resolution.unknown_lead_count,
        "warnings": warnings,
    }


class CreateEmailDraftArgs(CampaignAudienceArgs):
    name: str = Field(min_length=1, max_length=255)
    sender_uuid: str | None = Field(
        default=None, description="From list_email_senders; omit to choose later."
    )
    template_uuid: str | None = Field(
        default=None, description="From list_email_templates; omit to choose later."
    )
    consent_policy: Literal["opted_in_only", "include_unknown"] = "opted_in_only"
    consent_override_reason: str | None = Field(default=None, max_length=500)


async def _create_email_campaign_draft(
    ctx: CopilotContext, args: CreateEmailDraftArgs
) -> dict:
    name = args.name.strip()
    if not name:
        raise ValueError("Give the draft a name.")
    rule = _audience_rule(args)

    reason: str | None = None
    warning: str | None = None
    if args.consent_policy == "include_unknown":
        reason = (args.consent_override_reason or "").strip()
        if not reason:
            raise ValueError("Including unknown-consent leads needs a written reason.")
        warning = EMAIL_CONSENT_WARNING

    sender = None
    if args.sender_uuid:
        sender = await db_client.get_email_sender(ctx.organization_id, args.sender_uuid)
        if sender is None:
            raise ValueError(
                "Sender not found in this workspace. Use list_email_senders."
            )
    template = None
    if args.template_uuid:
        template = await db_client.get_email_template(
            ctx.organization_id, args.template_uuid
        )
        if template is None:
            raise ValueError(
                "Template not found in this workspace. Use list_email_templates."
            )

    try:
        campaign = await db_client.create_email_campaign(
            organization_id=ctx.organization_id,
            user_id=ctx.user_id,
            name=name,
            sender_id=sender.id if sender else None,
            template_id=template.id if template else None,
            audience_type=rule.audience_type,
            audience=stored_audience(rule),
            consent_policy=args.consent_policy,
            consent_override_reason=reason,
            consent_override_warning=warning,
        )
    except EmailCampaignNameConflictError as exc:
        raise ValueError(f"A campaign named {name!r} already exists.") from exc

    return {
        "campaign_uuid": campaign.campaign_uuid,
        "name": campaign.name,
        "status": campaign.status,
        "sender": sender.from_email if sender else None,
        "template": template.name if template else None,
        "note": (
            "Draft saved. Nothing is sent until the campaign is launched, "
            "which is a separate approved step."
        ),
    }


class LaunchEmailCampaignArgs(BaseModel):
    campaign_uuid: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "Campaign id from list_email_campaigns (the draft to send). "
            "Only draft or ready campaigns can be launched."
        ),
    )


async def _launch_email_campaign(
    ctx: CopilotContext, args: LaunchEmailCampaignArgs
) -> dict:
    try:
        result = await launch_campaign(
            organization_id=ctx.organization_id,
            user_id=ctx.user_id,
            campaign_uuid=args.campaign_uuid.strip(),
        )
    except SendingDisabledError as exc:
        raise ValueError(
            "Campaign sending is disabled on this deployment, so nothing was "
            "sent. The workspace owner can enable it in the server environment."
        ) from exc
    except CampaignNotFoundError as exc:
        raise ValueError(
            "Email campaign not found in this workspace. Use list_email_campaigns."
        ) from exc
    except EmailLaunchError as exc:
        raise ValueError(str(exc)) from exc
    return {
        "launched": result.ready,
        "status": result.status,
        "queued_count": result.queued_count,
        "preconditions": result.preconditions,
        "message": result.message,
    }


class ListWhatsAppTemplatesArgs(BaseModel):
    config_id: int | None = Field(
        default=None,
        description="WhatsApp account id from list_whatsapp_accounts; omit when there is one.",
    )
    provider: str | None = Field(
        default=None,
        max_length=64,
        description="Messaging provider; omit for the workspace default.",
    )


async def _list_whatsapp_accounts(
    ctx: CopilotContext, args: ListEmailAccountsArgs
) -> dict:
    accounts = await whatsapp_messaging.list_accounts(ctx.organization_id)
    accounts["accounts"] = accounts["accounts"][: args.limit]
    return accounts


async def _list_whatsapp_templates(
    ctx: CopilotContext, args: ListWhatsAppTemplatesArgs
) -> dict:
    try:
        return await whatsapp_messaging.list_templates(
            ctx.organization_id,
            config_id=args.config_id,
            provider_name=args.provider,
        )
    except WhatsAppConfigNotFoundError:
        # Models often reach for the Meta tool first. Without a Business
        # Account it used to answer "no templates" even when the workspace
        # has click-to-chat templates, so surface those here too.
        click_to_chat = await _list_whatsapp_click_templates(
            ctx, WhatsAppClickTemplatesArgs()
        )
        if not click_to_chat["templates"]:
            raise ValueError(
                "No Meta WhatsApp Business Account is connected, and this "
                "workspace has no click-to-chat templates."
            ) from None
        click_to_chat["note"] = (
            "No Meta Business Account is connected; these are the workspace's "
            "click-to-chat templates. The owner opens them in their own "
            "WhatsApp and Bolo never sends them."
        )
        return click_to_chat
    except WhatsAppMessagingError as exc:
        raise ValueError(str(exc)) from exc


class WhatsAppMessageArgs(BaseModel):
    lead_id: int = Field(description="Lead id from search_leads.")
    template_name: str = Field(
        min_length=1,
        max_length=512,
        description="Approved Meta template name from list_whatsapp_templates.",
    )
    language: str = Field(
        default="en", min_length=2, max_length=16, description="Template language."
    )
    body_params: list[str] = Field(
        default_factory=list,
        max_length=MAX_BODY_PARAMS,
        description="Values for the template's body placeholders, in order.",
    )
    config_id: int | None = Field(
        default=None, description="WhatsApp account id; omit when there is one."
    )
    from_number: str | None = Field(
        default=None,
        max_length=32,
        description="Sending number; omit when the account has one active number.",
    )
    provider: str | None = Field(
        default=None,
        max_length=64,
        description="Messaging provider; omit for the workspace default.",
    )


async def _preview_whatsapp_message(
    ctx: CopilotContext, args: WhatsAppMessageArgs
) -> dict:
    try:
        return await whatsapp_messaging.preview_message(
            ctx.organization_id,
            ctx.user_id,
            lead_id=args.lead_id,
            template_name=args.template_name,
            language=args.language,
            body_params=tuple(args.body_params),
            config_id=args.config_id,
            from_number=args.from_number,
            provider_name=args.provider,
        )
    except WhatsAppMessagingError as exc:
        raise ValueError(str(exc)) from exc


async def _send_whatsapp_message(
    ctx: CopilotContext, args: WhatsAppMessageArgs
) -> dict:
    try:
        return await whatsapp_messaging.send_message(
            ctx.organization_id,
            ctx.user_id,
            lead_id=args.lead_id,
            template_name=args.template_name,
            language=args.language,
            body_params=tuple(args.body_params),
            config_id=args.config_id,
            from_number=args.from_number,
            provider_name=args.provider,
        )
    except WhatsAppMessagingError as exc:
        raise ValueError(str(exc)) from exc


class WhatsAppClickTemplatesArgs(BaseModel):
    limit: int = Field(
        default=25, ge=1, le=50, description="Maximum templates to return."
    )


async def _list_whatsapp_click_templates(
    ctx: CopilotContext, args: WhatsAppClickTemplatesArgs
) -> dict:
    templates = await db_client.list_whatsapp_templates(ctx.organization_id)
    return {
        "mode": "click_to_chat",
        "templates": [
            {
                "name": template.name,
                "language": template.language,
                "category": template.category,
                "body": template.body,
                "placeholders": template.variables or [],
            }
            for template in templates[: args.limit]
        ],
        "total": len(templates),
        "note": (
            "These are plain-text templates the owner opens in their own "
            "WhatsApp client. They need no Meta Business account, and Bolo "
            "never sends them."
        ),
    }


class ListWhatsAppSendersArgs(BaseModel):
    limit: int = Field(
        default=25, ge=1, le=50, description="Maximum senders to return."
    )


async def _list_whatsapp_senders(
    ctx: CopilotContext, args: ListWhatsAppSendersArgs
) -> dict:
    senders = await db_client.list_whatsapp_senders(ctx.organization_id)
    return {
        "senders": [
            {
                "label": sender.label,
                "phone_e164": sender.phone_e164,
                "country": sender.country,
                "is_default": sender.is_default,
            }
            for sender in senders[: args.limit]
        ],
        "total": len(senders),
    }


class WhatsAppLeadLinksArgs(BaseModel):
    lead_id: int = Field(description="Lead id from search_leads.")


async def _whatsapp_lead_links(
    ctx: CopilotContext, args: WhatsAppLeadLinksArgs
) -> dict:
    try:
        result = await build_lead_links(
            organization_id=ctx.organization_id,
            user_id=ctx.user_id,
            lead_id=args.lead_id,
        )
    except LeadLinksLeadNotFoundError as exc:
        raise ValueError("Lead not found in this workspace.") from exc
    except LeadLinksDisclaimerRequiredError:
        return {
            "available": False,
            "reason": "disclaimer_required",
            "message": (
                "The owner must accept the WhatsApp disclaimer under "
                "WhatsApp → Templates before links can be created."
            ),
        }
    return {
        "available": result.available,
        "reason": result.reason,
        "phone_e164": result.phone_e164,
        "country": result.country,
        "links": [
            {
                "template": link.name,
                "language": link.language,
                "body": link.body,
                "url": link.url,
            }
            for link in result.links
        ],
        "note": (
            "Opening one of these links pre-fills the owner's own WhatsApp; "
            "Bolo does not send the message."
        ),
    }


class ListWorkspaceFilesArgs(BaseModel):
    limit: int = Field(default=25, ge=1, le=50, description="Maximum files to return.")


async def _list_workspace_files(
    ctx: CopilotContext, args: ListWorkspaceFilesArgs
) -> dict:
    documents = await db_client.get_documents_for_organization(
        ctx.organization_id, limit=args.limit
    )
    return {
        "files": [
            {
                "document_uuid": document.document_uuid,
                "filename": document.filename,
                "retrieval_mode": document.retrieval_mode,
                "status": document.processing_status,
                "chunks": document.total_chunks,
                "size_bytes": document.file_size_bytes,
                "uploaded_at": _iso(document.created_at),
            }
            for document in documents
        ],
        "total": len(documents),
        "note": (
            "Use read_document for a full-document file and "
            "search_knowledge_base for a chunked file."
        ),
    }


class SearchKnowledgeBaseArgs(BaseModel):
    query: str = Field(
        min_length=2,
        max_length=500,
        description="What to look for in the workspace files.",
    )
    limit: int = Field(
        default=5, ge=1, le=10, description="Maximum matching excerpts to return."
    )
    document_uuids: list[str] = Field(
        default_factory=list,
        max_length=10,
        description=(
            "Optional subset of file ids from list_workspace_files or the chat "
            "attachments. Omit to search every chunked file in the workspace."
        ),
    )


async def _search_knowledge_base(
    ctx: CopilotContext, args: SearchKnowledgeBaseArgs
) -> dict:
    # Imported lazily to avoid an import cycle between the copilot and the
    # configuration layer, matching the knowledge-base route.
    from api.services.configuration.ai_model_configuration import (
        apply_managed_embeddings_base_url,
        get_resolved_ai_model_configuration,
    )
    from api.services.gen_ai import build_embedding_service

    resolved = await get_resolved_ai_model_configuration(
        organization_id=ctx.organization_id
    )
    embeddings = resolved.effective.embeddings
    if embeddings is None:
        raise ValueError(
            "This workspace has no embedding model configured. Add one under "
            "Model configurations → Embeddings, or attach the file to the chat "
            "instead."
        )

    provider = getattr(embeddings, "provider", None)
    embedding_service = await build_embedding_service(
        db_client=db_client,
        provider=provider,
        api_key=embeddings.api_key,
        model=embeddings.model,
        base_url=apply_managed_embeddings_base_url(
            provider=provider,
            base_url=getattr(embeddings, "base_url", None),
        ),
        endpoint=getattr(embeddings, "endpoint", None),
        api_version=getattr(embeddings, "api_version", None),
        resolve_correlation=True,
    )
    results = await embedding_service.search_similar_chunks(
        query=args.query,
        organization_id=ctx.organization_id,
        limit=args.limit,
        document_uuids=list(dict.fromkeys(args.document_uuids)) or None,
    )
    return {
        "query": args.query,
        "results": [
            {
                "filename": row.get("filename"),
                "document_uuid": row.get("document_uuid"),
                "chunk_index": row.get("chunk_index"),
                "similarity": round(row.get("similarity") or 0, 4),
                "text": row.get("contextualized_text") or row.get("chunk_text"),
            }
            for row in results
        ],
        "total": len(results),
    }


class ReadDocumentArgs(BaseModel):
    document_uuid: str = Field(
        min_length=1,
        max_length=36,
        description=(
            "The document UUID from the attached-documents list in the user's message."
        ),
    )


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


MAX_DOCUMENT_CHARS = 20_000


async def _read_document(ctx: CopilotContext, args: ReadDocumentArgs) -> dict:
    document = await db_client.get_document_by_uuid(
        args.document_uuid, ctx.organization_id
    )
    if document is None:
        return {"error": "No file with that id in this workspace."}
    text = (document.full_text or "").strip()
    from_chunks = False
    if not text:
        chunks = await db_client.get_chunks_for_document(
            document.id, ctx.organization_id
        )
        text = "\n\n".join(chunk.chunk_text.strip() for chunk in chunks).strip()
        from_chunks = True
    result = {
        "document": document.filename,
        "retrieval_mode": document.retrieval_mode,
        "status": document.processing_status,
    }
    if from_chunks:
        result["source"] = "chunks"
    if not text:
        result["note"] = "This document has no readable text yet."
        return result
    result["truncated"] = len(text) > MAX_DOCUMENT_CHARS
    result["text"] = text[:MAX_DOCUMENT_CHARS]
    return result


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
            name="voice_campaign_status",
            description=(
                "Get one voice campaign's state, dialed progress, failed calls "
                "and source sync status. Use after start_voice_campaign or when "
                "asked how a calling campaign is going."
            ),
            args_schema=VoiceCampaignArgs,
            risk=RISK_READ,
            handler=_voice_campaign_status,
        ),
        CopilotTool(
            name="start_voice_campaign",
            description=(
                "Start an existing outbound voice calling campaign. This places "
                "real phone calls to real contacts, so the user approves it "
                "first. Before proposing it, name the campaign and how many "
                "contacts it will dial. Use list_voice_campaigns for the id."
            ),
            args_schema=VoiceCampaignArgs,
            risk=RISK_WRITE,
            handler=_start_voice_campaign,
        ),
        CopilotTool(
            name="pause_voice_campaign",
            description=(
                "Pause a running or syncing voice campaign so no new calls are "
                "placed. Calls already in progress finish. The user approves it "
                "first."
            ),
            args_schema=VoiceCampaignArgs,
            risk=RISK_WRITE,
            handler=_pause_voice_campaign,
        ),
        CopilotTool(
            name="resume_voice_campaign",
            description=(
                "Resume a paused voice campaign. Dialing starts again, so the "
                "user approves it first."
            ),
            args_schema=VoiceCampaignArgs,
            risk=RISK_WRITE,
            handler=_resume_voice_campaign,
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
            name="start_lead_search",
            description=(
                "Start a new lead-generation search for a category and location. "
                "It spends a scraper run and takes minutes, so the user approves "
                "it first. Default to target_results=25 unless they ask for more. "
                "Tell them what category, place and size you are about to search."
            ),
            args_schema=StartLeadSearchArgs,
            risk=RISK_WRITE,
            handler=_start_lead_search,
        ),
        CopilotTool(
            name="lead_search_status",
            description=(
                "Check one lead search: status, results so far, new leads and "
                "any error. Use after start_lead_search or when asked if a "
                "search finished."
            ),
            args_schema=LeadSearchStatusArgs,
            risk=RISK_READ,
            handler=_lead_search_status,
        ),
        CopilotTool(
            name="list_lead_searches",
            description=(
                "List recent lead searches with status and counts. Use to find "
                "the search id for lead_search_status."
            ),
            args_schema=ListSearchesArgs,
            risk=RISK_READ,
            handler=_list_lead_searches,
        ),
        CopilotTool(
            name="list_email_senders",
            description=(
                "List the workspace's email sending identities with verification "
                "status. Use before creating an email draft."
            ),
            args_schema=ListEmailAccountsArgs,
            risk=RISK_READ,
            handler=_list_email_senders,
        ),
        CopilotTool(
            name="list_email_templates",
            description=(
                "List reusable email templates and whether each has an "
                "unsubscribe link. Use before creating an email draft."
            ),
            args_schema=ListEmailAccountsArgs,
            risk=RISK_READ,
            handler=_list_email_templates,
        ),
        CopilotTool(
            name="preview_email_audience",
            description=(
                "Count who an email audience would reach and why leads are "
                "excluded, without saving or sending anything. Review this "
                "before creating a draft. Returns masked sample addresses only."
            ),
            args_schema=PreviewAudienceArgs,
            risk=RISK_READ,
            handler=_preview_email_audience,
        ),
        CopilotTool(
            name="create_email_campaign_draft",
            description=(
                "Save an email campaign draft with an audience, optional sender, "
                "template and consent policy. The user approves it first. Nothing "
                "is sent; launching is a separate approved step. Preview the "
                "audience and name the recipient count before calling."
            ),
            args_schema=CreateEmailDraftArgs,
            risk=RISK_WRITE,
            handler=_create_email_campaign_draft,
        ),
        CopilotTool(
            name="launch_email_campaign",
            description=(
                "Launch a saved email campaign: freeze its audience and queue real "
                "emails to real recipients. This is irreversible, so the user must "
                "approve each launch. Before calling, name the campaign and its "
                "eligible recipient count. If launch is blocked, relay every "
                "precondition instead of retrying."
            ),
            args_schema=LaunchEmailCampaignArgs,
            risk=RISK_WRITE,
            handler=_launch_email_campaign,
        ),
        CopilotTool(
            name="list_whatsapp_accounts",
            description=(
                "List the workspace's connected WhatsApp Business Accounts and "
                "their sendable numbers. Use before any WhatsApp preview or send."
            ),
            args_schema=ListEmailAccountsArgs,
            risk=RISK_READ,
            handler=_list_whatsapp_accounts,
        ),
        CopilotTool(
            name="list_whatsapp_templates",
            description=(
                "List Meta WhatsApp Business Cloud templates (approval status, "
                "parameters) for a connected Business Account. This does not "
                "cover click-to-chat templates: when the user asks what "
                "WhatsApp templates they have, prefer "
                "list_whatsapp_click_templates, which works without Meta."
            ),
            args_schema=ListWhatsAppTemplatesArgs,
            risk=RISK_READ,
            handler=_list_whatsapp_templates,
        ),
        CopilotTool(
            name="preview_whatsapp_message",
            description=(
                "Resolve one WhatsApp message without sending: recipient number, "
                "sending number, approved template and the rendered body. Use "
                "this and quote the recipient before proposing a send."
            ),
            args_schema=WhatsAppMessageArgs,
            risk=RISK_READ,
            handler=_preview_whatsapp_message,
        ),
        CopilotTool(
            name="send_whatsapp_message",
            description=(
                "Send one approved WhatsApp template to one lead through the "
                "workspace's own account. Real message to a real person, so the "
                "user must approve it. Never batch: preview first and name the "
                "recipient, account and template before calling."
            ),
            args_schema=WhatsAppMessageArgs,
            risk=RISK_WRITE,
            handler=_send_whatsapp_message,
        ),
        CopilotTool(
            name="list_whatsapp_click_templates",
            description=(
                "List the workspace's click-to-chat WhatsApp templates. These "
                "are plain-text messages the owner opens in their own WhatsApp "
                "client; they need no Meta Business account and Bolo never "
                "sends them. This is the default tool when the user asks what "
                "WhatsApp templates they have; call it before saying WhatsApp "
                "is unavailable just because no Business Account is connected."
            ),
            args_schema=WhatsAppClickTemplatesArgs,
            risk=RISK_READ,
            handler=_list_whatsapp_click_templates,
        ),
        CopilotTool(
            name="list_whatsapp_senders",
            description=(
                "List the WhatsApp sending numbers the owner saved. They are "
                "reference numbers for click-to-chat and the country hint for "
                "leads whose phone has no country code."
            ),
            args_schema=ListWhatsAppSendersArgs,
            risk=RISK_READ,
            handler=_list_whatsapp_senders,
        ),
        CopilotTool(
            name="whatsapp_lead_links",
            description=(
                "Build ready click-to-chat wa.me links for one lead using every "
                "saved template, with placeholders already filled. Returns why "
                "a link is impossible when the lead has no usable number or the "
                "disclaimer is not accepted. Nothing is sent."
            ),
            args_schema=WhatsAppLeadLinksArgs,
            risk=RISK_READ,
            handler=_whatsapp_lead_links,
        ),
        CopilotTool(
            name="list_workspace_files",
            description=(
                "List the files in the workspace knowledge base (the Files "
                "menu) with their document ids, retrieval mode and processing "
                "status. Use it before searching or reading a workspace file."
            ),
            args_schema=ListWorkspaceFilesArgs,
            risk=RISK_READ,
            handler=_list_workspace_files,
        ),
        CopilotTool(
            name="search_knowledge_base",
            description=(
                "Search the text of the workspace's chunked files for the "
                "excerpts most relevant to a query. Use this for questions "
                "whose answer lives in a company document. Scope it with "
                "document_uuids when the user names files."
            ),
            args_schema=SearchKnowledgeBaseArgs,
            risk=RISK_READ,
            handler=_search_knowledge_base,
        ),
        CopilotTool(
            name="read_document",
            description=(
                "Read one workspace file in full by document_uuid: a file "
                "attached to this chat or one returned by list_workspace_files. "
                "Call this whenever the answer depends on a file's contents."
            ),
            args_schema=ReadDocumentArgs,
            risk=RISK_READ,
            handler=_read_document,
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
