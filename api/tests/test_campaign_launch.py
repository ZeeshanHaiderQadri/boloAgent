"""Unit tests for the shared voice campaign start/pause/resume service."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.campaign import launch
from api.services.campaign.launch import (
    CampaignNotFoundError,
    CampaignQuotaExceededError,
    CampaignStateError,
    TelephonyNotConfiguredError,
    campaign_progress,
    pause_campaign,
    resume_campaign,
    start_campaign,
)

ACTOR = SimpleNamespace(id=9)


def _runner():
    return SimpleNamespace(
        start_campaign=AsyncMock(),
        pause_campaign=AsyncMock(),
        resume_campaign=AsyncMock(),
        get_campaign_status=AsyncMock(),
    )


def _campaign(**overrides):
    values = {
        "id": 5,
        "name": "Spring cleaning",
        "workflow_id": 3,
        "organization_id": 42,
        "state": "created",
        "orchestrator_metadata": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_start_requires_telephony_configuration():
    with (
        patch.object(launch, "db_client") as db,
        patch.object(launch, "campaign_runner_service", _runner()) as runner,
    ):
        db.list_telephony_configurations = AsyncMock(return_value=[])
        with pytest.raises(TelephonyNotConfiguredError, match="configure telephony"):
            await start_campaign(organization_id=42, campaign_id=5, actor_user=ACTOR)

    runner.start_campaign.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_is_org_scoped_and_checks_quota_for_the_actor():
    campaign = _campaign()
    with (
        patch.object(launch, "db_client") as db,
        patch.object(launch, "campaign_runner_service", _runner()) as runner,
        patch.object(
            launch,
            "authorize_workflow_run_start",
            AsyncMock(return_value=SimpleNamespace(has_quota=True)),
        ) as authorize,
    ):
        db.list_telephony_configurations = AsyncMock(return_value=[object()])
        db.get_campaign = AsyncMock(return_value=campaign)
        await start_campaign(organization_id=42, campaign_id=5, actor_user=ACTOR)

    db.get_campaign.assert_awaited_once_with(5, 42)
    authorize.assert_awaited_once_with(
        workflow_id=3, organization_id=42, actor_user=ACTOR
    )
    runner.start_campaign.assert_awaited_once_with(5)


@pytest.mark.asyncio
async def test_start_rejects_campaign_from_another_organization():
    with (
        patch.object(launch, "db_client") as db,
        patch.object(launch, "campaign_runner_service", _runner()) as runner,
    ):
        db.list_telephony_configurations = AsyncMock(return_value=[object()])
        db.get_campaign = AsyncMock(return_value=None)
        with pytest.raises(CampaignNotFoundError):
            await start_campaign(organization_id=42, campaign_id=5, actor_user=ACTOR)

    runner.start_campaign.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_reports_quota_refusal_message():
    with (
        patch.object(launch, "db_client") as db,
        patch.object(launch, "campaign_runner_service", _runner()) as runner,
        patch.object(
            launch,
            "authorize_workflow_run_start",
            AsyncMock(
                return_value=SimpleNamespace(
                    has_quota=False, error_message="Your plan has no call credits left."
                )
            ),
        ),
    ):
        db.list_telephony_configurations = AsyncMock(return_value=[object()])
        db.get_campaign = AsyncMock(return_value=_campaign())
        with pytest.raises(CampaignQuotaExceededError, match="no call credits"):
            await start_campaign(organization_id=42, campaign_id=5, actor_user=ACTOR)

    runner.start_campaign.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_checks_each_traffic_split_variant():
    campaign = _campaign(orchestrator_metadata={"traffic_split": {"variants": []}})
    resolved = [
        (SimpleNamespace(id=3), SimpleNamespace(id=11)),
        (SimpleNamespace(id=4), SimpleNamespace(id=12)),
    ]
    with (
        patch.object(launch, "db_client") as db,
        patch.object(launch, "campaign_runner_service", _runner()) as runner,
        patch.object(launch, "campaign_split", return_value={"variants": []}),
        patch.object(
            launch, "resolve_variants", AsyncMock(return_value=resolved)
        ) as resolve,
        patch.object(
            launch,
            "authorize_workflow_run_start",
            AsyncMock(return_value=SimpleNamespace(has_quota=True)),
        ) as authorize,
    ):
        db.list_telephony_configurations = AsyncMock(return_value=[object()])
        db.get_campaign = AsyncMock(return_value=campaign)
        await start_campaign(organization_id=42, campaign_id=5, actor_user=ACTOR)

    resolve.assert_awaited_once_with(db, [], 42)
    assert [call.kwargs["definition_id"] for call in authorize.await_args_list] == [
        11,
        12,
    ]
    runner.start_campaign.assert_awaited_once_with(5)


@pytest.mark.asyncio
async def test_start_maps_runner_refusals_to_state_errors():
    with (
        patch.object(launch, "db_client") as db,
        patch.object(launch, "campaign_runner_service", _runner()) as runner,
        patch.object(
            launch,
            "authorize_workflow_run_start",
            AsyncMock(return_value=SimpleNamespace(has_quota=True)),
        ),
    ):
        db.list_telephony_configurations = AsyncMock(return_value=[object()])
        db.get_campaign = AsyncMock(return_value=_campaign())
        runner.start_campaign.side_effect = ValueError(
            "Campaign must be in 'created' state to start"
        )
        with pytest.raises(CampaignStateError, match="created"):
            await start_campaign(organization_id=42, campaign_id=5, actor_user=ACTOR)


@pytest.mark.asyncio
async def test_pause_is_org_scoped_and_needs_an_active_campaign():
    with (
        patch.object(launch, "db_client") as db,
        patch.object(launch, "campaign_runner_service", _runner()) as runner,
    ):
        db.get_campaign = AsyncMock(return_value=_campaign(state="paused"))
        runner.pause_campaign.side_effect = ValueError(
            "Campaign must be in 'running' or 'syncing' state to pause"
        )
        with pytest.raises(CampaignStateError, match="running"):
            await pause_campaign(organization_id=42, campaign_id=5)

    db.get_campaign.assert_awaited_once_with(5, 42)


@pytest.mark.asyncio
async def test_pause_rejects_a_campaign_outside_the_organization():
    with (
        patch.object(launch, "db_client") as db,
        patch.object(launch, "campaign_runner_service", _runner()) as runner,
    ):
        db.get_campaign = AsyncMock(return_value=None)
        with pytest.raises(CampaignNotFoundError):
            await pause_campaign(organization_id=42, campaign_id=5)

    runner.pause_campaign.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_requires_telephony_and_quota():
    with (
        patch.object(launch, "db_client") as db,
        patch.object(launch, "campaign_runner_service", _runner()) as runner,
        patch.object(
            launch,
            "authorize_workflow_run_start",
            AsyncMock(return_value=SimpleNamespace(has_quota=True)),
        ),
    ):
        db.list_telephony_configurations = AsyncMock(return_value=[object()])
        db.get_campaign = AsyncMock(return_value=_campaign(state="paused"))
        await resume_campaign(organization_id=42, campaign_id=5, actor_user=ACTOR)

    runner.resume_campaign.assert_awaited_once_with(5)


@pytest.mark.asyncio
async def test_progress_merges_campaign_name_into_runner_status():
    campaign = _campaign()
    status = {
        "campaign_id": 5,
        "state": "running",
        "total_rows": 100,
        "processed_rows": 40,
        "failed_calls": 2,
        "progress_percentage": 40.0,
        "source_sync": {"status": "completed", "last_synced_at": None, "error": None},
        "rate_limit": 1,
        "started_at": None,
        "completed_at": None,
    }
    with (
        patch.object(launch, "db_client") as db,
        patch.object(launch, "campaign_runner_service", _runner()) as runner,
    ):
        db.get_campaign = AsyncMock(return_value=campaign)
        runner.get_campaign_status.return_value = status
        result = await campaign_progress(organization_id=42, campaign_id=5)

    assert result["name"] == "Spring cleaning"
    assert result["processed_rows"] == 40


@pytest.mark.asyncio
async def test_progress_rejects_campaigns_outside_the_organization():
    with patch.object(launch, "db_client") as db:
        db.get_campaign = AsyncMock(return_value=None)
        with pytest.raises(CampaignNotFoundError):
            await campaign_progress(organization_id=42, campaign_id=5)
