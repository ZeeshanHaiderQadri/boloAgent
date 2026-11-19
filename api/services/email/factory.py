"""Construct a provider adapter from a sender row and decrypted credentials.

Shared by the sender routes (verify / test-send), campaign launch and the send
worker so every caller validates settings the same way.
"""

from __future__ import annotations

from typing import Any, Mapping

from api.enums import EmailProvider
from api.services.email.adapter import EmailAdapter, EmailConfigurationError, EmailSenderConfig
from api.services.email.ses import SesEmailAdapter
from api.services.email.smtp import SmtpEmailAdapter


def sender_config(sender: Any) -> EmailSenderConfig:
    return EmailSenderConfig(
        provider=EmailProvider(sender.provider),
        from_name=sender.from_name,
        from_email=sender.from_email,
        reply_to=sender.reply_to_email,
        settings=sender.settings or {},
    )


def build_adapter(
    sender: Any, credentials: Mapping[str, Any]
) -> EmailAdapter:
    provider = EmailProvider(sender.provider)
    config = sender_config(sender)
    if provider is EmailProvider.SMTP:
        return SmtpEmailAdapter(config, credentials)
    if provider is EmailProvider.SES:
        return SesEmailAdapter(config, credentials)
    raise EmailConfigurationError(f"{provider.value} is not available yet")
