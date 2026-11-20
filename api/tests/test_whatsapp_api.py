"""WhatsApp templates, senders and click-to-chat links API.

Nothing in the feature sends a message, so these tests only assert stored
configuration, rendering and organization scoping.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from api.db.models import LeadModel
from api.db.whatsapp_client import sender_conflict_message

PAYLOAD = {
    "name": "Warm intro",
    "body": "Hi {{business_name}}, we help salons in {{city}} get booked.",
    "language": "en",
    "category": "marketing",
}


async def _user_and_org(db_session, slug: str):
    user, _ = await db_session.get_or_create_user_by_provider_id(f"{slug}_user")
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        f"{slug}_org", user.id
    )
    await db_session.update_user_selected_organization(user.id, org.id)
    snapshot = SimpleNamespace(id=user.id, selected_organization_id=org.id, email=None)
    return snapshot, org


async def _lead(
    db_session, org_id, *, phone=None, phone_e164=None, country=None, city="Austin"
):
    async with db_session.async_session() as session:
        lead = LeadModel(
            organization_id=org_id,
            dedupe_key=f"wa-lead-{org_id}-{phone_e164 or phone}",
            title="Apex Labs",
            category="Marketing agency",
            city=city,
            country=country,
            phone=phone,
            phone_e164=phone_e164,
        )
        session.add(lead)
        await session.commit()
        return lead.id


async def _create_template(client, **overrides):
    payload = dict(PAYLOAD)
    payload.update(overrides)
    return await client.post("/api/v1/whatsapp/templates", json=payload)


async def _create_sender(client, **overrides):
    payload = {"label": "Front desk", "phone": "(512) 555-0100", "country": "US"}
    payload.update(overrides)
    return await client.post("/api/v1/whatsapp/senders", json=payload)


async def _accept_disclaimer(client):
    response = await client.post("/api/v1/whatsapp/disclaimer")
    assert response.status_code == 200, response.text


# ----------------------------------------------------------------------
# Templates
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_template_crud_stores_variables(db_session, test_client_factory):
    user, _ = await _user_and_org(db_session, "wa_template_crud")
    async with test_client_factory(user) as client:
        created = await _create_template(client)
        assert created.status_code == 201
        template = created.json()
        assert template["variables"] == ["business_name", "city"]
        assert template["category"] == "marketing"
        assert template["language"] == "en"

        listed = await client.get("/api/v1/whatsapp/templates")
        assert listed.status_code == 200
        assert [item["uuid"] for item in listed.json()] == [template["uuid"]]

        fetched = await client.get(f"/api/v1/whatsapp/templates/{template['uuid']}")
        assert fetched.status_code == 200
        assert fetched.json()["body"] == PAYLOAD["body"]

        updated = await client.patch(
            f"/api/v1/whatsapp/templates/{template['uuid']}",
            json={"name": "Warm intro v2", "body": "Hi {{business_name}}"},
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Warm intro v2"
        assert updated.json()["variables"] == ["business_name"]

        deleted = await client.delete(f"/api/v1/whatsapp/templates/{template['uuid']}")
        assert deleted.status_code == 200
        missing = await client.get(f"/api/v1/whatsapp/templates/{template['uuid']}")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_template_name_conflict_and_validation(db_session, test_client_factory):
    user, _ = await _user_and_org(db_session, "wa_template_validation")
    async with test_client_factory(user) as client:
        assert (await _create_template(client)).status_code == 201
        assert (await _create_template(client)).status_code == 409

        unknown = await _create_template(
            client, name="Unknown", body="Hi {{first_name}}"
        )
        assert unknown.status_code == 422
        assert "first_name" in unknown.json()["detail"]

        empty = await _create_template(client, name="Empty", body="   ")
        assert empty.status_code == 422

        bad_category = await _create_template(
            client, name="Bad category", category="promo"
        )
        assert bad_category.status_code == 422

        bad_language = await _create_template(
            client, name="Bad language", language="english language"
        )
        assert bad_language.status_code == 422

        long_name = await _create_template(client, name="x" * 256)
        assert long_name.status_code == 422

        too_long = await _create_template(client, name="Long body", body="x" * 1001)
        assert too_long.status_code == 422
        assert "1000" in too_long.json()["detail"]

        at_limit = await _create_template(client, name="At limit", body="x" * 1000)
        assert at_limit.status_code == 201


@pytest.mark.asyncio
async def test_over_long_language_and_label_are_422_not_500(
    db_session, test_client_factory
):
    """Values wider than their columns must fail validation, not the database."""
    user, _ = await _user_and_org(db_session, "wa_lengths")
    async with test_client_factory(user) as client:
        long_language = await _create_template(
            client, name="Long language", language="en-" + "a" * 30
        )
        assert long_language.status_code == 422

        template = (await _create_template(client)).json()
        patched = await client.patch(
            f"/api/v1/whatsapp/templates/{template['uuid']}",
            json={"language": "en-" + "a" * 30},
        )
        assert patched.status_code == 422

        long_label = await _create_sender(client, label="x" * 256)
        assert long_label.status_code == 422

        at_limit = await _create_sender(client, label="x" * 255)
        assert at_limit.status_code == 201


# ----------------------------------------------------------------------
# Senders
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sender_crud_default_promotion_and_phone_rules(
    db_session, test_client_factory
):
    user, _ = await _user_and_org(db_session, "wa_sender_crud")
    async with test_client_factory(user) as client:
        first = await _create_sender(client)
        assert first.status_code == 201
        sender_a = first.json()
        assert sender_a["phone_e164"] == "+15125550100"
        assert sender_a["is_default"] is True

        second = await _create_sender(
            client,
            label="Owner",
            phone="+44 7700 900123",
            country="GB",
            is_default=True,
        )
        assert second.status_code == 201
        sender_b = second.json()

        listed = (await client.get("/api/v1/whatsapp/senders")).json()
        assert [item["uuid"] for item in listed] == [sender_b["uuid"], sender_a["uuid"]]
        assert [item["is_default"] for item in listed] == [True, False]

        duplicate = await _create_sender(client, label="Again", phone="5125550100")
        assert duplicate.status_code == 409

        bad_country = await _create_sender(client, country="USA")
        assert bad_country.status_code == 422

        unreadable = await _create_sender(client, phone="12", country="US")
        assert unreadable.status_code == 422

        phone_conflict = await client.patch(
            f"/api/v1/whatsapp/senders/{sender_a['uuid']}",
            json={"phone": sender_b["phone_e164"], "country": "GB"},
        )
        assert phone_conflict.status_code == 409

        renamed = await client.patch(
            f"/api/v1/whatsapp/senders/{sender_a['uuid']}", json={"label": "Salon"}
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["label"] == "Salon"

        deleted = await client.delete(f"/api/v1/whatsapp/senders/{sender_b['uuid']}")
        assert deleted.status_code == 200
        remaining = (await client.get("/api/v1/whatsapp/senders")).json()
        assert remaining[0]["uuid"] == sender_a["uuid"]
        assert remaining[0]["is_default"] is True


@pytest.mark.asyncio
async def test_sender_country_must_be_a_real_iso_code(db_session, test_client_factory):
    user, _ = await _user_and_org(db_session, "wa_sender_iso")
    async with test_client_factory(user) as client:
        for country in ["UK", "EU", "ZZ", "USA", ""]:
            response = await _create_sender(
                client, country=country, phone="+44 7700 900123"
            )
            assert response.status_code == 422, country
        accepted = await _create_sender(client, country="GB", phone="+44 7700 900123")
        assert accepted.status_code == 201
        assert accepted.json()["phone_e164"] == "+447700900123"


@pytest.mark.asyncio
async def test_sender_rejects_a_number_from_another_country(
    db_session, test_client_factory
):
    user, _ = await _user_and_org(db_session, "wa_sender_mismatch")
    async with test_client_factory(user) as client:
        mismatch = await _create_sender(client, phone="+1 512 555 0100", country="GB")
        assert mismatch.status_code == 422
        detail = mismatch.json()["detail"]
        assert "+1" in detail
        assert "United States" in detail
        assert "+44" in detail

        # A Tokyo landline cannot be read as a US number either.
        local = await _create_sender(client, phone="0312345678", country="US")
        assert local.status_code == 422
        assert "United States" in local.json()["detail"]

        accepted = await _create_sender(client, phone="+1 (512) 555-0100", country="US")
        assert accepted.status_code == 201
        assert accepted.json()["phone_e164"] == "+15125550100"

        # Changing only the country must not silently rewrite the saved number.
        changed = await client.patch(
            f"/api/v1/whatsapp/senders/{accepted.json()['uuid']}",
            json={"country": "GB"},
        )
        assert changed.status_code == 422


@pytest.mark.asyncio
async def test_templates_and_senders_are_org_scoped(db_session, test_client_factory):
    user_a, _ = await _user_and_org(db_session, "wa_scope_a")
    user_b, _ = await _user_and_org(db_session, "wa_scope_b")
    async with test_client_factory(user_a) as client:
        template = (await _create_template(client)).json()
        sender = (await _create_sender(client)).json()

    async with test_client_factory(user_b) as client:
        assert (await client.get("/api/v1/whatsapp/templates")).json() == []
        assert (await client.get("/api/v1/whatsapp/senders")).json() == []
        assert (
            await client.get(f"/api/v1/whatsapp/templates/{template['uuid']}")
        ).status_code == 404
        assert (
            await client.patch(
                f"/api/v1/whatsapp/templates/{template['uuid']}",
                json={"name": "Stolen"},
            )
        ).status_code == 404
        assert (
            await client.delete(f"/api/v1/whatsapp/templates/{template['uuid']}")
        ).status_code == 404
        assert (
            await client.get(f"/api/v1/whatsapp/senders/{sender['uuid']}")
        ).status_code == 404
        assert (
            await client.delete(f"/api/v1/whatsapp/senders/{sender['uuid']}")
        ).status_code == 404


@pytest.mark.asyncio
async def test_editing_the_default_sender_always_leaves_a_default_sender(
    db_session, test_client_factory
):
    user, _ = await _user_and_org(db_session, "wa_default_edit")
    async with test_client_factory(user) as client:
        first = (await _create_sender(client)).json()
        second = (
            await _create_sender(client, label="Owner", phone="+1 512 555 0101")
        ).json()
        assert first["is_default"] is True
        assert second["is_default"] is False

        # Stepping down promotes the oldest remaining number instead of leaving
        # the organization without a default.
        demoted = await client.patch(
            f"/api/v1/whatsapp/senders/{first['uuid']}", json={"is_default": False}
        )
        assert demoted.status_code == 200, demoted.text
        assert demoted.json()["is_default"] is False
        listed = (await client.get("/api/v1/whatsapp/senders")).json()
        assert [(item["uuid"], item["is_default"]) for item in listed] == [
            (second["uuid"], True),
            (first["uuid"], False),
        ]

        # The only remaining number stays the default even when asked to step
        # down.
        assert (
            await client.delete(f"/api/v1/whatsapp/senders/{first['uuid']}")
        ).status_code == 200
        only = await client.patch(
            f"/api/v1/whatsapp/senders/{second['uuid']}", json={"is_default": False}
        )
        assert only.status_code == 200
        assert only.json()["is_default"] is True


def test_default_slot_race_gets_its_own_conflict_message():
    """Two concurrent first saves must not be blamed on a duplicate number."""
    race = IntegrityError(
        "insert", {}, Exception("duplicate key ... ix_whatsapp_senders_one_default")
    )
    assert "just saved as the default" in sender_conflict_message(race)

    duplicate_phone = IntegrityError(
        "insert", {}, Exception("duplicate key ... uq_whatsapp_senders_org_phone")
    )
    message = sender_conflict_message(duplicate_phone)
    assert "phone number is already saved" in message
    assert "default" not in message


# ----------------------------------------------------------------------
# Lead links
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lead_links_render_every_template_for_an_e164_lead(
    db_session, test_client_factory
):
    user, org = await _user_and_org(db_session, "wa_links")
    lead_id = await _lead(
        db_session,
        org.id,
        phone="(512) 555-0199",
        phone_e164="+15125550199",
        country="US",
    )
    async with test_client_factory(user) as client:
        await _accept_disclaimer(client)
        await _create_template(client)
        response = await client.get(f"/api/v1/whatsapp/leads/{lead_id}/links")
        assert response.status_code == 200
        payload = response.json()
        assert payload["available"] is True
        assert payload["phone_e164"] == "+15125550199"
        assert payload["country"] == "US"
        assert len(payload["links"]) == 1
        link = payload["links"][0]
        assert "Apex Labs" in link["body"]
        assert "Austin" in link["body"]
        assert link["url"].startswith("https://wa.me/15125550199?text=Hi%20Apex%20Labs")


@pytest.mark.asyncio
async def test_lead_links_fall_back_to_the_sender_default_country(
    db_session, test_client_factory
):
    user, org = await _user_and_org(db_session, "wa_links_country")
    lead_id = await _lead(db_session, org.id, phone="5125550188", phone_e164=None)
    async with test_client_factory(user) as client:
        await _accept_disclaimer(client)
        await _create_sender(client, country="US")
        await _create_template(client)
        payload = (await client.get(f"/api/v1/whatsapp/leads/{lead_id}/links")).json()
        assert payload["available"] is True
        assert payload["phone_e164"] == "+15125550188"
        assert payload["links"][0]["url"].startswith("https://wa.me/15125550188")


@pytest.mark.asyncio
async def test_lead_links_never_guess_a_country_without_a_sender(
    db_session, test_client_factory
):
    user, org = await _user_and_org(db_session, "wa_links_unknown")
    with_phone = await _lead(db_session, org.id, phone="5125550177", phone_e164=None)
    no_phone = await _lead(db_session, org.id, phone=None, phone_e164=None)
    known = await _lead(
        db_session, org.id, phone="+15125550166", phone_e164="+15125550166"
    )
    async with test_client_factory(user) as client:
        await _accept_disclaimer(client)
        unknown = (
            await client.get(f"/api/v1/whatsapp/leads/{with_phone}/links")
        ).json()
        assert unknown == {
            "available": False,
            "reason": "country_unknown",
            "phone_e164": None,
            "country": None,
            "links": [],
        }
        missing = (await client.get(f"/api/v1/whatsapp/leads/{no_phone}/links")).json()
        assert missing["available"] is False
        assert missing["reason"] == "no_phone"

        # A known number without any template still reports availability.
        empty = (await client.get(f"/api/v1/whatsapp/leads/{known}/links")).json()
        assert empty["available"] is True
        assert empty["links"] == []


@pytest.mark.asyncio
async def test_lead_links_resolve_a_gb_lead_with_a_us_default_sender(
    db_session, test_client_factory
):
    user, org = await _user_and_org(db_session, "wa_links_gb")
    lead_id = await _lead(db_session, org.id, phone="020 7946 0018", country="GB")
    async with test_client_factory(user) as client:
        await _accept_disclaimer(client)
        await _create_sender(client, country="US")
        await _create_template(client)
        payload = (await client.get(f"/api/v1/whatsapp/leads/{lead_id}/links")).json()
        assert payload["available"] is True
        assert payload["phone_e164"] == "+442079460018"
        assert payload["country"] == "GB"
        assert payload["links"][0]["url"].startswith("https://wa.me/442079460018?text=")


@pytest.mark.asyncio
async def test_lead_links_never_rewrite_a_plus_number(db_session, test_client_factory):
    user, org = await _user_and_org(db_session, "wa_links_plus")
    lead_id = await _lead(
        db_session,
        org.id,
        phone="+44 20 7946 0018",
        phone_e164="+15125550199",
        country="US",
    )
    async with test_client_factory(user) as client:
        await _accept_disclaimer(client)
        await _create_sender(client, country="US")
        await _create_template(client)
        payload = (await client.get(f"/api/v1/whatsapp/leads/{lead_id}/links")).json()
        assert payload["phone_e164"] == "+442079460018"


@pytest.mark.asyncio
async def test_lead_links_ignore_a_stored_e164_that_contradicts_the_country(
    db_session, test_client_factory
):
    user, org = await _user_and_org(db_session, "wa_links_wrong_e164")
    # The importer used to prefix "+1 " onto numbers when the search hinted US.
    lead_id = await _lead(
        db_session,
        org.id,
        phone="020 7946 0018",
        phone_e164="+1202079460018",
        country="GB",
    )
    async with test_client_factory(user) as client:
        await _accept_disclaimer(client)
        await _create_template(client)
        payload = (await client.get(f"/api/v1/whatsapp/leads/{lead_id}/links")).json()
        assert payload["phone_e164"] == "+442079460018"


@pytest.mark.asyncio
async def test_lead_links_never_prefix_a_japanese_number_with_a_us_hint(
    db_session, test_client_factory
):
    user, org = await _user_and_org(db_session, "wa_links_jp")
    japanese = await _lead(db_session, org.id, phone="03-1234-5678", country="Japan")
    unlabelled = await _lead(db_session, org.id, phone="03 1234 5678")
    async with test_client_factory(user) as client:
        await _accept_disclaimer(client)
        await _create_sender(client, country="US")
        resolved = (await client.get(f"/api/v1/whatsapp/leads/{japanese}/links")).json()
        assert resolved["phone_e164"] == "+81312345678"

        refused = (
            await client.get(f"/api/v1/whatsapp/leads/{unlabelled}/links")
        ).json()
        assert refused["available"] is False
        assert refused["reason"] == "unrecognized_number"


@pytest.mark.asyncio
async def test_lead_links_do_not_trust_e164_without_a_known_country(
    db_session, test_client_factory
):
    user, org = await _user_and_org(db_session, "wa_links_unknown_country")
    lead_id = await _lead(
        db_session, org.id, phone="5125550177", phone_e164="+15125550177"
    )
    async with test_client_factory(user) as client:
        await _accept_disclaimer(client)
        payload = (await client.get(f"/api/v1/whatsapp/leads/{lead_id}/links")).json()
        assert payload["available"] is False
        assert payload["reason"] == "country_unknown"


@pytest.mark.asyncio
async def test_lead_links_are_org_scoped(db_session, test_client_factory):
    _user_a, org_a = await _user_and_org(db_session, "wa_links_org_a")
    user_b, _ = await _user_and_org(db_session, "wa_links_org_b")
    lead_id = await _lead(db_session, org_a.id, phone_e164="+15125550155")
    async with test_client_factory(user_b) as client:
        response = await client.get(f"/api/v1/whatsapp/leads/{lead_id}/links")
        assert response.status_code == 404


# ----------------------------------------------------------------------
# Lead list availability
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lead_list_flags_which_leads_can_open_whatsapp(
    db_session, test_client_factory
):
    """The CRM only shows the chat icon for leads whose link can be built.

    Availability is computed by the backend with the same rules as the link
    endpoint, so the list and the chooser can never disagree.
    """
    user, org = await _user_and_org(db_session, "wa_lead_availability")
    local = await _lead(db_session, org.id, phone="512 555 0123")
    explicit = await _lead(
        db_session, org.id, phone="+44 20 7946 0018", phone_e164="+442079460018"
    )
    unreadable = await _lead(db_session, org.id, phone="03 1234 5678")
    no_phone = await _lead(db_session, org.id, phone=None)

    async def availability(client) -> dict[int, bool]:
        response = await client.get("/api/v1/leads", params={"limit": 100})
        assert response.status_code == 200, response.text
        return {
            item["id"]: item["whatsapp_available"] for item in response.json()["leads"]
        }

    async with test_client_factory(user) as client:
        # Nothing identifies the country of a bare local number yet.
        before = await availability(client)
        assert before[local] is False
        assert before[explicit] is True
        assert before[unreadable] is False
        assert before[no_phone] is False

        await _create_sender(client, country="US")
        after = await availability(client)
        assert after[local] is True
        # A Tokyo landline is not silently read as a US number.
        assert after[unreadable] is False

        detail = await client.get(f"/api/v1/leads/{local}")
        assert detail.status_code == 200
        assert detail.json()["lead"]["whatsapp_available"] is True


# ----------------------------------------------------------------------
# Disclaimer
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lead_links_require_the_disclaimer_once(db_session, test_client_factory):
    user, org = await _user_and_org(db_session, "wa_disclaimer")
    lead_id = await _lead(db_session, org.id, phone="+15125550155")
    async with test_client_factory(user) as client:
        status = (await client.get("/api/v1/whatsapp/disclaimer")).json()
        assert status["accepted"] is False
        assert status["accepted_at"] is None
        assert status["version"]
        assert "WhatsApp" in status["text"]

        blocked = await client.get(f"/api/v1/whatsapp/leads/{lead_id}/links")
        assert blocked.status_code == 403

        accepted = await client.post("/api/v1/whatsapp/disclaimer")
        assert accepted.status_code == 200
        assert accepted.json()["accepted"] is True
        assert accepted.json()["accepted_at"]

        status = (await client.get("/api/v1/whatsapp/disclaimer")).json()
        assert status["accepted"] is True

        allowed = await client.get(f"/api/v1/whatsapp/leads/{lead_id}/links")
        assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_disclaimer_acceptance_is_per_user(db_session, test_client_factory):
    user_a, org = await _user_and_org(db_session, "wa_disclaimer_user_a")
    user_b, _ = await db_session.get_or_create_user_by_provider_id(
        "wa_disclaimer_user_b"
    )
    await db_session.update_user_selected_organization(user_b.id, org.id)
    snapshot_b = SimpleNamespace(
        id=user_b.id, selected_organization_id=org.id, email=None
    )

    async with test_client_factory(user_a) as client:
        assert (await client.post("/api/v1/whatsapp/disclaimer")).status_code == 200

    async with test_client_factory(snapshot_b) as client:
        status = (await client.get("/api/v1/whatsapp/disclaimer")).json()
        assert status["accepted"] is False
