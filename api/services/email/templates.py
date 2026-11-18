"""Authoring helpers for email templates: merge fields, sanitizing, fallbacks.

Templates store three things: the source the author types (HTML or the MJML
subset in :mod:`api.services.email.mjml`), the compiled and sanitized HTML that
is safe to preview and, later, send, and a plain-text fallback generated from
that HTML.

Merge fields use ``{{field_name}}``. Unknown fields are rejected when the
template is saved, not when a campaign sends it, so a typo cannot silently ship
as literal braces to a recipient.

The sanitizer is an allowlist. It removes, rather than escapes:

* ``<script>``, ``<style>``, ``<iframe>``, ``<object>``, ``<embed>``,
  ``<form>``, ``<input>``, ``<button>``, ``<textarea>``, ``<select>``,
  ``<meta>``, ``<link>``, ``<base>`` and every other tag outside the list below;
* every ``on*`` event handler attribute (``onclick``, ``onerror``, ...);
* ``javascript:`` and ``vbscript:`` URLs in ``href``/``src``, and every scheme
  other than http, https and mailto;
* ``expression(...)``, ``javascript:``, ``vbscript:``, ``behavior:``,
  ``-moz-binding`` and ``@import`` inside ``style`` attributes.

Comments are removed too. Kept tags are ordinary formatting, tables, images,
links, headings and lists — the markup email clients actually render.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape as html_escape
from html.parser import HTMLParser
from typing import Mapping

import bleach
from bleach.css_sanitizer import CSSSanitizer

from api.services.email.mjml import MjmlError, compile_mjml

MAX_SOURCE_BYTES = 256 * 1024
MAX_COMPILED_BYTES = 512 * 1024
MAX_SUBJECT_LENGTH = 998

UNSUBSCRIBE_FIELD = "unsubscribe_link"
# Deliberately a reserved documentation host so a test send cannot point at a
# real lead's unsubscribe page.
UNSUBSCRIBE_SAMPLE_URL = "https://boloagent.com/unsubscribe/preview-sample"

_ALLOWED_TAGS = [
    "a",
    "abbr",
    "b",
    "blockquote",
    "br",
    "caption",
    "center",
    "code",
    "col",
    "colgroup",
    "div",
    "em",
    "font",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
]

_ALLOWED_ATTRIBUTES = {
    "*": [
        "style",
        "class",
        "title",
        "align",
        "valign",
        "width",
        "height",
        "bgcolor",
        "dir",
        "lang",
        "role",
    ],
    "a": ["href", "name", "target", "rel"],
    "img": ["src", "alt", "border", "hspace", "vspace"],
    "table": ["border", "cellpadding", "cellspacing"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan", "scope"],
    "font": ["color", "face", "size"],
    "col": ["span"],
    "ol": ["start", "type"],
}

_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]

# Inline styles are how email markup works. tinycss2 parses each declaration;
# anything not listed here is dropped, which also rejects at-rules and
# non-standard values. _scrub_style_attributes runs afterwards as a second
# barrier against expression()/javascript: payloads.
_ALLOWED_CSS_PROPERTIES = [
    "-ms-text-size-adjust",
    "-webkit-text-size-adjust",
    "background-color",
    "border",
    "border-bottom",
    "border-bottom-color",
    "border-bottom-style",
    "border-bottom-width",
    "border-collapse",
    "border-color",
    "border-left",
    "border-left-color",
    "border-left-style",
    "border-left-width",
    "border-radius",
    "border-right",
    "border-right-color",
    "border-right-style",
    "border-right-width",
    "border-spacing",
    "border-style",
    "border-top",
    "border-top-color",
    "border-top-style",
    "border-top-width",
    "border-width",
    "color",
    "direction",
    "display",
    "font",
    "font-family",
    "font-size",
    "font-style",
    "font-variant",
    "font-weight",
    "height",
    "letter-spacing",
    "line-height",
    "margin",
    "margin-bottom",
    "margin-left",
    "margin-right",
    "margin-top",
    "max-width",
    "min-width",
    "mso-line-height-rule",
    "mso-table-lspace",
    "mso-table-rspace",
    "padding",
    "padding-bottom",
    "padding-left",
    "padding-right",
    "padding-top",
    "text-align",
    "text-decoration",
    "text-indent",
    "text-transform",
    "vertical-align",
    "white-space",
    "width",
    "word-break",
    "word-wrap",
]

_CSS_SANITIZER = CSSSanitizer(allowed_css_properties=_ALLOWED_CSS_PROPERTIES)

_CLEANER = bleach.sanitizer.Cleaner(
    tags=_ALLOWED_TAGS,
    attributes=_ALLOWED_ATTRIBUTES,
    protocols=_ALLOWED_PROTOCOLS,
    css_sanitizer=_CSS_SANITIZER,
    strip=True,
    strip_comments=True,
)

_STYLE_ATTRIBUTE = re.compile(
    r"""style\s*=\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)""",
    re.IGNORECASE | re.DOTALL,
)
_UNSAFE_CSS = re.compile(
    r"expression\s*\(|javascript\s*:|vbscript\s*:|behavior\s*:|-moz-binding|@import",
    re.IGNORECASE,
)

# Bleach removes a disallowed tag but keeps its text content, which would turn
# ``<script>alert(1)</script>`` into the visible text ``alert(1)``. These blocks
# are removed with their contents before the allowlist runs.
_CONTENT_BLOCK_TAGS = (
    "script",
    "style",
    "iframe",
    "object",
    "embed",
    "noscript",
    "template",
    "svg",
    "math",
)

_MERGE_PATTERN = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


@dataclass(frozen=True)
class MergeField:
    name: str
    label: str
    description: str
    fallback: str
    sample: str
    required: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "fallback": self.fallback,
            "sample": self.sample,
            "required": self.required,
        }


MERGE_FIELDS: dict[str, MergeField] = {
    field.name: field
    for field in (
        MergeField(
            "business_name",
            "Business name",
            "The lead's business name, as stored in the CRM.",
            "there",
            "Acme Demo Co.",
        ),
        MergeField(
            "category",
            "Business category",
            "The category recorded for the lead.",
            "your business",
            "IT services",
        ),
        MergeField(
            "address",
            "Street address",
            "The lead's street address.",
            "",
            "100 Demo Street",
        ),
        MergeField("city", "City", "The lead's city.", "your area", "Springfield"),
        MergeField(
            "state", "State or region", "The lead's state or region.", "", "Illinois"
        ),
        MergeField("country", "Country", "The lead's country.", "", "United States"),
        MergeField(
            "postal_code", "Postal code", "The lead's postal code.", "", "62704"
        ),
        MergeField("phone", "Phone", "The lead's phone number.", "", "+1 555 0100"),
        MergeField(
            "email",
            "Email",
            "The lead's email address.",
            "",
            "jordan@apex-software-demo.com",
        ),
        MergeField(
            "website",
            "Website",
            "The lead's website.",
            "",
            "https://apex-software-demo.com",
        ),
        MergeField("rating", "Rating", "The lead's average rating.", "", "4.8"),
        MergeField(
            "review_count", "Review count", "How many reviews the lead has.", "", "126"
        ),
        MergeField(
            UNSUBSCRIBE_FIELD,
            "Unsubscribe link",
            "Replaced with each recipient's personal unsubscribe link. "
            "Required for every template used in a campaign.",
            "",
            UNSUBSCRIBE_SAMPLE_URL,
            required=True,
        ),
    )
}


class TemplateValidationError(ValueError):
    """Template input cannot be compiled or is not safe to store."""


def sample_values() -> dict[str, str]:
    """Fake values used for previews and test sends. Never lead data."""
    return {name: field.sample for name, field in MERGE_FIELDS.items()}


def extract_merge_fields(*texts: str) -> list[str]:
    found: set[str] = set()
    for text in texts:
        if text:
            found.update(_MERGE_PATTERN.findall(text))
    return sorted(found)


def validate_merge_fields(*texts: str) -> list[str]:
    """Return the fields used; raise for any name outside the registry."""
    used = extract_merge_fields(*texts)
    unknown = [name for name in used if name not in MERGE_FIELDS]
    if unknown:
        rendered = ", ".join(f"{{{{{name}}}}}" for name in unknown)
        supported = ", ".join(f"{{{{{name}}}}}" for name in MERGE_FIELDS)
        raise TemplateValidationError(
            f"Unknown merge field(s): {rendered}. Supported fields: {supported}."
        )
    return used


def has_unsubscribe_placeholder(subject: str, source: str) -> bool:
    return UNSUBSCRIBE_FIELD in extract_merge_fields(subject, source)


def _subject_value(value: str) -> str:
    # Header injection is rejected by the MIME builder later; normalize now so a
    # multi-line lead field cannot turn into a broken subject.
    return re.sub(r"\s+", " ", value).strip()


def _render(
    text: str,
    values: Mapping[str, str],
    *,
    escape_values: bool,
    subject: bool = False,
) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        field = MERGE_FIELDS[name]
        raw = values.get(name)
        value = raw.strip() if isinstance(raw, str) else ""
        if not value:
            value = field.fallback
        if subject:
            value = _subject_value(value)
        return html_escape(value) if escape_values else value

    return _MERGE_PATTERN.sub(replace, text)


def render_subject(subject: str, values: Mapping[str, str]) -> str:
    return _subject_value(_render(subject, values, escape_values=False, subject=True))


def render_html(html: str, values: Mapping[str, str]) -> str:
    return _render(html, values, escape_values=True)


def render_text(text: str, values: Mapping[str, str]) -> str:
    return _render(text, values, escape_values=False)


def normalize_subject(subject: str) -> str:
    cleaned = subject.strip()
    if not cleaned:
        raise TemplateValidationError("Subject cannot be empty.")
    if "\r" in cleaned or "\n" in cleaned:
        raise TemplateValidationError("Subject must be a single line.")
    if len(cleaned) > MAX_SUBJECT_LENGTH:
        raise TemplateValidationError(
            f"Subject must be at most {MAX_SUBJECT_LENGTH} characters."
        )
    return cleaned


def _scrub_style_attributes(html: str) -> str:
    def replace(match: re.Match[str]) -> str:
        cleaned = _UNSAFE_CSS.sub("", match.group("value"))
        return f'style="{cleaned}"'

    return _STYLE_ATTRIBUTE.sub(replace, html)


def _remove_content_blocks(html: str) -> str:
    cleaned = html
    for tag in _CONTENT_BLOCK_TAGS:
        paired = re.compile(
            rf"<\s*{tag}\b[^>]*>.*?<\s*/\s*{tag}\s*>",
            re.IGNORECASE | re.DOTALL,
        )
        previous = None
        while previous != cleaned:
            previous = cleaned
            cleaned = paired.sub("", cleaned)
        # An unclosed block would otherwise leak its contents past the tag.
        cleaned = re.sub(
            rf"<\s*{tag}\b[^>]*>.*\Z",
            "",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
    return cleaned


def sanitize_html(html: str) -> str:
    """Return allowlisted HTML safe to store, preview and send."""
    without_blocks = _remove_content_blocks(html)
    return _scrub_style_attributes(_CLEANER.clean(without_blocks))


class _TextExtractor(HTMLParser):
    _BLOCK_TAGS = {
        "address",
        "article",
        "blockquote",
        "br",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._links: list[tuple[str, int]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in {"head", "script", "style", "title"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        attributes = {name: value for name, value in attrs}
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")
        if tag == "br":
            self.parts.append("\n")
        if tag == "img" and attributes.get("alt"):
            self.parts.append(attributes["alt"])
        if tag == "a" and attributes.get("href"):
            self._links.append((attributes["href"], len(self.parts)))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"head", "script", "style", "title"}:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "a":
            if self._links:
                href, _ = self._links.pop()
                self.parts.append(f" ({href})")
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Produce a readable plain-text fallback from compiled HTML."""
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    raw = "".join(extractor.parts).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@dataclass(frozen=True)
class PreparedTemplate:
    subject: str
    source_format: str
    source: str
    html_body: str
    text_body: str
    has_unsubscribe_placeholder: bool
    warnings: list[str]


def prepare_template(
    *,
    subject: str,
    source_format: str,
    source: str,
) -> PreparedTemplate:
    """Validate, compile and sanitize one template version."""
    cleaned_subject = normalize_subject(subject)
    cleaned_source = source.strip()
    if not cleaned_source:
        raise TemplateValidationError("Template source cannot be empty.")
    source_bytes = len(cleaned_source.encode("utf-8"))
    if source_bytes > MAX_SOURCE_BYTES:
        raise TemplateValidationError(
            f"The template source is {source_bytes} bytes; the limit is "
            f"{MAX_SOURCE_BYTES} bytes."
        )

    validate_merge_fields(cleaned_subject, cleaned_source)

    try:
        compiled = (
            compile_mjml(cleaned_source) if source_format == "mjml" else cleaned_source
        )
    except MjmlError as exc:
        raise TemplateValidationError(str(exc)) from exc

    sanitized = sanitize_html(compiled)
    if not html_to_text(sanitized):
        raise TemplateValidationError(
            "The template has no visible content after unsafe markup is removed."
        )
    compiled_bytes = len(sanitized.encode("utf-8"))
    if compiled_bytes > MAX_COMPILED_BYTES:
        raise TemplateValidationError(
            f"The compiled template is {compiled_bytes} bytes; the limit is "
            f"{MAX_COMPILED_BYTES} bytes."
        )

    unsubscribe = has_unsubscribe_placeholder(cleaned_subject, cleaned_source)
    warnings: list[str] = []
    if not unsubscribe:
        warnings.append(
            "This template has no {{unsubscribe_link}} placeholder. Add one "
            "before using it in a campaign."
        )

    return PreparedTemplate(
        subject=cleaned_subject,
        source_format=source_format,
        source=cleaned_source,
        html_body=sanitized,
        text_body=html_to_text(sanitized),
        has_unsubscribe_placeholder=unsubscribe,
        warnings=warnings,
    )


@dataclass(frozen=True)
class RenderedTemplate:
    subject: str
    html: str
    text: str
    has_unsubscribe_placeholder: bool
    warnings: list[str]


def render_preview(
    *,
    subject: str,
    source_format: str,
    source: str,
    values: Mapping[str, str] | None = None,
) -> RenderedTemplate:
    """Compile unsaved source and substitute sample merge values."""
    prepared = prepare_template(
        subject=subject, source_format=source_format, source=source
    )
    return render_prepared(prepared, values)


def render_prepared(
    prepared: PreparedTemplate, values: Mapping[str, str] | None = None
) -> RenderedTemplate:
    chosen = values or sample_values()
    rendered_html = sanitize_html(render_html(prepared.html_body, chosen))
    return RenderedTemplate(
        subject=render_subject(prepared.subject, chosen),
        html=rendered_html,
        text=render_text(prepared.text_body, chosen),
        has_unsubscribe_placeholder=prepared.has_unsubscribe_placeholder,
        warnings=list(prepared.warnings),
    )
