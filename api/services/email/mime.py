"""MIME construction shared by every outbound email adapter.

The message shape is provider-neutral; only the transport differs (smtplib
versus an HTTP API). Keeping it in one place means SMTP and SES cannot drift on
the rules that matter for deliverability and safety: reserved headers, header
injection rejection, and the authoritative ``Message-ID``.

``headers`` from the caller are authoritative and copied verbatim, which is what
RFC 8058 one-click unsubscribe and future list-management headers depend on.
"""

from __future__ import annotations

import io
from email.generator import BytesGenerator
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from api.services.email.adapter import (
    EmailConfigurationError,
    EmailSenderConfig,
    OutboundEmail,
)

# Headers the adapter owns; a caller cannot override them through ``headers``.
RESERVED_HEADERS = frozenset({"from", "to", "subject", "reply-to", "message-id"})


def without_newlines(value: str, *, what: str) -> str:
    if "\r" in value or "\n" in value:
        raise EmailConfigurationError(f"{what} must not contain line breaks")
    return value


def message_id_domain(config: EmailSenderConfig) -> str | None:
    """Domain used for the generated Message-ID, taken from the From address."""
    return config.from_email.rpartition("@")[2] or None


def build_mime_message(
    config: EmailSenderConfig, message: OutboundEmail
) -> EmailMessage:
    """Build one RFC 5322 message from a sender config and an outbound email."""
    msg = EmailMessage()
    msg["From"] = formataddr(
        (
            without_newlines(config.from_name, what="From name"),
            without_newlines(config.from_email, what="From address"),
        )
    )
    to_name = (
        without_newlines(message.to_name, what="To name") if message.to_name else ""
    )
    msg["To"] = formataddr(
        (to_name, without_newlines(message.to_email, what="To address"))
    )
    msg["Subject"] = without_newlines(message.subject, what="Subject")
    if config.reply_to:
        msg["Reply-To"] = without_newlines(config.reply_to, what="Reply-To address")

    for name, value in message.headers.items():
        if name.lower() in RESERVED_HEADERS:
            continue
        without_newlines(str(name), what="Header name")
        without_newlines(str(value), what=f"Header {name}")
        msg[name] = str(value)

    # Message-ID is reserved: the adapter owns it so every message has one and
    # callers cannot smuggle an unvalidated value through ``headers``.
    msg["Message-ID"] = make_msgid(domain=message_id_domain(config))

    if message.text_body:
        msg.set_content(message.text_body)
        msg.add_alternative(message.html_body, subtype="html")
    else:
        msg.set_content(message.html_body, subtype="html")
    return msg


def serialize_mime(msg: EmailMessage) -> bytes:
    """Serialize with CRLF line endings, as RFC 5322 and SES require.

    ``EmailMessage.as_bytes()`` uses the policy's default ``\\n`` line endings,
    which some providers tolerate and some reject. SES's raw content must be
    RFC 5322 bytes, so the generator is asked for CRLF explicitly.
    """
    buffer = io.BytesIO()
    generator = BytesGenerator(buffer, mangle_from_=False, maxheaderlen=0)
    generator.flatten(msg, linesep="\r\n")
    return buffer.getvalue()
