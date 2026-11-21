from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.leads import router
from api.services.auth.depends import get_user
from api.services.leads.dispatch import DispatchQuotaError
from api.services.leads.socials import SocialScan

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
USER = SimpleNamespace(id=3, selected_organization_id=11)


def _app():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user] = lambda: USER
    return TestClient(app)


def _search(**overrides):
    values = {
        "id": 21,
        "name": "Software Companies in California",
        "category": "Software Companies",
        "location": "California, US",
        "country_code": "US",
        "language": "en",
        "provider": "demo",
        "status": "queued",
        "target_results": 25,
        "external_job_id": None,
        "results_count": 0,
        "new_leads_count": 0,
        "auto_call_campaign_id": None,
        "auto_call_error": None,
        "error_message": None,
        "params": {"depth": 8},
        "created_by": 3,
        "started_at": None,
        "completed_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _lead(**overrides):
    values = {
        "id": 1,
        "title": "Apex Software Labs",
        "category": "Software company",
        "address": "123 Market St",
        "city": "San Francisco",
        "state": "CA",
        "country": "US",
        "postal_code": "94105",
        "phone": "(415) 555-0142",
        "phone_e164": "+14155550142",
        "email": "hello@apex-software-demo.com",
        "website": "https://apex-software-demo.com",
        "social_links": {},
        "socials_checked_at": None,
        "place_id": "abc123",
        "cid": None,
        "rating": 4.6,
        "review_count": 128,
        "latitude": 37.79,
        "longitude": -122.4,
        "open_hours": {},
        "crm_status": "new",
        "owner_user_id": None,
        "tags": [],
        "notes": None,
        "source": "demo",
        "last_contacted_at": None,
        "next_follow_up_at": None,
        "email_consent": "unknown",
        "email_consent_source": None,
        "email_consent_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _mock_db() -> MagicMock:
    """Mock DB client with the async defaults every lead response awaits.

    Lead responses embed ``whatsapp_available``, which resolves the org's
    default sender. Without an AsyncMock here the route raises
    "MagicMock can't be used in 'await' expression".
    """
    db = MagicMock()
    db.get_default_whatsapp_sender = AsyncMock(return_value=None)
    return db


def test_meta_lists_crm_statuses_formats_and_providers():
    with patch("api.routes.leads.db_client", _mock_db()):
        response = _app().get("/leads/meta")

    assert response.status_code == 200
    payload = response.json()
    assert payload["statuses"][0] == "new"
    assert "do_not_call" in payload["statuses"]
    assert payload["export_formats"] == ["csv", "xlsx", "json", "pdf", "png"]
    assert "google_maps" in payload["providers"]


def test_create_search_enqueues_worker_and_scopes_to_organization():
    db = _mock_db()
    db.create_lead_search = AsyncMock(return_value=_search())
    db.update_lead_search = AsyncMock(return_value=_search())
    db.get_lead_search = AsyncMock(return_value=_search())

    with patch("api.routes.leads.db_client", db):
        with patch(
            "api.services.leads.searches.enqueue_job", new=AsyncMock()
        ) as enqueue:
            response = _app().post(
                "/leads/searches",
                json={
                    "category": "Software Companies",
                    "location": "California, US",
                    "country_code": "us",
                    "provider": "demo",
                },
            )

    assert response.status_code == 200, response.text
    assert response.json()["id"] == 21
    kwargs = db.create_lead_search.await_args.kwargs
    assert kwargs["organization_id"] == 11
    assert kwargs["created_by"] == 3
    assert kwargs["country_code"] == "US"
    enqueue.assert_awaited_once()
    assert enqueue.await_args.args[0] == "run_lead_search"
    assert enqueue.await_args.args[1] == 21


def test_create_search_rejects_provider_not_available_on_deployment():
    with patch("api.routes.leads.db_client", _mock_db()):
        response = _app().post(
            "/leads/searches",
            json={
                "category": "Gyms",
                "location": "London, UK",
                "provider": "magic",
            },
        )

    assert response.status_code == 400
    assert "not available" in response.json()["detail"]


def test_create_search_marks_failed_when_queue_is_down():
    db = _mock_db()
    db.create_lead_search = AsyncMock(return_value=_search())
    db.update_lead_search = AsyncMock(return_value=_search())

    with (
        patch("api.routes.leads.db_client", db),
        patch("api.services.leads.searches.db_client", db),
        patch(
            "api.services.leads.searches.enqueue_job",
            new=AsyncMock(side_effect=RuntimeError("redis down")),
        ),
    ):
        response = _app().post(
            "/leads/searches",
            json={"category": "Gyms", "location": "London, UK", "provider": "demo"},
        )

    assert response.status_code == 503
    assert db.update_lead_search.await_args.kwargs["status"] == "failed"


def test_cancel_only_allows_active_searches():
    db = _mock_db()
    db.get_lead_search = AsyncMock(return_value=_search(status="completed"))

    with patch("api.routes.leads.db_client", db):
        response = _app().post("/leads/searches/21/cancel")

    assert response.status_code == 400
    assert "completed" in response.json()["detail"]


def test_create_search_passes_target_and_auto_call_options():
    db = _mock_db()
    db.create_lead_search = AsyncMock(return_value=_search(target_results=10))
    db.update_lead_search = AsyncMock(return_value=_search(target_results=10))
    db.get_lead_search = AsyncMock(return_value=_search(target_results=10))

    with (
        patch("api.routes.leads.db_client", db),
        patch("api.services.leads.searches.enqueue_job", new=AsyncMock()),
    ):
        response = _app().post(
            "/leads/searches",
            json={
                "category": "Dentists",
                "location": "Austin, TX",
                "provider": "demo",
                "target_results": 10,
                "website_filter": "no_website",
                "auto_call": True,
                "workflow_id": 7,
                "telephony_configuration_id": 4,
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["target_results"] == 10
    kwargs = db.create_lead_search.await_args.kwargs
    assert kwargs["target_results"] == 10
    assert kwargs["params"]["website_filter"] == "no_website"
    assert kwargs["params"]["auto_call"] is True
    assert kwargs["params"]["workflow_id"] == 7
    assert kwargs["params"]["telephony_configuration_id"] == 4


def test_create_search_requires_an_agent_when_auto_call_is_on():
    with patch("api.routes.leads.db_client", _mock_db()):
        response = _app().post(
            "/leads/searches",
            json={
                "category": "Dentists",
                "location": "Austin, TX",
                "provider": "demo",
                "auto_call": True,
            },
        )

    assert response.status_code == 422


@pytest.mark.parametrize("status", ["queued", "running", "stopping"])
def test_stop_finishes_without_a_live_worker_or_queue(status):
    db = _mock_db()
    db.get_lead_search = AsyncMock(
        return_value=_search(status=status, results_count=96)
    )
    db.update_lead_search = AsyncMock(
        return_value=_search(status="stopped", results_count=96)
    )

    with (
        patch("api.routes.leads.db_client", db),
        patch(
            "api.services.leads.searches.enqueue_job",
            new=AsyncMock(side_effect=RuntimeError("queue down")),
        ),
    ):
        response = _app().post("/leads/searches/21/stop")

    assert response.status_code == 200
    assert response.json()["status"] == "stopped"
    assert response.json()["results_count"] == 96
    assert db.update_lead_search.await_args.kwargs["status"] == "stopped"
    assert db.update_lead_search.await_args.kwargs["expected_run"] == (None, None)


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_stop_queues_captured_job_cleanup_and_socials_best_effort(cleanup_fails):
    search = _search(
        status="running",
        provider="google_maps",
        external_job_id="job-old",
        started_at=NOW,
    )
    stopped = _search(
        status="stopped",
        provider="google_maps",
        external_job_id="job-old",
        started_at=NOW,
        completed_at=datetime.now(UTC),
        results_count=96,
    )
    db = _mock_db()
    db.get_lead_search = AsyncMock(return_value=search)
    db.update_lead_search = AsyncMock(return_value=stopped)

    async def enqueue_job(name, *args, **kwargs):
        # Stop is already durable before any best-effort queue access.
        db.update_lead_search.assert_awaited_once()
        if cleanup_fails and name == "cancel_lead_scraper":
            raise RuntimeError("cleanup queue unavailable")

    enqueue = AsyncMock(side_effect=enqueue_job)
    with (
        patch("api.routes.leads.db_client", db),
        patch("api.routes.leads.enqueue_job", enqueue),
        patch("api.routes.leads.LEADS_SOCIAL_ENRICHMENT_ENABLED", True),
    ):
        response = _app().post("/leads/searches/21/stop")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "stopped"
    assert response.json()["results_count"] == 96
    db.get_lead_search.assert_awaited_once_with(21, USER.selected_organization_id)
    assert db.update_lead_search.await_args.args == (21, USER.selected_organization_id)
    values = db.update_lead_search.await_args.kwargs
    assert values["status"] == "stopped"
    assert values["expected_run"] == (NOW, "job-old")
    assert values["expected_statuses"] == {"queued", "running", "stopping"}
    assert isinstance(values["completed_at"], datetime)
    assert values["error_message"] is None
    assert "results_count" not in values and "new_leads_count" not in values
    assert enqueue.await_count == 2
    enqueue.assert_any_await("cancel_lead_scraper", "google_maps", "job-old")
    enqueue.assert_any_await("enrich_lead_socials", 21, _job_id="lead-socials:21")


def test_stop_is_idempotent_for_an_already_stopped_search():
    search = _search(
        status="stopped", external_job_id="job-old", results_count=96, completed_at=NOW
    )
    db = _mock_db()
    db.get_lead_search = AsyncMock(return_value=search)
    db.update_lead_search = AsyncMock()
    enqueue = AsyncMock()

    with (
        patch("api.routes.leads.db_client", db),
        patch("api.routes.leads.enqueue_job", enqueue),
    ):
        client = _app()
        first = client.post("/leads/searches/21/stop")
        second = client.post("/leads/searches/21/stop")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["status"] == "stopped"
    assert first.json()["results_count"] == 96
    assert datetime.fromisoformat(first.json()["completed_at"]) == NOW
    db.update_lead_search.assert_not_awaited()
    enqueue.assert_not_awaited()


@pytest.mark.parametrize("new_status", ["queued", "running", "completed", "stopped"])
def test_stop_lost_cas_reloads_newer_state_without_cleanup(new_status):
    search = _search(status="running", started_at=NOW, external_job_id="job-old")
    newer = _search(
        status=new_status,
        started_at=datetime.now(UTC),
        external_job_id="job-new",
        results_count=101,
    )
    db = _mock_db()
    db.get_lead_search = AsyncMock(side_effect=[search, newer])
    db.update_lead_search = AsyncMock(return_value=None)
    enqueue = AsyncMock()

    with (
        patch("api.routes.leads.db_client", db),
        patch("api.routes.leads.enqueue_job", enqueue),
    ):
        response = _app().post("/leads/searches/21/stop")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == new_status
    assert response.json()["external_job_id"] == "job-new"
    assert response.json()["results_count"] == 101
    assert datetime.fromisoformat(response.json()["started_at"]) == newer.started_at
    assert db.get_lead_search.await_count == 2
    assert all(
        call.args == (21, USER.selected_organization_id)
        for call in db.get_lead_search.await_args_list
    )
    db.update_lead_search.assert_awaited_once()
    assert db.update_lead_search.await_args.kwargs["expected_run"] == (NOW, "job-old")
    enqueue.assert_not_awaited()


def test_stop_cannot_access_another_organizations_search():
    foreign = _search(
        status="running", organization_id=USER.selected_organization_id + 1
    )
    db = _mock_db()

    async def get_search(search_id, organization_id):
        if search_id == foreign.id and organization_id == foreign.organization_id:
            return foreign
        return None

    db.get_lead_search = AsyncMock(side_effect=get_search)
    db.update_lead_search = AsyncMock()
    enqueue = AsyncMock()
    with (
        patch("api.routes.leads.db_client", db),
        patch("api.routes.leads.enqueue_job", enqueue),
    ):
        response = _app().post(f"/leads/searches/{foreign.id}/stop")

    assert response.status_code == 404
    assert response.json()["detail"] == "Search not found"
    db.get_lead_search.assert_awaited_once_with(
        foreign.id, USER.selected_organization_id
    )
    db.update_lead_search.assert_not_awaited()
    enqueue.assert_not_awaited()


def test_stop_rejects_a_finished_search():
    db = _mock_db()
    db.get_lead_search = AsyncMock(return_value=_search(status="completed"))

    with patch("api.routes.leads.db_client", db):
        response = _app().post("/leads/searches/21/stop")

    assert response.status_code == 400


def test_list_leads_passes_filters_and_org_scope():
    db = _mock_db()
    db.list_leads = AsyncMock(return_value=([_lead()], 1))

    with patch("api.routes.leads.db_client", db):
        response = _app().get(
            "/leads?status=new&has_phone=true&q=apex&tag=hot&search_id=21"
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_count"] == 1
    assert payload["leads"][0]["phone_e164"] == "+14155550142"
    kwargs = db.list_leads.await_args.kwargs
    assert kwargs["crm_status"] == "new"
    assert kwargs["has_phone"] is True
    assert kwargs["query"] == "apex"
    assert kwargs["tag"] == "hot"
    assert kwargs["search_id"] == 21
    assert db.list_leads.await_args.args[0] == 11


def test_export_returns_downloadable_attachment():
    db = _mock_db()
    db.list_leads = AsyncMock(return_value=([_lead()], 1))
    db.get_lead_search = AsyncMock(return_value=_search())

    with patch("api.routes.leads.db_client", db):
        response = _app().get("/leads/export?format=csv&search_id=21")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert (
        'attachment; filename="bolo_leads_' in response.headers["content-disposition"]
    )
    assert response.content.startswith(b"\xef\xbb\xbf")


def test_export_without_matches_is_a_400():
    db = _mock_db()
    db.list_leads = AsyncMock(return_value=([], 0))

    with patch("api.routes.leads.db_client", db):
        response = _app().get("/leads/export?format=csv")

    assert response.status_code == 400
    assert response.json()["detail"] == "No leads match these filters"


def test_bulk_update_records_one_status_activity_per_changed_lead():
    db = _mock_db()
    db.get_leads_by_ids = AsyncMock(
        return_value=[
            _lead(id=1, crm_status="new"),
            _lead(id=2, crm_status="contacted"),
        ]
    )
    db.bulk_update_leads = AsyncMock(return_value=2)
    db.add_lead_activities_bulk = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        response = _app().post(
            "/leads/bulk", json={"lead_ids": [1, 2], "crm_status": "contacted"}
        )

    assert response.status_code == 200
    assert response.json() == {"updated": 2}
    activities = db.add_lead_activities_bulk.await_args.args[0]
    assert len(activities) == 1
    assert activities[0]["lead_id"] == 1
    assert activities[0]["extra_metadata"] == {
        "from": "new",
        "to": "contacted",
        "bulk": True,
    }


def test_dispatch_maps_quota_failures_to_402():
    with (
        patch("api.routes.leads.db_client", _mock_db()),
        patch(
            "api.routes.leads.dispatch_leads_to_campaign",
            new=AsyncMock(side_effect=DispatchQuotaError("Quota exhausted")),
        ),
    ):
        response = _app().post(
            "/leads/dispatch-campaign",
            json={
                "lead_ids": [1],
                "name": "Q4",
                "workflow_id": 7,
            },
        )

    assert response.status_code == 402
    assert response.json()["detail"] == "Quota exhausted"


def test_lead_detail_is_org_scoped_and_includes_relations():
    db = _mock_db()
    db.get_lead = AsyncMock(return_value=_lead())
    db.list_lead_activities = AsyncMock(return_value=[])
    db.list_lead_calls = AsyncMock(return_value=[])
    db.list_lead_search_links = AsyncMock(return_value=[])

    with patch("api.routes.leads.db_client", db):
        response = _app().get("/leads/1")

    assert response.status_code == 200
    assert response.json()["lead"]["id"] == 1
    db.get_lead.assert_awaited_once_with(1, 11)


def test_lead_responses_survive_a_whatsapp_sender_lookup_failure():
    db = _mock_db()
    db.get_default_whatsapp_sender = AsyncMock(side_effect=RuntimeError("db down"))
    db.list_leads = AsyncMock(return_value=([_lead()], 1))
    db.get_lead = AsyncMock(return_value=_lead())
    db.list_lead_activities = AsyncMock(return_value=[])
    db.list_lead_calls = AsyncMock(return_value=[])
    db.list_lead_search_links = AsyncMock(return_value=[])
    db.update_lead = AsyncMock(return_value=_lead(crm_status="contacted"))
    db.add_lead_activity = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        listing = _app().get("/leads")
        detail = _app().get("/leads/1")
        edited = _app().patch("/leads/1", json={"crm_status": "contacted"})

    assert listing.status_code == 200, listing.text
    assert listing.json()["leads"][0]["whatsapp_available"] is False
    assert detail.status_code == 200, detail.text
    assert detail.json()["lead"]["whatsapp_available"] is False
    assert edited.status_code == 200, edited.text
    assert edited.json()["whatsapp_available"] is False


def test_patch_status_change_appends_activity():
    db = _mock_db()
    db.get_lead = AsyncMock(return_value=_lead())
    db.update_lead = AsyncMock(return_value=_lead(crm_status="interested"))
    db.add_lead_activity = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        response = _app().patch("/leads/1", json={"crm_status": "interested"})

    assert response.status_code == 200
    assert response.json()["crm_status"] == "interested"
    activity = db.add_lead_activity.await_args.kwargs
    assert activity["activity_type"] == "status_change"
    assert activity["metadata"] == {"from": "new", "to": "interested"}
    assert db.update_lead.await_args.kwargs["last_contacted_at"] is not None


def test_unknown_lead_returns_404():
    db = _mock_db()
    db.get_lead = AsyncMock(return_value=None)

    with patch("api.routes.leads.db_client", db):
        response = _app().get("/leads/999")

    assert response.status_code == 404


def _edit_payload(**overrides):
    payload = {
        "name": "Taxi Service in Limassol",
        "category": "Taxi Service",
        "location": "Limassol, Cyprus",
        "country_code": "cy",
        "language": "en",
        "provider": "demo",
        "depth": 4,
        "zoom": 12,
        "target_results": 100,
        "website_filter": "any",
    }
    payload.update(overrides)
    return payload


def test_list_leads_passes_geography_and_contact_filters():
    db = _mock_db()
    db.list_leads = AsyncMock(return_value=([_lead()], 1))

    with patch("api.routes.leads.db_client", db):
        response = _app().get(
            "/leads?country=US&state=CA&city=San%20Francisco"
            "&category=Coffee%20Shops"
            "&has_website=true&has_email=true&has_phone=false"
        )

    assert response.status_code == 200, response.text
    kwargs = db.list_leads.await_args.kwargs
    assert kwargs["country"] == "US"
    assert kwargs["state"] == "CA"
    assert kwargs["city"] == "San Francisco"
    assert kwargs["category"] == "Coffee Shops"
    assert kwargs["has_website"] is True
    assert kwargs["has_email"] is True
    assert kwargs["has_phone"] is False
    assert db.list_leads.await_args.args[0] == 11


def test_facets_returns_dependent_filter_values():
    db = _mock_db()
    db.lead_facets = AsyncMock(
        return_value={
            "countries": ["CY", "US"],
            "states": ["Limassol"],
            "cities": ["Limassol"],
            "categories": ["Coffee Shops", "Taxi Service"],
        }
    )

    with patch("api.routes.leads.db_client", db):
        response = _app().get("/leads/facets?country=CY&state=Limassol")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "countries": ["CY", "US"],
        "states": ["Limassol"],
        "cities": ["Limassol"],
        "categories": ["Coffee Shops", "Taxi Service"],
    }
    assert db.lead_facets.await_args.args[0] == 11
    assert db.lead_facets.await_args.kwargs == {
        "country": "CY",
        "state": "Limassol",
    }


def test_update_search_requires_an_existing_csv_campaign():
    search = _search(status="stopped", results_count=1, target_results=25)
    db = _mock_db()
    db.get_lead_search = AsyncMock(return_value=search)
    db.get_campaign = AsyncMock(return_value=None)
    db.update_lead_search = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        response = _app().patch(
            "/leads/searches/21",
            json=_edit_payload(add_to_campaign_id=99),
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Campaign not found"
    db.update_lead_search.assert_not_awaited()


def test_update_search_can_append_current_leads_to_existing_campaign():
    search = _search(status="stopped", results_count=2, target_results=25)
    updated = _search(status="queued", target_results=100, results_count=2)
    db = _mock_db()
    db.get_lead_search = AsyncMock(return_value=search)
    db.get_campaign = AsyncMock(
        return_value=SimpleNamespace(
            id=9, source_type="csv", state="running", name="Cyprus taxi calls"
        )
    )
    db.list_leads = AsyncMock(
        return_value=(
            [
                _lead(id=1, crm_status="new"),
                _lead(id=2, crm_status="do_not_call"),
            ],
            2,
        )
    )
    db.update_lead_search = AsyncMock(return_value=updated)

    with (
        patch("api.routes.leads.db_client", db),
        patch("api.routes.leads.add_leads_to_campaign", new=AsyncMock()) as append,
        patch("api.services.leads.searches.enqueue_job", new=AsyncMock()),
    ):
        response = _app().patch(
            "/leads/searches/21",
            json=_edit_payload(add_to_campaign_id=9),
        )

    assert response.status_code == 200, response.text
    assert append.await_args.kwargs["campaign_id"] == 9
    assert append.await_args.kwargs["lead_ids"] == [1]
    assert append.await_args.kwargs["organization_id"] == 11
    kwargs = db.update_lead_search.await_args.kwargs
    assert kwargs["params"]["add_to_campaign_id"] == 9
    assert kwargs["status"] == "queued"


def test_update_search_rejects_adding_to_a_campaign_that_was_not_started():
    search = _search(status="stopped", results_count=1, target_results=25)
    db = _mock_db()
    db.get_lead_search = AsyncMock(return_value=search)
    db.get_campaign = AsyncMock(
        return_value=SimpleNamespace(
            id=9, source_type="csv", state="created", name="Draft"
        )
    )
    db.update_lead_search = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        response = _app().patch(
            "/leads/searches/21",
            json=_edit_payload(add_to_campaign_id=9),
        )

    assert response.status_code == 400
    assert "Start the campaign" in response.json()["detail"]
    db.update_lead_search.assert_not_awaited()


def test_update_search_requeues_with_new_settings_and_keeps_leads():
    search = _search(status="stopped", results_count=1, target_results=25)
    updated = _search(
        name="Taxi Service in Limassol",
        category="Taxi Service",
        location="Limassol, Cyprus",
        status="queued",
        target_results=100,
    )
    db = _mock_db()
    db.get_lead_search = AsyncMock(return_value=search)
    db.update_lead_search = AsyncMock(return_value=updated)
    db.clear_search_results = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        with patch(
            "api.services.leads.searches.enqueue_job", new=AsyncMock()
        ) as enqueue:
            response = _app().patch("/leads/searches/21", json=_edit_payload())

    assert response.status_code == 200, response.text
    kwargs = db.update_lead_search.await_args.kwargs
    assert db.update_lead_search.await_args.args == (21, 11)
    assert kwargs["category"] == "Taxi Service"
    assert kwargs["location"] == "Limassol, Cyprus"
    assert kwargs["country_code"] == "CY"
    assert kwargs["target_results"] == 100
    assert kwargs["params"]["depth"] == 4
    assert kwargs["status"] == "queued"
    # Job state is reset so the worker submits a fresh scraper run.
    assert kwargs["external_job_id"] is None
    assert kwargs["started_at"] is None
    assert kwargs["completed_at"] is None
    # Default keeps already collected leads.
    db.clear_search_results.assert_not_awaited()
    assert "results_count" not in kwargs
    enqueue.assert_awaited_once_with("run_lead_search", 21)


def test_update_search_can_start_from_scratch():
    search = _search(status="completed", results_count=80, target_results=80)
    db = _mock_db()
    db.get_lead_search = AsyncMock(return_value=search)
    db.update_lead_search = AsyncMock(
        return_value=_search(status="queued", results_count=0)
    )
    db.clear_search_results = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        with patch("api.services.leads.searches.enqueue_job", new=AsyncMock()):
            response = _app().patch(
                "/leads/searches/21",
                json=_edit_payload(target_results=25, keep_existing_results=False),
            )

    assert response.status_code == 200, response.text
    db.clear_search_results.assert_awaited_once_with(21, 11)
    kwargs = db.update_lead_search.await_args.kwargs
    assert kwargs["results_count"] == 0
    assert kwargs["new_leads_count"] == 0


def test_update_search_rejects_target_below_existing_leads():
    search = _search(status="completed", results_count=80, target_results=80)
    db = _mock_db()
    db.get_lead_search = AsyncMock(return_value=search)
    db.update_lead_search = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        with patch(
            "api.services.leads.searches.enqueue_job", new=AsyncMock()
        ) as enqueue:
            response = _app().patch(
                "/leads/searches/21", json=_edit_payload(target_results=50)
            )

    assert response.status_code == 400
    assert "already has 80 leads" in response.json()["detail"]
    db.update_lead_search.assert_not_awaited()
    enqueue.assert_not_awaited()


def test_update_search_rejects_while_running():
    db = _mock_db()
    db.get_lead_search = AsyncMock(return_value=_search(status="running"))

    with patch("api.routes.leads.db_client", db):
        response = _app().patch("/leads/searches/21", json=_edit_payload())

    assert response.status_code == 400
    assert "Stop or cancel" in response.json()["detail"]


def test_extend_search_raises_target_and_keeps_planned_places():
    search = _search(
        status="completed",
        target_results=50,
        results_count=12,
        params={
            "depth": 3,
            "website_filter": "no_website",
            "fanout_places": ["Fresno", "Oakland"],
        },
    )
    updated = _search(status="queued", target_results=250)
    db = _mock_db()
    db.get_lead_search = AsyncMock(return_value=search)
    db.update_lead_search = AsyncMock(return_value=updated)

    with patch("api.routes.leads.db_client", db):
        with patch(
            "api.services.leads.searches.enqueue_job", new=AsyncMock()
        ) as enqueue:
            response = _app().post(
                "/leads/searches/21/extend", json={"additional_results": 200}
            )

    assert response.status_code == 200, response.text
    kwargs = db.update_lead_search.await_args.kwargs
    assert kwargs["target_results"] == 250
    # Extending fans out to new places; the places already covered are kept so
    # the worker can skip them, and the scraper depth stays as it was.
    assert kwargs["params"]["depth"] == 3
    assert kwargs["params"]["website_filter"] == "no_website"
    assert kwargs["params"]["fanout_places"] == ["Fresno", "Oakland"]
    assert kwargs["status"] == "queued"
    assert kwargs["external_job_id"] is None
    enqueue.assert_awaited_once_with("run_lead_search", 21)


def test_extend_search_replans_places_when_the_previous_run_was_interrupted():
    search = _search(
        status="stopped",
        target_results=50,
        results_count=12,
        params={
            "depth": 3,
            "keywords": ["plumbers in Fresno, CA"],
            "fanout_places": ["Fresno", "Oakland"],
        },
    )
    updated = _search(status="queued", target_results=250)
    db = _mock_db()
    db.get_lead_search = AsyncMock(return_value=search)
    db.update_lead_search = AsyncMock(return_value=updated)

    with patch("api.routes.leads.db_client", db):
        with patch("api.services.leads.searches.enqueue_job", new=AsyncMock()):
            response = _app().post(
                "/leads/searches/21/extend", json={"additional_results": 200}
            )

    assert response.status_code == 200, response.text
    kwargs = db.update_lead_search.await_args.kwargs
    assert kwargs["target_results"] == 250
    assert kwargs["params"]["depth"] == 3
    # The interrupted run never queried every place it had reserved, so Extend
    # must re-plan instead of silently skipping them.
    assert "fanout_places" not in kwargs["params"]
    assert "keywords" not in kwargs["params"]


def test_retry_search_replans_reserved_places():
    search = _search(
        status="failed",
        target_results=50,
        results_count=12,
        error_message="Discovery job failed",
        params={
            "depth": 3,
            "keywords": ["plumbers in Fresno, CA"],
            "fanout_places": ["Fresno", "Oakland"],
        },
    )
    updated = _search(status="queued", target_results=50)
    db = _mock_db()
    db.get_lead_search = AsyncMock(return_value=search)
    db.update_lead_search = AsyncMock(return_value=updated)

    with patch("api.routes.leads.db_client", db):
        with patch(
            "api.services.leads.searches.enqueue_job", new=AsyncMock()
        ) as enqueue:
            response = _app().post("/leads/searches/21/retry")

    assert response.status_code == 200, response.text
    kwargs = db.update_lead_search.await_args.kwargs
    assert kwargs["params"]["depth"] == 3
    assert "fanout_places" not in kwargs["params"]
    assert "keywords" not in kwargs["params"]
    assert kwargs["status"] == "queued"
    assert kwargs["error_message"] is None
    assert kwargs["external_job_id"] is None
    enqueue.assert_awaited_once_with("run_lead_search", 21)


def test_extend_search_cannot_exceed_the_result_limit():
    search = _search(status="completed", target_results=900, results_count=900)
    db = _mock_db()
    db.get_lead_search = AsyncMock(return_value=search)
    db.update_lead_search = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        with patch(
            "api.services.leads.searches.enqueue_job", new=AsyncMock()
        ) as enqueue:
            response = _app().post(
                "/leads/searches/21/extend", json={"additional_results": 200}
            )

    assert response.status_code == 400
    assert "above the limit" in response.json()["detail"]
    db.update_lead_search.assert_not_awaited()
    enqueue.assert_not_awaited()


def test_list_leads_and_stats_accept_a_project_scope():
    db = _mock_db()
    db.list_leads = AsyncMock(return_value=([_lead()], 1))
    db.lead_stats = AsyncMock(
        return_value={
            "total": 1,
            "new": 1,
            "contacted": 0,
            "interested": 0,
            "not_interested": 0,
            "won": 0,
            "lost": 0,
            "do_not_call": 0,
            "with_phone": 1,
            "with_email": 0,
            "with_website": 1,
            "searches": 0,
        }
    )

    with patch("api.routes.leads.db_client", db):
        listed = _app().get("/leads?project_id=5")
        stats = _app().get("/leads/stats?project_id=5")

    assert listed.status_code == 200
    assert db.list_leads.await_args.kwargs["project_id"] == 5
    assert stats.status_code == 200
    assert db.lead_stats.await_args.kwargs["project_id"] == 5


def test_export_uses_the_project_name_as_the_file_title():
    db = _mock_db()
    db.list_leads = AsyncMock(return_value=([_lead()], 1))
    db.get_lead_project = AsyncMock(
        return_value=(SimpleNamespace(name="US coffee shops"), 1, 1)
    )

    with patch("api.routes.leads.db_client", db):
        response = _app().get("/leads/export?format=csv&project_id=5")

    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    db.get_lead_project.assert_awaited_once_with(5, 11)
    assert db.list_leads.await_args.kwargs["project_id"] == 5


def test_export_unknown_project_is_a_404():
    db = _mock_db()
    db.list_leads = AsyncMock(return_value=([_lead()], 1))
    db.get_lead_project = AsyncMock(return_value=None)

    with patch("api.routes.leads.db_client", db):
        response = _app().get("/leads/export?format=csv&project_id=99")

    assert response.status_code == 404


def test_patch_contact_fields_normalizes_phone_and_logs_an_edit():
    db = _mock_db()
    db.get_lead = AsyncMock(return_value=_lead())
    db.update_lead = AsyncMock(
        return_value=_lead(
            phone="(512) 555-0100",
            phone_e164="+15125550100",
            city="Austin",
            website=None,
        )
    )
    db.add_lead_activity = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        response = _app().patch(
            "/leads/1",
            json={
                "phone": "(512) 555-0100",
                "city": "Austin",
                "website": "",
            },
        )

    assert response.status_code == 200, response.text
    values = db.update_lead.await_args.kwargs
    assert values["city"] == "Austin"
    assert values["website"] is None
    assert values["phone"] == "(512) 555-0100"
    assert values["phone_e164"] == "+15125550100"
    activity = db.add_lead_activity.await_args.kwargs
    assert activity["activity_type"] == "lead_edit"
    assert set(activity["metadata"]["fields"]) == {"phone", "city", "website"}


def test_patch_website_clears_socials_so_the_new_site_is_rescanned():
    db = _mock_db()
    db.get_lead = AsyncMock(return_value=_lead())
    db.update_lead = AsyncMock(return_value=_lead(website="https://new.example"))
    db.add_lead_activity = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        response = _app().patch("/leads/1", json={"website": "https://new.example"})

    assert response.status_code == 200, response.text
    values = db.update_lead.await_args.kwargs
    assert values["website"] == "https://new.example"
    assert values["social_links"] == {}
    assert values["socials_checked_at"] is None
    assert values["socials_attempts"] == 0
    assert values["socials_next_attempt_at"] is None


def test_patch_keeps_socials_when_the_website_is_unchanged():
    db = _mock_db()
    db.get_lead = AsyncMock(return_value=_lead())
    db.update_lead = AsyncMock(return_value=_lead(city="Oakland"))
    db.add_lead_activity = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        response = _app().patch("/leads/1", json={"city": "Oakland"})

    assert response.status_code == 200, response.text
    values = db.update_lead.await_args.kwargs
    assert "social_links" not in values
    assert "socials_checked_at" not in values


def test_patch_rejects_clearing_the_business_name():
    db = _mock_db()
    db.get_lead = AsyncMock(return_value=_lead())

    with patch("api.routes.leads.db_client", db):
        response = _app().patch("/leads/1", json={"title": "  "})

    assert response.status_code == 422
    db.update_lead.assert_not_called()


def test_bulk_update_can_append_tags_without_replacing_them():
    db = _mock_db()
    db.get_leads_by_ids = AsyncMock(return_value=[_lead(id=1), _lead(id=2)])
    db.bulk_update_leads = AsyncMock(return_value=2)
    db.add_tags_to_leads = AsyncMock(return_value=2)

    with patch("api.routes.leads.db_client", db):
        response = _app().post(
            "/leads/bulk",
            json={"lead_ids": [1, 2], "add_tags": ["menu-ready"]},
        )

    assert response.status_code == 200
    assert response.json() == {"updated": 2}
    db.add_tags_to_leads.assert_awaited_once_with(11, [1, 2], ["menu-ready"])
    assert "tags" not in db.bulk_update_leads.await_args.kwargs


def test_list_leads_passes_the_email_consent_filter():
    db = _mock_db()
    db.list_leads = AsyncMock(return_value=([_lead(email_consent="opted_in")], 1))

    with patch("api.routes.leads.db_client", db):
        response = _app().get("/leads?email_consent=opted_in")

    assert response.status_code == 200, response.text
    assert response.json()["leads"][0]["email_consent"] == "opted_in"
    assert db.list_leads.await_args.kwargs["email_consent"] == "opted_in"


def test_recording_email_consent_requires_a_written_reason_for_opt_in():
    db = _mock_db()
    db.get_lead = AsyncMock(return_value=_lead())

    with patch("api.routes.leads.db_client", db):
        response = _app().patch(
            "/leads/1/email-consent",
            json={"email_consent": "opted_in", "reason": "   "},
        )

    assert response.status_code == 422
    db.update_lead.assert_not_called()


def test_recording_email_consent_stores_source_time_and_warning():
    db = _mock_db()
    db.get_lead = AsyncMock(
        return_value=_lead(email_consent="unknown", email_consent_source=None)
    )
    db.update_lead = AsyncMock(
        return_value=_lead(
            email_consent="opted_in",
            email_consent_source="manual",
            email_consent_at=NOW,
        )
    )
    db.add_lead_activity = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        response = _app().patch(
            "/leads/1/email-consent",
            json={
                "email_consent": "opted_in",
                "reason": "Customer asked to receive seasonal offers on 17 Nov.",
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["email_consent"] == "opted_in"
    values = db.update_lead.await_args.kwargs
    assert values["email_consent"] == "opted_in"
    assert values["email_consent_source"] == "manual"
    assert values["email_consent_at"] is not None
    activity = values["activities"][0]
    assert activity["activity_type"] == "email_consent"
    assert activity["user_id"] == USER.id
    assert activity["body"] == "Customer asked to receive seasonal offers on 17 Nov."
    assert activity["extra_metadata"]["from"] == "unknown"
    assert activity["extra_metadata"]["to"] == "opted_in"
    assert activity["extra_metadata"]["source"] == "manual"
    assert activity["extra_metadata"]["bulk"] is False
    assert activity["extra_metadata"]["changed"] is True
    assert "anti-spam" in activity["extra_metadata"]["warning"]
    # The audit row is committed with the consent change, not afterwards.
    db.add_lead_activity.assert_not_called()


def test_recording_email_consent_can_opt_out_without_a_reason():
    db = _mock_db()
    db.get_lead = AsyncMock(return_value=_lead(email_consent="opted_in"))
    db.update_lead = AsyncMock(
        return_value=_lead(
            email_consent="opted_out",
            email_consent_source="manual",
            email_consent_at=NOW,
        )
    )
    db.add_lead_activity = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        response = _app().patch(
            "/leads/1/email-consent", json={"email_consent": "opted_out"}
        )

    assert response.status_code == 200, response.text
    assert response.json()["email_consent"] == "opted_out"
    activities = db.update_lead.await_args.kwargs["activities"]
    assert activities[0]["body"] is None


def test_reconfirming_the_same_consent_keeps_the_original_source_and_time():
    db = _mock_db()
    db.get_lead = AsyncMock(
        return_value=_lead(
            email_consent="opted_in",
            email_consent_source="manual",
            email_consent_at=NOW,
        )
    )
    db.update_lead = AsyncMock()
    db.add_lead_activity = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        response = _app().patch(
            "/leads/1/email-consent",
            json={
                "email_consent": "opted_in",
                "reason": "Customer confirmed again by phone on 18 Nov.",
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["email_consent"] == "opted_in"
    assert response.json()["email_consent_source"] == "manual"
    # Nothing changed, so the lead row and its original decision timestamp stay.
    db.update_lead.assert_not_called()
    activity = db.add_lead_activity.await_args.kwargs
    assert activity["title"] == "Email consent confirmed: Opted in"
    assert activity["body"] == "Customer confirmed again by phone on 18 Nov."
    assert activity["metadata"]["changed"] is False
    assert activity["metadata"]["from"] == "opted_in"
    assert activity["metadata"]["to"] == "opted_in"


def test_clearing_email_consent_removes_source_and_timestamp():
    db = _mock_db()
    db.get_lead = AsyncMock(
        return_value=_lead(
            email_consent="opted_out",
            email_consent_source="manual",
            email_consent_at=NOW,
        )
    )
    db.update_lead = AsyncMock(return_value=_lead(email_consent="unknown"))
    db.add_lead_activity = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        response = _app().patch(
            "/leads/1/email-consent", json={"email_consent": "unknown"}
        )

    assert response.status_code == 200, response.text
    values = db.update_lead.await_args.kwargs
    assert values["email_consent"] == "unknown"
    assert values["email_consent_source"] is None
    assert values["email_consent_at"] is None


def test_bulk_email_consent_updates_leads_and_logs_every_matched_row():
    db = _mock_db()
    db.get_leads_by_ids = AsyncMock(
        return_value=[
            _lead(id=1, email_consent="unknown"),
            _lead(id=2, email_consent="opted_out"),
            _lead(id=3, email_consent="opted_in"),
        ]
    )
    db.bulk_update_leads = AsyncMock(return_value=2)
    db.add_lead_activities_bulk = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        response = _app().post(
            "/leads/email-consent/bulk",
            json={
                "lead_ids": [1, 2, 3],
                "email_consent": "opted_in",
                "reason": "All three signed up at the trade show booth.",
            },
        )

    assert response.status_code == 200, response.text
    assert response.json() == {"matched": 3, "changed": 2}
    # Only rows whose decision actually changed are written.
    assert db.bulk_update_leads.await_args.args[1] == [1, 2]
    values = db.bulk_update_leads.await_args.kwargs
    assert values["email_consent"] == "opted_in"
    assert values["email_consent_source"] == "bulk"
    assert values["email_consent_at"] is not None
    activities = values["activities"]
    assert [item["lead_id"] for item in activities] == [1, 2, 3]
    assert activities[0]["extra_metadata"]["source"] == "bulk"
    assert activities[0]["extra_metadata"]["bulk"] is True
    assert activities[0]["extra_metadata"]["changed"] is True
    assert activities[0]["body"] == "All three signed up at the trade show booth."
    # The third lead was already opted in. Its reason is still recorded.
    assert activities[2]["extra_metadata"]["changed"] is False
    assert activities[2]["title"] == "Email consent confirmed: Opted in"
    assert activities[2]["body"] == "All three signed up at the trade show booth."
    # The audit rows are committed with the consent changes.
    db.add_lead_activities_bulk.assert_not_called()


def test_bulk_email_consent_records_a_reason_when_nothing_changes():
    db = _mock_db()
    db.get_leads_by_ids = AsyncMock(
        return_value=[_lead(id=1, email_consent="opted_in")]
    )
    db.bulk_update_leads = AsyncMock(return_value=0)

    with patch("api.routes.leads.db_client", db):
        response = _app().post(
            "/leads/email-consent/bulk",
            json={
                "lead_ids": [1],
                "email_consent": "opted_in",
                "reason": "Signed the form again at the follow-up meeting.",
            },
        )

    assert response.status_code == 200, response.text
    assert response.json() == {"matched": 1, "changed": 0}
    assert db.bulk_update_leads.await_args.args[1] == []
    activities = db.bulk_update_leads.await_args.kwargs["activities"]
    assert len(activities) == 1
    assert activities[0]["body"] == "Signed the form again at the follow-up meeting."


def test_bulk_email_consent_requires_a_reason_for_opt_in():
    with patch("api.routes.leads.db_client", _mock_db()):
        response = _app().post(
            "/leads/email-consent/bulk",
            json={"lead_ids": [1], "email_consent": "opted_in"},
        )

    assert response.status_code == 422


def test_editing_the_email_resets_consent_in_the_same_update():
    db = _mock_db()
    db.get_lead = AsyncMock(
        return_value=_lead(
            email="old@apex-software-demo.com",
            email_consent="opted_in",
            email_consent_source="manual",
            email_consent_at=NOW,
        )
    )
    db.update_lead = AsyncMock(
        return_value=_lead(email="new@apex-software-demo.com", email_consent="unknown")
    )
    db.add_lead_activity = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        response = _app().patch(
            "/leads/1", json={"email": "new@apex-software-demo.com"}
        )

    assert response.status_code == 200, response.text
    values = db.update_lead.await_args.kwargs
    assert values["email"] == "new@apex-software-demo.com"
    assert values["email_consent"] == "unknown"
    assert values["email_consent_source"] is None
    assert values["email_consent_at"] is None
    activity = values["activities"][0]
    assert activity["extra_metadata"]["reason"] == "email_changed"
    assert activity["extra_metadata"]["from"] == "opted_in"
    assert activity["extra_metadata"]["source"] == "manual"
    assert "old@apex-software-demo.com" in activity["body"]
    assert "new@apex-software-demo.com" in activity["body"]


def test_editing_the_email_without_consent_does_not_log_a_reset():
    db = _mock_db()
    db.get_lead = AsyncMock(
        return_value=_lead(email="old@apex-software-demo.com", email_consent="unknown")
    )
    db.update_lead = AsyncMock(
        return_value=_lead(email="new@apex-software-demo.com", email_consent="unknown")
    )
    db.add_lead_activity = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        response = _app().patch(
            "/leads/1", json={"email": "new@apex-software-demo.com"}
        )

    assert response.status_code == 200, response.text
    values = db.update_lead.await_args.kwargs
    assert "email_consent" not in values
    assert values["activities"] is None
    # The ordinary edit activity already records that the email changed.
    activity = db.add_lead_activity.await_args.kwargs
    assert activity["activity_type"] == "lead_edit"
    assert "email" in activity["metadata"]["fields"]


def test_adding_an_address_clears_consent_that_had_no_address():
    db = _mock_db()
    db.get_lead = AsyncMock(
        return_value=_lead(
            email=None,
            email_consent="opted_in",
            email_consent_source="bulk",
            email_consent_at=NOW,
        )
    )
    db.update_lead = AsyncMock(
        return_value=_lead(
            email="hello@apex-software-demo.com", email_consent="unknown"
        )
    )
    db.add_lead_activity = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        response = _app().patch(
            "/leads/1", json={"email": "hello@apex-software-demo.com"}
        )

    assert response.status_code == 200, response.text
    values = db.update_lead.await_args.kwargs
    assert values["email_consent"] == "unknown"
    # Nothing was tied to an old address, so this is not called a reset.
    assert values["activities"] is None


def test_editing_only_the_email_case_keeps_consent():
    db = _mock_db()
    db.get_lead = AsyncMock(return_value=_lead(email="Hello@Apex-Software-Demo.com"))
    db.update_lead = AsyncMock(return_value=_lead(email="hello@apex-software-demo.com"))
    db.add_lead_activity = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        response = _app().patch(
            "/leads/1", json={"email": "hello@apex-software-demo.com"}
        )

    assert response.status_code == 200, response.text
    values = db.update_lead.await_args.kwargs
    assert "email_consent" not in values
    assert values["activities"] is None


def test_delete_search_rejects_active_search():
    db = _mock_db()
    db.get_lead_search = AsyncMock(return_value=_search(status="running"))

    with patch("api.routes.leads.db_client", db):
        response = _app().delete("/leads/searches/21")

    assert response.status_code == 400
    assert response.json()["detail"] == "Cancel this search before deleting it"
    db.delete_lead_search.assert_not_called()


def test_delete_search_removes_finished_search_for_org():
    db = _mock_db()
    db.get_lead_search = AsyncMock(return_value=_search(status="completed"))
    db.delete_lead_search = AsyncMock(return_value=True)

    with patch("api.routes.leads.db_client", db):
        response = _app().delete("/leads/searches/21")

    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    db.delete_lead_search.assert_awaited_once_with(21, USER.selected_organization_id)


def test_delete_search_unknown_is_404():
    db = _mock_db()
    db.get_lead_search = AsyncMock(return_value=None)

    with patch("api.routes.leads.db_client", db):
        response = _app().delete("/leads/searches/21")

    assert response.status_code == 404


def test_bulk_delete_is_org_scoped_and_reports_count():
    db = _mock_db()
    db.delete_leads = AsyncMock(return_value=2)

    with patch("api.routes.leads.db_client", db):
        response = _app().post("/leads/bulk-delete", json={"lead_ids": [1, 1, 2]})

    assert response.status_code == 200
    assert response.json() == {"deleted": 2}
    db.delete_leads.assert_awaited_once_with([1, 2], USER.selected_organization_id)


def test_bulk_delete_without_matches_is_404():
    db = _mock_db()
    db.delete_leads = AsyncMock(return_value=0)

    with patch("api.routes.leads.db_client", db):
        response = _app().post("/leads/bulk-delete", json={"lead_ids": [999]})

    assert response.status_code == 404
    assert response.json()["detail"] == "No matching leads"


def test_refresh_socials_scans_the_website_and_saves_org_scoped_links():
    db = _mock_db()
    db.get_lead = AsyncMock(return_value=_lead())
    db.save_lead_socials = AsyncMock(
        return_value=_lead(
            social_links={"instagram": "https://www.instagram.com/apex/"},
            socials_checked_at=NOW,
        )
    )
    discover = AsyncMock(
        return_value=SocialScan(
            {"instagram": "https://www.instagram.com/apex/"},
            checked=True,
            replace_links=True,
        )
    )

    with (
        patch("api.routes.leads.db_client", db),
        patch("api.routes.leads.discover_social_links", discover),
        patch(
            "api.routes.leads.allow_refresh_socials",
            AsyncMock(return_value=True),
        ),
        patch(
            "api.routes.leads.acquire_org_scan_slot",
            AsyncMock(return_value="slot-1"),
        ),
        patch("api.routes.leads.release_org_scan_slot", AsyncMock()) as release,
    ):
        response = _app().post("/leads/1/refresh-socials")

    assert response.status_code == 200, response.text
    assert response.json()["social_links"] == {
        "instagram": "https://www.instagram.com/apex/"
    }
    db.get_lead.assert_awaited_once_with(1, USER.selected_organization_id)
    discover.assert_awaited_once_with(
        "https://apex-software-demo.com", business_name="Apex Software Labs"
    )
    kwargs = db.save_lead_socials.await_args.kwargs
    assert kwargs["social_links"] == {"instagram": "https://www.instagram.com/apex/"}
    assert db.save_lead_socials.await_args.args == (1, USER.selected_organization_id)
    release.assert_awaited_once_with(USER.selected_organization_id, "slot-1")


def test_refresh_socials_keeps_links_when_the_scan_fails_temporarily():
    db = _mock_db()
    db.get_lead = AsyncMock(return_value=_lead())
    discover = AsyncMock(
        return_value=SocialScan(
            {}, checked=False, replace_links=False, error="http 503"
        )
    )

    with (
        patch("api.routes.leads.db_client", db),
        patch("api.routes.leads.discover_social_links", discover),
        patch(
            "api.routes.leads.allow_refresh_socials",
            AsyncMock(return_value=True),
        ),
        patch(
            "api.routes.leads.acquire_org_scan_slot",
            AsyncMock(return_value="slot-1"),
        ),
        patch("api.routes.leads.release_org_scan_slot", AsyncMock()),
    ):
        response = _app().post("/leads/1/refresh-socials")

    assert response.status_code == 502
    assert "Stored links were kept" in response.json()["detail"]
    db.save_lead_socials.assert_not_called()
    db.mark_lead_socials_checked.assert_not_called()


def test_refresh_socials_marks_a_robots_denial_as_checked():
    db = _mock_db()
    db.get_lead = AsyncMock(return_value=_lead())
    db.mark_lead_socials_checked = AsyncMock(return_value=_lead())
    discover = AsyncMock(return_value=SocialScan({}, checked=True, replace_links=False))

    with (
        patch("api.routes.leads.db_client", db),
        patch("api.routes.leads.discover_social_links", discover),
        patch(
            "api.routes.leads.allow_refresh_socials",
            AsyncMock(return_value=True),
        ),
        patch(
            "api.routes.leads.acquire_org_scan_slot",
            AsyncMock(return_value="slot-1"),
        ),
        patch("api.routes.leads.release_org_scan_slot", AsyncMock()),
    ):
        response = _app().post("/leads/1/refresh-socials")

    assert response.status_code == 200, response.text
    db.mark_lead_socials_checked.assert_awaited_once()
    db.save_lead_socials.assert_not_called()


def test_refresh_socials_returns_422_for_a_malformed_website():
    db = _mock_db()
    db.get_lead = AsyncMock(return_value=_lead(website="http://[::1"))
    discover = AsyncMock()

    with (
        patch("api.routes.leads.db_client", db),
        patch("api.routes.leads.discover_social_links", discover),
        patch(
            "api.routes.leads.allow_refresh_socials",
            AsyncMock(return_value=True),
        ),
    ):
        response = _app().post("/leads/1/refresh-socials")

    assert response.status_code == 422
    discover.assert_not_awaited()


def test_refresh_socials_fails_closed_when_redis_is_down():
    from api.services.call_concurrency.rate_limiter import RateLimiterUnavailable

    db = _mock_db()
    db.get_lead = AsyncMock(return_value=_lead())
    discover = AsyncMock()

    with (
        patch("api.routes.leads.db_client", db),
        patch("api.routes.leads.discover_social_links", discover),
        patch(
            "api.routes.leads.allow_refresh_socials",
            AsyncMock(side_effect=RateLimiterUnavailable("bucket")),
        ),
    ):
        response = _app().post("/leads/1/refresh-socials")

    assert response.status_code == 503
    discover.assert_not_awaited()


def test_refresh_socials_is_rate_limited_per_user_and_org():
    db = _mock_db()
    db.get_lead = AsyncMock(return_value=_lead())
    discover = AsyncMock()

    with (
        patch("api.routes.leads.db_client", db),
        patch("api.routes.leads.discover_social_links", discover),
        patch(
            "api.routes.leads.allow_refresh_socials",
            AsyncMock(return_value=False),
        ),
    ):
        response = _app().post("/leads/1/refresh-socials")

    assert response.status_code == 429
    discover.assert_not_awaited()


def test_refresh_socials_caps_concurrent_scans_per_org():
    db = _mock_db()
    db.get_lead = AsyncMock(return_value=_lead())
    discover = AsyncMock()

    with (
        patch("api.routes.leads.db_client", db),
        patch("api.routes.leads.discover_social_links", discover),
        patch(
            "api.routes.leads.allow_refresh_socials",
            AsyncMock(return_value=True),
        ),
        patch(
            "api.routes.leads.acquire_org_scan_slot",
            AsyncMock(return_value=None),
        ),
    ):
        response = _app().post("/leads/1/refresh-socials")

    assert response.status_code == 429
    discover.assert_not_awaited()


def test_refresh_socials_returns_504_when_the_scan_times_out():
    db = _mock_db()
    db.get_lead = AsyncMock(return_value=_lead())
    db.mark_lead_socials_checked = AsyncMock()

    async def slow_scan(*args, **kwargs):
        import asyncio

        await asyncio.sleep(10)

    with (
        patch("api.routes.leads.db_client", db),
        patch("api.routes.leads.discover_social_links", slow_scan),
        patch("api.routes.leads.SCAN_TIMEOUT_SECONDS", 0.01),
        patch(
            "api.routes.leads.allow_refresh_socials",
            AsyncMock(return_value=True),
        ),
        patch(
            "api.routes.leads.acquire_org_scan_slot",
            AsyncMock(return_value="slot-1"),
        ),
        patch("api.routes.leads.release_org_scan_slot", AsyncMock()),
    ):
        response = _app().post("/leads/1/refresh-socials")

    assert response.status_code == 504
    db.mark_lead_socials_checked.assert_not_called()


def test_refresh_socials_requires_a_lead_website():
    db = _mock_db()
    db.get_lead = AsyncMock(return_value=_lead(website=None))

    with patch("api.routes.leads.db_client", db):
        response = _app().post("/leads/1/refresh-socials")

    assert response.status_code == 400
    db.save_lead_socials.assert_not_called()


def test_refresh_socials_is_scoped_to_the_selected_organization():
    db = _mock_db()
    db.get_lead = AsyncMock(return_value=None)

    with patch("api.routes.leads.db_client", db):
        response = _app().post("/leads/99/refresh-socials")

    assert response.status_code == 404
    db.get_lead.assert_awaited_once_with(99, USER.selected_organization_id)
