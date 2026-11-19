"""Signed unsubscribe links for campaign recipients.

The link is public: it carries an HMAC-signed ``(organization, address,
campaign)`` triple so the route can act without a session, and a tampered
token cannot suppress an address the sender never mailed. Tokens are
deterministic and need no storage or expiry table.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Optional

from api import constants

_VERSION = "v1"
_MAX_TOKEN_LENGTH = 2048


class UnsubscribeTokenError(ValueError):
    """The token is missing, malformed, or its signature does not match."""


@dataclass(frozen=True)
class UnsubscribeClaim:
    organization_id: int
    email: str
    campaign_id: Optional[int]


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _signature(payload_part: str) -> str:
    message = f"{_VERSION}.{payload_part}".encode("ascii")
    digest = hmac.new(
        constants.EMAIL_UNSUBSCRIBE_SECRET.encode("utf-8"),
        message,
        hashlib.sha256,
    ).digest()
    return _b64encode(digest)


def build_unsubscribe_token(
    *, organization_id: int, email: str, campaign_id: int | None
) -> str:
    payload = json.dumps(
        {
            "o": int(organization_id),
            "e": email.strip().lower(),
            "c": int(campaign_id) if campaign_id is not None else None,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    payload_part = _b64encode(payload.encode("utf-8"))
    return f"{payload_part}.{_signature(payload_part)}"


def parse_unsubscribe_token(token: str) -> UnsubscribeClaim:
    if not token or len(token) > _MAX_TOKEN_LENGTH:
        raise UnsubscribeTokenError("Invalid unsubscribe link.")
    payload_part, separator, signature = token.partition(".")
    if not separator or not payload_part or not signature:
        raise UnsubscribeTokenError("Invalid unsubscribe link.")
    if not hmac.compare_digest(_signature(payload_part), signature):
        raise UnsubscribeTokenError("Invalid unsubscribe link.")
    try:
        payload = json.loads(_b64decode(payload_part))
        organization_id = int(payload["o"])
        email = str(payload["e"] or "").strip().lower()
        campaign_id = payload.get("c")
        campaign_id = int(campaign_id) if campaign_id is not None else None
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise UnsubscribeTokenError("Invalid unsubscribe link.") from exc
    if not email or "@" not in email:
        raise UnsubscribeTokenError("Invalid unsubscribe link.")
    return UnsubscribeClaim(
        organization_id=organization_id,
        email=email,
        campaign_id=campaign_id,
    )


def unsubscribe_url(public_base_url: str, token: str) -> str:
    return f"{public_base_url.rstrip('/')}/api/v1/unsubscribe/{token}"


def unsubscribe_headers(url: str) -> dict[str, str]:
    """RFC 2369 / RFC 8058 headers for one recipient's message."""
    return {
        "List-Unsubscribe": f"<{url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
