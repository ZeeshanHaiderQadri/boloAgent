"""The single launch path for an email campaign.

The HTTP route and Bolo Agent both call ``launch_campaign`` so the sending
switch, rate limit, precondition checks and freeze step cannot drift. Launch
freezes the audience into ``email_messages`` rows and queues the send worker;
the caller is responsible for getting an explicit human approval first.

Failures are typed so each caller can map them to its own surface (HTTP status
or a tool error) without re-implementing the checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from api import constants
from api.db import db_client
from api.db.models import EmailCampaignModel
from api.enums import EmailCampaignAudienceType, EmailCampaignStatus
from api.services.call_concurrency.rate_limiter import RateLimiterUnavailable
from api.services.email.audience import (
    AudienceError,
    AudienceRule,
    resolve_audience,
)
from api.services.email.campaign_dispatch import freeze_and_queue_campaign
from api.services.email.campaign_launch import check_launch_preconditions
from api.services.email.campaign_rate_limits import allow_campaign_launch_attempt
from api.services.email.campaign_state import (
    CampaignStateError,
    assert_campaign_transition,
)
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames

# Only draft and ready campaigns can be launched.
LAUNCHABLE_STATUSES = frozenset(
    {
        EmailCampaignStatus.DRAFT.value,
        EmailCampaignStatus.READY.value,
    }
)


class EmailLaunchError(Exception):
    """Base class for launch refusals; the message is safe to show a user."""


class SendingDisabledError(EmailLaunchError):
    """``EMAIL_CAMPAIGN_SENDING_ENABLED`` is off; nothing ran."""


class LaunchRateLimitedError(EmailLaunchError):
    pass


class LaunchRateLimiterUnavailableError(EmailLaunchError):
    pass


class CampaignNotFoundError(EmailLaunchError):
    pass


class CampaignNotLaunchableError(EmailLaunchError):
    pass


class LaunchAudienceError(EmailLaunchError):
    pass


class LaunchFailedError(EmailLaunchError):
    pass


class NoEligibleRecipientsError(EmailLaunchError):
    pass


class SendQueueUnavailableError(EmailLaunchError):
    pass


@dataclass(frozen=True)
class CampaignLaunchResult:
    status: str
    ready: bool
    preconditions: list[str] = field(default_factory=list)
    message: str = ""
    queued_count: int = 0


def rule_from_stored(campaign: EmailCampaignModel) -> AudienceRule:
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


async def launch_campaign(
    *, organization_id: int, user_id: int, campaign_uuid: str
) -> CampaignLaunchResult:
    """Freeze the campaign audience and hand it to the send worker.

    A campaign that fails preconditions is returned with ``ready=False`` and
    every failure listed; nothing is written. ``EMAIL_CAMPAIGN_SENDING_ENABLED``
    is checked first and the attempt is rate limited per org and per user.
    """
    if not constants.EMAIL_CAMPAIGN_SENDING_ENABLED:
        raise SendingDisabledError(
            "Campaign sending is disabled on this deployment. "
            "EMAIL_CAMPAIGN_SENDING_ENABLED is off; no email was sent and "
            "nothing was queued."
        )

    try:
        allowed = await allow_campaign_launch_attempt(
            user_id=user_id, organization_id=organization_id
        )
    except RateLimiterUnavailable as exc:
        raise LaunchRateLimiterUnavailableError(
            "Campaign launch is temporarily unavailable."
        ) from exc
    if not allowed:
        raise LaunchRateLimitedError("Too many launch attempts. Try again later.")

    campaign = await db_client.get_email_campaign(organization_id, campaign_uuid)
    if campaign is None:
        raise CampaignNotFoundError("Email campaign not found")
    if campaign.status not in LAUNCHABLE_STATUSES:
        raise CampaignNotLaunchableError(
            f"A campaign in {campaign.status!r} can no longer be launched; "
            "only draft or ready campaigns can be launched."
        )

    rule = rule_from_stored(campaign)
    try:
        resolution = await resolve_audience(
            organization_id, rule, consent_policy=campaign.consent_policy
        )
    except AudienceError as exc:
        raise LaunchAudienceError(str(exc)) from exc

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
        return CampaignLaunchResult(
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
        raise CampaignNotLaunchableError(str(exc)) from exc

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
            user_id=user_id,
        )
    except Exception as exc:
        reason = f"Could not prepare the campaign for sending: {exc}"
        logger.exception("Campaign {} freeze failed", campaign.id)
        await _mark_failed(reason)
        raise LaunchFailedError(reason) from exc

    if queued_count == 0:
        reason = "No eligible recipients were left when the audience was frozen."
        await _mark_failed(reason)
        raise NoEligibleRecipientsError(reason)

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
        raise SendQueueUnavailableError(reason) from exc

    return CampaignLaunchResult(
        status=EmailCampaignStatus.SENDING.value,
        ready=True,
        preconditions=[],
        message=(
            f"Campaign launched: {queued_count} recipient(s) queued for delivery."
        ),
        queued_count=queued_count,
    )
