"""Recipients endpoint: paginated, bucket-classified, masked email list."""

from types import SimpleNamespace

import pytest

from api.db.models import EmailSuppressionModel, LeadModel
from api.services.call_concurrency.rate_limiter import RateLimiterUnavailable


@pytest.fixture(autouse=True)
def _allow_preview_limits(monkeypatch):
    """Keep Redis out of these tests; rate-limit behaviour has its own tests."""

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


async def _lead(session, org_id, key, *, email=None, consent="opted_in", title=None):
    lead = LeadModel(
        organization_id=org_id,
        dedupe_key=key,
        title=title or f"Lead {key}",
        email=email,
        email_consent=consent,
    )
    session.add(lead)
    await session.flush()
    return lead


async def _create_campaign(client, lead_ids, *, name="Outreach", **overrides):
    payload = {
        "name": name,
        "audience": {"audience_type": "leads", "lead_ids": lead_ids},
    }
    payload.update(overrides)
    res = await client.post("/api/v1/email/campaigns", json=payload)
    assert res.status_code == 201, res.text
    return res.json()


@pytest.mark.asyncio
async def test_recipients_returns_paginated_rows_with_masked_emails(
    db_session, test_client_factory
):
    user, org = await _user_and_org(db_session, "recip_basic")
    async with db_session.async_session() as session:
        lead1 = await _lead(
            session, org.id, "recip-1", email="alice@acme.com", title="Alice"
        )
        lead2 = await _lead(
            session, org.id, "recip-2", email="bob@acme.com", title="Bob"
        )
        await session.commit()
        ids = [lead1.id, lead2.id]

    async with test_client_factory(user) as client:
        campaign = await _create_campaign(client, ids)
        uuid = campaign["uuid"]

        res = await client.get(
            f"/api/v1/email/campaigns/{uuid}/recipients?page=1&limit=10"
        )

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 2
    assert body["page"] == 1
    assert len(body["items"]) == 2
    # Emails must be masked
    for item in body["items"]:
        assert "@" in item["masked_email"]
        assert "alice@acme.com" not in item["masked_email"]
        assert "bob@acme.com" not in item["masked_email"]
        assert item["masked_email"].count("*") >= 3
        assert item["bucket"] == "eligible"
    # Raw emails must never appear anywhere in the response text
    assert "alice@acme.com" not in res.text
    assert "bob@acme.com" not in res.text


@pytest.mark.asyncio
async def test_recipients_bucket_filter_returns_only_matching_rows(
    db_session, test_client_factory
):
    user, org = await _user_and_org(db_session, "recip_filter")
    async with db_session.async_session() as session:
        opted_in = await _lead(
            session, org.id, "rf-opted-in", email="opted@acme.com", consent="opted_in"
        )
        opted_out = await _lead(
            session,
            org.id,
            "rf-opted-out",
            email="out@acme.com",
            consent="opted_out",
        )
        no_email = await _lead(session, org.id, "rf-no-email", email=None)
        await session.commit()
        ids = [opted_in.id, opted_out.id, no_email.id]

    async with test_client_factory(user) as client:
        campaign = await _create_campaign(client, ids, name="Filter test")
        uuid = campaign["uuid"]

        eligible_res = await client.get(
            f"/api/v1/email/campaigns/{uuid}/recipients?bucket=eligible"
        )
        opted_out_res = await client.get(
            f"/api/v1/email/campaigns/{uuid}/recipients?bucket=opted_out"
        )
        no_email_res = await client.get(
            f"/api/v1/email/campaigns/{uuid}/recipients?bucket=no_email"
        )

    assert eligible_res.status_code == 200
    eligible_body = eligible_res.json()
    assert eligible_body["total"] == 1
    assert eligible_body["items"][0]["bucket"] == "eligible"

    assert opted_out_res.status_code == 200
    assert opted_out_res.json()["total"] == 1
    assert opted_out_res.json()["items"][0]["bucket"] == "opted_out"

    assert no_email_res.status_code == 200
    assert no_email_res.json()["total"] == 1
    assert no_email_res.json()["items"][0]["bucket"] == "no_email"


@pytest.mark.asyncio
async def test_recipients_manual_exclusions_appear_as_manually_excluded(
    db_session, test_client_factory
):
    user, org = await _user_and_org(db_session, "recip_manual_excl")
    async with db_session.async_session() as session:
        lead_a = await _lead(
            session, org.id, "me-a", email="keepme@test.io", consent="opted_in"
        )
        lead_b = await _lead(
            session, org.id, "me-b", email="exclude@test.io", consent="opted_in"
        )
        await session.commit()
        ids = [lead_a.id, lead_b.id]

    async with test_client_factory(user) as client:
        campaign = await _create_campaign(client, ids, name="Manual excl test")
        uuid = campaign["uuid"]

        # Patch the audience to manually exclude lead_b
        patched = await client.patch(
            f"/api/v1/email/campaigns/{uuid}",
            json={
                "audience": {
                    "audience_type": "leads",
                    "lead_ids": ids,
                    "excluded_lead_ids": [lead_b.id],
                }
            },
        )
        assert patched.status_code == 200, patched.text

        res = await client.get(f"/api/v1/email/campaigns/{uuid}/recipients")

    assert res.status_code == 200
    body = res.json()
    buckets = {item["lead_id"]: item["bucket"] for item in body["items"]}
    assert buckets[lead_a.id] == "eligible"
    assert buckets[lead_b.id] == "manually_excluded"

    # totals_per_bucket must reflect both
    assert body["totals_per_bucket"]["eligible"] == 1
    assert body["totals_per_bucket"]["manually_excluded"] == 1


@pytest.mark.asyncio
async def test_recipients_cross_org_returns_404(db_session, test_client_factory):
    user, org = await _user_and_org(db_session, "recip_scope_a")
    other_user, _ = await _user_and_org(db_session, "recip_scope_b")
    async with db_session.async_session() as session:
        lead = await _lead(session, org.id, "scope-lead", email="x@a.com")
        await session.commit()

    async with test_client_factory(user) as client:
        campaign = await _create_campaign(client, [lead.id])
        uuid = campaign["uuid"]

    async with test_client_factory(other_user) as other_client:
        res = await other_client.get(f"/api/v1/email/campaigns/{uuid}/recipients")

    assert res.status_code == 404


@pytest.mark.asyncio
async def test_recipients_rate_limited_returns_429_redis_down_returns_503(
    db_session, test_client_factory, monkeypatch
):
    user, org = await _user_and_org(db_session, "recip_limits")
    async with db_session.async_session() as session:
        lead = await _lead(session, org.id, "rl-lead", email="rl@demo.com")
        await session.commit()

    async with test_client_factory(user) as client:
        campaign = await _create_campaign(client, [lead.id], name="Rate limit test")
        uuid = campaign["uuid"]

        async def deny(**kwargs):
            return False

        async def unavailable(**kwargs):
            raise RateLimiterUnavailable("redis down")

        monkeypatch.setattr("api.routes.email_campaigns.allow_campaign_preview", deny)
        denied = await client.get(f"/api/v1/email/campaigns/{uuid}/recipients")

        monkeypatch.setattr(
            "api.routes.email_campaigns.allow_campaign_preview", unavailable
        )
        down = await client.get(f"/api/v1/email/campaigns/{uuid}/recipients")

    assert denied.status_code == 429
    assert down.status_code == 503


@pytest.mark.asyncio
async def test_recipients_suppressed_leads_appear_in_suppressed_bucket(
    db_session, test_client_factory
):
    user, org = await _user_and_org(db_session, "recip_suppressed")
    async with db_session.async_session() as session:
        lead = await _lead(
            session,
            org.id,
            "supp-lead",
            email="suppressed@suppressed-domain.com",
            consent="opted_in",
        )
        suppression = EmailSuppressionModel(
            organization_id=org.id,
            email="suppressed@suppressed-domain.com",
            reason="hard_bounce",
        )
        session.add(suppression)
        await session.commit()

    async with test_client_factory(user) as client:
        campaign = await _create_campaign(client, [lead.id], name="Suppressed test")
        uuid = campaign["uuid"]
        res = await client.get(
            f"/api/v1/email/campaigns/{uuid}/recipients?bucket=suppressed"
        )

    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert body["items"][0]["bucket"] == "suppressed"
    # Email must be masked even for suppressed leads
    assert "suppressed@suppressed-domain.com" not in res.text
