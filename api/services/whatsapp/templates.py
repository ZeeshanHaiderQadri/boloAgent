"""Validate, render and link WhatsApp click-to-chat templates.

The message is typed into the operator's WhatsApp client through a ``wa.me``
link. There is no WhatsApp Business API call anywhere in this module, so a
template needs no Meta approval and Bolo cannot send on its own.
"""

from __future__ import annotations

import re
from typing import Mapping
from urllib.parse import quote

MAX_TEMPLATE_BODY_CHARS = 4096
MAX_TEMPLATE_NAME_CHARS = 255

VARIABLES: dict[str, str] = {
    "business_name": "Business name",
    "category": "Category",
    "city": "City",
}
ALLOWED_VARIABLES = frozenset(VARIABLES)

CATEGORIES = frozenset({"marketing", "utility", "authentication"})

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


class WhatsAppTemplateError(ValueError):
    """The template or link input is not something Bolo can render."""


def extract_variables(body: str) -> list[str]:
    """Return placeholder names in order of first use, without duplicates."""
    seen: list[str] = []
    for match in _PLACEHOLDER_RE.finditer(body):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def validate_template_body(body: str) -> tuple[str, list[str]]:
    cleaned = (body or "").strip()
    if not cleaned:
        raise WhatsAppTemplateError("Message body cannot be empty.")
    if len(cleaned) > MAX_TEMPLATE_BODY_CHARS:
        raise WhatsAppTemplateError(
            f"Message body is longer than {MAX_TEMPLATE_BODY_CHARS} characters."
        )
    variables = extract_variables(cleaned)
    unknown = [name for name in variables if name not in ALLOWED_VARIABLES]
    if unknown:
        allowed = ", ".join("{{%s}}" % name for name in sorted(ALLOWED_VARIABLES))
        raise WhatsAppTemplateError(
            "Unknown placeholder(s): "
            + ", ".join("{{%s}}" % name for name in unknown)
            + f". Use only {allowed}."
        )
    return cleaned, variables


def normalize_category(value: str) -> str:
    cleaned = (value or "").strip().lower()
    if cleaned not in CATEGORIES:
        raise WhatsAppTemplateError(
            "Category must be one of: " + ", ".join(sorted(CATEGORIES)) + "."
        )
    return cleaned


def normalize_language(value: str) -> str:
    cleaned = (value or "").strip()
    if not _LANGUAGE_RE.match(cleaned):
        raise WhatsAppTemplateError(
            "Language must be a short code such as en or en-US."
        )
    return cleaned


def render_body(body: str, values: Mapping[str, str | None]) -> str:
    """Fill known placeholders; a missing value becomes an empty string."""

    def replace(match: re.Match[str]) -> str:
        return values.get(match.group(1)) or ""

    lines = []
    for line in _PLACEHOLDER_RE.sub(replace, body).splitlines():
        # A missing value must not leave a double space or a dangling comma-less gap.
        lines.append(re.sub(r"[ \t]{2,}", " ", line).strip())
    return "\n".join(lines).strip()


def lead_values(
    *, business_name: str | None, category: str | None, city: str | None
) -> dict[str, str | None]:
    return {
        "business_name": business_name,
        "category": category,
        "city": city,
    }


def sample_values() -> dict[str, str]:
    return {
        "business_name": "Apex Labs",
        "category": "Marketing agency",
        "city": "Austin",
    }


def build_wa_me_url(phone_e164: str, text: str) -> str:
    """Build a click-to-chat URL for an E.164 number.

    ``wa.me`` wants digits only. A number without a leading ``+`` (and so
    without a known country code) is refused rather than guessed.
    """
    phone = (phone_e164 or "").strip()
    if not _E164_RE.match(phone):
        raise WhatsAppTemplateError(
            "A WhatsApp link needs a phone number in international format."
        )
    url = f"https://wa.me/{phone[1:]}"
    if text:
        url += f"?text={quote(text, safe='')}"
    return url
