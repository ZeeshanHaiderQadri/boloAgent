"""Outbound WhatsApp messages through the workspace's own provider.

The account, token and numbers all come from the organization's WhatsApp
configuration; the provider is whatever they connected (Meta directly or a
BSP). Only Meta-approved message templates can be sent, because WhatsApp
refuses business-initiated free-form messages.

``preview_message`` resolves everything and returns the exact request without
sending. ``send_message`` repeats the same checks, claims an abuse-limit token
and performs the single provider call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from loguru import logger

from api.db import db_client
from api.db.models import TelephonyConfigurationModel, TelephonyPhoneNumberModel
from api.services.call_concurrency.rate_limiter import (
    RateLimiterUnavailable,
    rate_limiter,
)
from api.services.whatsapp.countries import resolve_lead_phone
from api.services.whatsapp.disclaimer import WHATSAPP_DISCLAIMER_VERSION
from api.services.whatsapp.providers import (
    WhatsAppProviderError,
    WhatsAppSendRequest,
    WhatsAppTemplate,
    available_providers,
    build_provider,
)
from api.services.whatsapp.providers.cloud_api import PROVIDER_NAME as CLOUD_API

WHATSAPP_CONFIG_PROVIDER = "whatsapp"
MAX_BODY_PARAMS = 20
MAX_PARAM_CHARS = 1024

SEND_LIMIT_PER_HOUR_ORG = 50
SEND_LIMIT_PER_HOUR_USER = 20
_WINDOW_SECONDS = 3600

_PARAM_RE = re.compile(r"\{\{\s*(\d+)\s*\}\}")


class WhatsAppMessagingError(Exception):
    """A refusal or failure whose message is safe to show the user."""


class WhatsAppConfigNotFoundError(WhatsAppMessagingError):
    pass


class WhatsAppConfigNotChosenError(WhatsAppMessagingError):
    pass


class WhatsAppNumberError(WhatsAppMessagingError):
    pass


class WhatsAppLeadPhoneError(WhatsAppMessagingError):
    pass


class WhatsAppTemplateNotFoundError(WhatsAppMessagingError):
    pass


class WhatsAppTemplateNotApprovedError(WhatsAppMessagingError):
    pass


class WhatsAppParamCountError(WhatsAppMessagingError):
    pass


class WhatsAppDisclaimerRequiredError(WhatsAppMessagingError):
    pass


class WhatsAppRateLimitedError(WhatsAppMessagingError):
    pass


class WhatsAppRateLimitUnavailableError(WhatsAppMessagingError):
    pass


class WhatsAppProviderUnavailableError(WhatsAppMessagingError):
    pass


@dataclass(frozen=True)
class PreparedWhatsAppSend:
    config: TelephonyConfigurationModel
    number: TelephonyPhoneNumberModel
    provider: Any
    template: WhatsAppTemplate
    to_e164: str
    body_params: tuple[str, ...]

    def request(self) -> WhatsAppSendRequest:
        return WhatsAppSendRequest(
            phone_number_id=self.number.external_id or "",
            to_e164=self.to_e164,
            template_name=self.template.name,
            language=self.template.language,
            body_params=self.body_params,
        )

    def preview_text(self) -> str:
        return render_preview(self.template.body_text, self.body_params)


def render_preview(body: str | None, params: tuple[str, ...]) -> str:
    """Fill Meta's positional placeholders for display only."""

    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1)) - 1
        if 0 <= index < len(params):
            return params[index]
        return match.group(0)

    return _PARAM_RE.sub(replace, body or "").strip()


def _digits(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


async def _require_disclaimer(organization_id: int, user_id: int) -> None:
    acceptance = await db_client.get_whatsapp_disclaimer(organization_id, user_id)
    if acceptance is None or acceptance.text_version != WHATSAPP_DISCLAIMER_VERSION:
        raise WhatsAppDisclaimerRequiredError(
            "Accept the WhatsApp disclaimer in the app before sending messages."
        )


async def _config(
    organization_id: int, config_id: int | None
) -> TelephonyConfigurationModel:
    configs = await db_client.list_telephony_configurations_by_provider(
        organization_id, WHATSAPP_CONFIG_PROVIDER, active_only=True
    )
    if config_id is not None:
        config = next((c for c in configs if c.id == config_id), None)
        if config is None:
            raise WhatsAppConfigNotFoundError(
                "WhatsApp configuration not found in this workspace."
            )
        return config
    if not configs:
        raise WhatsAppConfigNotFoundError(
            "No active WhatsApp Business Account is connected in this workspace."
        )
    if len(configs) > 1:
        raise WhatsAppConfigNotChosenError(
            "This workspace has several WhatsApp accounts. Choose one with "
            "whatsapp_account_id."
        )
    return configs[0]


async def _sending_number(
    config: TelephonyConfigurationModel, from_number: str | None
) -> TelephonyPhoneNumberModel:
    numbers = await db_client.list_phone_numbers_for_config(config.id)
    usable = [n for n in numbers if n.is_active and n.external_id]
    if from_number:
        wanted = _digits(from_number)
        match = next(
            (n for n in usable if _digits(n.address_normalized) == wanted), None
        )
        if match is None:
            raise WhatsAppNumberError(
                f"{from_number} is not an active sending number on WhatsApp "
                f"account {config.name!r}."
            )
        return match
    if not usable:
        raise WhatsAppNumberError(
            f"WhatsApp account {config.name!r} has no active number that Meta "
            "gave a phone number id to. Sync the account's numbers first."
        )
    if len(usable) > 1:
        raise WhatsAppNumberError(
            f"WhatsApp account {config.name!r} has several numbers. Choose one "
            "with from_number."
        )
    return usable[0]


def _provider(config: TelephonyConfigurationModel, provider_name: str | None) -> Any:
    name = (provider_name or CLOUD_API).strip()
    if name not in available_providers():
        raise WhatsAppProviderUnavailableError(
            f"WhatsApp messaging provider {name!r} is not available. "
            f"Available: {', '.join(available_providers())}."
        )
    credentials = dict(config.credentials or {})
    credentials["provider"] = name
    try:
        return build_provider(name, credentials)
    except ValueError as exc:
        raise WhatsAppProviderUnavailableError(str(exc)) from exc


async def _approved_template(
    provider: Any, name: str, language: str, param_count: int
) -> WhatsAppTemplate:
    try:
        templates = await provider.list_templates()
    except WhatsAppProviderError as exc:
        raise WhatsAppProviderUnavailableError(
            f"WhatsApp could not list the account's templates: {exc}"
        ) from exc
    match = next(
        (t for t in templates if t.name == name and t.language == language), None
    )
    if match is None:
        raise WhatsAppTemplateNotFoundError(
            f"No Meta message template named {name!r} in language {language!r} "
            "exists on this WhatsApp account. Use the template names from "
            "list_whatsapp_templates."
        )
    if not match.approved:
        raise WhatsAppTemplateNotApprovedError(
            f"Meta template {name!r} ({language}) is {match.status}, so it "
            "cannot be sent until Meta approves it."
        )
    if match.param_count != param_count:
        raise WhatsAppParamCountError(
            f"Template {name!r} ({language}) takes {match.param_count} body "
            f"parameter(s); {param_count} were given."
        )
    return match


async def _prepare(
    organization_id: int,
    *,
    lead_id: int,
    template_name: str,
    language: str,
    body_params: tuple[str, ...],
    config_id: int | None,
    from_number: str | None,
    provider_name: str | None,
) -> PreparedWhatsAppSend:
    if len(body_params) > MAX_BODY_PARAMS:
        raise WhatsAppMessagingError(
            f"At most {MAX_BODY_PARAMS} template parameters are supported."
        )
    if any(len(value) > MAX_PARAM_CHARS for value in body_params):
        raise WhatsAppMessagingError(
            f"Each template parameter must be at most {MAX_PARAM_CHARS} characters."
        )

    config = await _config(organization_id, config_id)
    number = await _sending_number(config, from_number)
    provider = _provider(config, provider_name)

    lead = await db_client.get_lead(lead_id, organization_id)
    if lead is None:
        raise WhatsAppMessagingError("Lead not found in this workspace.")
    resolution = resolve_lead_phone(
        lead.phone, lead.phone_e164, lead.country, number.country_code
    )
    if not resolution.phone_e164:
        raise WhatsAppLeadPhoneError(
            "This lead has no usable phone number in international format, so "
            "no WhatsApp message can be addressed to it."
        )

    template = await _approved_template(
        provider, template_name, language, len(body_params)
    )
    return PreparedWhatsAppSend(
        config=config,
        number=number,
        provider=provider,
        template=template,
        to_e164=resolution.phone_e164,
        body_params=body_params,
    )


async def list_accounts(organization_id: int) -> dict:
    """The workspace's WhatsApp accounts with their sendable numbers."""
    configs = await db_client.list_telephony_configurations_by_provider(
        organization_id, WHATSAPP_CONFIG_PROVIDER, active_only=True
    )
    accounts = []
    for config in configs:
        numbers = await db_client.list_phone_numbers_for_config(config.id)
        accounts.append(
            {
                "config_id": config.id,
                "name": config.name,
                "numbers": [
                    {
                        "from_number": number.address_normalized,
                        "label": number.label,
                        "phone_number_id": number.external_id,
                        "active": bool(number.is_active),
                    }
                    for number in numbers
                ],
            }
        )
    return {
        "accounts": accounts,
        "messaging_providers": list(available_providers()),
        "note": (
            "Concurrency and calling use the same WhatsApp Business Account; "
            "messages are sent from one of its numbers."
        ),
    }


async def list_templates(
    organization_id: int,
    *,
    config_id: int | None = None,
    provider_name: str | None = None,
) -> dict:
    """Meta message templates on the account, approved or not."""
    config = await _config(organization_id, config_id)
    provider = _provider(config, provider_name)
    try:
        templates = await provider.list_templates()
    except WhatsAppProviderError as exc:
        raise WhatsAppProviderUnavailableError(
            f"WhatsApp could not list the account's templates: {exc}"
        ) from exc
    return {
        "config_id": config.id,
        "name": config.name,
        "templates": [
            {
                "name": template.name,
                "language": template.language,
                "status": template.status,
                "category": template.category,
                "parameters": template.param_count,
                "body_preview": (template.body_text or "")[:300],
                "sendable": template.approved,
            }
            for template in templates
        ],
        "note": (
            "These are Meta-approved message templates. Only sendable ones can "
            "be sent; Bolo's click-to-chat templates are separate."
        ),
    }


async def preview_message(
    organization_id: int,
    user_id: int,
    *,
    lead_id: int,
    template_name: str,
    language: str,
    body_params: tuple[str, ...] = (),
    config_id: int | None = None,
    from_number: str | None = None,
    provider_name: str | None = None,
) -> dict:
    """Resolve a send completely without contacting Meta's send endpoint."""
    await _require_disclaimer(organization_id, user_id)
    prepared = await _prepare(
        organization_id,
        lead_id=lead_id,
        template_name=template_name,
        language=language,
        body_params=body_params,
        config_id=config_id,
        from_number=from_number,
        provider_name=provider_name,
    )
    return {
        "config_id": prepared.config.id,
        "account": prepared.config.name,
        "provider": prepared.provider.name,
        "from_number": prepared.number.address_normalized,
        "to_e164": prepared.to_e164,
        "template_name": prepared.template.name,
        "language": prepared.template.language,
        "rendered_body": prepared.preview_text(),
        "parameters": len(prepared.body_params),
        "note": (
            "Nothing was sent. Sending is a separate approved step, and only "
            "message people who agreed to hear from you."
        ),
    }


async def _allow_send(*, user_id: int, organization_id: int) -> bool:
    try:
        if not await rate_limiter.acquire_token_in_window(
            f"whatsapp:send:org:{organization_id}",
            limit=SEND_LIMIT_PER_HOUR_ORG,
            window_seconds=_WINDOW_SECONDS,
        ):
            return False
        return await rate_limiter.acquire_token_in_window(
            f"whatsapp:send:user:{user_id}",
            limit=SEND_LIMIT_PER_HOUR_USER,
            window_seconds=_WINDOW_SECONDS,
        )
    except RateLimiterUnavailable as exc:
        raise WhatsAppRateLimitUnavailableError(
            "WhatsApp sending is temporarily unavailable."
        ) from exc


async def send_message(
    organization_id: int,
    user_id: int,
    *,
    lead_id: int,
    template_name: str,
    language: str,
    body_params: tuple[str, ...] = (),
    config_id: int | None = None,
    from_number: str | None = None,
    provider_name: str | None = None,
) -> dict:
    """Send one approved template to one lead through the owner's provider."""
    await _require_disclaimer(organization_id, user_id)
    if not await _allow_send(user_id=user_id, organization_id=organization_id):
        raise WhatsAppRateLimitedError(
            "Too many WhatsApp messages from this workspace; try again later."
        )
    prepared = await _prepare(
        organization_id,
        lead_id=lead_id,
        template_name=template_name,
        language=language,
        body_params=body_params,
        config_id=config_id,
        from_number=from_number,
        provider_name=provider_name,
    )
    try:
        receipt = await prepared.provider.send_template(prepared.request())
    except WhatsAppProviderError as exc:
        raise WhatsAppProviderUnavailableError(
            f"WhatsApp did not accept the message: {exc}"
        ) from exc
    logger.info(
        "WhatsApp message {} sent to number ending {} via {}",
        receipt.message_id,
        prepared.to_e164[-4:],
        receipt.provider,
    )
    return {
        "message_id": receipt.message_id,
        "provider": receipt.provider,
        "config_id": prepared.config.id,
        "from_number": prepared.number.address_normalized,
        "to_e164": prepared.to_e164,
        "template_name": prepared.template.name,
        "language": prepared.template.language,
    }
