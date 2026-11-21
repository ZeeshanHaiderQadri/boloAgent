"""Shared start/pause/resume path for outbound voice campaigns.

HTTP routes and the master agent both go through here so the telephony,
ownership, quota and state checks cannot drift between surfaces.
"""

from __future__ import annotations

from typing import Any

from api.db import db_client
from api.services.campaign.runner import campaign_runner_service
from api.services.campaign.traffic_split import campaign_split, resolve_variants
from api.services.quota_service import authorize_workflow_run_start

TELEPHONY_SETUP_MESSAGE = (
    "You must configure telephony first by going to APP_URL/configure-telephony"
)


class CampaignActionError(Exception):
    """Base for refusals a caller can relay to the user."""


class CampaignNotFoundError(CampaignActionError):
    pass


class TelephonyNotConfiguredError(CampaignActionError):
    pass


class CampaignVariantsError(CampaignActionError):
    pass


class CampaignQuotaExceededError(CampaignActionError):
    pass


class CampaignStateError(CampaignActionError):
    pass


async def load_campaign(*, organization_id: int, campaign_id: int):
    campaign = await db_client.get_campaign(campaign_id, organization_id)
    if campaign is None:
        raise CampaignNotFoundError("Campaign not found")
    return campaign


async def _require_outbound_telephony(organization_id: int) -> None:
    configs = await db_client.list_telephony_configurations(organization_id)
    if not configs:
        raise TelephonyNotConfiguredError(TELEPHONY_SETUP_MESSAGE)


async def _authorize_quota(*, organization_id: int, campaign, actor_user: Any) -> None:
    if (campaign.orchestrator_metadata or {}).get("traffic_split"):
        try:
            resolved = await resolve_variants(
                db_client,
                campaign_split(campaign)["variants"],
                organization_id,
            )
        except ValueError as exc:
            raise CampaignVariantsError(str(exc)) from exc
        selections = [(workflow.id, definition.id) for workflow, definition in resolved]
    else:
        selections = [(campaign.workflow_id, None)]

    for workflow_id, definition_id in selections:
        result = await authorize_workflow_run_start(
            workflow_id=workflow_id,
            organization_id=organization_id,
            actor_user=actor_user,
            **({"definition_id": definition_id} if definition_id is not None else {}),
        )
        if not result.has_quota:
            raise CampaignQuotaExceededError(
                result.error_message or "No call quota is available for this workflow."
            )


async def _transition(
    *, organization_id: int, campaign_id: int, actor_user: Any, action: str
) -> None:
    await _require_outbound_telephony(organization_id)
    campaign = await load_campaign(
        organization_id=organization_id, campaign_id=campaign_id
    )
    await _authorize_quota(
        organization_id=organization_id, campaign=campaign, actor_user=actor_user
    )
    runner_call = getattr(campaign_runner_service, f"{action}_campaign")
    try:
        await runner_call(campaign_id)
    except ValueError as exc:
        raise CampaignStateError(str(exc)) from exc


async def start_campaign(
    *, organization_id: int, campaign_id: int, actor_user: Any
) -> None:
    await _transition(
        organization_id=organization_id,
        campaign_id=campaign_id,
        actor_user=actor_user,
        action="start",
    )


async def resume_campaign(
    *, organization_id: int, campaign_id: int, actor_user: Any
) -> None:
    await _transition(
        organization_id=organization_id,
        campaign_id=campaign_id,
        actor_user=actor_user,
        action="resume",
    )


async def pause_campaign(*, organization_id: int, campaign_id: int) -> None:
    await load_campaign(organization_id=organization_id, campaign_id=campaign_id)
    try:
        await campaign_runner_service.pause_campaign(campaign_id)
    except ValueError as exc:
        raise CampaignStateError(str(exc)) from exc


async def campaign_progress(*, organization_id: int, campaign_id: int) -> dict:
    campaign = await load_campaign(
        organization_id=organization_id, campaign_id=campaign_id
    )
    try:
        progress = await campaign_runner_service.get_campaign_status(campaign_id)
    except ValueError as exc:
        raise CampaignStateError(str(exc)) from exc
    return {
        "campaign_id": campaign.id,
        "name": campaign.name,
        "workflow_id": campaign.workflow_id,
        **progress,
    }
