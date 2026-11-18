"""Deep MJML nesting is a validation error from the API, never a 500."""

from types import SimpleNamespace

import pytest


def _payload(name: str, source: str) -> dict:
    return {
        "name": name,
        "subject": "Hi",
        "source_format": "mjml",
        "source": source,
    }


async def _user(db_session):
    user, _ = await db_session.get_or_create_user_by_provider_id("template_deep_user")
    user_id = user.id
    organization, _ = await db_session.get_or_create_organization_by_provider_id(
        "template_deep_org", user_id
    )
    organization_id = organization.id
    await db_session.update_user_selected_organization(user_id, organization_id)
    return SimpleNamespace(
        id=user_id, selected_organization_id=organization_id, email=None
    )


@pytest.mark.asyncio
async def test_deeply_nested_mjml_is_a_validation_error_not_a_server_error(
    db_session, test_client_factory
):
    user = await _user(db_session)
    nested = "<div>" * 200 + "x" + "</div>" * 200
    source = (
        "<mjml><mj-body><mj-section><mj-column>"
        f"<mj-text>{nested}</mj-text>"
        "</mj-column></mj-section></mj-body></mjml>"
    )
    async with test_client_factory(user) as client:
        created = await client.post(
            "/api/v1/email/templates", json=_payload("Deep", source)
        )
        preview = await client.post(
            "/api/v1/email/templates/preview",
            json={
                "subject": "Hi",
                "source_format": "mjml",
                "source": source,
            },
        )

    assert created.status_code == 422
    assert "depth" in created.json()["detail"]
    assert preview.status_code == 422
    assert "depth" in preview.json()["detail"]
