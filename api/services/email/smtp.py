"""SMTP adapter, including the Hostinger preset.

Hostinger's shared mailboxes only accept authenticated SMTP on
``smtp.hostinger.com:465`` with implicit TLS, so that combination is available
as a named preset. Any other SMTP server works by passing explicit settings.
"""

from __future__ import annotations

import asyncio
import smtplib
import socket
import ssl
from collections.abc import Mapping
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
from api.services.email.mime import build_mime_message, message_id_domain
from api.utils.url_security import (
    ServiceHostResolutionError,
    resolve_user_configured_service_host,
    validate_user_configured_smtp_port,
)

HOSTINGER_SMTP_PRESET: dict[str, Any] = {
    "host": "smtp.hostinger.com",
    "port": 465,
    "security": "ssl",
}

DEFAULT_TIMEOUT_SECONDS = 15


def _parse_port(value: Any) -> int:
    if value is None or value == "":
        return 465
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise EmailConfigurationError("SMTP port must be a whole number") from None
    if not 1 <= port <= 65535:
        raise EmailConfigurationError("SMTP port must be between 1 and 65535")
    return port


def _resolve_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    if settings.get("preset") == "hostinger":
        resolved.update(HOSTINGER_SMTP_PRESET)
    resolved.update({k: v for k, v in settings.items() if k != "preset"})

    host = str(resolved.get("host") or "").strip()
    if not host:
        raise EmailConfigurationError("SMTP settings must include a host")

    port = _parse_port(resolved.get("port"))
    security = (
        str(resolved.get("security") or ("ssl" if port == 465 else "starttls"))
        .strip()
        .lower()
    )
    if security not in {"ssl", "starttls"}:
        raise EmailConfigurationError("SMTP security must be 'ssl' or 'starttls'")

    # Same egress policy as every other user-configured outbound destination:
    # in SaaS mode the server must be public, on a standard submission port.
    try:
        validate_user_configured_smtp_port(port, field_name="SMTP port")
        resolve_user_configured_service_host(host, port=port, field_name="SMTP host")
    except ServiceHostResolutionError as exc:
        # A DNS outage is transient; retrying is the correct response. Only a
        # policy rejection (private address, bad port) is a configuration error.
        raise EmailDeliveryError("SMTP host could not be resolved") from exc
    except ValueError as exc:
        raise EmailConfigurationError(str(exc)) from exc

    resolved.update(host=host, port=port, security=security)
    return resolved


class _PinnedSMTP(smtplib.SMTP):
    """SMTP client that dials an address the egress policy already checked.

    The hostname is still handed to smtplib so EHLO and STARTTLS certificate
    verification use the name the user configured, while the TCP connection
    goes to the validated address. A fresh DNS lookup here would reopen the
    rebinding window the check exists to close.
    """

    def __init__(
        self, hostname: str, port: int, *, pinned_ip: str, timeout: float
    ) -> None:
        self._pinned_ip = pinned_ip
        super().__init__(hostname, port, timeout=timeout)

    def _get_socket(self, host, port, timeout):
        return socket.create_connection(
            (self._pinned_ip, port), timeout, self.source_address
        )


class _PinnedSMTPSSL(smtplib.SMTP_SSL):
    """Implicit-TLS variant of :class:`_PinnedSMTP`."""

    def __init__(
        self,
        hostname: str,
        port: int,
        *,
        pinned_ip: str,
        timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        self._pinned_ip = pinned_ip
        super().__init__(hostname, port, timeout=timeout, context=context)

    def _get_socket(self, host, port, timeout):
        sock = socket.create_connection(
            (self._pinned_ip, port), timeout, self.source_address
        )
        return self.context.wrap_socket(sock, server_hostname=self._host)


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
        try:
            self._timeout = float(
                self._settings.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS
            )
        except (TypeError, ValueError):
            raise EmailConfigurationError(
                "SMTP timeout_seconds must be a number"
            ) from None
        if self._timeout <= 0:
            raise EmailConfigurationError("SMTP timeout_seconds must be positive")

    @property
    def settings(self) -> Mapping[str, Any]:
        """Resolved settings (preset applied), safe to show in the UI."""
        return dict(self._settings)

    @property
    def message_id_domain(self) -> str | None:
        return message_id_domain(self._config)

    def _connection(self) -> smtplib.SMTP:
        host = self._settings["host"]
        port = self._settings["port"]
        # DNS can be rebound between saving settings and connecting, so the
        # policy runs again here and the checked address is the one dialed.
        try:
            pinned_ip = resolve_user_configured_service_host(
                host, port=port, field_name="SMTP host"
            )
        except ServiceHostResolutionError as exc:
            raise EmailDeliveryError("SMTP host could not be resolved") from exc
        except ValueError as exc:
            raise EmailConfigurationError(str(exc)) from exc

        context = ssl.create_default_context()
        if self._settings["security"] == "ssl":
            if pinned_ip is not None:
                return _PinnedSMTPSSL(
                    host,
                    port,
                    pinned_ip=pinned_ip,
                    timeout=self._timeout,
                    context=context,
                )
            return smtplib.SMTP_SSL(
                host,
                port,
                timeout=self._timeout,
                context=context,
            )
        if pinned_ip is not None:
            connection: smtplib.SMTP = _PinnedSMTP(
                host, port, pinned_ip=pinned_ip, timeout=self._timeout
            )
        else:
            connection = smtplib.SMTP(host, port, timeout=self._timeout)
        connection.ehlo()
        connection.starttls(context=context)
        connection.ehlo()
        return connection

    def _send_blocking(self, message: OutboundEmail) -> str:
        try:
            msg = build_mime_message(self._config, message)
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
        except smtplib.SMTPNotSupportedError as exc:
            raise EmailConfigurationError(
                "SMTP server does not support the requested security mode"
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
        except ValueError as exc:
            # Building the message must only surface typed adapter errors.
            raise EmailConfigurationError(
                f"Invalid email content: {type(exc).__name__}"
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
        except smtplib.SMTPNotSupportedError as exc:
            raise EmailConfigurationError(
                "SMTP server does not support the requested security mode"
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
