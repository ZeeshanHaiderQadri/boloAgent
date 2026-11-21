"""Meta's WhatsApp Cloud API as a messaging provider.

The owner's system-user access token and WhatsApp Business Account id come
from their own saved configuration; Bolo never supplies either. Requests are
the two documented Graph calls: list the account's message templates, and ask
one of its numbers to send one approved template.
"""

from __future__ import annotations

import json
import re
from typing import Any

import aiohttp
from loguru import logger

from api.services.whatsapp.providers.base import (
    WhatsAppProviderError,
    WhatsAppSendReceipt,
    WhatsAppSendRequest,
    WhatsAppTemplate,
)
from api.services.whatsapp.providers.registry import register

PROVIDER_NAME = "whatsapp_cloud_api"

# Pinned to the version the WhatsApp telephony provider answers calls on.
GRAPH_API_BASE_URL = "https://graph.facebook.com/v23.0"

TEMPLATE_FIELDS = "name,language,status,category,components"
TEMPLATE_PAGE_LIMIT = 100
# A WABA holds a bounded number of templates; this only stops a paging loop
# from running away on a malformed ``next`` link.
MAX_TEMPLATE_PAGES = 20

_PARAM_RE = re.compile(r"\{\{\s*\d+\s*\}\}")


def param_count(body: str | None) -> int:
    return len(_PARAM_RE.findall(body or ""))


def _template_from_payload(item: dict[str, Any]) -> WhatsAppTemplate | None:
    name = str(item.get("name") or "").strip()
    language = str(item.get("language") or "").strip()
    if not name or not language:
        return None
    body_text: str | None = None
    for component in item.get("components") or []:
        if (
            isinstance(component, dict)
            and str(component.get("type") or "").upper() == "BODY"
        ):
            body_text = component.get("text")
            break
    return WhatsAppTemplate(
        name=name,
        language=language,
        status=str(item.get("status") or "UNKNOWN").upper(),
        category=item.get("category"),
        body_text=body_text,
        param_count=param_count(body_text),
    )


async def _graph_request(
    method: str,
    url: str,
    access_token: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with aiohttp.ClientSession() as session:
            request = getattr(session, method)
            async with request(
                url, headers=headers, params=params, json=json_body
            ) as response:
                raw = await response.text()
                try:
                    payload = json.loads(raw) if raw else {}
                except ValueError:
                    payload = {"raw": raw[:500]}
                if response.status >= 300:
                    raise WhatsAppProviderError(
                        f"Meta Graph API {response.status}: {raw[:500]}",
                        status_code=response.status,
                    )
    except WhatsAppProviderError:
        raise
    except Exception as exc:
        raise WhatsAppProviderError(f"Meta Graph API request failed: {exc}") from exc
    return payload if isinstance(payload, dict) else {}


class CloudApiProvider:
    """Send approved templates through Meta's Cloud API."""

    name = PROVIDER_NAME

    def __init__(self, config: dict[str, Any]) -> None:
        self.access_token = str(config.get("access_token") or "")
        self.waba_id = str(config.get("waba_id") or "")
        for field in ("access_token", "waba_id"):
            if not getattr(self, field):
                raise ValueError(f"{field} is required for the WhatsApp Cloud API")

    async def list_templates(self) -> list[WhatsAppTemplate]:
        url: str | None = f"{GRAPH_API_BASE_URL}/{self.waba_id}/message_templates"
        params: dict[str, Any] | None = {
            "fields": TEMPLATE_FIELDS,
            "limit": TEMPLATE_PAGE_LIMIT,
        }
        found: list[WhatsAppTemplate] = []
        visited: set[str] = set()
        for _ in range(MAX_TEMPLATE_PAGES):
            if not url or url in visited:
                raise WhatsAppProviderError(
                    "Meta's template listing repeated a page or lost its next link."
                )
            visited.add(url)
            payload = await _graph_request("get", url, self.access_token, params=params)
            data = payload.get("data")
            if not isinstance(data, list):
                raise WhatsAppProviderError(
                    "Meta answered the template listing without a data list."
                )
            for item in data:
                if isinstance(item, dict):
                    template = _template_from_payload(item)
                    if template is not None:
                        found.append(template)
            paging = payload.get("paging")
            url = paging.get("next") if isinstance(paging, dict) else None
            params = None
            if not url:
                return found
        raise WhatsAppProviderError(
            f"Meta's template listing ran past {MAX_TEMPLATE_PAGES} pages."
        )

    async def send_template(self, request: WhatsAppSendRequest) -> WhatsAppSendReceipt:
        components: list[dict[str, Any]] = []
        if request.body_params:
            components.append(
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": value} for value in request.body_params
                    ],
                }
            )
        template: dict[str, Any] = {
            "name": request.template_name,
            "language": {"code": request.language},
        }
        if components:
            template["components"] = components
        payload = await _graph_request(
            "post",
            f"{GRAPH_API_BASE_URL}/{request.phone_number_id}/messages",
            self.access_token,
            json_body={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": request.to_e164.lstrip("+"),
                "type": "template",
                "template": template,
            },
        )
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            raise WhatsAppProviderError(
                "Meta accepted the request but returned no message id."
            )
        message_id = str((messages[0] or {}).get("id") or "")
        if not message_id:
            raise WhatsAppProviderError(
                "Meta accepted the request but returned no message id."
            )
        logger.info(
            "WhatsApp message {} sent from phone number id {}",
            message_id,
            request.phone_number_id,
        )
        return WhatsAppSendReceipt(
            provider=self.name,
            message_id=message_id,
            phone_number_id=request.phone_number_id,
        )


def _factory(config: dict[str, Any]) -> CloudApiProvider:
    return CloudApiProvider(config)


register(PROVIDER_NAME, _factory)
