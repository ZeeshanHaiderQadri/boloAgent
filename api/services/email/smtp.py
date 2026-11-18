"""SMTP adapter, including the Hostinger preset.

Hostinger's shared mailboxes only accept authenticated SMTP on
``smtp.hostinger.com:465`` with implicit TLS, so that combination is available
as a named preset. Any other SMTP server works by passing explicit settings.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from collections.abc import Mapping
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any, ClassVar

from api.enums import EmailProvider
from api.services.email.adapter import (
    EmailAdapter,
    EmailConfigurationError,
    EmailDeliveryError,
    EmailSenderConfig,
    OutboundEmail,
    ProviderSendResult,
)

HOSTINGER_SMTP_PRESET: dict[str, Any] = {
    "host": "smtp.hostinger.com",
    "port": 465,
    "security": "ssl",
}

DEFAULT_TIMEOUT_SECONDS = 15

# Headers the adapter owns; a caller cannot override them through ``headers``.
_RESERVED_HEADERS = {"from", "to", "subject", "reply-to", "message-id"}


def _resolve_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    if settings.get("preset") == "hostinger":
        resolved.update(HOSTINGER_SMTP_PRESET)
    resolved.update({k: v for k, v in settings.items() if k != "preset"})

    host = str(resolved.get("host") or "").strip()
    if not host:
        raise EmailConfigurationError("SMTP settings must include a host")

    port = int(resolved.get("port") or 465)
    security = str(resolved.get("security") or ("ssl" if port == 465 else "starttls"))
    if security not in {"ssl", "starttls"}:
        raise EmailConfigurationError("SMTP security must be 'ssl' or 'starttls'")

    resolved.update(host=host, port=port, security=security)
    return resolved


def _without_newlines(value: str, *, what: str) -> str:
    if "\r" in value or "\n" in value:
        raise EmailConfigurationError(f"{what} must not contain line breaks")
    return value


def _build_message(config: EmailSenderConfig, message: OutboundEmail) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr(
        (
            _without_newlines(config.from_name, what="From name"),
            _without_newlines(config.from_email, what="From address"),
        )
    )
    to_name = (
        _without_newlines(message.to_name, what="To name") if message.to_name else ""
    )
    msg["To"] = formataddr(
        (to_name, _without_newlines(message.to_email, what="To address"))
    )
    msg["Subject"] = _without_newlines(message.subject, what="Subject")
    if config.reply_to:
        msg["Reply-To"] = _without_newlines(config.reply_to, what="Reply-To address")

    for name, value in message.headers.items():
        if name.lower() in _RESERVED_HEADERS:
            continue
        _without_newlines(str(name), what="Header name")
        _without_newlines(str(value), what=f"Header {name}")
        msg[name] = str(value)

    message_id = str(message.headers.get("Message-ID") or "").strip()
    if not message_id:
        domain = config.from_email.rpartition("@")[2] or None
        message_id = make_msgid(domain=domain)
    msg["Message-ID"] = message_id

    if message.text_body:
        msg.set_content(message.text_body)
        msg.add_alternative(message.html_body, subtype="html")
    else:
        msg.set_content(message.html_body, subtype="html")
    return msg


class SmtpEmailAdapter(EmailAdapter):
    """Universal SMTP sender. Blocking I/O runs in a worker thread."""

    provider: ClassVar[EmailProvider] = EmailProvider.SMTP

    def __init__(self, config: EmailSenderConfig, credentials: Mapping[str, Any]):
        super().__init__(config, credentials)
        self._settings = _resolve_settings(config.settings)

        username = str(credentials.get("username") or "").strip()
        password = str(credentials.get("password") or "")
        if not username or not password:
            raise EmailConfigurationError(
                "SMTP credentials must include a username and password"
            )
        self._username = username
        self._password = password
        self._timeout = float(
            self._settings.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS
        )

    @property
    def settings(self) -> Mapping[str, Any]:
        """Resolved settings (preset applied), safe to show in the UI."""
        return dict(self._settings)

    @property
    def message_id_domain(self) -> str | None:
        return self._config.from_email.rpartition("@")[2] or None

    def _connection(self) -> smtplib.SMTP:
        host = self._settings["host"]
        port = self._settings["port"]
        if self._settings["security"] == "ssl":
            return smtplib.SMTP_SSL(
                host,
                port,
                timeout=self._timeout,
                context=ssl.create_default_context(),
            )
        connection = smtplib.SMTP(host, port, timeout=self._timeout)
        connection.ehlo()
        connection.starttls(context=ssl.create_default_context())
        connection.ehlo()
        return connection

    def _send_blocking(self, message: OutboundEmail) -> str:
        msg = _build_message(self._config, message)
        try:
            with self._connection() as connection:
                connection.login(self._username, self._password)
                connection.send_message(
                    msg,
                    from_addr=self._config.from_email,
                    to_addrs=[message.to_email],
                )
        except smtplib.SMTPAuthenticationError as exc:
            raise EmailConfigurationError(
                "SMTP authentication failed; check the username and password"
            ) from exc
        except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected) as exc:
            raise EmailDeliveryError(
                f"SMTP connection failed: {exc.smtp_code}"
            ) from exc
        except smtplib.SMTPRecipientsRefused as exc:
            code = next(iter(exc.recipients.values()), (0, b""))[0]
            raise EmailDeliveryError(f"SMTP recipient refused (code {code})") from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailDeliveryError(
                f"SMTP delivery failed: {type(exc).__name__}"
            ) from exc
        return str(msg["Message-ID"])

    def _verify_blocking(self) -> None:
        try:
            with self._connection() as connection:
                connection.login(self._username, self._password)
                connection.noop()
        except smtplib.SMTPAuthenticationError as exc:
            raise EmailConfigurationError(
                "SMTP authentication failed; check the username and password"
            ) from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailDeliveryError(
                f"SMTP connection failed: {type(exc).__name__}"
            ) from exc

    async def verify(self) -> None:
        await asyncio.to_thread(self._verify_blocking)

    async def send(self, message: OutboundEmail) -> ProviderSendResult:
        provider_message_id = await asyncio.to_thread(self._send_blocking, message)
        return ProviderSendResult(provider_message_id=provider_message_id)
