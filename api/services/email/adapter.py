"""Provider-neutral interface for outbound email adapters.

Concrete adapters (SMTP, Amazon SES, Mailgun, Mandrill) are thin translation
layers: they turn an :class:`OutboundEmail` into one provider call and report
either a provider message id or a typed error. Everything that must survive a
crash — retries, attempt counting, status transitions — belongs to the queue
layer, not to an adapter.

Rules every adapter must follow:

* Never retry internally. Raise :class:`EmailDeliveryError` and let the worker
  decide, so one message never produces two deliveries.
* Raise :class:`EmailConfigurationError` for problems that retrying cannot fix
  (bad credentials, rejected sender, missing region). The queue marks the
  message failed instead of burning attempts.
* Never log credentials, message bodies, or recipient addresses. Log the
  message uuid and the provider error code only.
* Treat ``headers`` as authoritative: unsubscribe headers and future RFC 8058
  headers are built upstream and must be sent verbatim.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

from api.enums import EmailProvider


@dataclass(frozen=True)
class EmailSenderConfig:
    """Non-secret half of a sender, taken from ``email_senders``.

    Secret material (SMTP password, SES secret key, API tokens) is decrypted by
    the caller and passed separately, so adapters never read the database or
    the environment.
    """

    provider: EmailProvider
    from_name: str
    from_email: str
    reply_to: str | None = None
    # Provider options such as {"host": ..., "port": 465, "security": "ssl"} or
    # {"region": "eu-west-1", "configuration_set": "..."}.
    settings: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OutboundEmail:
    """One message to deliver, already rendered and authorized to send."""

    to_email: str
    subject: str
    html_body: str
    text_body: str | None = None
    to_name: str | None = None
    # Extra MIME headers, e.g. List-Unsubscribe / List-Unsubscribe-Post.
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderSendResult:
    """What the queue needs to record after a successful hand-off."""

    provider_message_id: str


class EmailAdapterError(Exception):
    """Base class for adapter failures. Never includes credentials or bodies."""


class EmailConfigurationError(EmailAdapterError):
    """Misconfiguration that retrying cannot fix. Fails the message at once."""


class EmailDeliveryError(EmailAdapterError):
    """Transient delivery failure. The worker may retry with backoff."""

    def __init__(self, message: str, *, retry_after_seconds: float | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class EmailAdapter(ABC):
    """One provider implementation.

    Instances are cheap and stateless apart from their configuration; the
    worker creates one per send attempt. Implementations must be safe to run
    concurrently in the ARQ worker.
    """

    provider: ClassVar[EmailProvider]

    def __init__(
        self,
        config: EmailSenderConfig,
        credentials: Mapping[str, Any],
    ) -> None:
        if config.provider is not self.provider:
            raise EmailConfigurationError(
                f"{type(self).__name__} handles {self.provider.value}, "
                f"not {config.provider.value}"
            )
        self._config = config
        self._credentials = dict(credentials)

    @property
    def config(self) -> EmailSenderConfig:
        return self._config

    @abstractmethod
    async def verify(self) -> None:
        """Prove the sender can send, without sending a message.

        SMTP adapters open a connection and authenticate; SES calls
        ``GetAccountSendingEnabled`` / verifies the identity. Raise
        :class:`EmailConfigurationError` with a non-secret explanation when the
        sender is not usable yet.
        """

    @abstractmethod
    async def send(self, message: OutboundEmail) -> ProviderSendResult:
        """Deliver exactly one message and return the provider's id for it."""
