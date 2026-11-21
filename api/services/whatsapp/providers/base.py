"""The messaging-provider seam for WhatsApp.

A provider is whatever the workspace owner connected: Meta's Cloud API
directly, or a BSP reselling it. Bolo only ever holds the owner's own
credentials and never provides a WhatsApp account of its own.

Providers speak in approved templates because WhatsApp refuses
business-initiated free-form messages: the template must already exist in the
business account and be approved by Meta.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class WhatsAppTemplate:
    """One Meta message template, as the provider reports it."""

    name: str
    language: str
    status: str
    category: str | None = None
    body_text: str | None = None
    param_count: int = 0

    @property
    def approved(self) -> bool:
        return self.status.upper() == "APPROVED"


@dataclass(frozen=True)
class WhatsAppSendRequest:
    phone_number_id: str
    to_e164: str
    template_name: str
    language: str
    body_params: tuple[str, ...] = ()


@dataclass(frozen=True)
class WhatsAppSendReceipt:
    provider: str
    message_id: str
    phone_number_id: str


class WhatsAppProviderError(Exception):
    """The provider refused a request or could not be reached."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class WhatsAppProvider(Protocol):
    name: str

    async def list_templates(self) -> list[WhatsAppTemplate]: ...

    async def send_template(
        self, request: WhatsAppSendRequest
    ) -> WhatsAppSendReceipt: ...
