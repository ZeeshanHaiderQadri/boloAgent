"""Draft email campaigns: audience preview and CRUD.

Nothing here sends email or creates recipient rows. Launch is a separate
endpoint, and in this release it is disabled by default and sends nothing.

Every route is organization-scoped: a campaign, sender, template, project or
lead id belonging to another organization is indistinguishable from a missing
one.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
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
)
from api.routes.email import _clean_text, _require_organization
from api.schemas.leads import LeadProjectFilters
from api.services.auth.depends import get_user
from api.services.call_concurrency.rate_limiter import RateLimiterUnavailable
from api.services.email.audience import (
    AudienceError,
    AudienceRule,
    resolve_audience,
    validate_audience_rule,
)
from api.services.email.campaign_launch import check_launch_preconditions
from api.services.email.campaign_rate_limits import (
    allow_campaign_launch_attempt,
    allow_campaign_preview,
)
from api.services.email.campaign_state import (
    CampaignStateError,
    assert_campaign_deletable,
)
from api.services.email.limits import effective_limits

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


class LaunchResponse(BaseModel):
    # The campaign's current status; launch never changes it in this release.
    status: str
    ready: bool
    # Plain-language reasons launch is blocked; empty when ready is true.
    preconditions: list[str]
    message: str


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
    )


def _stored_audience(rule: AudienceRule) -> dict[str, Any]:
    if rule.audience_type == EmailCampaignAudienceType.LEADS.value:
        return {"lead_ids": list(rule.lead_ids)}
    if rule.audience_type == EmailCampaignAudienceType.PROJECT.value:
        return {"project_id": rule.project_id}
    if rule.audience_type == EmailCampaignAudienceType.TAG.value:
        return {"tag": rule.tag}
    return {"filters": dict(rule.filters), "all_matching": True}


def _rule_from_stored(campaign: EmailCampaignModel) -> AudienceRule:
    """Rebuild the resolver rule from what the campaign row stored."""
    audience = campaign.audience or {}
    if campaign.audience_type == EmailCampaignAudienceType.LEADS.value:
        return AudienceRule(
            audience_type=campaign.audience_type,
            lead_ids=tuple(audience.get("lead_ids") or ()),
        )
    if campaign.audience_type == EmailCampaignAudienceType.PROJECT.value:
        return AudienceRule(
            audience_type=campaign.audience_type,
            project_id=audience.get("project_id"),
        )
    if campaign.audience_type == EmailCampaignAudienceType.TAG.value:
        return AudienceRule(
            audience_type=campaign.audience_type, tag=audience.get("tag")
        )
    return AudienceRule(
        audience_type=campaign.audience_type,
        filters=dict(audience.get("filters") or {}),
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
    return CampaignCountsResponse(
        recipient_count=campaign.recipient_count,
        eligible_count=campaign.eligible_count,
        excluded_no_email_count=campaign.excluded_no_email_count,
        excluded_invalid_email_count=campaign.excluded_invalid_email_count,
        excluded_suppressed_count=campaign.excluded_suppressed_count,
        excluded_opted_out_count=campaign.excluded_opted_out_count,
        excluded_unknown_count=campaign.excluded_unknown_count,
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
        policy, reason, warning = _consent_values(
            request.consent_policy, request.consent_override_reason
        )
        if request.consent_policy == EmailCampaignConsentPolicy.INCLUDE_UNKNOWN:
            # Reuse the stored reason when the operator only re-asserts include.
            reason = reason or (campaign.consent_override_reason or "").strip()
            if not reason:
                raise HTTPException(
                    status_code=422,
                    detail="Including unknown-consent leads requires a written reason.",
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
    """Check launch preconditions; never send or queue anything in this release.

    ``EMAIL_CAMPAIGN_SENDING_ENABLED`` is off by default and the endpoint
    answers 403. Even when it is on, this release only reports readiness: it
    creates no recipient rows and sends no email.
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

    if readiness.ready:
        message = (
            "All launch preconditions pass. Sending is not part of this "
            "release, so no email was sent and nothing was queued."
        )
    else:
        message = (
            "Launch is blocked. Fix the listed items, preview again, then "
            "retry. No email was sent and nothing was queued."
        )
    return LaunchResponse(
        status=campaign.status,
        ready=readiness.ready,
        preconditions=list(readiness.failures),
        message=message,
    )
