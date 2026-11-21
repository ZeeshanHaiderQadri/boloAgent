"""Unit tests for outbound WhatsApp message resolution and sending."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.call_concurrency.rate_limiter import RateLimiterUnavailable
from api.services.whatsapp import messaging
from api.services.whatsapp.disclaimer import WHATSAPP_DISCLAIMER_VERSION
from api.services.whatsapp.messaging import (
    WhatsAppConfigNotChosenError,
    WhatsAppConfigNotFoundError,
    WhatsAppDisclaimerRequiredError,
    WhatsAppLeadPhoneError,
    WhatsAppNumberError,
    WhatsAppParamCountError,
    WhatsAppProviderUnavailableError,
    WhatsAppRateLimitedError,
    WhatsAppRateLimitUnavailableError,
    WhatsAppTemplateNotApprovedError,
    WhatsAppTemplateNotFoundError,
    preview_message,
    send_message,
)
from api.services.whatsapp.providers import WhatsAppProviderError, WhatsAppTemplate

ORG = 42
USER = 9


def _config(config_id=1, name="Sales"):
    return SimpleNamespace(id=config_id, name=name, credentials={"access_token": "t"})


def _number(address="+15550000001", phone_number_id="phone-1"):
    return SimpleNamespace(
        address_normalized=address,
        external_id=phone_number_id,
        label="Sales line",
        is_active=True,
        country_code="US",
    )


def _lead(phone="+15550000002"):
    return SimpleNamespace(
        id=7, phone=phone, phone_e164=phone, country="US", title="Acme"
    )


def _approved(name="promo", language="en", params=1, status="APPROVED"):
    return WhatsAppTemplate(
        name=name,
        language=language,
        status=status,
        category="MARKETING",
        body_text="Hi {{1}}, a quick note.",
        param_count=params,
    )


class _Provider:
    name = "whatsapp_cloud_api"

    def __init__(self, templates=None, error=None):
        self.templates = templates or [_approved()]
        self.error = error
        self.sent = []

    async def list_templates(self):
        if self.error:
            raise self.error
        return self.templates

    async def send_template(self, request):
        self.sent.append(request)
        return SimpleNamespace(
            provider=self.name,
            message_id="wamid.1",
            phone_number_id=request.phone_number_id,
        )


_MISSING = object()


def _patches(provider, configs=None, numbers=None, lead=_MISSING):
    db = SimpleNamespace(
        list_telephony_configurations_by_provider=AsyncMock(
            return_value=[_config()] if configs is None else configs
        ),
        list_phone_numbers_for_config=AsyncMock(
            return_value=[_number()] if numbers is None else numbers
        ),
        get_lead=AsyncMock(return_value=_lead() if lead is _MISSING else lead),
        get_whatsapp_disclaimer=AsyncMock(
            return_value=SimpleNamespace(text_version=WHATSAPP_DISCLAIMER_VERSION)
        ),
    )
    return (
        patch.object(messaging, "db_client", db),
        patch.object(messaging, "build_provider", lambda name, cfg: provider),
        db,
    )


def _call_kwargs(**overrides):
    kwargs = {
        "lead_id": 7,
        "template_name": "promo",
        "language": "en",
        "body_params": ("Acme",),
    }
    kwargs.update(overrides)
    return kwargs


@pytest.mark.asyncio
async def test_preview_resolves_without_sending():
    provider = _Provider()
    db_patch, build_patch, db = _patches(provider)
    with db_patch, build_patch:
        result = await preview_message(ORG, USER, **_call_kwargs())

    assert result["to_e164"] == "+15550000002"
    assert result["from_number"] == "+15550000001"
    assert result["rendered_body"] == "Hi Acme, a quick note."
    assert result["account"] == "Sales"
    assert provider.sent == []
    db.get_lead.assert_awaited_once_with(7, ORG)


@pytest.mark.asyncio
async def test_send_calls_the_provider_once():
    provider = _Provider()
    db_patch, build_patch, _ = _patches(provider)
    with (
        db_patch,
        build_patch,
        patch.object(messaging, "_allow_send", AsyncMock(return_value=True)) as allow,
    ):
        result = await send_message(ORG, USER, **_call_kwargs())

    allow.assert_awaited_once_with(user_id=USER, organization_id=ORG)
    assert len(provider.sent) == 1
    request = provider.sent[0]
    assert request.phone_number_id == "phone-1"
    assert request.to_e164 == "+15550000002"
    assert request.body_params == ("Acme",)
    assert result["message_id"] == "wamid.1"


@pytest.mark.asyncio
async def test_send_is_rate_limited_before_any_provider_work():
    provider = _Provider()
    db_patch, build_patch, _ = _patches(provider)
    with (
        db_patch,
        build_patch,
        patch.object(messaging, "_allow_send", AsyncMock(return_value=False)),
    ):
        with pytest.raises(WhatsAppRateLimitedError, match="Too many"):
            await send_message(ORG, USER, **_call_kwargs())

    assert provider.sent == []


@pytest.mark.asyncio
async def test_rate_limiter_outage_fails_closed():
    provider = _Provider()
    db_patch, build_patch, _ = _patches(provider)
    limiter = SimpleNamespace(
        acquire_token_in_window=AsyncMock(
            side_effect=RateLimiterUnavailable("redis is down")
        )
    )
    with (
        db_patch,
        build_patch,
        patch.object(messaging, "rate_limiter", limiter),
    ):
        with pytest.raises(WhatsAppRateLimitUnavailableError):
            await send_message(ORG, USER, **_call_kwargs())

    assert provider.sent == []


@pytest.mark.asyncio
async def test_disclaimer_must_be_accepted():
    provider = _Provider()
    db_patch, build_patch, db = _patches(provider)
    db.get_whatsapp_disclaimer = AsyncMock(return_value=None)
    with db_patch, build_patch:
        with pytest.raises(WhatsAppDisclaimerRequiredError):
            await preview_message(ORG, USER, **_call_kwargs())


@pytest.mark.asyncio
async def test_missing_config_is_explained():
    provider = _Provider()
    db_patch, build_patch, _ = _patches(provider, configs=[])
    with db_patch, build_patch:
        with pytest.raises(WhatsAppConfigNotFoundError, match="No active"):
            await preview_message(ORG, USER, **_call_kwargs())


@pytest.mark.asyncio
async def test_several_configs_require_a_choice():
    provider = _Provider()
    db_patch, build_patch, _ = _patches(
        provider, configs=[_config(1, "A"), _config(2, "B")]
    )
    with db_patch, build_patch:
        with pytest.raises(WhatsAppConfigNotChosenError):
            await preview_message(ORG, USER, **_call_kwargs())


@pytest.mark.asyncio
async def test_config_must_belong_to_the_workspace():
    provider = _Provider()
    db_patch, build_patch, _ = _patches(provider)
    with db_patch, build_patch:
        with pytest.raises(WhatsAppConfigNotFoundError):
            await preview_message(ORG, USER, **_call_kwargs(config_id=99))


@pytest.mark.asyncio
async def test_several_numbers_require_a_choice():
    provider = _Provider()
    numbers = [_number("+15550000001", "p1"), _number("+15550000003", "p3")]
    db_patch, build_patch, _ = _patches(provider, numbers=numbers)
    with db_patch, build_patch:
        with pytest.raises(WhatsAppNumberError, match="several numbers"):
            await preview_message(ORG, USER, **_call_kwargs())


@pytest.mark.asyncio
async def test_from_number_must_be_active_on_the_account():
    provider = _Provider()
    db_patch, build_patch, _ = _patches(provider)
    with db_patch, build_patch:
        with pytest.raises(WhatsAppNumberError, match="not an active"):
            await preview_message(ORG, USER, **_call_kwargs(from_number="+15559999999"))


@pytest.mark.asyncio
async def test_unmatched_lead_phone_is_refused():
    provider = _Provider()
    db_patch, build_patch, _ = _patches(provider, lead=_lead(phone=None))
    with db_patch, build_patch:
        with pytest.raises(WhatsAppLeadPhoneError):
            await preview_message(ORG, USER, **_call_kwargs())


@pytest.mark.asyncio
async def test_missing_lead_is_scoped():
    provider = _Provider()
    db_patch, build_patch, db = _patches(provider, lead=None)
    with db_patch, build_patch:
        with pytest.raises(messaging.WhatsAppMessagingError, match="not found"):
            await preview_message(ORG, USER, **_call_kwargs())
    db.get_lead.assert_awaited_once_with(7, ORG)


@pytest.mark.asyncio
async def test_template_must_exist_on_the_account():
    provider = _Provider(templates=[_approved(name="other")])
    db_patch, build_patch, _ = _patches(provider)
    with db_patch, build_patch:
        with pytest.raises(WhatsAppTemplateNotFoundError):
            await preview_message(ORG, USER, **_call_kwargs())


@pytest.mark.asyncio
async def test_unapproved_template_is_refused():
    provider = _Provider(templates=[_approved(status="PENDING")])
    db_patch, build_patch, _ = _patches(provider)
    with db_patch, build_patch:
        with pytest.raises(WhatsAppTemplateNotApprovedError, match="PENDING"):
            await preview_message(ORG, USER, **_call_kwargs())


@pytest.mark.asyncio
async def test_parameter_count_must_match_the_template():
    provider = _Provider(templates=[_approved(params=3)])
    db_patch, build_patch, _ = _patches(provider)
    with db_patch, build_patch:
        with pytest.raises(WhatsAppParamCountError, match="takes 3"):
            await preview_message(ORG, USER, **_call_kwargs())


@pytest.mark.asyncio
async def test_provider_failures_are_reported_plainly():
    provider = _Provider(error=WhatsAppProviderError("Meta said no"))
    db_patch, build_patch, _ = _patches(provider)
    with db_patch, build_patch:
        with pytest.raises(WhatsAppProviderUnavailableError, match="Meta said no"):
            await preview_message(ORG, USER, **_call_kwargs())


@pytest.mark.asyncio
async def test_list_accounts_reports_numbers_and_providers():
    provider = _Provider()
    db_patch, build_patch, db = _patches(provider)
    db.list_phone_numbers_for_config = AsyncMock(return_value=[_number()])
    with db_patch, build_patch:
        result = await messaging.list_accounts(ORG)

    assert result["accounts"][0]["config_id"] == 1
    assert result["accounts"][0]["numbers"][0]["phone_number_id"] == "phone-1"
    assert "whatsapp_cloud_api" in result["messaging_providers"]
    db.list_telephony_configurations_by_provider.assert_awaited_once_with(
        ORG, "whatsapp", active_only=True
    )
