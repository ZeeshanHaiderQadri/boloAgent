"""Lead projects (saved lists): route contract and real DB behaviour."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.lead_projects import router
from api.services.auth.depends import get_user

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
USER = SimpleNamespace(id=3, selected_organization_id=11)


def _app():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user] = lambda: USER
    return TestClient(app)


def _project(**overrides):
    values = {
        "id": 5,
        "name": "US coffee shops",
        "organization_id": 11,
        "created_by": 3,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# ---------------------------------------------------------------------------
# Route contract (mocked client)
# ---------------------------------------------------------------------------
def test_list_projects_includes_counts():
    db = MagicMock()
    db.list_lead_projects = AsyncMock(return_value=[(_project(), 25, 18)])

    with patch("api.routes.lead_projects.db_client", db):
        response = _app().get("/lead-projects")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["projects"][0] == {
        "id": 5,
        "name": "US coffee shops",
        "lead_count": 25,
        "callable_count": 18,
        "created_at": NOW.isoformat().replace("+00:00", "Z"),
        "updated_at": NOW.isoformat().replace("+00:00", "Z"),
    }
    db.list_lead_projects.assert_awaited_once_with(11)


def test_create_project_is_org_scoped_and_returns_201():
    db = MagicMock()
    db.get_lead_project_by_name = AsyncMock(return_value=None)
    db.create_lead_project = AsyncMock(return_value=_project())
    db.get_lead_project = AsyncMock(return_value=(_project(), 0, 0))

    with patch("api.routes.lead_projects.db_client", db):
        response = _app().post("/lead-projects", json={"name": "US coffee shops"})

    assert response.status_code == 201, response.text
    db.get_lead_project_by_name.assert_awaited_once_with(11, "US coffee shops")
    assert db.create_lead_project.await_args.args == (11, "US coffee shops")
    assert db.create_lead_project.await_args.kwargs["created_by"] == 3
    db.assign_leads_to_project_by_filters.assert_not_called()


def test_create_project_with_filters_saves_the_exact_filtered_set():
    db = MagicMock()
    db.get_lead_project_by_name = AsyncMock(return_value=None)
    db.create_lead_project = AsyncMock(return_value=_project())
    db.assign_leads_to_project_by_filters = AsyncMock(return_value=25)
    db.get_lead_project = AsyncMock(return_value=(_project(), 25, 25))

    with patch("api.routes.lead_projects.db_client", db):
        response = _app().post(
            "/lead-projects",
            json={
                "name": "UK coffee shops",
                "filters": {
                    "q": "coffee",
                    "status": "new",
                    "has_website": False,
                    "country": "GB",
                    "tag": "menu-ready",
                },
            },
        )

    assert response.status_code == 201, response.text
    assert response.json()["lead_count"] == 25
    kwargs = db.assign_leads_to_project_by_filters.await_args.kwargs
    assert kwargs == {
        "search_id": None,
        "crm_status": "new",
        "query": "coffee",
        "has_phone": None,
        "has_website": False,
        "has_email": None,
        "country": "GB",
        "state": None,
        "city": None,
        "category": None,
        "tag": "menu-ready",
    }


def test_duplicate_project_name_is_a_409():
    db = MagicMock()
    db.get_lead_project_by_name = AsyncMock(return_value=_project())

    with patch("api.routes.lead_projects.db_client", db):
        response = _app().post("/lead-projects", json={"name": "us COFFEE shops"})

    assert response.status_code == 409
    db.create_lead_project.assert_not_called()


def test_rename_rejects_a_name_taken_by_another_project():
    db = MagicMock()
    db.get_lead_project = AsyncMock(return_value=(_project(), 0, 0))
    db.get_lead_project_by_name = AsyncMock(return_value=_project(id=9, name="Taken"))

    with patch("api.routes.lead_projects.db_client", db):
        response = _app().patch("/lead-projects/5", json={"name": "Taken"})

    assert response.status_code == 409
    db.rename_lead_project.assert_not_called()


def test_rename_to_same_name_is_allowed():
    db = MagicMock()
    db.get_lead_project = AsyncMock(return_value=(_project(), 0, 0))
    db.get_lead_project_by_name = AsyncMock(return_value=_project())
    db.rename_lead_project = AsyncMock(return_value=_project())

    with patch("api.routes.lead_projects.db_client", db):
        response = _app().patch("/lead-projects/5", json={"name": "US coffee shops"})

    assert response.status_code == 200
    db.rename_lead_project.assert_awaited_once_with(5, 11, "US coffee shops")


def test_delete_project_unfiles_leads_without_deleting_them():
    db = MagicMock()
    db.delete_lead_project = AsyncMock(return_value=25)

    with patch("api.routes.lead_projects.db_client", db):
        response = _app().delete("/lead-projects/5")

    assert response.status_code == 200
    assert response.json() == {"deleted": True, "unassigned": 25}
    db.delete_lead_project.assert_awaited_once_with(5, 11)


def test_unknown_project_is_a_404():
    db = MagicMock()
    db.get_lead_project = AsyncMock(return_value=None)
    db.delete_lead_project = AsyncMock(return_value=None)

    with patch("api.routes.lead_projects.db_client", db):
        assert _app().get("/lead-projects/99").status_code == 404
        assert _app().delete("/lead-projects/99").status_code == 404


def test_assign_leads_by_ids_dedupes_and_returns_counts():
    db = MagicMock()
    db.get_lead_project = AsyncMock(return_value=(_project(), 2, 1))
    db.assign_leads_to_project = AsyncMock(return_value=2)

    with patch("api.routes.lead_projects.db_client", db):
        response = _app().post(
            "/lead-projects/5/leads", json={"lead_ids": [7, 8, 7]}
        )

    assert response.status_code == 200
    assert response.json()["assigned"] == 2
    assert response.json()["project"]["lead_count"] == 2
    db.assign_leads_to_project.assert_awaited_once_with(11, 5, [7, 8])


def test_assign_by_filters_maps_schema_onto_client_filters():
    db = MagicMock()
    db.get_lead_project = AsyncMock(return_value=(_project(), 4, 4))
    db.assign_leads_to_project_by_filters = AsyncMock(return_value=4)

    with patch("api.routes.lead_projects.db_client", db):
        response = _app().post(
            "/lead-projects/5/assign",
            json={"category": "Coffee shop", "has_phone": True, "q": "usa"},
        )

    assert response.status_code == 200
    assert response.json()["assigned"] == 4
    kwargs = db.assign_leads_to_project_by_filters.await_args.kwargs
    assert kwargs["category"] == "Coffee shop"
    assert kwargs["has_phone"] is True
    assert kwargs["query"] == "usa"


def test_unassign_leads_returns_removed_count():
    db = MagicMock()
    db.get_lead_project = AsyncMock(return_value=(_project(), 3, 3))
    db.unassign_leads_from_project = AsyncMock(return_value=2)

    with patch("api.routes.lead_projects.db_client", db):
        response = _app().request(
            "DELETE", "/lead-projects/5/leads", json={"lead_ids": [7, 8]}
        )

    assert response.status_code == 200
    assert response.json() == {"removed": 2}
    db.unassign_leads_from_project.assert_awaited_once_with(11, 5, [7, 8])


# ---------------------------------------------------------------------------
# Real PostgreSQL behaviour
# ---------------------------------------------------------------------------
def _row(title, place_id, phone="+14155550142", **overrides):
    from api.services.leads.normalization import normalize_lead_row

    raw = {
        "title": title,
        "category": "Coffee shop",
        "address": "1 Main St",
        "city": "Austin",
        "state": "TX",
        "country": "US",
        "phone": phone,
        "place_id": place_id,
        "review_rating": "4.6",
        "review_count": "10",
        **overrides,
    }
    lead = normalize_lead_row(raw, country_hint="US", source="demo")
    assert lead is not None
    return lead


async def _org(db_client):
    user, _ = await db_client.get_or_create_user_by_provider_id(
        f"projects-{uuid.uuid4().hex}"
    )
    org, _ = await db_client.get_or_create_organization_by_provider_id(
        f"projects-{uuid.uuid4().hex}", user.id
    )
    return user, org


async def _seed_leads(db_client, user, org, rows):
    search = await db_client.create_lead_search(
        organization_id=org.id,
        created_by=user.id,
        name="Coffee",
        category="Coffee",
        location="Austin",
        country_code="US",
        language="en",
        provider="demo",
        params={"depth": 2},
    )
    await db_client.upsert_leads_for_search(
        search_id=search.id, organization_id=org.id, leads=rows
    )
    leads, _ = await db_client.list_leads(org.id)
    return search, {lead.title: lead for lead in leads}


@pytest.mark.asyncio
async def test_project_counts_and_org_scoping(db_session):
    user, org = await _org(db_session)
    _search, leads = await _seed_leads(
        db_session,
        user,
        org,
        [_row("Apex Coffee", "p1"), _row("Cedar Coffee", "p2", phone=None)],
    )
    project = await db_session.create_lead_project(org.id, "Austin coffee")

    ids = [lead.id for lead in leads.values()]
    assert await db_session.assign_leads_to_project(org.id, project.id, ids) == 2

    rows = await db_session.list_lead_projects(org.id)
    assert [(row[0].name, row[1], row[2]) for row in rows] == [
        ("Austin coffee", 2, 1)
    ]

    _other_user, other_org = await _org(db_session)
    assert await db_session.list_lead_projects(other_org.id) == []
    assert await db_session.get_lead_project(project.id, other_org.id) is None
    # Another org cannot see or move these leads through the project endpoint.
    assert (
        await db_session.assign_leads_to_project(other_org.id, project.id, ids) == 0
    )


@pytest.mark.asyncio
async def test_one_project_per_lead_moves_instead_of_copying(db_session):
    user, org = await _org(db_session)
    _search, leads = await _seed_leads(
        db_session, user, org, [_row("Apex Coffee", "p1")]
    )
    us = await db_session.create_lead_project(org.id, "US")
    uk = await db_session.create_lead_project(org.id, "UK")
    lead_id = leads["Apex Coffee"].id

    await db_session.assign_leads_to_project(org.id, us.id, [lead_id])
    assert await db_session.assign_leads_to_project(org.id, uk.id, [lead_id]) == 1

    us_row = await db_session.get_lead_project(us.id, org.id)
    uk_row = await db_session.get_lead_project(uk.id, org.id)
    assert us_row[1] == 0
    assert uk_row[1] == 1
    # The row itself is the same CRM record, not a duplicate.
    assert len((await db_session.list_leads(org.id))[0]) == 1
    assert len((await db_session.list_leads(org.id, project_id=uk.id))[0]) == 1


@pytest.mark.asyncio
async def test_assign_by_filters_uses_the_same_predicates_as_the_crm(db_session):
    user, org = await _org(db_session)
    _search, leads = await _seed_leads(
        db_session,
        user,
        org,
        [
            _row("Apex Coffee", "p1", website="https://apex.example.com"),
            _row("Cedar Coffee", "p2", phone=None, website=None),
            _row(
                "Diner",
                "p3",
                category="Diner",
                website="https://diner.example.com",
            ),
        ],
    )
    project = await db_session.create_lead_project(org.id, "No website")

    assigned = await db_session.assign_leads_to_project_by_filters(
        org.id, project.id, has_website=False
    )
    assert assigned == 1
    listed = (await db_session.list_leads(org.id, project_id=project.id))[0]
    assert [lead.title for lead in listed] == ["Cedar Coffee"]

    # Excluding already-filed leads works through the project filter too.
    other = await db_session.create_lead_project(org.id, "All coffee")
    assigned = await db_session.assign_leads_to_project_by_filters(
        org.id, other.id, category="Coffee shop"
    )
    assert assigned == 2
    assert (await db_session.get_lead_project(project.id, org.id))[1] == 0


@pytest.mark.asyncio
async def test_project_stats_export_scope_and_unassign(db_session):
    user, org = await _org(db_session)
    search, leads = await _seed_leads(
        db_session, user, org, [_row("Apex Coffee", "p1"), _row("Cedar Coffee", "p2")]
    )
    project = await db_session.create_lead_project(org.id, "Coffee")
    lead_id = leads["Apex Coffee"].id
    await db_session.assign_leads_to_project(org.id, project.id, [lead_id])

    stats = await db_session.lead_stats(org.id, project_id=project.id)
    assert stats["total"] == 1
    assert stats["with_phone"] == 1
    by_search = await db_session.lead_stats(org.id, search_id=search.id)
    assert by_search["total"] == 2

    assert await db_session.unassign_leads_from_project(org.id, project.id, [lead_id]) == 1
    assert (await db_session.get_lead_project(project.id, org.id))[1] == 0


@pytest.mark.asyncio
async def test_deleting_a_project_unfiles_leads_and_frees_the_name(db_session):
    user, org = await _org(db_session)
    _search, leads = await _seed_leads(
        db_session, user, org, [_row("Apex Coffee", "p1")]
    )
    project = await db_session.create_lead_project(org.id, "Temp")
    await db_session.assign_leads_to_project(org.id, project.id, [leads["Apex Coffee"].id])

    assert await db_session.delete_lead_project(project.id, org.id) == 1
    assert await db_session.get_lead_project(project.id, org.id) is None
    remaining, total = await db_session.list_leads(org.id)
    assert total == 1
    assert remaining[0].project_id is None

    recreated = await db_session.create_lead_project(org.id, "Temp")
    assert recreated.id != project.id


@pytest.mark.asyncio
async def test_add_tags_is_additive_deduped_and_capped(db_session):
    user, org = await _org(db_session)
    _search, leads = await _seed_leads(
        db_session, user, org, [_row("Apex Coffee", "p1")]
    )
    lead_id = leads["Apex Coffee"].id
    await db_session.update_lead(lead_id, org.id, tags=["hot"])

    assert await db_session.add_tags_to_leads(org.id, [lead_id], ["HOT", "menu"]) == 1
    refreshed = await db_session.get_lead(lead_id, org.id)
    assert refreshed.tags == ["hot", "menu"]

    # Idempotent: nothing new to add.
    assert await db_session.add_tags_to_leads(org.id, [lead_id], ["menu"]) == 0
    refreshed = await db_session.get_lead(lead_id, org.id)
    assert refreshed.tags == ["hot", "menu"]

    # Another organization cannot tag this lead.
    _other_user, other_org = await _org(db_session)
    assert await db_session.add_tags_to_leads(other_org.id, [lead_id], ["x"]) == 0
