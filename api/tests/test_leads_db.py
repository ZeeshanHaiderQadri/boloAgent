"""Real PostgreSQL coverage for the lead client: dedupe, CRM fields, filters."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.db.models import LeadSearchResultModel
from api.services.leads.normalization import normalize_lead_row


async def _org(db_client):
    user, _ = await db_client.get_or_create_user_by_provider_id(
        f"leads-{uuid.uuid4().hex}"
    )
    org, _ = await db_client.get_or_create_organization_by_provider_id(
        f"leads-{uuid.uuid4().hex}", user.id
    )
    return user, org


def _row(title, place_id, phone="+14155550142", **overrides):
    raw = {
        "title": title,
        "category": "Software company",
        "address": "123 Market St",
        "city": "San Francisco",
        "state": "CA",
        "country": "US",
        "phone": phone,
        "place_id": place_id,
        "review_rating": "4.6",
        "review_count": "128",
        **overrides,
    }
    lead = normalize_lead_row(raw, country_hint="US", source="demo")
    assert lead is not None
    return lead


async def _search(db_client, user, org):
    return await db_client.create_lead_search(
        organization_id=org.id,
        created_by=user.id,
        name="Software in California",
        category="Software",
        location="California, US",
        country_code="US",
        language="en",
        provider="demo",
        params={"depth": 8},
    )


@pytest.mark.asyncio
async def test_upsert_dedupes_within_and_across_searches(db_session):
    user, org = await _org(db_session)
    search = await _search(db_session, user, org)

    first = await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[_row("Apex Labs", "p1"), _row("Apex Labs duplicate", "p1")],
    )
    assert first == {
        "results_count": 1,
        "new_leads_count": 1,
        "updated_leads_count": 0,
    }

    second = await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[
            _row("Apex Labs renamed", "p1"),
            _row("Cedar Works", "p2"),
        ],
    )
    assert second == {
        "results_count": 2,
        "new_leads_count": 1,
        "updated_leads_count": 1,
    }

    leads, total = await db_session.list_leads(org.id, search_id=search.id)
    assert total == 2
    assert {lead.title for lead in leads} == {"Apex Labs renamed", "Cedar Works"}

    other_org_user, other_org = await _org(db_session)
    other_search = await _search(db_session, other_org_user, other_org)
    await db_session.upsert_leads_for_search(
        search_id=other_search.id,
        organization_id=other_org.id,
        leads=[_row("Apex Labs", "p1")],
    )
    # Same place id in another organization must be a separate lead.
    other_leads, other_total = await db_session.list_leads(other_org.id)
    assert other_total == 1
    assert other_leads[0].id not in {lead.id for lead in leads}


@pytest.mark.asyncio
async def test_upsert_preserves_crm_fields_and_refreshes_business_fields(db_session):
    user, org = await _org(db_session)
    search = await _search(db_session, user, org)
    await db_session.upsert_leads_for_search(
        search_id=search.id, organization_id=org.id, leads=[_row("Apex Labs", "p1")]
    )
    lead = (await db_session.list_leads(org.id))[0][0]
    await db_session.update_lead(
        lead.id,
        org.id,
        crm_status="interested",
        notes="Called once",
        tags=["priority"],
    )

    await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[_row("Apex Labs New Name", "p1", review_count="200")],
    )
    # The test fixture reuses one session with expire_on_commit=False, so force
    # a reload to see committed values the way a fresh request session would.
    async with db_session.async_session() as session:
        await session.refresh(lead)
    refreshed = await db_session.get_lead(lead.id, org.id)
    assert refreshed.title == "Apex Labs New Name"
    assert refreshed.review_count == 200
    assert refreshed.crm_status == "interested"
    assert refreshed.notes == "Called once"
    assert refreshed.tags == ["priority"]


@pytest.mark.asyncio
async def test_filters_search_phone_tag_and_status(db_session):
    user, org = await _org(db_session)
    search = await _search(db_session, user, org)
    await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[
            _row("Apex Labs", "p1"),
            _row("Cedar Works", "p2", phone=None),
        ],
    )
    with_phone = (await db_session.list_leads(org.id, has_phone=True))[0]
    assert [lead.title for lead in with_phone] == ["Apex Labs"]

    by_text = (await db_session.list_leads(org.id, query="cedar"))[0]
    assert [lead.title for lead in by_text] == ["Cedar Works"]

    await db_session.update_lead(with_phone[0].id, org.id, crm_status="do_not_call")
    assert len((await db_session.list_leads(org.id, crm_status="do_not_call"))[0]) == 1

    await db_session.update_lead(with_phone[0].id, org.id, tags=["enterprise"])
    tagged = (await db_session.list_leads(org.id, tag="enterprise"))[0]
    assert [lead.id for lead in tagged] == [with_phone[0].id]


@pytest.mark.asyncio
async def test_stats_activities_and_search_links(db_session):
    user, org = await _org(db_session)
    search = await _search(db_session, user, org)
    summary = await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[_row("Apex Labs", "p1"), _row("Cedar Works", "p2", phone=None)],
    )
    assert summary["new_leads_count"] == 2

    await db_session.update_lead_search(
        search.id,
        org.id,
        status="completed",
        results_count=2,
        new_leads_count=2,
    )
    stats = await db_session.lead_stats(org.id)
    assert stats["total"] == 2
    assert stats["with_phone"] == 1
    assert stats["searches"] == 1

    lead = (await db_session.list_leads(org.id, has_phone=True))[0][0]
    await db_session.add_lead_activity(
        organization_id=org.id,
        lead_id=lead.id,
        user_id=user.id,
        activity_type="note",
        title="Note",
        body="Follow up Monday",
    )
    activities = await db_session.list_lead_activities(lead.id, org.id)
    assert activities[0].body == "Follow up Monday"
    assert activities[0].extra_metadata == {}
    links = await db_session.list_lead_search_links(lead.id, org.id)
    assert [link.id for link in links] == [search.id]


@pytest.mark.asyncio
async def test_call_history_query_compiles_for_lead_without_dispatches(db_session):
    _user, org = await _org(db_session)
    # Regression: the dispatch→queued→run→campaign join needs an explicit
    # select_from, otherwise SQLAlchemy raises an ambiguous-FROM error.
    assert await db_session.list_lead_calls(987654, org.id) == []


@pytest.mark.asyncio
async def test_search_target_and_incremental_result_count(db_session):
    user, org = await _org(db_session)
    search = await db_session.create_lead_search(
        organization_id=org.id,
        created_by=user.id,
        name="Coffee in Sacramento",
        category="Coffee",
        location="Sacramento",
        country_code="US",
        language="en",
        provider="demo",
        params={"target_results": 2},
        target_results=2,
    )
    assert search.target_results == 2

    await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[_row("Apex Coffee", "p1")],
    )
    assert await db_session.count_search_results(search.id) == 1
    await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[_row("Apex Coffee", "p1"), _row("Cedar Bakery", "p2")],
    )
    assert await db_session.count_search_results(search.id) == 2


@pytest.mark.asyncio
async def test_pending_call_outcome_query_compiles(db_session):
    # Same join as call history, plus the JSONB anti-join on call activities.
    assert await db_session.list_pending_lead_call_outcomes() == []


@pytest.mark.asyncio
async def test_upsert_keeps_the_existing_email_when_a_later_scrape_finds_none(
    db_session,
):
    user, org = await _org(db_session)
    search = await _search(db_session, user, org)
    await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[_row("Apex Labs", "p1", email="sales@apex-software.com")],
    )
    lead = (await db_session.list_leads(org.id))[0][0]
    assert lead.email == "sales@apex-software.com"

    for missing in (None, ""):
        await db_session.upsert_leads_for_search(
            search_id=search.id,
            organization_id=org.id,
            leads=[_row("Apex Labs", "p1", email=missing)],
        )
        refreshed = await db_session.get_lead(lead.id, org.id)
        assert refreshed.email == "sales@apex-software.com"


@pytest.mark.asyncio
async def test_email_consent_survives_scraper_refresh_and_filters(db_session):
    user, org = await _org(db_session)
    search = await _search(db_session, user, org)
    await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[_row("Apex Labs", "p1"), _row("Cedar Works", "p2")],
    )
    leads = {lead.title: lead for lead in (await db_session.list_leads(org.id))[0]}

    recorded_at = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
    await db_session.update_lead(
        leads["Apex Labs"].id,
        org.id,
        email_consent="opted_in",
        email_consent_source="manual",
        email_consent_at=recorded_at,
    )
    await db_session.update_lead(
        leads["Cedar Works"].id,
        org.id,
        email_consent="opted_out",
        email_consent_source="bulk",
        email_consent_at=recorded_at,
    )

    # A later scraper run refreshes business fields but must never overwrite a
    # consent decision.
    await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[
            _row("Apex Labs Renamed", "p1", review_count="999"),
            _row("Cedar Works", "p2"),
        ],
    )
    refreshed = await db_session.get_lead(leads["Apex Labs"].id, org.id)
    assert refreshed.email_consent == "opted_in"
    assert refreshed.email_consent_source == "manual"
    assert refreshed.email_consent_at == recorded_at

    opted_in, opted_in_total = await db_session.list_leads(
        org.id, email_consent="opted_in"
    )
    assert opted_in_total == 1
    assert [lead.id for lead in opted_in] == [leads["Apex Labs"].id]

    opted_out, opted_out_total = await db_session.list_leads(
        org.id, email_consent="opted_out"
    )
    assert opted_out_total == 1
    assert [lead.id for lead in opted_out] == [leads["Cedar Works"].id]

    unknown, unknown_total = await db_session.list_leads(
        org.id, email_consent="unknown"
    )
    assert unknown_total == 0
    assert unknown == []


@pytest.mark.asyncio
async def test_scraper_email_change_resets_consent_and_records_why(db_session):
    user, org = await _org(db_session)
    search = await _search(db_session, user, org)
    await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[_row("Apex Labs", "p1", email="old@apex-software-demo.com")],
    )
    lead = (await db_session.list_leads(org.id))[0][0]
    await db_session.update_lead(
        lead.id,
        org.id,
        email_consent="opted_in",
        email_consent_source="manual",
        email_consent_at=datetime(2026, 11, 17, 9, 30, tzinfo=UTC),
    )

    # The business now publishes a different address. The decision recorded for
    # the old mailbox cannot be reused for the new one.
    await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[_row("Apex Labs", "p1", email="new@apex-software-demo.com")],
    )

    lead_id = lead.id
    org_id = org.id
    refreshed = await db_session.get_lead(lead_id, org_id)
    # The scraper writes through raw SQL, so drop the ORM's cached copy before
    # checking what actually landed in the database.
    inspect(refreshed).session.expire_all()
    refreshed = await db_session.get_lead(lead_id, org_id)
    assert refreshed.email == "new@apex-software-demo.com"
    assert refreshed.email_consent == "unknown"
    assert refreshed.email_consent_source is None
    assert refreshed.email_consent_at is None

    activities = await db_session.list_lead_activities(lead_id, org_id)
    reset = [
        item
        for item in activities
        if item.extra_metadata.get("reason") == "email_changed"
    ]
    assert len(reset) == 1
    assert reset[0].extra_metadata["source"] == "scraper"
    assert reset[0].extra_metadata["from"] == "opted_in"
    assert reset[0].extra_metadata["previous_email"] == "old@apex-software-demo.com"
    assert reset[0].extra_metadata["new_email"] == "new@apex-software-demo.com"
    assert "old@apex-software-demo.com" in reset[0].body


@pytest.mark.asyncio
async def test_address_change_without_a_prior_decision_is_not_called_a_reset(
    db_session,
):
    user, org = await _org(db_session)
    search = await _search(db_session, user, org)
    await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[
            _row("Apex Labs", "p1", email="old@apex-software-demo.com"),
            # This lead has no address on file at all.
            _row("Cedar Works", "p2"),
        ],
    )
    await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[
            _row("Apex Labs", "p1", email="new@apex-software-demo.com"),
            _row("Cedar Works", "p2", email="hello@cedar-demo.com"),
        ],
    )

    leads, _ = await db_session.list_leads(org.id)
    for lead in leads:
        assert lead.email_consent == "unknown"
        activities = await db_session.list_lead_activities(lead.id, org.id)
        assert not [
            item
            for item in activities
            if item.extra_metadata.get("reason") == "email_changed"
        ]
        changes = [item for item in activities if item.title == "Email address changed"]
        assert len(changes) == 1
        assert changes[0].extra_metadata["source"] == "scraper"


@pytest.mark.asyncio
async def test_first_address_clears_consent_without_a_reset_entry(db_session):
    user, org = await _org(db_session)
    search = await _search(db_session, user, org)
    await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[_row("Apex Labs", "p1")],
    )
    lead = (await db_session.list_leads(org.id))[0][0]
    await db_session.update_lead(
        lead.id,
        org.id,
        email_consent="opted_in",
        email_consent_source="bulk",
        email_consent_at=datetime.now(UTC),
    )

    # The lead had a decision but no address. A discovered address must not
    # inherit that decision, yet the timeline should not claim a reset tied to
    # an address that never existed.
    lead_id = lead.id
    org_id = org.id
    await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org_id,
        leads=[_row("Apex Labs", "p1", email="hello@apex-software-demo.com")],
    )

    refreshed = await db_session.get_lead(lead_id, org_id)
    inspect(refreshed).session.expire_all()
    refreshed = await db_session.get_lead(lead_id, org_id)
    assert refreshed.email == "hello@apex-software-demo.com"
    assert refreshed.email_consent == "unknown"

    activities = await db_session.list_lead_activities(lead_id, org_id)
    assert not [
        item
        for item in activities
        if item.extra_metadata.get("reason") == "email_changed"
    ]
    changes = [item for item in activities if item.title == "Email address changed"]
    assert len(changes) == 1


@pytest.mark.asyncio
async def test_social_enrichment_queue_is_org_scoped_and_tracks_scans(db_session):
    user, org = await _org(db_session)
    other_user, other_org = await _org(db_session)
    search = await _search(db_session, user, org)
    other_search = await _search(db_session, other_user, other_org)

    await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[
            _row("Apex Labs", "p1", website="https://apex-labs.com"),
            _row("Cedar Works", "p2", website=""),
        ],
    )
    await db_session.upsert_leads_for_search(
        search_id=other_search.id,
        organization_id=other_org.id,
        leads=[_row("Other Org Co", "o1", website="https://other.example.com")],
    )

    pending = await db_session.list_leads_needing_socials(org.id, search_id=search.id)

    assert [lead.title for lead in pending] == ["Apex Labs"]
    assert await db_session.count_leads_needing_socials(org.id) == 1

    updated = await db_session.save_lead_socials(
        pending[0].id,
        org.id,
        social_links={"instagram": "https://www.instagram.com/apex/"},
        checked_at=datetime.now(UTC),
    )

    assert updated is not None
    assert updated.social_links == {"instagram": "https://www.instagram.com/apex/"}
    assert updated.socials_checked_at is not None
    assert updated.socials_attempts == 0
    assert await db_session.count_leads_needing_socials(org.id) == 0
    # The other organization's lead was never part of this batch.
    assert await db_session.count_leads_needing_socials(other_org.id) == 1


@pytest.mark.asyncio
async def test_social_failure_keeps_links_and_leaves_checked_at(db_session):
    user, org = await _org(db_session)
    search = await _search(db_session, user, org)
    await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[_row("Apex Labs", "p1", website="https://apex-labs.com")],
    )
    lead = (await db_session.list_leads_needing_socials(org.id, search_id=search.id))[0]
    checked = datetime.now(UTC)
    await db_session.save_lead_socials(
        lead.id,
        org.id,
        social_links={"instagram": "https://www.instagram.com/apex/"},
        checked_at=checked,
    )
    # A later scan failure must not remove the good links or uncheck the lead.
    await db_session.record_lead_socials_failure(
        lead.id,
        org.id,
        next_attempt_at=datetime.now(UTC) + timedelta(hours=1),
    )

    kept = await db_session.get_lead(lead.id, org.id)
    assert kept.social_links == {"instagram": "https://www.instagram.com/apex/"}
    assert kept.socials_checked_at == checked
    assert kept.socials_attempts == 1


@pytest.mark.asyncio
async def test_social_failure_backs_off_a_pending_lead(db_session):
    user, org = await _org(db_session)
    search = await _search(db_session, user, org)
    await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[_row("Cedar Works", "p2", website="https://cedar.example")],
    )
    lead = (await db_session.list_leads_needing_socials(org.id, search_id=search.id))[0]

    await db_session.record_lead_socials_failure(
        lead.id,
        org.id,
        next_attempt_at=datetime.now(UTC) + timedelta(hours=1),
    )

    # Not due again until the backoff expires, and nothing was marked checked.
    assert await db_session.count_leads_needing_socials(org.id) == 0
    assert (await db_session.get_lead(lead.id, org.id)).socials_checked_at is None

    await db_session.record_lead_socials_failure(
        lead.id, org.id, next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    assert await db_session.count_leads_needing_socials(org.id) == 1
    assert (await db_session.get_lead(lead.id, org.id)).socials_attempts == 2


@pytest.mark.asyncio
async def test_social_terminal_failure_stops_retrying_but_keeps_links(db_session):
    user, org = await _org(db_session)
    search = await _search(db_session, user, org)
    await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[_row("Cedar Works", "p2", website="https://cedar.example")],
    )
    lead = (await db_session.list_leads_needing_socials(org.id, search_id=search.id))[0]
    await db_session.save_lead_socials(
        lead.id,
        org.id,
        social_links={"x": "https://x.com/cedar"},
        checked_at=datetime.now(UTC),
    )
    await db_session.record_lead_socials_failure(
        lead.id, org.id, next_attempt_at=None, terminal_at=datetime.now(UTC)
    )

    done = await db_session.get_lead(lead.id, org.id)
    assert done.social_links == {"x": "https://x.com/cedar"}
    assert done.socials_checked_at is not None
    assert done.socials_attempts == 1
    assert done.socials_next_attempt_at is None
    assert await db_session.count_leads_needing_socials(org.id) == 0


@pytest.mark.asyncio
async def test_website_change_on_rescrape_clears_old_socials(db_session):
    user, org = await _org(db_session)
    search = await _search(db_session, user, org)
    await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[_row("Apex Labs", "p1", website="https://old.example")],
    )
    lead = (await db_session.list_leads_needing_socials(org.id, search_id=search.id))[0]
    await db_session.save_lead_socials(
        lead.id,
        org.id,
        social_links={"instagram": "https://www.instagram.com/apex/"},
        checked_at=datetime.now(UTC),
    )

    await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        leads=[_row("Apex Labs", "p1", website="https://new.example")],
    )

    # The upsert is a Core conflict update, so the session's identity map still
    # holds the old row; expire it before reading the new state.
    lead_id, org_id = lead.id, org.id
    stale = await db_session.get_lead(lead_id, org_id)
    inspect(stale).session.expire_all()
    refreshed = await db_session.get_lead(lead_id, org_id)
    assert refreshed.website == "https://new.example"
    assert refreshed.social_links == {}
    assert refreshed.socials_checked_at is None
    assert refreshed.socials_attempts == 0
    # The new site is scanned from scratch.
    assert await db_session.count_leads_needing_socials(org_id) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["stopping", "stopped"])
async def test_update_lead_search_fences_stopped_and_cross_org(db_session, status):
    user, org = await _org(db_session)
    _, other_org = await _org(db_session)
    search = await _search(db_session, user, org)
    run = (datetime.now(UTC), "original-job")
    await db_session.update_lead_search(
        search.id, org.id, status="running", started_at=run[0], external_job_id=run[1]
    )
    assert (
        await db_session.update_lead_search(
            search.id,
            other_org.id,
            expected_run=run,
            expected_statuses={"running"},
            status="completed",
            results_count=99,
        )
        is None
    )
    assert (await db_session.get_lead_search(search.id, org.id)).status == "running"

    stopped = await db_session.update_lead_search(search.id, org.id, status=status)
    stopped_at = stopped.updated_at
    assert (
        await db_session.update_lead_search(
            search.id,
            org.id,
            expected_run=run,
            expected_statuses={"running"},
            status="completed",
            results_count=99,
        )
        is None
    )
    async with db_session.async_session() as session:
        await session.refresh(stopped)
    assert stopped.status == status
    assert stopped.results_count == 0
    assert stopped.updated_at == stopped_at


@pytest.mark.asyncio
@pytest.mark.parametrize("has_started_at", [False, True])
@pytest.mark.parametrize("has_job_id", [False, True])
async def test_update_lead_search_fences_run_identity(
    db_session, has_started_at, has_job_id
):
    user, org = await _org(db_session)
    search = await _search(db_session, user, org)
    now = datetime.now(UTC)
    run = (now if has_started_at else None, "original-job" if has_job_id else None)
    await db_session.update_lead_search(
        search.id, org.id, status="running", started_at=run[0], external_job_id=run[1]
    )
    # Each component, including NULL, must match independently.
    for stale_run in ((now + timedelta(seconds=1), run[1]), (run[0], "other-job")):
        assert (
            await db_session.update_lead_search(
                search.id,
                org.id,
                expected_run=stale_run,
                expected_statuses={"running"},
                results_count=99,
            )
            is None
        )
    assert (
        await db_session.update_lead_search(
            search.id, org.id, expected_statuses=set(), results_count=99
        )
        is None
    )

    # Evaluate the fence against old values, before installing a new attempt.
    next_run = (now + timedelta(minutes=1), "next-job")
    updated = await db_session.update_lead_search(
        search.id,
        org.id,
        expected_run=run,
        expected_statuses={"queued", "running"},
        started_at=next_run[0],
        external_job_id=next_run[1],
    )
    assert updated is not None
    assert (updated.started_at, updated.external_job_id) == next_run
    assert (
        await db_session.update_lead_search(
            search.id,
            org.id,
            expected_run=run,
            expected_statuses={"running"},
            status="completed",
            results_count=99,
        )
        is None
    )
    async with db_session.async_session() as session:
        await session.refresh(updated)
    assert updated.status == "running"
    assert updated.results_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lost_fence",
    [
        "queued",
        "stopping",
        "stopped",
        "completed",
        "started_at",
        "job_id",
        "null_run",
        "cross_org",
    ],
)
async def test_guarded_upsert_rejects_late_rows(db_session, lost_fence):
    user, org = await _org(db_session)
    _, other_org = await _org(db_session)
    search = await _search(db_session, user, org)
    await db_session.upsert_leads_for_search(
        search_id=search.id, organization_id=org.id, leads=[_row("Original", "p1")]
    )
    run = (datetime.now(UTC), "original-job")
    await db_session.update_lead_search(
        search.id,
        org.id,
        status="running",
        started_at=run[0],
        external_job_id=run[1],
        results_count=1,
        new_leads_count=1,
    )
    organization_id = org.id
    if lost_fence in {"queued", "stopping", "stopped", "completed"}:
        await db_session.update_lead_search(search.id, org.id, status=lost_fence)
    elif lost_fence == "started_at":
        await db_session.update_lead_search(
            search.id, org.id, started_at=run[0] + timedelta(seconds=1)
        )
    elif lost_fence == "job_id":
        await db_session.update_lead_search(
            search.id, org.id, external_job_id="next-job"
        )
    elif lost_fence == "null_run":
        run = (None, None)
    else:
        organization_id = other_org.id
    before = await db_session.get_lead_search(search.id, org.id)
    updated_at = before.updated_at

    counts = await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=organization_id,
        expected_run=run,
        leads=[_row("Late refresh", "p1"), _row("Late new lead", "p2")],
    )
    assert counts == {
        "results_count": 0,
        "new_leads_count": 0,
        "updated_leads_count": 0,
    }
    leads, total = await db_session.list_leads(org.id)
    async with db_session.async_session() as session:
        await session.refresh(leads[0])
        await session.refresh(before)
    assert total == 1
    assert leads[0].title == "Original"
    assert await db_session.list_leads(other_org.id) == ([], 0)
    assert await db_session.count_search_results(search.id) == 1
    assert (before.results_count, before.new_leads_count) == (1, 1)
    assert before.updated_at == updated_at


@pytest.mark.asyncio
async def test_guarded_upsert_updates_cumulative_counters(db_session):
    user, org = await _org(db_session)
    previous = await _search(db_session, user, org)
    await db_session.upsert_leads_for_search(
        search_id=previous.id,
        organization_id=org.id,
        leads=[_row("Existing in CRM", "p0")],
    )
    # Unguarded callers retain their existing counter behavior.
    async with db_session.async_session() as session:
        await session.refresh(previous)
    assert (previous.results_count, previous.new_leads_count) == (0, 0)

    search = await _search(db_session, user, org)
    await db_session.update_lead_search(
        search.id, org.id, status="running", results_count=99, new_leads_count=7
    )
    updated_at = search.updated_at
    # NULL components are valid run identities, not omitted guards.
    first = await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        expected_run=(None, None),
        leads=[
            _row("Existing in CRM", "p0"),
            _row("New", "p1"),
            _row("Duplicate", "p1"),
        ],
    )
    assert first == {"results_count": 2, "new_leads_count": 1, "updated_leads_count": 1}
    async with db_session.async_session() as session:
        await session.refresh(search)
    assert (search.results_count, search.new_leads_count) == (2, 8)
    assert search.updated_at > updated_at

    second = await db_session.upsert_leads_for_search(
        search_id=search.id,
        organization_id=org.id,
        expected_run=(None, None),
        leads=[_row("New renamed", "p1"), _row("Another", "p2")],
    )
    assert second == {
        "results_count": 2,
        "new_leads_count": 1,
        "updated_leads_count": 1,
    }
    async with db_session.async_session() as session:
        await session.refresh(search)
    assert search.results_count == await db_session.count_search_results(search.id) == 3
    assert search.new_leads_count == 9


@pytest.mark.asyncio
async def test_guarded_upsert_rolls_back_leads_when_counter_write_fails(
    db_session, monkeypatch
):
    user, org = await _org(db_session)
    search = await _search(db_session, user, org)
    run = (datetime.now(UTC), "original-job")
    await db_session.update_lead_search(
        search.id,
        org.id,
        status="running",
        started_at=run[0],
        external_job_id=run[1],
        new_leads_count=2147483647,
    )
    search_id, org_id = search.id, org.id
    updated_at = search.updated_at
    async with db_session.async_session() as session:
        connection = await session.connection()

    # Isolate the overflow rollback from the fixture's setup transaction.
    with monkeypatch.context() as patch:
        patch.setattr(
            db_session,
            "async_session",
            async_sessionmaker(
                bind=connection, join_transaction_mode="create_savepoint"
            ),
        )
        with pytest.raises(DBAPIError, match="out of.*range"):
            await db_session.upsert_leads_for_search(
                search_id=search_id,
                organization_id=org_id,
                expected_run=run,
                leads=[_row("Must roll back", "p1")],
            )

    assert await db_session.list_leads(org_id) == ([], 0)
    assert await db_session.count_search_results(search_id) == 0
    async with db_session.async_session() as session:
        await session.refresh(search)
    assert search.results_count == 0
    assert search.new_leads_count == 2147483647
    assert search.updated_at == updated_at


@pytest.mark.asyncio
async def test_list_lead_searches_needing_recovery_selects_due_not_future(db_session):
    user, org = await _org(db_session)
    before = datetime.now(UTC)
    old = before - timedelta(hours=1)
    future = before + timedelta(hours=1)
    cases = [
        ("stopping", future, future, True),
        ("queued", None, old, True),
        ("running", old, future, True),
        ("running", None, old, True),
        ("queued", None, future, False),
        ("running", future, old, False),
        ("queued", old, future, False),
        ("queued", None, before, False),
        ("running", None, before, False),
        ("stopped", old, old, False),
        ("completed", old, old, False),
        ("failed", old, old, False),
        ("cancelled", old, old, False),
    ]
    expected_ids = []
    for status, started_at, updated_at, due in cases:
        search = await _search(db_session, user, org)
        async with db_session.async_session() as session:
            search.status = status
            search.started_at = started_at
            search.created_at = old
            search.updated_at = updated_at
            await session.commit()
        if due:
            expected_ids.append(search.id)
    recovered = await db_session.list_lead_searches_needing_recovery(before)
    assert [search.id for search in recovered] == expected_ids
    limited = await db_session.list_lead_searches_needing_recovery(before, limit=2)
    assert [search.id for search in limited] == expected_ids[:2]


@pytest.mark.asyncio
async def test_list_searches_needing_socials_is_due_distinct_and_scoped(db_session):
    user, org = await _org(db_session)
    other_user, other_org = await _org(db_session)
    now = datetime.now(UTC)
    expected_ids = []
    for status in (
        "queued",
        "running",
        "stopping",
        "stopped",
        "completed",
        "failed",
        "cancelled",
    ):
        search = await _search(db_session, user, org)
        await db_session.update_lead_search(
            search.id, org.id, provider="google_maps", status=status
        )
        await db_session.upsert_leads_for_search(
            search_id=search.id,
            organization_id=org.id,
            leads=[
                _row("Due", f"{status}-1", website="https://due.example"),
                _row("Also due", f"{status}-2", website="https://also-due.example"),
            ],
        )
        expected_ids.append(search.id)

    # Worker discovery spans tenants, but every link must still match its org.
    other_search = await _search(db_session, other_user, other_org)
    await db_session.update_lead_search(
        other_search.id, other_org.id, provider="google_maps"
    )
    await db_session.upsert_leads_for_search(
        search_id=other_search.id,
        organization_id=other_org.id,
        leads=[_row("Other org due", "other", website="https://other.example")],
    )
    foreign_lead = (await db_session.list_leads(other_org.id))[0][0]
    await db_session.record_lead_socials_failure(
        foreign_lead.id, other_org.id, next_attempt_at=now - timedelta(hours=1)
    )
    expected_ids.append(other_search.id)

    for reason in (
        "demo",
        "future",
        "checked",
        "empty_website",
        "null_website",
        "unlinked",
        "foreign_link",
    ):
        search = await _search(db_session, user, org)
        await db_session.update_lead_search(
            search.id,
            org.id,
            provider="demo" if reason == "demo" else "google_maps",
            status="completed",
        )
        if reason == "foreign_link":
            # Simulate a legacy malformed link; FK validity alone does not
            # establish tenant ownership.
            async with db_session.async_session() as session:
                session.add(
                    LeadSearchResultModel(search_id=search.id, lead_id=foreign_lead.id)
                )
                await session.commit()
            continue
        await db_session.upsert_leads_for_search(
            search_id=search.id,
            organization_id=org.id,
            leads=[_row("Excluded", reason, website="https://excluded.example")],
        )
        lead = (await db_session.list_leads(org.id, search_id=search.id))[0][0]
        if reason == "future":
            await db_session.record_lead_socials_failure(
                lead.id, org.id, next_attempt_at=now + timedelta(hours=1)
            )
        elif reason == "checked":
            await db_session.mark_lead_socials_checked(lead.id, org.id, checked_at=now)
        elif reason in {"empty_website", "null_website"}:
            await db_session.update_lead(
                lead.id, org.id, website="" if reason == "empty_website" else None
            )
        elif reason == "unlinked":
            await db_session.clear_search_results(search.id, org.id)

    pending = await db_session.list_searches_needing_socials()
    assert [search.id for search in pending] == expected_ids
    limited = await db_session.list_searches_needing_socials(limit=2)
    assert [search.id for search in limited] == expected_ids[:2]
