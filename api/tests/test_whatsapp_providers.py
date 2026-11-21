"""Unit tests for the pluggable WhatsApp messaging providers."""

from unittest.mock import AsyncMock, patch

import pytest

from api.services.whatsapp.providers import (
    WhatsAppProviderError,
    WhatsAppSendRequest,
    available_providers,
    build_provider,
    cloud_api,
)
from api.services.whatsapp.providers.cloud_api import (
    CloudApiProvider,
    param_count,
)

CONFIG = {"access_token": "token-123", "waba_id": "waba-1"}


def _template_payload():
    return {
        "data": [
            {
                "name": "promo",
                "language": "en",
                "status": "APPROVED",
                "category": "MARKETING",
                "components": [
                    {"type": "HEADER", "format": "TEXT"},
                    {"type": "BODY", "text": "Hi {{1}}, about {{2}}."},
                ],
            },
            {"name": "no-language"},
        ]
    }


def test_cloud_api_registers_itself():
    assert cloud_api.PROVIDER_NAME in available_providers()
    provider = build_provider(cloud_api.PROVIDER_NAME, CONFIG)
    assert isinstance(provider, CloudApiProvider)


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="Unknown WhatsApp messaging provider"):
        build_provider("some-bsp", CONFIG)


def test_cloud_api_requires_credentials():
    with pytest.raises(ValueError, match="access_token"):
        CloudApiProvider({"waba_id": "waba-1"})


def test_param_count_counts_positional_placeholders():
    assert param_count("Hi {{1}}, about {{2}}.") == 2
    assert param_count("Hi {{ 3 }}") == 1
    assert param_count(None) == 0


@pytest.mark.asyncio
async def test_list_templates_parses_body_and_skips_incomplete_rows():
    with patch.object(
        cloud_api,
        "_graph_request",
        AsyncMock(return_value=_template_payload()),
    ) as request:
        templates = await CloudApiProvider(CONFIG).list_templates()

    assert [t.name for t in templates] == ["promo"]
    template = templates[0]
    assert template.approved is True
    assert template.param_count == 2
    assert template.body_text == "Hi {{1}}, about {{2}}."
    url = request.await_args.args[1]
    assert url.endswith("/waba-1/message_templates")


@pytest.mark.asyncio
async def test_send_template_posts_the_documented_payload():
    with patch.object(
        cloud_api,
        "_graph_request",
        AsyncMock(return_value={"messages": [{"id": "wamid.1"}]}),
    ) as request:
        receipt = await CloudApiProvider(CONFIG).send_template(
            WhatsAppSendRequest(
                phone_number_id="phone-1",
                to_e164="+15551234567",
                template_name="promo",
                language="en",
                body_params=("Acme", "roofing"),
            )
        )

    method = request.await_args.args[0]
    url = request.await_args.args[1]
    body = request.await_args.kwargs["json_body"]
    assert method == "post"
    assert url.endswith("/phone-1/messages")
    assert body["to"] == "15551234567"
    assert body["type"] == "template"
    assert body["template"]["name"] == "promo"
    assert body["template"]["language"] == {"code": "en"}
    assert body["template"]["components"] == [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": "Acme"},
                {"type": "text", "text": "roofing"},
            ],
        }
    ]
    assert receipt.message_id == "wamid.1"
    assert receipt.provider == cloud_api.PROVIDER_NAME


@pytest.mark.asyncio
async def test_send_template_omits_components_without_params():
    with patch.object(
        cloud_api,
        "_graph_request",
        AsyncMock(return_value={"messages": [{"id": "wamid.2"}]}),
    ) as request:
        await CloudApiProvider(CONFIG).send_template(
            WhatsAppSendRequest(
                phone_number_id="phone-1",
                to_e164="+15551234567",
                template_name="promo",
                language="en",
            )
        )

    assert "components" not in request.await_args.kwargs["json_body"]["template"]


@pytest.mark.asyncio
async def test_send_without_a_message_id_is_an_error():
    with patch.object(
        cloud_api, "_graph_request", AsyncMock(return_value={"messages": []})
    ):
        with pytest.raises(WhatsAppProviderError, match="no message id"):
            await CloudApiProvider(CONFIG).send_template(
                WhatsAppSendRequest(
                    phone_number_id="phone-1",
                    to_e164="+15551234567",
                    template_name="promo",
                    language="en",
                )
            )
