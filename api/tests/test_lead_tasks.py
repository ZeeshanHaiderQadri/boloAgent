"""Worker behaviour: progressive sync, target auto-stop, and Stop & keep."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.leads.providers import LeadProviderError, ProviderJobStatus
from api.services.leads.socials import SocialScan
from api.tasks import lead_tasks

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
ROWS = [
    {"title": "Apex Coffee", "place_id": "p1", "phone": "+14155550100"},
    {"title": "Cedar Bakery", "place_id": "p2", "phone": "+14155550101"},
]


class FakeProvider:
    name = "demo"

    def __init__(self, rows=None, poll_status="running", fail_fetch_after_cancel=False):
        self.rows = rows if rows is not None else ROWS
        self.poll_status = poll_status
        self.fail_fetch_after_cancel = fail_fetch_after_cancel
        self.cancelled = False
        self.events: list[tuple] = []

    async def submit(self, search):
        return "job-1"

    async def poll(self, search):
        return ProviderJobStatus(status=self.poll_status)

    async def fetch_results(self, search, *, final=False):
        if self.fail_fetch_after_cancel and self.cancelled:
            raise LeadProviderError("Scraper job finished without a results file")
        self.events.append(("fetch", final))
        return list(self.rows)

    async def cancel(self, search):
        self.cancelled = True
        self.events.append(("cancel",))


def _search(**overrides):
    values = {
        "id": 5,
        "organization_id": 3,
        "created_by": 7,
        "name": "Coffee in Sacramento",
        "category": "Coffee",
        "location": "Sacramento",
        "country_code": "US",
        "provider": "demo",
        "status": "running",
        "external_job_id": "job-1",
        "params": {},
        "target_results": None,
        "results_count": 0,
        "new_leads_count": 0,
        "auto_call_campaign_id": None,
        "auto_call_error": None,
        "started_at": datetime.now(UTC),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _wire_db(monkeypatch, search, *, statuses=None):
    seen: set[str] = set()
    updates: list[dict] = []
    status_iter = iter(statuses or [])

    async def get_search(_search_id):
        status = next(status_iter, None)
        # Separate DB reads return snapshots, not the worker's mutable object.
        return SimpleNamespace(**{**vars(search), "status": status or search.status})

    async def update_search(_search_id, _organization_id, **values):
        expected_run = values.pop("expected_run", None)
        expected_statuses = values.pop("expected_statuses", None)
        if expected_run is not None and expected_run != (
            search.started_at,
            search.external_job_id,
        ):
            return None
        if expected_statuses is not None and search.status not in expected_statuses:
            return None
        updates.append(values)
        for key, value in values.items():
            setattr(search, key, value)
        return search

    async def upsert(*, search_id, organization_id, leads, expected_run=None):
        if expected_run is not None and (
            expected_run != (search.started_at, search.external_job_id)
            or search.status != "running"
        ):
            return {
                "results_count": 0,
                "new_leads_count": 0,
                "updated_leads_count": 0,
            }
        new = 0
        for lead in leads:
            if lead["dedupe_key"] not in seen:
                seen.add(lead["dedupe_key"])
                new += 1
        search.results_count = len(seen)
        search.new_leads_count += new
        return {
            "results_count": len(leads),
            "new_leads_count": new,
            "updated_leads_count": len(leads) - new,
        }

    async def count_results(_search_id):
        return len(seen)

    monkeypatch.setattr(lead_tasks.db_client, "get_lead_search_unscoped", get_search)
    monkeypatch.setattr(lead_tasks.db_client, "update_lead_search", update_search)
    monkeypatch.setattr(lead_tasks.db_client, "upsert_leads_for_search", upsert)
    monkeypatch.setattr(lead_tasks.db_client, "count_search_results", count_results)
    monkeypatch.setattr(lead_tasks, "LEADS_SCRAPER_POLL_INTERVAL_SECONDS", 0)
    return updates


@pytest.mark.asyncio
async def test_pending_scraper_job_fails_with_actionable_error(monkeypatch):
    provider = FakeProvider(poll_status="pending")
    search = _search(external_job_id=None)
    updates = _wire_db(monkeypatch, search)
    monkeypatch.setattr(lead_tasks, "get_provider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(lead_tasks, "LEADS_SCRAPER_PENDING_TIMEOUT_SECONDS", -1)

    result = await lead_tasks.run_lead_search({}, search.id)

    assert result["status"] == "failed"
    assert updates[-1]["error_message"].startswith(
        "The Google Maps scraper has not started"
    )
    assert provider.cancelled is True


@pytest.mark.asyncio
async def test_search_completes_at_target_and_enqueues_auto_calls(monkeypatch):
    provider = FakeProvider()
    search = _search(target_results=2, params={"auto_call": True, "workflow_id": 9})
    updates = _wire_db(monkeypatch, search)
    redis = SimpleNamespace(enqueue_job=AsyncMock())
    monkeypatch.setattr(lead_tasks, "get_provider", lambda *args, **kwargs: provider)

    result = await lead_tasks.run_lead_search({"redis": redis}, search.id)

    assert result["status"] == "completed"
    assert result["results_count"] == 2
    assert provider.cancelled is True
    assert updates[-1]["status"] == "completed"
    assert search.results_count == 2
    redis.enqueue_job.assert_any_await("run_lead_auto_dispatch", search.id)
    redis.enqueue_job.assert_any_await(
        "enrich_lead_socials", search.id, _job_id=f"lead-socials:{search.id}"
    )


@pytest.mark.asyncio
async def test_large_ingest_releases_search_between_small_batches(monkeypatch):
    rows = [{"title": f"Cafe {i}", "place_id": f"p{i}"} for i in range(121)]
    provider = FakeProvider(rows=rows, poll_status="completed")
    search = _search()
    _wire_db(monkeypatch, search)
    upsert = AsyncMock(wraps=lead_tasks.db_client.upsert_leads_for_search)
    monkeypatch.setattr(lead_tasks.db_client, "upsert_leads_for_search", upsert)
    monkeypatch.setattr(lead_tasks, "get_provider", lambda *args, **kwargs: provider)

    result = await asyncio.wait_for(
        lead_tasks.run_lead_search({}, search.id), timeout=1
    )

    assert result["results_count"] == search.results_count == 121
    assert result["new_leads_count"] == search.new_leads_count == 121
    assert [len(call.kwargs["leads"]) for call in upsert.await_args_list] == [
        50,
        50,
        21,
        50,
        50,
        21,
    ]


@pytest.mark.asyncio
async def test_target_cancel_happens_after_the_final_ingest(monkeypatch):
    # Cancelling a scraper job deletes its CSV. The worker must ingest the
    # final batch before cancelling, or a target-triggered stop loses data.
    provider = FakeProvider(fail_fetch_after_cancel=True)
    search = _search(target_results=2)
    updates = _wire_db(monkeypatch, search)
    monkeypatch.setattr(lead_tasks, "get_provider", lambda *args, **kwargs: provider)

    result = await lead_tasks.run_lead_search({}, search.id)

    assert result["status"] == "completed"
    assert provider.cancelled is True
    assert ("fetch", True) in provider.events
    assert provider.events[-1] == ("cancel",)
    assert updates[-1]["status"] == "completed"
    assert search.results_count == 2


@pytest.mark.asyncio
async def test_stop_request_keeps_leads_found_so_far(monkeypatch):
    provider = FakeProvider()
    search = _search()
    # The API sets "stopping" while the worker is between polls.
    updates = _wire_db(monkeypatch, search, statuses=["running", "running", "stopping"])
    monkeypatch.setattr(lead_tasks, "get_provider", lambda *args, **kwargs: provider)

    result = await lead_tasks.run_lead_search({}, search.id)

    assert result["status"] == "stopped"
    assert result["results_count"] == 2
    assert provider.cancelled is True
    assert updates[-1]["status"] == "stopped"
    assert search.results_count == 2


@pytest.mark.asyncio
async def test_stop_before_the_worker_submits_never_starts_a_job(monkeypatch):
    provider = FakeProvider()
    provider.submit = AsyncMock()  # type: ignore[method-assign]
    search = _search(status="stopping", external_job_id=None)
    updates = _wire_db(monkeypatch, search)
    monkeypatch.setattr(lead_tasks, "get_provider", lambda *args, **kwargs: provider)

    result = await lead_tasks.run_lead_search({}, search.id)

    assert result["status"] == "stopped"
    provider.submit.assert_not_awaited()
    assert updates[-1]["status"] == "stopped"


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_method", ["poll", "fetch_results"])
async def test_search_deadline_interrupts_blocked_provider_io(
    monkeypatch, blocked_method
):
    provider = FakeProvider()
    search = _search()
    updates = _wire_db(monkeypatch, search)
    entered = asyncio.Event()
    interrupted = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            interrupted.set()

    monkeypatch.setattr(provider, blocked_method, blocked)
    provider.cancel = AsyncMock()
    monkeypatch.setattr(lead_tasks, "get_provider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(lead_tasks, "LEADS_SCRAPER_TIMEOUT_SECONDS", 0.05)

    result = await asyncio.wait_for(
        lead_tasks.run_lead_search({}, search.id), timeout=1
    )

    expected_count = 2 if blocked_method == "poll" else 0
    expected_status = "completed" if expected_count else "failed"
    assert entered.is_set() and interrupted.is_set()
    assert result["status"] == search.status == expected_status
    assert result["results_count"] == search.results_count == expected_count
    assert search.new_leads_count == expected_count
    assert updates[-1]["completed_at"] is not None
    assert "time limit" in updates[-1]["error_message"]
    provider.cancel.assert_awaited_once()
    assert provider.cancel.await_args.args[0].external_job_id == "job-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("rows, expected_status", [(ROWS, "stopped"), ([], "failed")])
async def test_external_cancellation_persists_status_before_reraising(
    monkeypatch, rows, expected_status
):
    provider = FakeProvider(rows=rows)
    search = _search()
    updates = _wire_db(monkeypatch, search)
    entered = asyncio.Event()

    async def blocked_poll(_search):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(provider, "poll", blocked_poll)
    provider.cancel = AsyncMock()
    monkeypatch.setattr(lead_tasks, "get_provider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(lead_tasks, "LEADS_SOCIAL_ENRICHMENT_ENABLED", True)
    redis = SimpleNamespace(enqueue_job=AsyncMock())
    task = asyncio.create_task(lead_tasks.run_lead_search({"redis": redis}, search.id))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert search.results_count == len(rows)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert search.status == expected_status
    assert search.results_count == search.new_leads_count == len(rows)
    assert await lead_tasks.db_client.count_search_results(search.id) == len(rows)
    assert updates[-1]["status"] == expected_status
    assert updates[-1]["completed_at"] is not None
    assert "interrupted" in updates[-1]["error_message"]
    provider.cancel.assert_awaited_once()
    assert provider.cancel.await_args.args[0].external_job_id == "job-1"
    redis.enqueue_job.assert_awaited_once_with(
        "enrich_lead_socials", search.id, _job_id=f"lead-socials:{search.id}"
    )


@pytest.mark.asyncio
async def test_stop_during_planning_does_not_submit_a_scraper_job(monkeypatch):
    search = _search(status="queued", provider="google_maps", external_job_id=None)
    _wire_db(monkeypatch, search)
    provider = FakeProvider()
    provider.name = "google_maps"
    provider.submit = AsyncMock()
    monkeypatch.setattr(lead_tasks, "get_provider", lambda *args, **kwargs: provider)

    async def plan(_search):
        search.status = "stopped"
        return SimpleNamespace(keywords=["Coffee in Sacramento"], used_places=[])

    monkeypatch.setattr(lead_tasks, "plan_fanout", plan)
    result = await asyncio.wait_for(
        lead_tasks.run_lead_search({}, search.id), timeout=1
    )

    assert result == {"status": "superseded"}
    assert search.status == "stopped"
    provider.submit.assert_not_awaited()
    assert not provider.cancelled


@pytest.mark.asyncio
async def test_late_submit_after_stop_cancels_captured_job_without_writes(monkeypatch):
    search = _search(status="queued", external_job_id=None)
    updates = _wire_db(monkeypatch, search)
    provider = FakeProvider()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def submit(_search):
        entered.set()
        await release.wait()
        return "late-job"

    monkeypatch.setattr(provider, "submit", submit)
    provider.cancel = AsyncMock()
    provider.fetch_results = AsyncMock()
    provider.poll = AsyncMock()
    monkeypatch.setattr(lead_tasks, "get_provider", lambda *args, **kwargs: provider)
    redis = SimpleNamespace(enqueue_job=AsyncMock())
    task = asyncio.create_task(lead_tasks.run_lead_search({"redis": redis}, search.id))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        search.status = "stopped"
        search.completed_at = datetime.now(UTC)
        stopped = dict(vars(search))
        updates.clear()
        release.set()
        result = await asyncio.wait_for(task, timeout=1)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert result == {"status": "superseded"}
    assert vars(search) == stopped
    assert updates == []
    provider.fetch_results.assert_not_awaited()
    provider.poll.assert_not_awaited()
    provider.cancel.assert_awaited_once()
    assert provider.cancel.await_args.args[0].external_job_id == "late-job"
    redis.enqueue_job.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("successor", ["stopped", "new_started_at", "new_job_id"])
async def test_inflight_worker_cannot_overwrite_stop_or_new_attempt(
    monkeypatch, successor
):
    search = _search()
    original_run = (search.started_at, search.external_job_id)
    updates = _wire_db(monkeypatch, search)
    upsert = AsyncMock(wraps=lead_tasks.db_client.upsert_leads_for_search)
    monkeypatch.setattr(lead_tasks.db_client, "upsert_leads_for_search", upsert)
    provider = FakeProvider()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def poll(_search):
        entered.set()
        await release.wait()
        return ProviderJobStatus(status="completed")

    monkeypatch.setattr(provider, "poll", poll)
    provider.cancel = AsyncMock()
    monkeypatch.setattr(lead_tasks, "get_provider", lambda *args, **kwargs: provider)
    redis = SimpleNamespace(enqueue_job=AsyncMock())
    task = asyncio.create_task(lead_tasks.run_lead_search({"redis": redis}, search.id))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert search.results_count == 2
        if successor == "stopped":
            search.status = "stopped"
            search.completed_at = datetime.now(UTC)
        elif successor == "new_started_at":
            search.started_at += timedelta(seconds=1)
        else:
            search.external_job_id = "new-job"
        newer = dict(vars(search))
        updates.clear()
        provider.rows = [*ROWS, {"title": "Late Cafe", "place_id": "p3"}]
        release.set()
        result = await asyncio.wait_for(task, timeout=1)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert result == {"status": "superseded"}
    assert vars(search) == newer
    assert updates == []
    assert upsert.await_count == 2
    assert all(
        call.kwargs["expected_run"] == original_run for call in upsert.await_args_list
    )
    assert await lead_tasks.db_client.count_search_results(search.id) == 2
    assert ("fetch", True) in provider.events
    provider.cancel.assert_awaited_once()
    assert provider.cancel.await_args.args[0].external_job_id == "job-1"
    redis.enqueue_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_website_filter_keeps_only_matching_leads(monkeypatch):
    rows = [
        {
            "title": "Apex Coffee",
            "place_id": "p1",
            "website": "https://apex.example.com",
        },
        {"title": "Cedar Bakery", "place_id": "p2", "website": ""},
    ]
    provider = FakeProvider(rows=rows)
    search = _search(target_results=1, params={"website_filter": "no_website"})
    _wire_db(monkeypatch, search)

    stored: set[str] = set()

    async def upsert(*, search_id, organization_id, leads, expected_run=None):
        stored.update(lead["title"] for lead in leads)
        return {
            "results_count": len(stored),
            "new_leads_count": len(leads),
            "updated_leads_count": 0,
        }

    async def count_results(_search_id):
        return len(stored)

    monkeypatch.setattr(lead_tasks.db_client, "upsert_leads_for_search", upsert)
    monkeypatch.setattr(lead_tasks.db_client, "count_search_results", count_results)
    monkeypatch.setattr(lead_tasks, "get_provider", lambda *args, **kwargs: provider)

    result = await lead_tasks.run_lead_search({}, search.id)

    assert result["status"] == "completed"
    assert stored == {"Cedar Bakery"}


@pytest.mark.asyncio
async def test_auto_dispatch_queues_dialable_leads_and_records_campaign(
    monkeypatch,
):
    from api.services.leads import dispatch as dispatch_module

    search = _search(
        status="completed",
        params={"auto_call": True, "workflow_id": 9, "telephony_configuration_id": 4},
    )
    updates = _wire_db(monkeypatch, search)
    leads = [
        SimpleNamespace(id=101, crm_status="new"),
        SimpleNamespace(id=102, crm_status="do_not_call"),
        SimpleNamespace(id=103, crm_status="contacted"),
    ]

    async def list_leads(*args, **kwargs):
        assert kwargs["has_phone"] is True
        assert kwargs["search_id"] == search.id
        return leads, len(leads)

    async def get_user(user_id):
        return SimpleNamespace(
            id=user_id, selected_organization_id=search.organization_id
        )

    dispatch_calls = []

    async def dispatch(**kwargs):
        dispatch_calls.append(kwargs)
        return SimpleNamespace(campaign_id=55, dispatched=2)

    monkeypatch.setattr(
        lead_tasks.db_client, "get_lead_search_unscoped", AsyncMock(return_value=search)
    )
    monkeypatch.setattr(lead_tasks.db_client, "get_user_by_id", get_user)
    monkeypatch.setattr(lead_tasks.db_client, "list_leads", list_leads)
    monkeypatch.setattr(dispatch_module, "dispatch_leads_to_campaign", dispatch)

    result = await lead_tasks.run_lead_auto_dispatch({}, search.id)

    assert result == {"status": "completed", "campaign_id": 55, "dispatched": 2}
    assert dispatch_calls[0]["lead_ids"] == [101, 103]
    assert dispatch_calls[0]["organization_id"] == search.organization_id
    assert dispatch_calls[0]["workflow_id"] == 9
    assert dispatch_calls[0]["telephony_configuration_id"] == 4
    assert dispatch_calls[0]["start_immediately"] is True
    assert updates[-1]["auto_call_campaign_id"] == 55


@pytest.mark.asyncio
async def test_auto_dispatch_explains_when_no_numbers_were_found(monkeypatch):
    search = _search(status="completed", params={"auto_call": True, "workflow_id": 9})
    updates = _wire_db(monkeypatch, search)

    async def get_user(user_id):
        return SimpleNamespace(
            id=user_id, selected_organization_id=search.organization_id
        )

    async def list_leads(*args, **kwargs):
        return [SimpleNamespace(id=1, crm_status="do_not_call")], 1

    monkeypatch.setattr(
        lead_tasks.db_client, "get_lead_search_unscoped", AsyncMock(return_value=search)
    )
    monkeypatch.setattr(lead_tasks.db_client, "get_user_by_id", get_user)
    monkeypatch.setattr(lead_tasks.db_client, "list_leads", list_leads)

    result = await lead_tasks.run_lead_auto_dispatch({}, search.id)

    assert result["status"] == "skipped"
    assert "No dialable phone numbers" in updates[-1]["auto_call_error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, results_count, expected_status",
    [
        ("queued", 0, "failed"),
        ("running", 0, "failed"),
        ("running", 2, "stopped"),
        ("stopping", 0, "stopped"),
    ],
)
async def test_sweep_recovers_stale_search_and_enqueues_due_socials(
    monkeypatch, status, results_count, expected_status
):
    search = _search(
        status=status,
        started_at=datetime.now(UTC) - timedelta(hours=1),
        results_count=results_count,
        new_leads_count=results_count,
    )
    original_run = (search.started_at, search.external_job_id)
    _wire_db(monkeypatch, search)
    update = AsyncMock(wraps=lead_tasks.db_client.update_lead_search)
    recovery = AsyncMock(return_value=[SimpleNamespace(**vars(search))])
    due_socials = AsyncMock(return_value=[SimpleNamespace(id=99)])
    monkeypatch.setattr(lead_tasks.db_client, "update_lead_search", update)
    monkeypatch.setattr(
        lead_tasks.db_client, "list_lead_searches_needing_recovery", recovery
    )
    monkeypatch.setattr(
        lead_tasks.db_client, "list_searches_needing_socials", due_socials
    )
    monkeypatch.setattr(lead_tasks, "LEADS_SCRAPER_TIMEOUT_SECONDS", 60)
    monkeypatch.setattr(lead_tasks, "LEADS_SOCIAL_ENRICHMENT_ENABLED", True)
    redis = SimpleNamespace(enqueue_job=AsyncMock())
    started = datetime.now(UTC)

    await lead_tasks.sweep_lead_searches({"redis": redis})

    recovery.assert_awaited_once()
    cutoff = recovery.await_args.args[0]
    grace = timedelta(seconds=60 + 120)
    assert started - grace <= cutoff <= datetime.now(UTC) - grace
    update.assert_awaited_once()
    assert update.await_args.args == (search.id, search.organization_id)
    assert update.await_args.kwargs["expected_run"] == original_run
    assert update.await_args.kwargs["expected_statuses"] == {status}
    assert search.status == expected_status
    assert search.results_count == search.new_leads_count == results_count
    assert search.completed_at >= started
    assert "interrupted" in search.error_message
    due_socials.assert_awaited_once_with()
    assert redis.enqueue_job.await_count == 2
    redis.enqueue_job.assert_any_await("cancel_lead_scraper", search.provider, "job-1")
    redis.enqueue_job.assert_any_await(
        "enrich_lead_socials", 99, _job_id="lead-socials:99"
    )


@pytest.mark.asyncio
async def test_sweep_lost_fence_keeps_new_attempt_and_still_queues_due_socials(
    monkeypatch,
):
    search = _search(started_at=datetime.now(UTC) - timedelta(hours=1))
    stale = SimpleNamespace(**vars(search))
    updates = _wire_db(monkeypatch, search)
    search.started_at = datetime.now(UTC)
    search.external_job_id = "new-job"
    newer = dict(vars(search))
    monkeypatch.setattr(
        lead_tasks.db_client,
        "list_lead_searches_needing_recovery",
        AsyncMock(return_value=[stale]),
    )
    monkeypatch.setattr(
        lead_tasks.db_client,
        "list_searches_needing_socials",
        AsyncMock(return_value=[SimpleNamespace(id=99)]),
    )
    monkeypatch.setattr(lead_tasks, "LEADS_SOCIAL_ENRICHMENT_ENABLED", True)
    redis = SimpleNamespace(enqueue_job=AsyncMock())

    await lead_tasks.sweep_lead_searches({"redis": redis})

    assert updates == []
    assert vars(search) == newer
    redis.enqueue_job.assert_awaited_once_with(
        "enrich_lead_socials", 99, _job_id="lead-socials:99"
    )


class FakeRedis:
    """The three Redis commands the enrichment job uses."""

    def __init__(self, lease: int = 1):
        self.lease = lease
        self.enqueued: list[tuple[tuple, dict]] = []
        self.released: list[tuple[str, str]] = []

    async def eval(self, *args):
        return self.lease

    async def zrem(self, key, token):
        self.released.append((key, token))
        return 1

    async def enqueue_job(self, *args, **kwargs):
        self.enqueued.append((args, kwargs))
        return SimpleNamespace(job_id="job")


def _lead(lead_id: int, url: str, title: str = "Apex Labs", **values):
    data = {
        "id": lead_id,
        "website": url,
        "title": title,
        "socials_attempts": 0,
    }
    data.update(values)
    return SimpleNamespace(**data)


def _wire_social_db(monkeypatch, search, leads, *, remaining=0):
    saved: list[tuple[int, dict]] = []
    marked: list[int] = []
    failures: list[dict] = []

    async def get_search(_search_id):
        return search

    async def list_pending(_organization_id, *, search_id=None, limit=25):
        return list(leads)

    async def count_pending(_organization_id, *, search_id=None):
        return remaining

    async def save(lead_id, _organization_id, *, social_links, checked_at):
        saved.append((lead_id, social_links))

    async def mark(lead_id, _organization_id, *, checked_at):
        marked.append(lead_id)

    async def record(
        lead_id, _organization_id, *, next_attempt_at=None, terminal_at=None
    ):
        failures.append(
            {
                "lead_id": lead_id,
                "next_attempt_at": next_attempt_at,
                "terminal_at": terminal_at,
            }
        )

    monkeypatch.setattr(lead_tasks.db_client, "get_lead_search_unscoped", get_search)
    monkeypatch.setattr(
        lead_tasks.db_client, "list_leads_needing_socials", list_pending
    )
    monkeypatch.setattr(
        lead_tasks.db_client, "count_leads_needing_socials", count_pending
    )
    monkeypatch.setattr(lead_tasks.db_client, "save_lead_socials", save)
    monkeypatch.setattr(lead_tasks.db_client, "mark_lead_socials_checked", mark)
    monkeypatch.setattr(lead_tasks.db_client, "record_lead_socials_failure", record)
    return saved, marked, failures


@pytest.mark.asyncio
async def test_social_enrichment_scans_pending_leads_and_chains_batches(monkeypatch):
    search = _search(provider="google_maps", status="completed")
    pending = [
        _lead(1, "https://apex.example", "Apex Labs"),
        _lead(2, "https://cedar.example", "Cedar Works"),
    ]
    saved, marked, failures = _wire_social_db(monkeypatch, search, pending, remaining=3)

    async def discover(url, *, business_name=None):
        if "apex" in url:
            return SocialScan(
                {"instagram": "https://www.instagram.com/apex/"},
                checked=True,
                replace_links=True,
            )
        return SocialScan({}, checked=True, replace_links=True)

    monkeypatch.setattr(lead_tasks, "discover_social_links", discover)
    redis = FakeRedis()

    result = await lead_tasks.enrich_lead_socials({"redis": redis}, search.id)

    assert result == {"status": "completed", "scanned": 2, "found": 1, "remaining": 3}
    assert saved == [
        (1, {"instagram": "https://www.instagram.com/apex/"}),
        (2, {}),
    ]
    assert marked == failures == []
    assert redis.enqueued == [
        (
            ("enrich_lead_socials", search.id, 1),
            {"_defer_by": timedelta(seconds=5)},
        )
    ]
    assert len(redis.released) == 1


@pytest.mark.asyncio
async def test_social_enrichment_keeps_good_links_when_the_scan_fails(monkeypatch):
    search = _search(provider="google_maps", status="completed")
    lead = _lead(1, "https://apex.example")
    saved, marked, failures = _wire_social_db(monkeypatch, search, [lead])

    async def discover(url, *, business_name=None):
        return SocialScan({}, checked=False, replace_links=False, error="http 503")

    monkeypatch.setattr(lead_tasks, "discover_social_links", discover)

    started = datetime.now(UTC)
    result = await lead_tasks.enrich_lead_socials({"redis": FakeRedis()}, search.id)

    assert result["scanned"] == 1
    assert saved == [] and marked == []
    assert failures[0]["lead_id"] == 1
    assert failures[0]["terminal_at"] is None
    assert failures[0]["next_attempt_at"] >= started + timedelta(seconds=3599)


@pytest.mark.asyncio
async def test_social_scan_exception_records_backoff_without_replacing_links(
    monkeypatch,
):
    search = _search(provider="google_maps", status="completed")
    lead = _lead(1, "https://apex.example", socials_attempts=1)
    saved, marked, failures = _wire_social_db(monkeypatch, search, [lead])
    monkeypatch.setattr(lead_tasks, "LEADS_SOCIAL_ENRICHMENT_ENABLED", True)
    discover = AsyncMock(side_effect=RuntimeError("unexpected page failure"))
    monkeypatch.setattr(lead_tasks, "discover_social_links", discover)
    redis = FakeRedis()
    started = datetime.now(UTC)

    result = await lead_tasks.enrich_lead_socials({"redis": redis}, search.id)

    assert result == {"status": "completed", "scanned": 1, "found": 0, "remaining": 0}
    discover.assert_awaited_once_with("https://apex.example", business_name=lead.title)
    assert saved == marked == []
    assert len(failures) == 1
    assert failures[0]["lead_id"] == lead.id
    assert failures[0]["terminal_at"] is None
    assert (
        started + timedelta(hours=2)
        <= failures[0]["next_attempt_at"]
        <= datetime.now(UTC) + timedelta(hours=2)
    )
    assert redis.enqueued == []
    assert len(redis.released) == 1


@pytest.mark.asyncio
async def test_social_enrichment_backs_off_exponentially(monkeypatch):
    search = _search(provider="google_maps", status="completed")
    lead = _lead(1, "https://apex.example", socials_attempts=2)
    _saved, _marked, failures = _wire_social_db(monkeypatch, search, [lead])

    async def discover(url, *, business_name=None):
        return SocialScan({}, checked=False, replace_links=False, error="dns")

    monkeypatch.setattr(lead_tasks, "discover_social_links", discover)

    started = datetime.now(UTC)
    await lead_tasks.enrich_lead_socials({"redis": FakeRedis()}, search.id)

    # Third failure -> 4 hours (1h, 2h, 4h, 8h, then stop).
    delay = failures[0]["next_attempt_at"] - started
    assert timedelta(hours=3, minutes=59) <= delay <= timedelta(hours=4, minutes=1)


@pytest.mark.asyncio
async def test_social_enrichment_stops_retrying_after_five_failures(monkeypatch):
    search = _search(provider="google_maps", status="completed")
    lead = _lead(1, "https://apex.example", socials_attempts=4)
    saved, _marked, failures = _wire_social_db(monkeypatch, search, [lead])

    async def discover(url, *, business_name=None):
        return SocialScan({}, checked=False, replace_links=False, error="tls")

    monkeypatch.setattr(lead_tasks, "discover_social_links", discover)

    await lead_tasks.enrich_lead_socials({"redis": FakeRedis()}, search.id)

    assert saved == []
    assert failures[0]["next_attempt_at"] is None
    assert failures[0]["terminal_at"] is not None


@pytest.mark.asyncio
async def test_social_enrichment_marks_checked_pages_without_findings(monkeypatch):
    search = _search(provider="google_maps", status="completed")
    lead = _lead(1, "https://apex.example")
    saved, marked, failures = _wire_social_db(monkeypatch, search, [lead])

    async def discover(url, *, business_name=None):
        return SocialScan({}, checked=True, replace_links=False)

    monkeypatch.setattr(lead_tasks, "discover_social_links", discover)

    await lead_tasks.enrich_lead_socials({"redis": FakeRedis()}, search.id)

    assert marked == [1]
    assert saved == [] and failures == []


@pytest.mark.asyncio
async def test_social_enrichment_scans_duplicate_websites_once(monkeypatch):
    search = _search(provider="google_maps", status="completed")
    leads = [
        _lead(1, "https://apex.example", "Apex"),
        _lead(2, "https://apex.example/", "Apex"),
    ]
    saved, _marked, _failures = _wire_social_db(monkeypatch, search, leads)
    calls: list[str] = []

    async def discover(url, *, business_name=None):
        calls.append(url)
        return SocialScan(
            {"instagram": "https://www.instagram.com/apex/"},
            checked=True,
            replace_links=True,
        )

    monkeypatch.setattr(lead_tasks, "discover_social_links", discover)

    result = await lead_tasks.enrich_lead_socials({"redis": FakeRedis()}, search.id)

    assert calls == ["https://apex.example"]
    assert result["scanned"] == 2 and result["found"] == 2
    assert {lead_id for lead_id, _links in saved} == {1, 2}


@pytest.mark.asyncio
async def test_social_enrichment_serialises_scans_for_the_same_host(monkeypatch):
    search = _search(provider="google_maps", status="completed")
    leads = [
        _lead(1, "https://apex.example/one", "Apex"),
        _lead(2, "https://apex.example/two", "Apex two"),
    ]
    _wire_social_db(monkeypatch, search, leads)
    active = 0
    peak = 0

    async def discover(url, *, business_name=None):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return SocialScan({}, checked=True, replace_links=True)

    monkeypatch.setattr(lead_tasks, "discover_social_links", discover)

    await lead_tasks.enrich_lead_socials({"redis": FakeRedis()}, search.id)

    assert peak == 1


@pytest.mark.asyncio
async def test_social_enrichment_defers_when_another_job_holds_the_lease(
    monkeypatch,
):
    search = _search(provider="google_maps", status="completed")
    _wire_social_db(monkeypatch, search, [_lead(1, "https://apex.example")])
    scan = AsyncMock()
    monkeypatch.setattr(lead_tasks, "discover_social_links", scan)
    redis = FakeRedis(lease=0)

    result = await lead_tasks.enrich_lead_socials({"redis": redis}, search.id)

    assert result == {"status": "deferred"}
    scan.assert_not_awaited()
    assert redis.enqueued == []


@pytest.mark.asyncio
async def test_social_enrichment_can_be_turned_off(monkeypatch):
    search = _search(provider="google_maps", status="completed")
    _wire_social_db(monkeypatch, search, [_lead(1, "https://apex.example")])
    monkeypatch.setattr(lead_tasks, "LEADS_SOCIAL_ENRICHMENT_ENABLED", False)
    scan = AsyncMock()
    monkeypatch.setattr(lead_tasks, "discover_social_links", scan)

    result = await lead_tasks.enrich_lead_socials({"redis": FakeRedis()}, search.id)

    assert result == {"status": "disabled"}
    scan.assert_not_awaited()


@pytest.mark.asyncio
async def test_social_enrichment_respects_the_batch_time_budget(monkeypatch):
    search = _search(provider="google_maps", status="completed")
    _wire_social_db(
        monkeypatch,
        search,
        [_lead(1, "https://apex.example")],
        remaining=7,
    )
    monkeypatch.setattr(lead_tasks, "SOCIAL_ENRICH_BATCH_BUDGET_SECONDS", -1)
    scan = AsyncMock()
    monkeypatch.setattr(lead_tasks, "discover_social_links", scan)
    redis = FakeRedis()

    result = await lead_tasks.enrich_lead_socials({"redis": redis}, search.id)

    assert result["scanned"] == 0 and result["remaining"] == 7
    scan.assert_not_awaited()
    # The next short job picks up what the budget did not reach.
    assert redis.enqueued[0][0][2] == 1


@pytest.mark.asyncio
async def test_social_batch_deadline_interrupts_hung_scan_and_chains_pending(
    monkeypatch,
):
    search = _search(provider="google_maps", status="completed")
    leads = [_lead(1, "https://apex.example"), _lead(2, "https://hung.example")]
    saved, marked, failures = _wire_social_db(monkeypatch, search, leads, remaining=1)
    monkeypatch.setattr(lead_tasks, "LEADS_SOCIAL_ENRICHMENT_ENABLED", True)
    monkeypatch.setattr(lead_tasks, "SOCIAL_ENRICH_BATCH_BUDGET_SECONDS", 0.05)
    entered = asyncio.Event()
    interrupted = asyncio.Event()
    links = {"instagram": "https://www.instagram.com/apex/"}

    async def discover(url, *, business_name=None):
        if url == "https://apex.example":
            return SocialScan(links, checked=True, replace_links=True)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            interrupted.set()

    monkeypatch.setattr(lead_tasks, "discover_social_links", discover)
    redis = FakeRedis()

    result = await asyncio.wait_for(
        lead_tasks.enrich_lead_socials({"redis": redis}, search.id, run=2), timeout=1
    )

    assert entered.is_set() and interrupted.is_set()
    assert result == {"status": "completed", "scanned": 1, "found": 1, "remaining": 1}
    assert saved == [(1, links)]
    # Deadline cancellation leaves the unfinished lead pending, not backed off.
    assert marked == failures == []
    assert redis.enqueued == [
        (("enrich_lead_socials", search.id, 3), {"_defer_by": timedelta(seconds=5)})
    ]
    assert len(redis.released) == 1


@pytest.mark.asyncio
async def test_social_enrichment_skips_generated_demo_websites(monkeypatch):
    search = _search(provider="demo", status="completed")

    async def get_search(_search_id):
        return search

    monkeypatch.setattr(lead_tasks.db_client, "get_lead_search_unscoped", get_search)
    scan = AsyncMock()
    monkeypatch.setattr(lead_tasks.db_client, "list_leads_needing_socials", scan)

    result = await lead_tasks.enrich_lead_socials({}, search.id)

    assert result == {"status": "skipped", "reason": "demo provider"}
    scan.assert_not_called()
