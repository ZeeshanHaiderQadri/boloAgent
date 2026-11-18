import csv
import hashlib
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.leads.dispatch import (
    DispatchError,
    DispatchQuotaError,
    _build_csv,
    add_leads_to_campaign,
    dispatch_leads_to_campaign,
)
from api.services.quota_service import QuotaCheckResult

USER = SimpleNamespace(id=3, selected_organization_id=11)
WORKFLOW = SimpleNamespace(id=7, name="Sales workflow")


def _lead(**overrides):
    values = {
        "id": 1,
        "title": "Apex Software Labs",
        "category": "Software company",
        "address": "123 Market St, Suite 4",
        "city": "San Francisco",
        "state": "CA",
        "country": "US",
        "website": "https://apex.example.com",
        "email": "hello@apex.example.com",
        "rating": 4.6,
        "review_count": 128,
        "crm_status": "new",
        "phone_e164": "+14155550142",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _patched_dispatch(leads, *, campaign_id=99):
    db = MagicMock()
    db.get_workflow = AsyncMock(return_value=WORKFLOW)
    db.get_leads_by_ids = AsyncMock(return_value=leads)
    db.create_campaign = AsyncMock(
        return_value=SimpleNamespace(id=campaign_id, name="Q4 outreach")
    )
    db.create_lead_dispatches = AsyncMock()
    db.add_lead_activities_bulk = AsyncMock()

    storage = MagicMock()
    storage.acreate_file_from_bytes = AsyncMock(return_value=True)

    return (
        patch("api.services.leads.dispatch.db_client", db),
        patch("api.services.leads.dispatch.storage_fs", storage),
        patch(
            "api.services.leads.dispatch.campaign_runner_service.start_campaign",
            new=AsyncMock(),
        ),
        patch(
            "api.services.leads.dispatch.authorize_workflow_run_start",
            new=AsyncMock(return_value=QuotaCheckResult(has_quota=True)),
        ),
        patch(
            "api.services.leads.dispatch.resolve_outbound_configuration_id",
            new=AsyncMock(return_value=5),
        ),
        patch(
            "api.services.leads.dispatch._org_concurrency_limit",
            new=AsyncMock(return_value=10),
        ),
        patch(
            "api.services.leads.dispatch._required_template_variables",
            new=AsyncMock(return_value={"first_name"}),
        ),
        db,
        storage,
    )


async def _dispatch(lead_ids, **overrides):
    values = {
        "user": USER,
        "lead_ids": lead_ids,
        "name": "Q4 outreach",
        "workflow_id": 7,
        "telephony_configuration_id": None,
        "max_concurrency": 3,
        "rate_limit_per_second": 2,
        "start_immediately": True,
    }
    values.update(overrides)
    return await dispatch_leads_to_campaign(**values)


async def test_add_leads_to_existing_campaign_appends_csv_and_queued_runs():
    campaign = SimpleNamespace(
        id=99,
        name="Q4 outreach",
        source_type="csv",
        source_id="campaigns/11/existing.csv",
        state="completed",
        workflow_id=7,
    )
    db = MagicMock()
    db.get_campaign = AsyncMock(return_value=campaign)
    db.get_workflow = AsyncMock(return_value=WORKFLOW)
    db.get_leads_by_ids = AsyncMock(return_value=[_lead()])
    db.list_dispatch_lead_ids = AsyncMock(return_value=[])
    db.create_lead_dispatches = AsyncMock()
    db.bulk_create_queued_runs = AsyncMock()
    db.add_lead_activities_bulk = AsyncMock()
    db.update_campaign = AsyncMock()

    storage = MagicMock()
    storage.acreate_file_from_bytes = AsyncMock(return_value=True)
    publisher = MagicMock()
    publisher.publish_sync_completed = AsyncMock()

    existing = [
        ["phone_number", "lead_id", "business_name", "first_name"],
        ["+14155550000", "40", "Existing Co", "Ada"],
    ]

    with (
        patch("api.services.leads.dispatch.db_client", db),
        patch("api.services.leads.dispatch.storage_fs", storage),
        patch(
            "api.services.leads.dispatch._read_source_csv",
            new=AsyncMock(return_value=existing),
        ),
        patch(
            "api.services.leads.dispatch._required_template_variables",
            new=AsyncMock(return_value={"first_name"}),
        ),
        patch(
            "api.services.leads.dispatch.get_campaign_event_publisher",
            new=AsyncMock(return_value=publisher),
        ),
    ):
        result = await add_leads_to_campaign(
            user=USER,
            organization_id=11,
            lead_ids=[1],
            campaign_id=99,
        )

    assert result.dispatched == 1
    assert result.skipped == []
    file_key, content = storage.acreate_file_from_bytes.await_args.args
    assert file_key == "campaigns/11/existing.csv"
    rows = list(csv.DictReader(io.StringIO(content.decode())))
    assert [row["phone_number"] for row in rows] == [
        "+14155550000",
        "+14155550142",
    ]
    assert rows[1]["first_name"] == ""

    expected_source = (
        f"csv_{hashlib.md5(campaign.source_id.encode()).hexdigest()[:8]}_row_2"
    )
    assert db.create_lead_dispatches.await_args.args[0] == [
        {
            "organization_id": 11,
            "lead_id": 1,
            "campaign_id": 99,
            "source_uuid": expected_source,
        }
    ]
    queued = db.bulk_create_queued_runs.await_args.args[0]
    assert queued[0]["source_uuid"] == expected_source
    assert queued[0]["context_variables"]["phone_number"] == "+14155550142"
    assert db.update_campaign.await_args.kwargs["state"] == "running"
    assert db.update_campaign.await_args.kwargs["total_rows"] == 2
    publisher.publish_sync_completed.assert_awaited_once()


async def test_add_leads_to_existing_campaign_skips_leads_already_queued():
    campaign = SimpleNamespace(
        id=99,
        name="Q4 outreach",
        source_type="csv",
        source_id="campaigns/11/existing.csv",
        state="running",
        workflow_id=7,
    )
    db = MagicMock()
    db.get_campaign = AsyncMock(return_value=campaign)
    db.get_workflow = AsyncMock(return_value=WORKFLOW)
    db.get_leads_by_ids = AsyncMock(return_value=[_lead()])
    db.list_dispatch_lead_ids = AsyncMock(return_value=[1])

    with (
        patch("api.services.leads.dispatch.db_client", db),
        patch("api.services.leads.dispatch.storage_fs", MagicMock()),
        patch(
            "api.services.leads.dispatch._read_source_csv", new=AsyncMock()
        ) as read_csv,
    ):
        with pytest.raises(DispatchError, match="already queued"):
            await add_leads_to_campaign(
                user=USER,
                organization_id=11,
                lead_ids=[1],
                campaign_id=99,
            )

    read_csv.assert_not_awaited()


def test_build_csv_puts_phone_first_and_appends_workflow_variables():
    content, headers = _build_csv([_lead()], {"first_name", "city"})

    assert headers[:3] == ["phone_number", "lead_id", "business_name"]
    assert headers[-1] == "first_name"
    rows = list(csv.DictReader(io.StringIO(content)))
    assert rows[0]["phone_number"] == "+14155550142"
    assert rows[0]["address"] == "123 Market St, Suite 4"
    assert rows[0]["first_name"] == ""


async def test_dispatch_skips_do_not_call_missing_phone_and_missing_leads():
    leads = [
        _lead(),
        _lead(id=2, title="Do Not Call Co", crm_status="do_not_call"),
        _lead(id=3, title="No Phone Co", phone_e164=None),
    ]
    patches = _patched_dispatch(leads)
    *contexts, db, storage = patches

    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4]:
        with contexts[5], contexts[6]:
            result = await _dispatch([1, 2, 3, 404])

    assert result.campaign_id == 99
    assert result.campaign_name == "Q4 outreach"
    assert result.dispatched == 1
    assert [(item["lead_id"], item["reason"]) for item in result.skipped] == [
        (2, "do_not_call"),
        (3, "missing_phone"),
        (404, "not_found"),
    ]
    assert any("3 lead(s) were skipped" in warning for warning in result.warnings)

    file_key, content = storage.acreate_file_from_bytes.await_args.args
    rows = list(csv.DictReader(io.StringIO(content.decode())))
    assert [row["phone_number"] for row in rows] == ["+14155550142"]
    assert "first_name" in rows[0]

    db.create_campaign.assert_awaited_once()
    assert db.create_campaign.await_args.kwargs["source_id"] == file_key
    assert db.create_campaign.await_args.kwargs["source_type"] == "csv"
    assert db.create_campaign.await_args.kwargs["max_concurrency"] == 3

    dispatches = db.create_lead_dispatches.await_args.args[0]
    expected_source = f"csv_{hashlib.md5(file_key.encode()).hexdigest()[:8]}_row_1"
    assert dispatches == [
        {
            "organization_id": 11,
            "lead_id": 1,
            "campaign_id": 99,
            "source_uuid": expected_source,
        }
    ]
    activities = db.add_lead_activities_bulk.await_args.args[0]
    assert activities[0]["activity_type"] == "campaign"
    assert activities[0]["extra_metadata"]["campaign_id"] == 99


async def test_dispatch_without_start_immediately_skips_quota_and_runner():
    patches = _patched_dispatch([_lead()])
    *contexts, db, _storage = patches

    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4]:
        with contexts[5], contexts[6]:
            await _dispatch([1], start_immediately=False)

    contexts[3].new.assert_not_awaited()
    contexts[2].new.assert_not_awaited()
    assert db.create_campaign.await_args.kwargs["retry_config"] is None


async def test_dispatch_rejects_when_nothing_is_dialable():
    patches = _patched_dispatch([_lead(crm_status="do_not_call")])
    *contexts, _db, _storage = patches

    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4]:
        with contexts[5], contexts[6]:
            with pytest.raises(DispatchError):
                await _dispatch([1])


async def test_dispatch_reports_quota_exhaustion_as_quota_error():
    patches = _patched_dispatch([_lead()])
    contexts = patches[:-2]

    with (
        contexts[0],
        contexts[1],
        contexts[2],
        patch(
            "api.services.leads.dispatch.authorize_workflow_run_start",
            new=AsyncMock(
                return_value=QuotaCheckResult(
                    has_quota=False, error_message="Quota exhausted"
                )
            ),
        ),
        contexts[4],
        contexts[5],
        contexts[6],
    ):
        with pytest.raises(DispatchQuotaError, match="Quota exhausted"):
            await _dispatch([1])


async def test_dispatch_rejects_concurrency_above_organization_limit():
    patches = _patched_dispatch([_lead()])
    *contexts, _db, _storage = patches

    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4]:
        with contexts[5], contexts[6]:
            with pytest.raises(DispatchError, match="cannot exceed"):
                await _dispatch([1], max_concurrency=50)


async def test_dispatch_rejects_unknown_workflow():
    patches = _patched_dispatch([_lead()])
    *contexts, db, _storage = patches
    db.get_workflow = AsyncMock(return_value=None)

    with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4]:
        with contexts[5], contexts[6]:
            with pytest.raises(DispatchError, match="Workflow not found"):
                await _dispatch([1])
