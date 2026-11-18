from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.leads import router
from api.services.auth.depends import get_user
from api.services.leads.dispatch import DispatchQuotaError

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
        "email": "hello@apex.example.com",
        "website": "https://apex.example.com",
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


def test_meta_lists_crm_statuses_formats_and_providers():
    with patch("api.routes.leads.db_client", MagicMock()):
        response = _app().get("/leads/meta")

    assert response.status_code == 200
    payload = response.json()
    assert payload["statuses"][0] == "new"
    assert "do_not_call" in payload["statuses"]
    assert payload["export_formats"] == ["csv", "xlsx", "json", "pdf", "png"]
    assert "google_maps" in payload["providers"]


def test_create_search_enqueues_worker_and_scopes_to_organization():
    db = MagicMock()
    db.create_lead_search = AsyncMock(return_value=_search())
    db.update_lead_search = AsyncMock(return_value=_search())
    db.get_lead_search = AsyncMock(return_value=_search())

    with patch("api.routes.leads.db_client", db):
        with patch("api.routes.leads.enqueue_job", new=AsyncMock()) as enqueue:
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
    with patch("api.routes.leads.db_client", MagicMock()):
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
    db = MagicMock()
    db.create_lead_search = AsyncMock(return_value=_search())
    db.update_lead_search = AsyncMock(return_value=_search())

    with (
        patch("api.routes.leads.db_client", db),
        patch(
            "api.routes.leads.enqueue_job",
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
    db = MagicMock()
    db.get_lead_search = AsyncMock(return_value=_search(status="completed"))

    with patch("api.routes.leads.db_client", db):
        response = _app().post("/leads/searches/21/cancel")

    assert response.status_code == 400
    assert "completed" in response.json()["detail"]


def test_create_search_passes_target_and_auto_call_options():
    db = MagicMock()
    db.create_lead_search = AsyncMock(return_value=_search(target_results=10))
    db.update_lead_search = AsyncMock(return_value=_search(target_results=10))
    db.get_lead_search = AsyncMock(return_value=_search(target_results=10))

    with (
        patch("api.routes.leads.db_client", db),
        patch("api.routes.leads.enqueue_job", new=AsyncMock()),
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
    with patch("api.routes.leads.db_client", MagicMock()):
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


def test_stop_marks_an_active_search_as_stopping():
    db = MagicMock()
    db.get_lead_search = AsyncMock(return_value=_search(status="running"))
    db.update_lead_search = AsyncMock(return_value=_search(status="stopping"))

    with patch("api.routes.leads.db_client", db):
        response = _app().post("/leads/searches/21/stop")

    assert response.status_code == 200
    assert response.json()["status"] == "stopping"
    assert db.update_lead_search.await_args.kwargs["status"] == "stopping"


def test_stop_rejects_a_finished_search():
    db = MagicMock()
    db.get_lead_search = AsyncMock(return_value=_search(status="completed"))

    with patch("api.routes.leads.db_client", db):
        response = _app().post("/leads/searches/21/stop")

    assert response.status_code == 400


def test_list_leads_passes_filters_and_org_scope():
    db = MagicMock()
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
    db = MagicMock()
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
    db = MagicMock()
    db.list_leads = AsyncMock(return_value=([], 0))

    with patch("api.routes.leads.db_client", db):
        response = _app().get("/leads/export?format=csv")

    assert response.status_code == 400
    assert response.json()["detail"] == "No leads match these filters"


def test_bulk_update_records_one_status_activity_per_changed_lead():
    db = MagicMock()
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
        patch("api.routes.leads.db_client", MagicMock()),
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
    db = MagicMock()
    db.get_lead = AsyncMock(return_value=_lead())
    db.list_lead_activities = AsyncMock(return_value=[])
    db.list_lead_calls = AsyncMock(return_value=[])
    db.list_lead_search_links = AsyncMock(return_value=[])

    with patch("api.routes.leads.db_client", db):
        response = _app().get("/leads/1")

    assert response.status_code == 200
    assert response.json()["lead"]["id"] == 1
    db.get_lead.assert_awaited_once_with(1, 11)


def test_patch_status_change_appends_activity():
    db = MagicMock()
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
    db = MagicMock()
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
    db = MagicMock()
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
    db = MagicMock()
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
    db = MagicMock()
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
    db = MagicMock()
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
        patch("api.routes.leads.enqueue_job", new=AsyncMock()),
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
    db = MagicMock()
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
    db = MagicMock()
    db.get_lead_search = AsyncMock(return_value=search)
    db.update_lead_search = AsyncMock(return_value=updated)
    db.clear_search_results = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        with patch("api.routes.leads.enqueue_job", new=AsyncMock()) as enqueue:
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
    db = MagicMock()
    db.get_lead_search = AsyncMock(return_value=search)
    db.update_lead_search = AsyncMock(
        return_value=_search(status="queued", results_count=0)
    )
    db.clear_search_results = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        with patch("api.routes.leads.enqueue_job", new=AsyncMock()):
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
    db = MagicMock()
    db.get_lead_search = AsyncMock(return_value=search)
    db.update_lead_search = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        with patch("api.routes.leads.enqueue_job", new=AsyncMock()) as enqueue:
            response = _app().patch(
                "/leads/searches/21", json=_edit_payload(target_results=50)
            )

    assert response.status_code == 400
    assert "already has 80 leads" in response.json()["detail"]
    db.update_lead_search.assert_not_awaited()
    enqueue.assert_not_awaited()


def test_update_search_rejects_while_running():
    db = MagicMock()
    db.get_lead_search = AsyncMock(return_value=_search(status="running"))

    with patch("api.routes.leads.db_client", db):
        response = _app().patch("/leads/searches/21", json=_edit_payload())

    assert response.status_code == 400
    assert "Stop or cancel" in response.json()["detail"]


def test_extend_search_raises_target_and_scraper_depth():
    search = _search(
        status="completed",
        target_results=50,
        results_count=12,
        params={"depth": 3, "website_filter": "no_website"},
    )
    updated = _search(status="queued", target_results=250)
    db = MagicMock()
    db.get_lead_search = AsyncMock(return_value=search)
    db.update_lead_search = AsyncMock(return_value=updated)

    with patch("api.routes.leads.db_client", db):
        with patch("api.routes.leads.enqueue_job", new=AsyncMock()) as enqueue:
            response = _app().post(
                "/leads/searches/21/extend", json={"additional_results": 200}
            )

    assert response.status_code == 200, response.text
    kwargs = db.update_lead_search.await_args.kwargs
    assert kwargs["target_results"] == 250
    # 250 results need 13 pages at ~20 per page; filters are preserved.
    assert kwargs["params"]["depth"] == 13
    assert kwargs["params"]["website_filter"] == "no_website"
    assert kwargs["status"] == "queued"
    assert kwargs["external_job_id"] is None
    enqueue.assert_awaited_once_with("run_lead_search", 21)


def test_extend_search_cannot_exceed_the_result_limit():
    search = _search(status="completed", target_results=900, results_count=900)
    db = MagicMock()
    db.get_lead_search = AsyncMock(return_value=search)
    db.update_lead_search = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        with patch("api.routes.leads.enqueue_job", new=AsyncMock()) as enqueue:
            response = _app().post(
                "/leads/searches/21/extend", json={"additional_results": 200}
            )

    assert response.status_code == 400
    assert "above the limit" in response.json()["detail"]
    db.update_lead_search.assert_not_awaited()
    enqueue.assert_not_awaited()


def test_list_leads_and_stats_accept_a_project_scope():
    db = MagicMock()
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
    db = MagicMock()
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
    db = MagicMock()
    db.list_leads = AsyncMock(return_value=([_lead()], 1))
    db.get_lead_project = AsyncMock(return_value=None)

    with patch("api.routes.leads.db_client", db):
        response = _app().get("/leads/export?format=csv&project_id=99")

    assert response.status_code == 404


def test_patch_contact_fields_normalizes_phone_and_logs_an_edit():
    db = MagicMock()
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


def test_patch_rejects_clearing_the_business_name():
    db = MagicMock()
    db.get_lead = AsyncMock(return_value=_lead())

    with patch("api.routes.leads.db_client", db):
        response = _app().patch("/leads/1", json={"title": "  "})

    assert response.status_code == 422
    db.update_lead.assert_not_called()


def test_bulk_update_can_append_tags_without_replacing_them():
    db = MagicMock()
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
    db = MagicMock()
    db.list_leads = AsyncMock(return_value=([_lead(email_consent="opted_in")], 1))

    with patch("api.routes.leads.db_client", db):
        response = _app().get("/leads?email_consent=opted_in")

    assert response.status_code == 200, response.text
    assert response.json()["leads"][0]["email_consent"] == "opted_in"
    assert db.list_leads.await_args.kwargs["email_consent"] == "opted_in"


def test_recording_email_consent_requires_a_written_reason_for_opt_in():
    db = MagicMock()
    db.get_lead = AsyncMock(return_value=_lead())

    with patch("api.routes.leads.db_client", db):
        response = _app().patch(
            "/leads/1/email-consent",
            json={"email_consent": "opted_in", "reason": "   "},
        )

    assert response.status_code == 422
    db.update_lead.assert_not_called()


def test_recording_email_consent_stores_source_time_and_warning():
    db = MagicMock()
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
    assert "anti-spam" in activity["extra_metadata"]["warning"]
    # The audit row is committed with the consent change, not afterwards.
    db.add_lead_activity.assert_not_called()


def test_recording_email_consent_can_opt_out_without_a_reason():
    db = MagicMock()
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


def test_clearing_email_consent_removes_source_and_timestamp():
    db = MagicMock()
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
    db = MagicMock()
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
    db = MagicMock()
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
    with patch("api.routes.leads.db_client", MagicMock()):
        response = _app().post(
            "/leads/email-consent/bulk",
            json={"lead_ids": [1], "email_consent": "opted_in"},
        )

    assert response.status_code == 422


def test_editing_the_email_resets_consent_in_the_same_update():
    db = MagicMock()
    db.get_lead = AsyncMock(
        return_value=_lead(
            email="old@apex.example.com",
            email_consent="opted_in",
            email_consent_source="manual",
            email_consent_at=NOW,
        )
    )
    db.update_lead = AsyncMock(
        return_value=_lead(email="new@apex.example.com", email_consent="unknown")
    )
    db.add_lead_activity = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        response = _app().patch("/leads/1", json={"email": "new@apex.example.com"})

    assert response.status_code == 200, response.text
    values = db.update_lead.await_args.kwargs
    assert values["email"] == "new@apex.example.com"
    assert values["email_consent"] == "unknown"
    assert values["email_consent_source"] is None
    assert values["email_consent_at"] is None
    activity = values["activities"][0]
    assert activity["extra_metadata"]["reason"] == "email_changed"
    assert activity["extra_metadata"]["from"] == "opted_in"
    assert activity["extra_metadata"]["source"] == "manual"
    assert "old@apex.example.com" in activity["body"]
    assert "new@apex.example.com" in activity["body"]


def test_editing_only_the_email_case_keeps_consent():
    db = MagicMock()
    db.get_lead = AsyncMock(return_value=_lead(email="Hello@Apex.example.com"))
    db.update_lead = AsyncMock(return_value=_lead(email="hello@apex.example.com"))
    db.add_lead_activity = AsyncMock()

    with patch("api.routes.leads.db_client", db):
        response = _app().patch("/leads/1", json={"email": "hello@apex.example.com"})

    assert response.status_code == 200, response.text
    values = db.update_lead.await_args.kwargs
    assert "email_consent" not in values
    assert values["activities"] is None
