"""Validate and normalize email addresses before they enter the system.

Lead scraping, CSV imports, manual edits and future campaign audiences must all
agree on what counts as a usable address. A regex alone is not enough: scraped
pages regularly produce image filenames (``logo@2x.webp``), placeholder domains
(``info@domain.com``) and RFC-reserved names. Those addresses bounce, damage
sender reputation and inflate campaign counts.

The validator is deliberately offline and deterministic: it does not perform
DNS/MX lookups. Deliverability is decided later by the provider.
"""

from __future__ import annotations

import re

MAX_EMAIL_LENGTH = 320
MAX_LOCAL_PART_LENGTH = 64
MAX_DOMAIN_LENGTH = 253
MAX_DOMAIN_LABEL_LENGTH = 63

# The same shape used to pull candidates out of scraped text and CSV cells.
# The trailing boundary keeps a punycode TLD such as ``xn--p1ai`` intact instead
# of letting the ASCII branch stop at ``xn``.
EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\."
    r"(?:[A-Za-z]{2,}|xn--[A-Za-z0-9\-]+)(?![A-Za-z0-9\-])"
)

_LOCAL_PART_PATTERN = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.\-]+$")

# File names that scrapers mistake for addresses, e.g. "logo@2x.webp".
_FILE_EXTENSION_TLDS = frozenset(
    {
        "avif",
        "bmp",
        "css",
        "gif",
        "htm",
        "html",
        "ico",
        "jpeg",
        "jpg",
        "js",
        "json",
        "mov",
        "mp3",
        "mp4",
        "otf",
        "pdf",
        "php",
        "png",
        "svg",
        "tif",
        "tiff",
        "ttf",
        "webm",
        "webp",
        "woff",
        "woff2",
        "xml",
    }
)

# Common placeholder domains used in templates, CMS demos and sample data.
_PLACEHOLDER_DOMAINS = frozenset(
    {
        "company.com",
        "domain.com",
        "email.com",
        "example.com",
        "example.net",
        "example.org",
        "mydomain.com",
        "test.com",
        "test.net",
        "test.org",
        "your-domain.com",
        "yourdomain.com",
    }
)

# RFC 2606/6761 names and private-use suffixes that never belong to a real
# business mailing address.
_RESERVED_SUFFIXES = (
    ".example",
    ".invalid",
    ".local",
    ".localhost",
    ".test",
)
_RESERVED_DOMAINS = frozenset({"localhost"})

_REASON_MESSAGES = {
    "empty": "Email address is empty",
    "too_long": "Email address is too long",
    "invalid_syntax": "Enter a valid email address",
    "invalid_local_part": "Email address has an invalid local part",
    "invalid_domain": "Email address has an invalid domain",
    "file_extension": "Email address ends in an image or file extension",
    "placeholder_domain": "Email address uses a placeholder domain",
    "reserved_domain": "Email address uses a reserved or private domain",
}


class EmailValidationError(ValueError):
    """A rejected address, with a stable machine-readable reason."""

    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(
            message or _REASON_MESSAGES.get(reason, "Invalid email address")
        )


def _strip_wrappers(value: str) -> str:
    text = value.strip()
    if text.lower().startswith("mailto:"):
        text = text[7:].strip()
    if text.startswith("<") and text.endswith(">"):
        text = text[1:-1].strip()
    return text


def classify_email(value: object) -> tuple[str | None, str | None]:
    """Return ``(normalized_address, rejection_reason)``.

    Exactly one side is non-None. ``normalized_address`` is trimmed and
    lowercased, which is what the database stores.
    """
    if value is None:
        return None, "empty"
    text = _strip_wrappers(str(value))
    if not text:
        return None, "empty"
    if len(text) > MAX_EMAIL_LENGTH:
        return None, "too_long"
    if text.count("@") != 1:
        return None, "invalid_syntax"

    local, domain = text.split("@", 1)
    local = local.strip()
    domain = domain.strip().lower().rstrip(".")

    if not local or len(local) > MAX_LOCAL_PART_LENGTH:
        return None, "invalid_local_part"
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return None, "invalid_local_part"
    if not _LOCAL_PART_PATTERN.fullmatch(local):
        return None, "invalid_local_part"

    if not domain or len(domain) > MAX_DOMAIN_LENGTH:
        return None, "invalid_domain"
    if domain in _RESERVED_DOMAINS or any(
        domain.endswith(suffix) for suffix in _RESERVED_SUFFIXES
    ):
        return None, "reserved_domain"
    if domain in _PLACEHOLDER_DOMAINS or any(
        domain.endswith(f".{placeholder}") for placeholder in _PLACEHOLDER_DOMAINS
    ):
        return None, "placeholder_domain"

    labels = domain.split(".")
    if len(labels) < 2:
        return None, "invalid_domain"
    for label in labels:
        if (
            not label
            or len(label) > MAX_DOMAIN_LABEL_LENGTH
            or label.startswith("-")
            or label.endswith("-")
            or not all(char.isalnum() or char == "-" for char in label)
        ):
            return None, "invalid_domain"

    tld = labels[-1]
    if tld in _FILE_EXTENSION_TLDS:
        return None, "file_extension"
    if len(tld) < 2 or not (tld.isalpha() or tld.startswith("xn--")):
        return None, "invalid_domain"

    return f"{local.lower()}@{domain}", None


def normalize_email(value: object) -> str | None:
    """Return the normalized address, or ``None`` when it is not usable."""
    normalized, _ = classify_email(value)
    return normalized


def validate_email_address(value: object) -> str:
    """Return the normalized address or raise :class:`EmailValidationError`."""
    normalized, reason = classify_email(value)
    if reason is not None:
        raise EmailValidationError(reason)
    assert normalized is not None
    return normalized


def is_valid_email(value: object) -> bool:
    return classify_email(value)[1] is None
