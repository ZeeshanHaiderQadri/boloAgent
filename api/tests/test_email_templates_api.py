"""API behaviour for email templates: CRUD, scoping, preview and test sends."""

from types import SimpleNamespace

import pytest

from api.db.models import UserModel
from api.services.email.adapter import ProviderSendResult

UNSUBSCRIBE = '<p><a href="{{unsubscribe_link}}">Unsubscribe</a></p>'

SMTP_SENDER = {
    "name": "Support",
    "provider": "smtp",
    "from_name": "Bolo Support",
    "from_email": "support@apex-software-demo.com",
    "settings": {"preset": "hostinger"},
    "credentials": {
        "username": "support@apex-software-demo.com",
        "password": "fake-smtp-password",
    },
}


@pytest.fixture(autouse=True)
def _allow_template_limits(monkeypatch):
    """Keep Redis out of the template API tests; limits have their own tests."""

    async def allow(**kwargs):
        return True

    monkeypatch.setattr("api.routes.email.allow_template_preview", allow)
    monkeypatch.setattr("api.routes.email.allow_template_test_send", allow)


async def _make_user_and_org(db_session, slug: str, email: str | None = None):
    user, _ = await db_session.get_or_create_user_by_provider_id(f"{slug}_user")
    user_id = user.id
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        f"{slug}_org", user_id
    )
    org_id = org.id
    await db_session.update_user_selected_organization(user_id, org_id)
    normalized = None
    if email is not None:
        normalized = email.lower()
        async with db_session.async_session() as session:
            row = await session.get(UserModel, user_id)
            row.email = normalized
            await session.commit()
    # Commits expire the identity-mapped row. The auth override only needs these
    # fields, so return a snapshot that cannot lazy-load during a request.
    snapshot = SimpleNamespace(
        id=user_id, selected_organization_id=org_id, email=normalized
    )
    return snapshot, org_id


def _payload(**overrides):
    payload = {
        "name": "Welcome",
        "subject": "Hi {{business_name}}",
        "source_format": "html",
        "source": f"<p>Hello {{{{business_name}}}}</p>{UNSUBSCRIBE}",
    }
    payload.update(overrides)
    return payload


async def _create(client, payload=None):
    return await client.post("/api/v1/email/templates", json=payload or _payload())


def test_paths_are_registered_before_the_dynamic_template_route():
    from api.routes.email import router

    paths = [route.path for route in router.routes]
    assert paths.index("/email/templates/merge-fields") < paths.index(
        "/email/templates/{template_uuid}"
    )


@pytest.mark.asyncio
async def test_create_list_get_update_and_delete_a_template(
    db_session, test_client_factory
):
    user, _ = await _make_user_and_org(db_session, "template_crud")
    async with test_client_factory(user) as client:
        created = await _create(client)
        assert created.status_code == 201, created.text
        body = created.json()
        uuid = body["uuid"]
        assert body["has_unsubscribe_placeholder"] is True
        assert body["warnings"] == []
        assert body["source_format"] == "html"
        assert body["merge_fields"] == ["business_name", "unsubscribe_link"]
        assert "{{business_name}}" in body["html_body"]
        assert body["text_body"].strip()

        listed = await client.get("/api/v1/email/templates")
        assert listed.status_code == 200
        assert [item["uuid"] for item in listed.json()] == [uuid]

        fetched = await client.get(f"/api/v1/email/templates/{uuid}")
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "Welcome"

        updated = await client.patch(
            f"/api/v1/email/templates/{uuid}",
            json={
                "subject": "Updated subject",
                "source": "<p>No unsubscribe here</p>",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["subject"] == "Updated subject"
        assert updated.json()["has_unsubscribe_placeholder"] is False
        assert "unsubscribe_link" in updated.json()["warnings"][0]

        deleted = await client.delete(f"/api/v1/email/templates/{uuid}")
        assert deleted.status_code == 200
        assert deleted.json() == {"status": "deleted", "uuid": uuid}

        missing = await client.get(f"/api/v1/email/templates/{uuid}")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_create_sanitizes_html_and_generates_a_text_fallback(
    db_session, test_client_factory
):
    user, _ = await _make_user_and_org(db_session, "template_sanitize")
    payload = _payload(
        source=(
            '<p onclick="evil()">Hello {{business_name}}</p>'
            "<script>alert(1)</script>"
            '<a href="javascript:alert(2)">bad</a>'
            f"{UNSUBSCRIBE}"
        )
    )
    async with test_client_factory(user) as client:
        response = await _create(client, payload)

    assert response.status_code == 201, response.text
    body = response.json()
    assert "onclick" not in body["html_body"]
    assert "alert(" not in body["html_body"]
    assert "javascript:" not in body["html_body"]
    assert "Unsubscribe" in body["text_body"]


@pytest.mark.asyncio
async def test_unknown_merge_fields_are_rejected_at_save_and_preview(
    db_session, test_client_factory
):
    user, _ = await _make_user_and_org(db_session, "template_unknown")
    async with test_client_factory(user) as client:
        valid = await _create(client)
        uuid = valid.json()["uuid"]
        created = await _create(
            client,
            _payload(name="Typo", source=f"<p>{{{{first_nmae}}}}</p>{UNSUBSCRIBE}"),
        )
        preview = await client.post(
            "/api/v1/email/templates/preview",
            json={
                "subject": "Hi",
                "source_format": "html",
                "source": "<p>{{first_nmae}}</p>",
            },
        )
        patched = await client.patch(
            f"/api/v1/email/templates/{uuid}",
            json={"source": "<p>{{first_nmae}}</p>"},
        )

    assert created.status_code == 422
    assert "Unknown merge field" in created.json()["detail"]
    assert "business_name" in created.json()["detail"]
    assert preview.status_code == 422
    assert "Unknown merge field" in preview.json()["detail"]
    assert patched.status_code == 422
    assert "Unknown merge field" in patched.json()["detail"]


@pytest.mark.asyncio
async def test_preview_renders_sample_values_and_flags_a_missing_unsubscribe(
    db_session, test_client_factory
):
    user, _ = await _make_user_and_org(db_session, "template_preview")
    async with test_client_factory(user) as client:
        response = await client.post(
            "/api/v1/email/templates/preview",
            json={
                "subject": "Hi {{business_name}}",
                "source_format": "html",
                "source": "<p>Call {{phone}}</p>",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["subject"] == "Hi Acme Demo Co."
    assert "+1 555 0100" in body["text"]
    assert body["has_unsubscribe_placeholder"] is False
    assert body["warnings"]


@pytest.mark.asyncio
async def test_mjml_is_compiled_on_the_server(db_session, test_client_factory):
    user, _ = await _make_user_and_org(db_session, "template_mjml")
    source = """
    <mjml><mj-body background-color="#ffffff" width="600px">
      <mj-section padding="20px"><mj-column width="100%">
        <mj-text font-size="16px">Hi {{business_name}}</mj-text>
        <mj-button href="https://apex-software-demo.com">Book</mj-button>
        <mj-text><a href="{{unsubscribe_link}}">Unsubscribe</a></mj-text>
      </mj-column></mj-section>
    </mj-body></mjml>
    """
    async with test_client_factory(user) as client:
        created = await _create(client, _payload(source_format="mjml", source=source))
        unsupported_tag = await _create(
            client,
            _payload(
                name="Hero",
                source_format="mjml",
                source="<mjml><mj-body><mj-hero /></mj-body></mjml>",
            ),
        )
        unsupported_attribute = await _create(
            client,
            _payload(
                name="Magic",
                source_format="mjml",
                source=(
                    "<mjml><mj-body><mj-section><mj-column>"
                    '<mj-text magic="yes">Hi</mj-text>'
                    "</mj-column></mj-section></mj-body></mjml>"
                ),
            ),
        )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["source_format"] == "mjml"
    assert "<table" in body["html_body"]
    assert "mj-" not in body["html_body"]
    assert body["has_unsubscribe_placeholder"] is True
    assert unsupported_tag.status_code == 422
    assert "mj-hero" in unsupported_tag.json()["detail"]
    assert "not allowed" in unsupported_tag.json()["detail"]
    assert unsupported_attribute.status_code == 422
    assert "Unsupported attribute" in unsupported_attribute.json()["detail"]


@pytest.mark.asyncio
async def test_comment_only_unsubscribe_placeholder_is_not_a_link(
    db_session, test_client_factory
):
    """Only a sanitized anchor href counts; a comment is stripped and ignored."""
    user, _ = await _make_user_and_org(db_session, "template_unsub_comment")
    source = "<p>Hello {{business_name}}</p><!-- {{unsubscribe_link}} -->"
    async with test_client_factory(user) as client:
        response = await _create(client, _payload(source=source))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["has_unsubscribe_placeholder"] is False
    assert "unsubscribe_link" in body["warnings"][0]
    assert "<!--" not in body["html_body"]


@pytest.mark.asyncio
async def test_names_are_unique_per_organization_and_every_lookup_is_scoped(
    db_session, test_client_factory
):
    user, _ = await _make_user_and_org(db_session, "template_scope_a")
    other_user, _ = await _make_user_and_org(db_session, "template_scope_b")

    async with test_client_factory(user) as client:
        first = await _create(client)
        conflict = await _create(client)
    assert first.status_code == 201
    assert conflict.status_code == 409
    assert "already exists" in conflict.json()["detail"]

    async with test_client_factory(other_user) as other_client:
        # The same name is fine in a different organization.
        same_name = await _create(other_client)
        assert same_name.status_code == 201, same_name.text
        uuid = first.json()["uuid"]
        cross_get = await other_client.get(f"/api/v1/email/templates/{uuid}")
        cross_patch = await other_client.patch(
            f"/api/v1/email/templates/{uuid}", json={"subject": "Hijack"}
        )
        cross_delete = await other_client.delete(f"/api/v1/email/templates/{uuid}")
        cross_duplicate = await other_client.post(
            f"/api/v1/email/templates/{uuid}/duplicate"
        )
    assert cross_get.status_code == 404
    assert cross_patch.status_code == 404
    assert cross_delete.status_code == 404
    assert cross_duplicate.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_uses_incrementing_copy_names(db_session, test_client_factory):
    user, _ = await _make_user_and_org(db_session, "template_copy")
    async with test_client_factory(user) as client:
        created = await _create(client)
        uuid = created.json()["uuid"]
        first = await client.post(f"/api/v1/email/templates/{uuid}/duplicate")
        second = await client.post(f"/api/v1/email/templates/{uuid}/duplicate")
        listed = await client.get("/api/v1/email/templates")

    assert first.status_code == 201, first.text
    assert first.json()["name"] == "Welcome (copy)"
    assert second.json()["name"] == "Welcome (copy 2)"
    names = {item["name"] for item in listed.json()}
    assert names == {"Welcome", "Welcome (copy)", "Welcome (copy 2)"}


@pytest.mark.asyncio
async def test_merge_fields_endpoint_describes_required_unsubscribe(
    db_session, test_client_factory
):
    user, _ = await _make_user_and_org(db_session, "template_fields")
    async with test_client_factory(user) as client:
        response = await client.get("/api/v1/email/templates/merge-fields")

    assert response.status_code == 200
    fields = {item["name"]: item for item in response.json()}
    assert "business_name" in fields
    assert fields["unsubscribe_link"]["required"] is True
    assert fields["unsubscribe_link"]["sample"].startswith("https://")


@pytest.mark.asyncio
async def test_oversized_source_is_rejected(db_session, test_client_factory):
    user, _ = await _make_user_and_org(db_session, "template_size")
    async with test_client_factory(user) as client:
        response = await _create(client, _payload(source="a" * (256 * 1024 + 1)))

    assert response.status_code == 422
    assert "limit" in response.json()["detail"]


class _RecordingAdapter:
    def __init__(self, sent: list):
        self.sent = sent

    async def send(self, message):
        self.sent.append(message)
        return ProviderSendResult(provider_message_id="fake-provider-id")


@pytest.mark.asyncio
async def test_test_send_goes_only_to_the_signed_in_user(
    db_session, test_client_factory, monkeypatch
):
    user, org_id = await _make_user_and_org(
        db_session, "template_send", email="Owner@Apex-Software-Demo.com"
    )
    sent: list = []

    async def fake_adapter(organization_id, sender):
        return _RecordingAdapter(sent)

    monkeypatch.setattr("api.routes.email._adapter_for_sender", fake_adapter)

    async with test_client_factory(user) as client:
        sender = await client.post("/api/v1/email/senders", json=SMTP_SENDER)
        assert sender.status_code == 201, sender.text
        await db_session.mark_email_sender_verified(org_id, sender.json()["uuid"])
        created = await _create(client)
        uuid = created.json()["uuid"]
        response = await client.post(
            f"/api/v1/email/templates/{uuid}/test-send",
            json={},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["to_email"] == "owner@apex-software-demo.com"
    assert len(sent) == 1
    message = sent[0]
    assert message.to_email == "owner@apex-software-demo.com"
    assert message.subject == "Hi Acme Demo Co."
    assert "Acme Demo Co." in message.html_body
    assert "https://boloagent.com/unsubscribe/preview-sample" in message.html_body
    assert message.headers == {"X-Bolo-Test-Send": "email-template"}


@pytest.mark.asyncio
async def test_test_send_rejects_a_recipient_field(
    db_session, test_client_factory, monkeypatch
):
    """There is no recipient field, and extra fields are refused outright."""
    user, _ = await _make_user_and_org(
        db_session, "template_send_extra", email="owner@apex-software-demo.com"
    )
    sent: list = []

    async def fake_adapter(organization_id, sender):
        return _RecordingAdapter(sent)

    monkeypatch.setattr("api.routes.email._adapter_for_sender", fake_adapter)

    async with test_client_factory(user) as client:
        sender = await client.post("/api/v1/email/senders", json=SMTP_SENDER)
        assert sender.status_code == 201, sender.text
        created = await _create(client)
        uuid = created.json()["uuid"]
        response = await client.post(
            f"/api/v1/email/templates/{uuid}/test-send",
            json={"to_email": "lead@apex-software-demo.com"},
        )

    assert response.status_code == 422
    assert sent == []


@pytest.mark.asyncio
async def test_test_send_requires_an_account_address_and_a_sender(
    db_session, test_client_factory
):
    no_email_user, _ = await _make_user_and_org(db_session, "template_no_email")
    async with test_client_factory(no_email_user) as client:
        created = await _create(client)
        uuid = created.json()["uuid"]
        missing_email = await client.post(
            f"/api/v1/email/templates/{uuid}/test-send", json={}
        )
    assert missing_email.status_code == 400
    assert "email address" in missing_email.json()["detail"]

    user, _ = await _make_user_and_org(
        db_session, "template_no_sender", email="owner@apex-software-demo.com"
    )
    async with test_client_factory(user) as client:
        created = await _create(client)
        uuid = created.json()["uuid"]
        missing_sender = await client.post(
            f"/api/v1/email/templates/{uuid}/test-send", json={}
        )
    assert missing_sender.status_code == 400
    assert "sender" in missing_sender.json()["detail"]


@pytest.mark.asyncio
async def test_preview_returns_429_when_the_limit_is_exhausted(
    db_session, test_client_factory, monkeypatch
):
    user, _ = await _make_user_and_org(db_session, "template_preview_limit")

    async def deny(**kwargs):
        return False

    monkeypatch.setattr("api.routes.email.allow_template_preview", deny)

    async with test_client_factory(user) as client:
        response = await client.post(
            "/api/v1/email/templates/preview",
            json={
                "subject": "Hi",
                "source_format": "html",
                "source": "<p>Hello</p>",
            },
        )

    assert response.status_code == 429
    assert "60" in response.json()["detail"]


@pytest.mark.asyncio
async def test_test_send_returns_429_and_sends_nothing_when_limited(
    db_session, test_client_factory, monkeypatch
):
    user, org_id = await _make_user_and_org(
        db_session, "template_send_limit", email="owner@apex-software-demo.com"
    )
    sent: list = []

    async def fake_adapter(organization_id, sender):
        return _RecordingAdapter(sent)

    async def deny(**kwargs):
        return False

    monkeypatch.setattr("api.routes.email._adapter_for_sender", fake_adapter)
    monkeypatch.setattr("api.routes.email.allow_template_test_send", deny)

    async with test_client_factory(user) as client:
        sender = await client.post("/api/v1/email/senders", json=SMTP_SENDER)
        assert sender.status_code == 201, sender.text
        await db_session.mark_email_sender_verified(org_id, sender.json()["uuid"])
        created = await _create(client)
        uuid = created.json()["uuid"]
        response = await client.post(
            f"/api/v1/email/templates/{uuid}/test-send", json={}
        )

    assert response.status_code == 429
    assert "10" in response.json()["detail"]
    assert "Nothing was sent" in response.json()["detail"]
    assert sent == []


@pytest.mark.asyncio
async def test_test_send_refuses_an_unverified_sender(
    db_session, test_client_factory, monkeypatch
):
    user, org_id = await _make_user_and_org(
        db_session, "template_send_unverified", email="owner@apex-software-demo.com"
    )
    sent: list = []

    async def fake_adapter(organization_id, sender):
        return _RecordingAdapter(sent)

    monkeypatch.setattr("api.routes.email._adapter_for_sender", fake_adapter)

    async with test_client_factory(user) as client:
        sender = await client.post("/api/v1/email/senders", json=SMTP_SENDER)
        assert sender.status_code == 201, sender.text
        sender_uuid = sender.json()["uuid"]
        created = await _create(client)
        uuid = created.json()["uuid"]
        refused = await client.post(
            f"/api/v1/email/templates/{uuid}/test-send", json={}
        )
        assert refused.status_code == 400
        assert "Verify the sender" in refused.json()["detail"]
        assert sent == []

        await db_session.mark_email_sender_verified(org_id, sender_uuid)
        accepted = await client.post(
            f"/api/v1/email/templates/{uuid}/test-send", json={}
        )

    assert accepted.status_code == 200, accepted.text
    assert len(sent) == 1
