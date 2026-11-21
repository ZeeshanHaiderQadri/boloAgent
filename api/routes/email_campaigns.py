"""Email campaigns: audience preview, CRUD and launch.

Launch freezes the audience into recipient rows and hands them to the ARQ send
worker; every other route here only reads or updates the draft. Sending is
behind ``EMAIL_CAMPAIGN_SENDING_ENABLED`` and off by default.

Every route is organization-scoped: a campaign, sender, template, project or
lead id belonging to another organization is indistinguishable from a missing
one.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from api import constants
from api.constants import EMAIL_CONSENT_WARNING
from api.db import db_client
from api.db.email_campaign_client import EmailCampaignNameConflictError
from api.db.models import (
    EmailCampaignModel,
    EmailSenderModel,
    EmailTemplateModel,
    UserModel,
)
from api.enums import (
    EmailCampaignAudienceType,
    EmailCampaignConsentPolicy,
    EmailCampaignStatus,
    EmailSenderStatus,
)
from api.routes.email import _clean_text, _require_organization
from api.schemas.leads import LeadProjectFilters
from api.services.auth.depends import get_user
from api.services.call_concurrency.rate_limiter import RateLimiterUnavailable
from api.services.email.audience import (
    AudienceError,
    AudienceRule,
    normalized_manual_addresses,
    resolve_audience,
    stored_audience,
    validate_audience_rule,
)
from api.services.email.campaign_dispatch import freeze_and_queue_campaign
from api.services.email.campaign_launch import check_launch_preconditions
from api.services.email.campaign_rate_limits import (
    allow_campaign_launch_attempt,
    allow_campaign_preview,
)
from api.services.email.campaign_state import (
    CampaignStateError,
    assert_campaign_deletable,
    assert_campaign_transition,
)
from api.services.email.limits import effective_limits
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames
from api.utils.email_buckets import EXCLUSION_ORDER

router = APIRouter(prefix="/email/campaigns")

# Only draft and ready campaigns may be edited or deleted before launch.
_EDITABLE_STATUSES = {
    EmailCampaignStatus.DRAFT.value,
    EmailCampaignStatus.READY.value,
}


# ----------------------------------------------------------------------
# Request / response schemas
# ----------------------------------------------------------------------


class CampaignFilters(LeadProjectFilters):
    """The leads-list filter, strict so a typo is rejected instead of ignored."""

    model_config = ConfigDict(extra="forbid")


class AudienceRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audience_type: EmailCampaignAudienceType
    # Explicit selection (audience_type=leads), capped by the resolver at 1,000.
    lead_ids: list[int] = Field(default_factory=list)
    project_id: Optional[int] = None
    tag: Optional[str] = None
    # audience_type=filter: the leads-list filter, applied to every match.
    filters: Optional[CampaignFilters] = None
    all_matching: bool = True
    # Manual exclusions from the recipients table. Stored inside the audience
    # JSON; the resolver re-applies them on every call. Capped at 5,000.
    excluded_lead_ids: list[int] = Field(default_factory=list)
    # Operator-typed addresses (e.g. the sender's own inbox for a test send).
    # They skip consent checks but still respect the suppression list.
    manual_emails: list[str] = Field(default_factory=list, max_length=100)
    # Individually appended leads for a filter/project/tag audience. Written by
    # the add-leads endpoint; sent back unchanged when the wizard edits the
    # audience so exclusions do not silently drop them.
    extra_lead_ids: list[int] = Field(default_factory=list)


class ConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consent_policy: EmailCampaignConsentPolicy = (
        EmailCampaignConsentPolicy.OPTED_IN_ONLY
    )
    # Required with include_unknown; stored with the user and the shared warning.
    consent_override_reason: Optional[str] = None


class CampaignPreviewRequest(ConsentRequest):
    audience: AudienceRuleRequest
    # campaign_uuid is no longer accepted. Count storage belongs to PATCH and
    # launch, which know the campaign's audience and consent policy.
    campaign_uuid: Optional[str] = Field(
        default=None,
        description="Deprecated: rejected with 422. Use PATCH to update counts.",
    )


class CampaignCountsResponse(BaseModel):
    recipient_count: int
    eligible_count: int
    excluded_no_email_count: int
    excluded_invalid_email_count: int
    excluded_suppressed_count: int
    excluded_opted_out_count: int
    excluded_unknown_count: int
    # Derived from len(audience.excluded_lead_ids) — no migration needed.
    excluded_manual_count: int = 0
    duplicates_collapsed_count: int


class CampaignPreviewResponse(BaseModel):
    counts: CampaignCountsResponse
    # Masked: never a full address.
    masked_sample: list[str]
    unknown_lead_count: int
    warnings: list[str]


class SenderSummaryResponse(BaseModel):
    uuid: str
    name: str
    status: str
    from_name: str
    from_email: str
    reply_to_email: Optional[str]
    daily_cap: int
    rate_per_second: float
    # "100 per day, this campaign will take N day(s)." None without recipients.
    estimated_days: Optional[int]
    guidance: str


class TemplateSummaryResponse(BaseModel):
    uuid: str
    name: str
    subject: str
    has_unsubscribe_placeholder: bool
    # Plain warning when launch would be blocked by the missing link.
    unsubscribe_warning: Optional[str]


class CampaignProgressResponse(BaseModel):
    """Delivery state of the frozen recipient rows, polled by the UI."""

    total: int = 0
    held: int = 0
    queued: int = 0
    sending: int = 0
    sent: int = 0
    failed: int = 0
    cancelled: int = 0
    bounced: int = 0
    complained: int = 0


class EmailCampaignResponse(BaseModel):
    uuid: str
    name: str
    status: str
    audience_type: str
    audience: dict
    consent_policy: str
    consent_override_reason: Optional[str]
    consent_override_at: Optional[datetime]
    scheduled_at: Optional[datetime]
    launched_at: Optional[datetime]
    cancelled_at: Optional[datetime]
    failure_reason: Optional[str]
    counts: CampaignCountsResponse
    progress: CampaignProgressResponse = CampaignProgressResponse()
    sender: Optional[SenderSummaryResponse]
    template: Optional[TemplateSummaryResponse]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]


class CreateEmailCampaignRequest(ConsentRequest):
    name: str
    audience: AudienceRuleRequest
    sender_uuid: Optional[str] = None
    template_uuid: Optional[str] = None


class UpdateEmailCampaignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    audience: Optional[AudienceRuleRequest] = None
    consent_policy: Optional[EmailCampaignConsentPolicy] = None
    consent_override_reason: Optional[str] = None
    sender_uuid: Optional[str] = None
    template_uuid: Optional[str] = None


class AddLeadsToCampaignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead_ids: list[int] = Field(min_length=1, max_length=1000)


class LaunchResponse(BaseModel):
    # The campaign's status after the attempt; ``sending`` once queued.
    status: str
    ready: bool
    # Plain-language reasons launch is blocked; empty when ready is true.
    preconditions: list[str]
    message: str
    # Recipient rows frozen and released to the send worker; 0 when blocked.
    queued_count: int = 0


class CampaignMessageItem(BaseModel):
    email: str
    status: str
    error_message: Optional[str]
    sent_at: Optional[datetime]
    updated_at: Optional[datetime]


class CampaignMessagesResponse(BaseModel):
    """Recent per-recipient delivery rows for one campaign."""

    items: list[CampaignMessageItem]
    total: int
    page: int
    limit: int


class RecipientItem(BaseModel):
    # None for an address the operator typed by hand (no CRM record).
    lead_id: Optional[int] = None
    title: str
    email: str
    email_consent: str
    bucket: str
    # Why this address landed in its bucket (validator, suppression, consent).
    reason: str


class CampaignRecipientsResponse(BaseModel):
    items: list[RecipientItem]
    page: int
    limit: int
    total: int
    totals_per_bucket: dict[str, int]


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _rule_from_request(request: AudienceRuleRequest) -> AudienceRule:
    filters: dict[str, Any] = {}
    if request.filters is not None:
        filters = request.filters.model_dump(exclude_none=True)
        filters["all_matching"] = request.all_matching
    return AudienceRule(
        audience_type=request.audience_type.value,
        lead_ids=tuple(request.lead_ids or ()),
        project_id=request.project_id,
        tag=request.tag,
        filters=filters,
        excluded_lead_ids=tuple(request.excluded_lead_ids or ()),
        manual_emails=tuple(
            address.strip()
            for address in (request.manual_emails or ())
            if address.strip()
        ),
        extra_lead_ids=tuple(request.extra_lead_ids or ()),
    )


def _stored_audience(rule: AudienceRule) -> dict[str, Any]:
    return stored_audience(rule)


def _rule_from_stored(campaign: EmailCampaignModel) -> AudienceRule:
    """Rebuild the resolver rule from what the campaign row stored."""
    audience = campaign.audience or {}
    shared: dict[str, Any] = {
        "excluded_lead_ids": tuple(audience.get("excluded_lead_ids") or ()),
        "manual_emails": tuple(audience.get("manual_emails") or ()),
        "extra_lead_ids": tuple(audience.get("extra_lead_ids") or ()),
    }
    if campaign.audience_type == EmailCampaignAudienceType.LEADS.value:
        return AudienceRule(
            audience_type=campaign.audience_type,
            lead_ids=tuple(audience.get("lead_ids") or ()),
            **shared,
        )
    if campaign.audience_type == EmailCampaignAudienceType.PROJECT.value:
        return AudienceRule(
            audience_type=campaign.audience_type,
            project_id=audience.get("project_id"),
            **shared,
        )
    if campaign.audience_type == EmailCampaignAudienceType.TAG.value:
        return AudienceRule(
            audience_type=campaign.audience_type,
            tag=audience.get("tag"),
            **shared,
        )
    return AudienceRule(
        audience_type=campaign.audience_type,
        filters=dict(audience.get("filters") or {}),
        **shared,
    )


def _validated_rule(request: AudienceRuleRequest) -> AudienceRule:
    rule = _rule_from_request(request)
    try:
        validate_audience_rule(rule)
    except AudienceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return rule


def _consent_values(
    policy: EmailCampaignConsentPolicy, reason: Optional[str]
) -> tuple[str, Optional[str], Optional[str]]:
    """Return ``(policy_value, reason, warning)`` or raise 422."""
    if policy == EmailCampaignConsentPolicy.INCLUDE_UNKNOWN:
        cleaned = (reason or "").strip()
        if not cleaned:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Including unknown-consent leads requires a written reason; "
                    "it is stored with your name, the time and the consent "
                    "warning."
                ),
            )
        return policy.value, cleaned, EMAIL_CONSENT_WARNING
    return policy.value, None, None


def _counts_response(campaign: EmailCampaignModel) -> CampaignCountsResponse:
    # excluded_manual_count is derived from the audience JSON, not a column.
    audience = campaign.audience or {}
    manual_count = len(audience.get("excluded_lead_ids") or [])
    return CampaignCountsResponse(
        recipient_count=campaign.recipient_count,
        eligible_count=campaign.eligible_count,
        excluded_no_email_count=campaign.excluded_no_email_count,
        excluded_invalid_email_count=campaign.excluded_invalid_email_count,
        excluded_suppressed_count=campaign.excluded_suppressed_count,
        excluded_opted_out_count=campaign.excluded_opted_out_count,
        excluded_unknown_count=campaign.excluded_unknown_count,
        excluded_manual_count=manual_count,
        duplicates_collapsed_count=campaign.duplicates_collapsed_count,
    )


def _sender_summary(
    sender: EmailSenderModel, eligible_count: int
) -> SenderSummaryResponse:
    limits = effective_limits(sender)
    days = (
        max(1, math.ceil(eligible_count / limits.daily_cap))
        if eligible_count > 0
        else None
    )
    if days is None:
        guidance = (
            f"{limits.daily_cap} per day. Preview the audience to estimate how "
            "many days this campaign will take."
        )
    else:
        guidance = (
            f"{limits.daily_cap} per day, this campaign will take {days} "
            f"day{'s' if days != 1 else ''}."
        )
    return SenderSummaryResponse(
        uuid=sender.sender_uuid,
        name=sender.name,
        status=sender.status,
        from_name=sender.from_name,
        from_email=sender.from_email,
        reply_to_email=sender.reply_to_email,
        daily_cap=limits.daily_cap,
        rate_per_second=limits.rate_per_second,
        estimated_days=days,
        guidance=guidance,
    )


def _template_summary(template: EmailTemplateModel) -> TemplateSummaryResponse:
    return TemplateSummaryResponse(
        uuid=template.template_uuid,
        name=template.name,
        subject=template.subject,
        has_unsubscribe_placeholder=template.has_unsubscribe_placeholder,
        unsubscribe_warning=(
            None
            if template.has_unsubscribe_placeholder
            else (
                "This template has no unsubscribe link. Marketing email must "
                "let people opt out, so launch will be blocked until you add one."
            )
        ),
    )


async def _sender_or_404(organization_id: int, sender_uuid: str) -> EmailSenderModel:
    sender = await db_client.get_email_sender(organization_id, sender_uuid)
    if sender is None:
        raise HTTPException(status_code=404, detail="Email sender not found")
    return sender


async def _template_or_404(
    organization_id: int, template_uuid: str
) -> EmailTemplateModel:
    template = await db_client.get_email_template(organization_id, template_uuid)
    if template is None:
        raise HTTPException(status_code=404, detail="Email template not found")
    return template


async def _campaign_or_404(
    organization_id: int, campaign_uuid: str
) -> EmailCampaignModel:
    campaign = await db_client.get_email_campaign(organization_id, campaign_uuid)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Email campaign not found")
    return campaign


async def _campaign_response(
    campaign: EmailCampaignModel,
    *,
    sender: Optional[EmailSenderModel],
    template: Optional[EmailTemplateModel],
) -> EmailCampaignResponse:
    if campaign.sender_id and sender is None:
        sender = await db_client.get_email_sender_by_id(
            campaign.organization_id, campaign.sender_id
        )
    if campaign.template_id and template is None:
        template = await db_client.get_email_template_by_id(
            campaign.organization_id, campaign.template_id
        )
    message_counts = await db_client.count_campaign_messages(
        campaign.organization_id, campaign.id
    )
    progress = CampaignProgressResponse(
        total=sum(message_counts.values()),
        **{
            field: int(message_counts.get(field, 0))
            for field in CampaignProgressResponse.model_fields
            if field != "total"
        },
    )
    return EmailCampaignResponse(
        uuid=campaign.campaign_uuid,
        name=campaign.name,
        status=campaign.status,
        audience_type=campaign.audience_type,
        audience=campaign.audience or {},
        consent_policy=campaign.consent_policy,
        consent_override_reason=campaign.consent_override_reason,
        consent_override_at=campaign.consent_override_at,
        scheduled_at=campaign.scheduled_at,
        launched_at=campaign.launched_at,
        cancelled_at=campaign.cancelled_at,
        failure_reason=campaign.failure_reason,
        counts=_counts_response(campaign),
        progress=progress,
        sender=(
            _sender_summary(sender, campaign.eligible_count)
            if sender is not None
            else None
        ),
        template=_template_summary(template) if template is not None else None,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
    )


# ----------------------------------------------------------------------
# Preview
# ----------------------------------------------------------------------


@router.post("/preview")
async def preview_campaign_audience(
    request: CampaignPreviewRequest,
    user: UserModel = Depends(get_user),
) -> CampaignPreviewResponse:
    """Resolve an audience rule without storing or sending anything.

    Counts are never written back to a campaign from here: the preview may use
    any audience or consent policy the caller likes, and writing those counts
    would corrupt the stored campaign. Use PATCH (which resets counts when the
    audience changes) or launch (which re-resolves and stores fresh counts).
    """
    organization_id = _require_organization(user)

    if request.campaign_uuid:
        raise HTTPException(
            status_code=422,
            detail=(
                "campaign_uuid is no longer accepted on preview. "
                "PATCH the campaign to store updated counts, or use the "
                "launch endpoint which re-resolves the audience itself."
            ),
        )

    # Validate cheaply before consuming a rate-limit token.
    rule = _validated_rule(request.audience)
    policy, _, _ = _consent_values(
        request.consent_policy, request.consent_override_reason
    )

    try:
        allowed = await allow_campaign_preview(
            user_id=user.id, organization_id=organization_id
        )
    except RateLimiterUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Campaign preview is temporarily unavailable.",
        ) from exc
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many campaign previews. Try again later.",
        )

    try:
        resolution = await resolve_audience(
            organization_id, rule, consent_policy=policy
        )
    except AudienceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    warnings: list[str] = []
    if (
        request.consent_policy == EmailCampaignConsentPolicy.INCLUDE_UNKNOWN
        and resolution.unknown_lead_count
    ):
        warnings.append(
            "Unknown-consent leads are included. The written reason and the "
            "consent warning are stored with the campaign."
        )
    if resolution.eligible_count == 0:
        warnings.append("No eligible recipients; launch would be blocked.")

    return CampaignPreviewResponse(
        counts=CampaignCountsResponse(**resolution.counters()),
        masked_sample=resolution.masked_sample,
        unknown_lead_count=resolution.unknown_lead_count,
        warnings=warnings,
    )


# ----------------------------------------------------------------------
# Draft campaign CRUD
# ----------------------------------------------------------------------


@router.get("")
async def list_email_campaigns(
    user: UserModel = Depends(get_user),
) -> list[EmailCampaignResponse]:
    organization_id = _require_organization(user)
    campaigns = await db_client.list_email_campaigns(organization_id)
    return [
        await _campaign_response(campaign, sender=None, template=None)
        for campaign in campaigns
    ]


@router.post("", status_code=201)
async def create_email_campaign(
    request: CreateEmailCampaignRequest,
    user: UserModel = Depends(get_user),
) -> EmailCampaignResponse:
    organization_id = _require_organization(user)
    name = _clean_text(request.name, field="Name", max_length=255)
    rule = _validated_rule(request.audience)
    policy, reason, warning = _consent_values(
        request.consent_policy, request.consent_override_reason
    )

    sender = (
        await _sender_or_404(organization_id, request.sender_uuid)
        if request.sender_uuid
        else None
    )
    template = (
        await _template_or_404(organization_id, request.template_uuid)
        if request.template_uuid
        else None
    )

    try:
        campaign = await db_client.create_email_campaign(
            organization_id=organization_id,
            user_id=user.id,
            name=name,
            sender_id=sender.id if sender else None,
            template_id=template.id if template else None,
            audience_type=rule.audience_type,
            audience=_stored_audience(rule),
            consent_policy=policy,
            consent_override_reason=reason,
            consent_override_warning=warning,
        )
    except EmailCampaignNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return await _campaign_response(campaign, sender=sender, template=template)


@router.get("/{campaign_uuid}")
async def get_email_campaign(
    campaign_uuid: str,
    user: UserModel = Depends(get_user),
) -> EmailCampaignResponse:
    organization_id = _require_organization(user)
    campaign = await _campaign_or_404(organization_id, campaign_uuid)
    return await _campaign_response(campaign, sender=None, template=None)


@router.patch("/{campaign_uuid}")
async def update_email_campaign(
    campaign_uuid: str,
    request: UpdateEmailCampaignRequest,
    user: UserModel = Depends(get_user),
) -> EmailCampaignResponse:
    organization_id = _require_organization(user)
    campaign = await _campaign_or_404(organization_id, campaign_uuid)
    if campaign.status not in _EDITABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A campaign in {campaign.status!r} can no longer be edited; "
                "only draft or ready campaigns are editable."
            ),
        )

    fields_set = request.model_fields_set
    name = (
        _clean_text(request.name, field="Name", max_length=255)
        if request.name is not None
        else None
    )

    audience_type = None
    audience = None
    audience_changed = False
    if request.audience is not None:
        rule = _validated_rule(request.audience)
        audience_type = rule.audience_type
        audience = _stored_audience(rule)
        audience_changed = True

    sender_id: Any = ...
    if "sender_uuid" in fields_set:
        sender_id = (
            (await _sender_or_404(organization_id, request.sender_uuid)).id
            if request.sender_uuid
            else None
        )
    template_id: Any = ...
    if "template_uuid" in fields_set:
        template_id = (
            (await _template_or_404(organization_id, request.template_uuid)).id
            if request.template_uuid
            else None
        )

    consent_kwargs: dict[str, Any] = {}
    if request.consent_policy is not None:
        # When re-asserting include_unknown without a new reason, fall back to
        # whatever reason is already stored. The fallback must come first so
        # _consent_values receives a non-empty reason and does not raise 422.
        override_reason = request.consent_override_reason
        if (
            request.consent_policy == EmailCampaignConsentPolicy.INCLUDE_UNKNOWN
            and not (override_reason or "").strip()
        ):
            override_reason = (campaign.consent_override_reason or "").strip() or None
        policy, reason, warning = _consent_values(
            request.consent_policy, override_reason
        )
        consent_kwargs = {
            "consent_policy": policy,
            "consent_override_reason": reason,
            "consent_override_warning": warning,
            "consent_override_user_id": user.id if reason else None,
        }

    try:
        updated = await db_client.update_email_campaign(
            organization_id,
            campaign_uuid,
            name=name,
            sender_id=sender_id,
            template_id=template_id,
            audience_type=audience_type,
            audience=audience,
            **consent_kwargs,
        )
    except EmailCampaignNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if updated is None:
        raise HTTPException(status_code=404, detail="Email campaign not found")

    if audience_changed or consent_kwargs:
        await db_client.update_email_campaign_counts(
            organization_id,
            campaign_uuid,
            counts={
                "recipient_count": 0,
                "eligible_count": 0,
                "excluded_no_email_count": 0,
                "excluded_invalid_email_count": 0,
                "excluded_suppressed_count": 0,
                "excluded_opted_out_count": 0,
                "excluded_unknown_count": 0,
                "duplicates_collapsed_count": 0,
            },
        )
        refreshed = await db_client.get_email_campaign(organization_id, campaign_uuid)
        if refreshed is None:
            raise HTTPException(status_code=404, detail="Email campaign not found")
        updated = refreshed

    return await _campaign_response(updated, sender=None, template=None)


@router.post("/{campaign_uuid}/add-leads")
async def add_leads_to_email_campaign(
    campaign_uuid: str,
    request: AddLeadsToCampaignRequest,
    user: UserModel = Depends(get_user),
) -> EmailCampaignResponse:
    """Append individually selected CRM leads to a draft campaign's audience.

    When the audience is already an explicit list the ids are merged into it.
    For a filter, project or tag audience the ids are stored as
    ``extra_lead_ids`` so the saved rule keeps selecting its own matches; the
    resolver unions both sets at preview and launch.
    """
    organization_id = _require_organization(user)
    campaign = await _campaign_or_404(organization_id, campaign_uuid)
    if campaign.status not in _EDITABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A campaign in {campaign.status!r} can no longer be edited; "
                "only draft or ready campaigns are editable."
            ),
        )

    unique_ids = list(dict.fromkeys(request.lead_ids))
    found = await db_client.audience_lead_ids(organization_id, unique_ids)
    missing = [lead_id for lead_id in unique_ids if lead_id not in found]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{len(missing)} selected lead(s) do not belong to this "
                "organization; the audience was not changed."
            ),
        )

    rule = _rule_from_stored(campaign)
    if rule.audience_type == EmailCampaignAudienceType.LEADS.value:
        merged = list(dict.fromkeys([*rule.lead_ids, *unique_ids]))
        if len(merged) > 1000:
            raise HTTPException(
                status_code=422,
                detail="A campaign may address at most 1000 selected leads.",
            )
        rule = replace(rule, lead_ids=tuple(merged))
    else:
        merged_extra = list(dict.fromkeys([*rule.extra_lead_ids, *unique_ids]))
        if len(merged_extra) > 1000:
            raise HTTPException(
                status_code=422,
                detail="At most 1000 leads may be added individually.",
            )
        rule = replace(rule, extra_lead_ids=tuple(merged_extra))
    still_excluded = tuple(
        lead_id for lead_id in rule.excluded_lead_ids if lead_id not in unique_ids
    )
    rule = replace(rule, excluded_lead_ids=still_excluded)

    updated = await db_client.update_email_campaign(
        organization_id,
        campaign_uuid,
        audience=_stored_audience(rule),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return await _campaign_response(updated, sender=None, template=None)


@router.delete("/{campaign_uuid}")
async def delete_email_campaign(
    campaign_uuid: str,
    user: UserModel = Depends(get_user),
) -> dict:
    organization_id = _require_organization(user)
    campaign = await _campaign_or_404(organization_id, campaign_uuid)
    try:
        assert_campaign_deletable(campaign.status)
    except CampaignStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db_client.delete_email_campaign(organization_id, campaign_uuid)
    return {"status": "deleted", "uuid": campaign_uuid}


@router.post("/{campaign_uuid}/launch")
async def launch_email_campaign(
    campaign_uuid: str,
    user: UserModel = Depends(get_user),
) -> LaunchResponse:
    """Freeze the audience and hand it to the send worker.

    ``EMAIL_CAMPAIGN_SENDING_ENABLED`` is off by default and the endpoint
    answers 403. Preconditions are checked first; a blocked campaign reports
    every failure and is left untouched. A ready campaign freezes its template
    and audience into ``email_messages`` rows, moves to ``sending`` and enqueues
    one ARQ job that delivers them.
    """
    organization_id = _require_organization(user)

    if not constants.EMAIL_CAMPAIGN_SENDING_ENABLED:
        raise HTTPException(
            status_code=403,
            detail=(
                "Campaign sending is disabled on this deployment. "
                "EMAIL_CAMPAIGN_SENDING_ENABLED is off; no email was sent and "
                "nothing was queued."
            ),
        )

    try:
        allowed = await allow_campaign_launch_attempt(
            user_id=user.id, organization_id=organization_id
        )
    except RateLimiterUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Campaign launch is temporarily unavailable.",
        ) from exc
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many launch attempts. Try again later.",
        )

    campaign = await _campaign_or_404(organization_id, campaign_uuid)
    if campaign.status not in _EDITABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A campaign in {campaign.status!r} can no longer be launched; "
                "only draft or ready campaigns can be launched."
            ),
        )

    rule = _rule_from_stored(campaign)
    try:
        resolution = await resolve_audience(
            organization_id, rule, consent_policy=campaign.consent_policy
        )
    except AudienceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    sender = (
        await db_client.get_email_sender_by_id(organization_id, campaign.sender_id)
        if campaign.sender_id
        else None
    )
    template = (
        await db_client.get_email_template_by_id(organization_id, campaign.template_id)
        if campaign.template_id
        else None
    )
    readiness = check_launch_preconditions(
        campaign=campaign,
        sender=sender,
        template=template,
        resolution=resolution,
        public_base_url=constants.PUBLIC_BASE_URL,
    )
    await db_client.update_email_campaign_counts(
        organization_id, campaign_uuid, counts=resolution.counters()
    )

    if not readiness.ready:
        return LaunchResponse(
            status=campaign.status,
            ready=False,
            preconditions=list(readiness.failures),
            message=(
                "Launch is blocked. Fix the listed items, preview again, then "
                "retry. No email was sent and nothing was queued."
            ),
        )

    async def _mark_failed(reason: str) -> None:
        await db_client.cancel_pending_messages(
            organization_id, campaign.id, reason=reason
        )
        await db_client.set_campaign_launch_fields(
            organization_id,
            campaign.id,
            status=EmailCampaignStatus.FAILED.value,
            failure_reason=reason,
        )

    # ``draft`` must pass through ``ready``; ``launching`` is the freeze step.
    current_status = campaign.status
    try:
        if current_status == EmailCampaignStatus.DRAFT.value:
            assert_campaign_transition(current_status, EmailCampaignStatus.READY.value)
            current_status = EmailCampaignStatus.READY.value
        assert_campaign_transition(current_status, EmailCampaignStatus.LAUNCHING.value)
    except CampaignStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await db_client.set_campaign_launch_fields(
        organization_id,
        campaign.id,
        status=EmailCampaignStatus.LAUNCHING.value,
        failure_reason=None,
    )

    try:
        queued_count = await freeze_and_queue_campaign(
            organization_id=organization_id,
            campaign=campaign,
            sender=sender,
            template=template,
            rule=rule,
            user_id=user.id,
        )
    except Exception as exc:
        reason = f"Could not prepare the campaign for sending: {exc}"
        logger.exception("Campaign {} freeze failed", campaign.id)
        await _mark_failed(reason)
        raise HTTPException(status_code=422, detail=reason) from exc

    if queued_count == 0:
        reason = "No eligible recipients were left when the audience was frozen."
        await _mark_failed(reason)
        raise HTTPException(status_code=409, detail=reason)

    await db_client.set_campaign_launch_fields(
        organization_id,
        campaign.id,
        status=EmailCampaignStatus.SENDING.value,
    )

    try:
        await enqueue_job(
            FunctionNames.SEND_EMAIL_CAMPAIGN, campaign.id, organization_id
        )
    except Exception as exc:
        logger.exception("Campaign {} could not be queued", campaign.id)
        reason = (
            "The send queue is unavailable, so the campaign was stopped before "
            "any email was sent. Retry the launch once the worker is back."
        )
        await _mark_failed(reason)
        raise HTTPException(status_code=503, detail=reason) from exc

    return LaunchResponse(
        status=EmailCampaignStatus.SENDING.value,
        ready=True,
        preconditions=[],
        message=(
            f"Campaign launched: {queued_count} recipient(s) queued for delivery."
        ),
        queued_count=queued_count,
    )


# ----------------------------------------------------------------------
# Sending controls (pause / resume / stop)
# ----------------------------------------------------------------------


@router.post("/{campaign_uuid}/pause")
async def pause_email_campaign(
    campaign_uuid: str,
    user: UserModel = Depends(get_user),
) -> EmailCampaignResponse:
    """Stop the worker from claiming new rows. Queued rows stay queued."""
    organization_id = _require_organization(user)
    campaign = await _campaign_or_404(organization_id, campaign_uuid)
    if campaign.status != EmailCampaignStatus.SENDING.value:
        raise HTTPException(
            status_code=409,
            detail=f"Only a sending campaign can be paused (now {campaign.status!r}).",
        )
    try:
        assert_campaign_transition(campaign.status, EmailCampaignStatus.PAUSED.value)
    except CampaignStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db_client.set_campaign_launch_fields(
        organization_id, campaign.id, status=EmailCampaignStatus.PAUSED.value
    )
    refreshed = await _campaign_or_404(organization_id, campaign_uuid)
    return await _campaign_response(refreshed, sender=None, template=None)


@router.post("/{campaign_uuid}/resume")
async def resume_email_campaign(
    campaign_uuid: str,
    user: UserModel = Depends(get_user),
) -> EmailCampaignResponse:
    """Return a paused campaign to ``sending`` and hand it back to the worker."""
    organization_id = _require_organization(user)
    if not constants.EMAIL_CAMPAIGN_SENDING_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Campaign sending is disabled on this deployment.",
        )
    campaign = await _campaign_or_404(organization_id, campaign_uuid)
    if campaign.status != EmailCampaignStatus.PAUSED.value:
        raise HTTPException(
            status_code=409,
            detail=f"Only a paused campaign can be resumed (now {campaign.status!r}).",
        )
    try:
        assert_campaign_transition(campaign.status, EmailCampaignStatus.SENDING.value)
    except CampaignStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db_client.set_campaign_launch_fields(
        organization_id, campaign.id, status=EmailCampaignStatus.SENDING.value
    )
    try:
        await enqueue_job(
            FunctionNames.SEND_EMAIL_CAMPAIGN, campaign.id, organization_id
        )
    except Exception as exc:
        logger.exception("Campaign {} could not be re-queued", campaign.id)
        raise HTTPException(
            status_code=503,
            detail="The send queue is unavailable; the campaign stays paused.",
        ) from exc
    refreshed = await _campaign_or_404(organization_id, campaign_uuid)
    return await _campaign_response(refreshed, sender=None, template=None)


@router.post("/{campaign_uuid}/stop")
async def stop_email_campaign(
    campaign_uuid: str,
    user: UserModel = Depends(get_user),
) -> EmailCampaignResponse:
    """Cancel a running campaign: queued rows are cancelled, delivered rows stay."""
    organization_id = _require_organization(user)
    campaign = await _campaign_or_404(organization_id, campaign_uuid)
    if campaign.status not in {
        EmailCampaignStatus.SENDING.value,
        EmailCampaignStatus.PAUSED.value,
        EmailCampaignStatus.LAUNCHING.value,
    }:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot stop a campaign in {campaign.status!r} state.",
        )
    try:
        assert_campaign_transition(campaign.status, EmailCampaignStatus.CANCELLED.value)
    except CampaignStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    cancelled = await db_client.cancel_pending_messages(
        organization_id,
        campaign.id,
        reason="Stopped by the operator before sending.",
    )
    await db_client.set_campaign_launch_fields(
        organization_id,
        campaign.id,
        status=EmailCampaignStatus.CANCELLED.value,
        cancelled_by=user.id,
        cancelled_at=datetime.now(UTC),
    )
    logger.info(
        "Campaign {} stopped by user {} ({} queued row(s) cancelled)",
        campaign.id,
        user.id,
        cancelled,
    )
    refreshed = await _campaign_or_404(organization_id, campaign_uuid)
    return await _campaign_response(refreshed, sender=None, template=None)


@router.post("/{campaign_uuid}/restart")
async def restart_email_campaign(
    campaign_uuid: str,
    user: UserModel = Depends(get_user),
) -> EmailCampaignResponse:
    """Restart a stopped or failed campaign without re-freezing the audience.

    Rows the stop or failure cancelled are returned to ``queued`` and the
    campaign goes back to ``sending``. Delivered rows stay terminal, so an
    address that already received the email is never mailed twice.
    """
    organization_id = _require_organization(user)

    if not constants.EMAIL_CAMPAIGN_SENDING_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Campaign sending is disabled on this deployment.",
        )

    try:
        allowed = await allow_campaign_launch_attempt(
            user_id=user.id, organization_id=organization_id
        )
    except RateLimiterUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Campaign restart is temporarily unavailable.",
        ) from exc
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many launch attempts. Try again later.",
        )

    campaign = await _campaign_or_404(organization_id, campaign_uuid)
    if campaign.status not in {
        EmailCampaignStatus.CANCELLED.value,
        EmailCampaignStatus.FAILED.value,
    }:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Only a cancelled or failed campaign can be restarted "
                f"(now {campaign.status!r})."
            ),
        )
    try:
        assert_campaign_transition(campaign.status, EmailCampaignStatus.SENDING.value)
    except CampaignStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    sender = (
        await db_client.get_email_sender_by_id(organization_id, campaign.sender_id)
        if campaign.sender_id
        else None
    )
    if sender is None or sender.status != EmailSenderStatus.VERIFIED.value:
        raise HTTPException(
            status_code=409,
            detail=(
                "The campaign's sender is missing or no longer verified. "
                "Verify the sender before restarting; nothing was requeued."
            ),
        )

    requeued = await db_client.requeue_cancelled_messages(organization_id, campaign.id)
    if requeued == 0:
        raise HTTPException(
            status_code=409,
            detail=(
                "Every recipient was already delivered or skipped; there is "
                "nothing left to send."
            ),
        )

    await db_client.set_campaign_launch_fields(
        organization_id,
        campaign.id,
        status=EmailCampaignStatus.SENDING.value,
        failure_reason=None,
        cancelled_by=None,
        cancelled_at=None,
    )
    try:
        await enqueue_job(
            FunctionNames.SEND_EMAIL_CAMPAIGN, campaign.id, organization_id
        )
    except Exception as exc:
        logger.exception("Campaign {} could not be restarted", campaign.id)
        await db_client.cancel_pending_messages(
            organization_id,
            campaign.id,
            reason="Restart could not reach the send queue; nothing was sent.",
        )
        await db_client.set_campaign_launch_fields(
            organization_id,
            campaign.id,
            status=campaign.status,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "The send queue is unavailable, so the campaign was not "
                "restarted. Try again once the worker is back."
            ),
        ) from exc

    logger.info(
        "Campaign {} restarted by user {} ({} row(s) requeued)",
        campaign.id,
        user.id,
        requeued,
    )
    refreshed = await _campaign_or_404(organization_id, campaign_uuid)
    return await _campaign_response(refreshed, sender=None, template=None)


# ----------------------------------------------------------------------
# Recipients (paginated lead table with bucket classification)
# ----------------------------------------------------------------------


@router.get("/{campaign_uuid}/recipients")
async def list_campaign_recipients(
    campaign_uuid: str,
    bucket: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
    user: UserModel = Depends(get_user),
) -> CampaignRecipientsResponse:
    """Return a paginated, bucket-classified list of leads for a campaign.

    Each row carries the address and a plain-language reason, so the operator
    can see which check (validator, suppression list, consent, manual
    exclusion) decided that row's bucket. Rate-limited like the preview.
    """
    if page < 1:
        raise HTTPException(status_code=422, detail="page must be >= 1")
    if not (1 <= limit <= 100):
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100")

    # An empty query value (`?bucket=`) means "all buckets"; only a non-empty
    # value narrows the result.
    bucket = (bucket or "").strip() or None
    if bucket is not None and bucket not in EXCLUSION_ORDER:
        raise HTTPException(
            status_code=422,
            detail="bucket must be one of: " + ", ".join(EXCLUSION_ORDER),
        )

    organization_id = _require_organization(user)

    try:
        allowed = await allow_campaign_preview(
            user_id=user.id, organization_id=organization_id
        )
    except RateLimiterUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Campaign recipients is temporarily unavailable.",
        ) from exc
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Try again later.",
        )

    campaign = await _campaign_or_404(organization_id, campaign_uuid)
    suppressed = await db_client.suppressed_audience_addresses(organization_id)
    rule = _rule_from_stored(campaign)

    explicit_ids: list[int] | None = None
    if campaign.audience_type == "leads":
        audience = campaign.audience or {}
        ids = [int(x) for x in (audience.get("lead_ids") or [])]
        if ids:
            found = await db_client.audience_lead_ids(organization_id, ids)
            explicit_ids = [i for i in ids if i in found]
        else:
            explicit_ids = []

    from api.services.email.audience import filter_kwargs as _rule_filter_kwargs

    filter_kwargs = _rule_filter_kwargs(rule)
    audience_data = campaign.audience or {}
    excluded_ids = set(int(x) for x in (audience_data.get("excluded_lead_ids") or []))
    include_unknown = campaign.consent_policy == "include_unknown"

    offset = (page - 1) * limit
    try:
        manual_addresses = (
            normalized_manual_addresses(rule.manual_emails)
            if rule.manual_emails
            else []
        )
    except AudienceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    items, total, bucket_counts = await db_client.stream_recipient_rows(
        organization_id,
        lead_ids=explicit_ids,
        filter_kwargs=filter_kwargs,
        suppressed=suppressed,
        excluded_ids=excluded_ids,
        include_unknown=include_unknown,
        bucket_filter=bucket,
        offset=offset,
        limit=limit,
        manual_addresses=manual_addresses,
    )

    return CampaignRecipientsResponse(
        items=[RecipientItem(**item) for item in items],
        page=page,
        limit=limit,
        total=total,
        totals_per_bucket=bucket_counts,
    )


@router.get("/{campaign_uuid}/messages")
async def list_campaign_messages(
    campaign_uuid: str,
    page: int = 1,
    limit: int = 50,
    user: UserModel = Depends(get_user),
) -> CampaignMessagesResponse:
    """Recent delivery rows for the live progress screen."""
    if page < 1:
        raise HTTPException(status_code=422, detail="page must be >= 1")
    if not (1 <= limit <= 100):
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
    organization_id = _require_organization(user)
    campaign = await _campaign_or_404(organization_id, campaign_uuid)
    rows, total = await db_client.list_campaign_messages(
        organization_id,
        campaign.id,
        limit=limit,
        offset=(page - 1) * limit,
    )
    return CampaignMessagesResponse(
        items=[
            CampaignMessageItem(
                email=row["to_email"],
                status=row["status"],
                error_message=row["error_message"],
                sent_at=row["sent_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ],
        total=total,
        page=page,
        limit=limit,
    )
