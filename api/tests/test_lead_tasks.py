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
        "started_at": NOW,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _wire_db(monkeypatch, search, *, statuses=None):
    seen: set[str] = set()
    updates: list[dict] = []
    status_iter = iter(statuses or [])

    async def get_search(_search_id):
        status = next(status_iter, None)
        if status is None:
            return search
        return SimpleNamespace(**{**vars(search), "status": status})

    async def update_search(_search_id, _organization_id, **values):
        updates.append(values)
        return search

    async def upsert(*, search_id, organization_id, leads):
        new = 0
        for lead in leads:
            if lead["dedupe_key"] not in seen:
                seen.add(lead["dedupe_key"])
                new += 1
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
    assert updates[-1]["results_count"] == 2
    redis.enqueue_job.assert_any_await("run_lead_auto_dispatch", search.id)
    redis.enqueue_job.assert_any_await("enrich_lead_socials", search.id)


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
    assert updates[-1]["results_count"] == 2


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
    assert updates[-1]["results_count"] == 2


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

    async def upsert(*, search_id, organization_id, leads):
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
    assert redis.enqueued == [
        (
            ("enrich_lead_socials", search.id, 0),
            {"_defer_by": timedelta(seconds=30)},
        )
    ]


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
