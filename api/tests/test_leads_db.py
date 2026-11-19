"""Real PostgreSQL coverage for the lead client: dedupe, CRM fields, filters."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect

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

    updated = await db_session.update_lead_socials(
        pending[0].id,
        org.id,
        social_links={"instagram": "https://www.instagram.com/apex/"},
        checked_at=datetime.now(UTC),
    )

    assert updated is not None
    assert updated.social_links == {"instagram": "https://www.instagram.com/apex/"}
    assert updated.socials_checked_at is not None
    assert await db_session.count_leads_needing_socials(org.id) == 0
    # The other organization's lead was never part of this batch.
    assert await db_session.count_leads_needing_socials(other_org.id) == 1
