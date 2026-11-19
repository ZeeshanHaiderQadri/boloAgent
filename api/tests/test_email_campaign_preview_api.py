"""Campaign audience preview: counts, masking, consent and rate limiting."""

from types import SimpleNamespace

import pytest

from api.db.models import LeadModel
from api.services.call_concurrency.rate_limiter import RateLimiterUnavailable
from api.services.email.audience import MAX_EXPLICIT_LEAD_IDS


@pytest.fixture(autouse=True)
def _allow_preview_limits(monkeypatch):
    """Keep Redis out of these tests; limits have their own coverage."""

    async def allow(**kwargs):
        return True

    monkeypatch.setattr("api.routes.email_campaigns.allow_campaign_preview", allow)


async def _user_and_org(db_session, slug: str):
    user, _ = await db_session.get_or_create_user_by_provider_id(f"{slug}_user")
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        f"{slug}_org", user.id
    )
    await db_session.update_user_selected_organization(user.id, org.id)
    return SimpleNamespace(id=user.id, selected_organization_id=org.id, email=None), org


async def _lead(session, org_id, key, *, email, consent="opted_in"):
    lead = LeadModel(
        organization_id=org_id,
        dedupe_key=key,
        title=f"Lead {key}",
        email=email,
        email_consent=consent,
    )
    session.add(lead)
    await session.flush()
    return lead


async def _leads(db_session, org_id, count=2):
    async with db_session.async_session() as session:
        leads = [
            await _lead(
                session,
                org_id,
                f"preview-lead-{index}",
                email=f"owner{index}@acme-plumbing.com",
            )
            for index in range(count)
        ]
        await session.commit()
        return [lead.id for lead in leads]


def _audience(*lead_ids: int) -> dict:
    return {"audience_type": "leads", "lead_ids": list(lead_ids)}


@pytest.mark.asyncio
async def test_preview_counts_and_masks_addresses(db_session, test_client_factory):
    user, org = await _user_and_org(db_session, "campaign_preview")
    ids = await _leads(db_session, org.id, count=2)
    async with db_session.async_session() as session:
        await _lead(session, org.id, "preview-dup", email="owner0@acme-plumbing.com")
        await _lead(
            session, org.id, "preview-unknown", email="who@x.com", consent="unknown"
        )
        await _lead(session, org.id, "preview-none", email=None)
        await session.commit()

    async with test_client_factory(user) as client:
        response = await client.post(
            "/api/v1/email/campaigns/preview",
            json={
                "audience": _audience(*ids),
                "consent_policy": "opted_in_only",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    # Only the explicitly selected ids are counted; a duplicate address collapses.
    assert body["counts"]["recipient_count"] == 2
    assert body["counts"]["eligible_count"] == 2
    assert body["counts"]["duplicates_collapsed_count"] == 0
    assert body["unknown_lead_count"] == 0
    assert body["warnings"] == []
    assert len(body["masked_sample"]) == 2
    for masked in body["masked_sample"]:
        assert masked.endswith("@acme-plumbing.com")
        assert masked.startswith("o***@")
    assert "owner0@acme-plumbing.com" not in response.text


@pytest.mark.asyncio
async def test_preview_collapses_duplicates_and_includes_unknown_with_a_reason(
    db_session, test_client_factory
):
    user, org = await _user_and_org(db_session, "campaign_preview_override")
    async with db_session.async_session() as session:
        first = await _lead(
            session, org.id, "override-first", email="owner@acme-plumbing.com"
        )
        duplicate = await _lead(
            session,
            org.id,
            "override-dup",
            email="OWNER@acme-plumbing.com",
            consent="unknown",
        )
        await session.commit()
        ids = [first.id, duplicate.id]

    async with test_client_factory(user) as client:
        without_reason = await client.post(
            "/api/v1/email/campaigns/preview",
            json={
                "audience": _audience(*ids),
                "consent_policy": "include_unknown",
            },
        )
        with_reason = await client.post(
            "/api/v1/email/campaigns/preview",
            json={
                "audience": _audience(*ids),
                "consent_policy": "include_unknown",
                "consent_override_reason": "All leads asked us to write.",
            },
        )

    assert without_reason.status_code == 422
    assert with_reason.status_code == 200, with_reason.text
    body = with_reason.json()
    assert body["counts"]["eligible_count"] == 1
    assert body["counts"]["duplicates_collapsed_count"] == 1
    assert body["unknown_lead_count"] == 1
    assert any("Unknown-consent" in warning for warning in body["warnings"])


@pytest.mark.asyncio
async def test_preview_is_rate_limited_and_fails_closed(
    db_session, test_client_factory, monkeypatch
):
    user, org = await _user_and_org(db_session, "campaign_preview_limits")
    ids = await _leads(db_session, org.id, count=1)

    async def deny(**kwargs):
        return False

    async def unavailable(**kwargs):
        raise RateLimiterUnavailable("redis is down")

    async with test_client_factory(user) as client:
        monkeypatch.setattr("api.routes.email_campaigns.allow_campaign_preview", deny)
        denied = await client.post(
            "/api/v1/email/campaigns/preview", json={"audience": _audience(*ids)}
        )
        monkeypatch.setattr(
            "api.routes.email_campaigns.allow_campaign_preview", unavailable
        )
        down = await client.post(
            "/api/v1/email/campaigns/preview", json={"audience": _audience(*ids)}
        )

    assert denied.status_code == 429
    assert down.status_code == 503


@pytest.mark.asyncio
async def test_preview_rejects_bad_rules_and_foreign_leads(
    db_session, test_client_factory
):
    user, org = await _user_and_org(db_session, "campaign_preview_rules")
    other_user, other_org = await _user_and_org(db_session, "campaign_preview_other")
    async with db_session.async_session() as session:
        foreign = await _lead(session, other_org.id, "foreign-lead", email="f@x.com")
        await session.commit()

    async with test_client_factory(user) as client:
        empty = await client.post(
            "/api/v1/email/campaigns/preview",
            json={"audience": {"audience_type": "leads", "lead_ids": []}},
        )
        too_many = await client.post(
            "/api/v1/email/campaigns/preview",
            json={
                "audience": {
                    "audience_type": "leads",
                    "lead_ids": list(range(1, MAX_EXPLICIT_LEAD_IDS + 2)),
                }
            },
        )
        foreign_leads = await client.post(
            "/api/v1/email/campaigns/preview",
            json={"audience": _audience(foreign.id)},
        )
        paged_filter = await client.post(
            "/api/v1/email/campaigns/preview",
            json={
                "audience": {
                    "audience_type": "filter",
                    "filters": {"has_email": True},
                    "all_matching": False,
                }
            },
        )
        unknown_field = await client.post(
            "/api/v1/email/campaigns/preview",
            json={
                "audience": {
                    "audience_type": "filter",
                    "filters": {"not_a_field": "x"},
                }
            },
        )

    assert empty.status_code == 422
    assert too_many.status_code == 422
    assert foreign_leads.status_code == 422
    assert paged_filter.status_code == 422
    assert unknown_field.status_code == 422


@pytest.mark.asyncio
async def test_preview_with_campaign_uuid_is_rejected_and_stores_nothing(
    db_session, test_client_factory
):
    """campaign_uuid on preview is a 422; it must not store counts from a
    different audience on the campaign."""
    user, org = await _user_and_org(db_session, "campaign_preview_no_store")
    ids = await _leads(db_session, org.id, count=2)

    async with test_client_factory(user) as client:
        created = await client.post(
            "/api/v1/email/campaigns",
            json={
                "name": "No-store campaign",
                "audience": _audience(*ids),
            },
        )
        assert created.status_code == 201, created.text
        campaign_uuid = created.json()["uuid"]

        # Preview with a different audience and campaign_uuid must be rejected.
        different = await client.post(
            "/api/v1/email/campaigns/preview",
            json={
                "audience": {"audience_type": "leads", "lead_ids": [ids[0]]},
                "campaign_uuid": campaign_uuid,
            },
        )
        fetched = await client.get(f"/api/v1/email/campaigns/{campaign_uuid}")

    assert different.status_code == 422
    assert "campaign_uuid" in different.json()["detail"]
    # The campaign's counts stay at the create-time defaults (all zero).
    counts = fetched.json()["counts"]
    assert counts["recipient_count"] == 0
    assert counts["eligible_count"] == 0
