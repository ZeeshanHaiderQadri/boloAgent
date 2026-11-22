"""Import an operator's own lead spreadsheet into the shared CRM.

Rows keep the same shape the Google Maps ingest produces (see
``api.services.leads.normalization``), so imported businesses show up with the
same WhatsApp links, social icons, filters and exports as scraped ones. The
only difference is provenance: ``source="import"`` and a dedupe key built from
the contact details a spreadsheet usually has instead of a place id.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

from api.constants import LEADS_IMPORT_MAX_ROWS
from api.services.leads.normalization import (
    normalize_lead_phone,
    normalize_lead_row,
)
from api.services.leads.socials import (
    SOCIAL_PLATFORMS,
    normalize_website,
)
from api.services.whatsapp.countries import (
    lead_country,
    normalize_country_code,
)

LEAD_IMPORT_EXTENSIONS = (".xlsx", ".csv")

TEAL = "1F7A6B"
TEAL_LIGHT = "E8F4F0"
INK = "101614"

# Canonical column labels, in the order the downloadable template shows them.
# ``phones`` collects every phone-ish column so a sheet can carry a landline and
# a mobile; the first one that parses to E.164 wins.
TEMPLATE_COLUMNS: list[tuple[str, str, int, str, str]] = [
    (
        "title",
        "Business name",
        30,
        "Bluebird Dental",
        "Required. One business per row.",
    ),
    (
        "category",
        "Category",
        22,
        "Dentist",
        "Optional. Used by CRM filters and campaigns.",
    ),
    (
        "phones",
        "Phone",
        20,
        "+1 512 555 0142",
        "Include the country code when you can. WhatsApp icons appear for valid numbers.",
    ),
    (
        "email",
        "Email",
        28,
        "hello@bluebirddental.com",
        "First valid address is kept. Email consent starts as Unknown.",
    ),
    (
        "website",
        "Website",
        30,
        "https://bluebirddental.com",
        "Accepts acme.com or https://acme.com.",
    ),
    (
        "address",
        "Address",
        36,
        "1200 Main St",
        "Street address. City/State/Country have their own columns.",
    ),
    ("city", "City", 18, "Austin", ""),
    ("state", "State", 12, "TX", ""),
    (
        "country",
        "Country",
        12,
        "US",
        "ISO code or country name. Also used to read local phone numbers.",
    ),
    ("postal_code", "Postal code", 12, "78701", ""),
    (
        "social:instagram",
        "Instagram",
        26,
        "@bluebirddental",
        "Full profile link or @handle.",
    ),
    (
        "social:linkedin",
        "LinkedIn",
        26,
        "linkedin.com/company/bluebird",
        "Full profile link or handle.",
    ),
    (
        "social:facebook",
        "Facebook",
        26,
        "facebook.com/bluebirddental",
        "Full profile link or handle.",
    ),
    ("social:x", "X", 20, "@bluebirddental", "Full profile link or @handle."),
    ("social:tiktok", "TikTok", 24, "@bluebirddental", "Full profile link or @handle."),
    (
        "social:youtube",
        "YouTube",
        26,
        "@bluebirddental",
        "Full profile link or @handle.",
    ),
    (
        "status",
        "Status",
        14,
        "New",
        "New, Contacted, Interested, Not interested, Won, Lost or Do not call.",
    ),
    ("tags", "Tags", 20, "dental, austin", "Comma-separated."),
    (
        "notes",
        "Notes",
        30,
        "Met at the Austin business expo",
        "Optional context for your team.",
    ),
    ("rating", "Rating", 8, "4.7", "Optional. 0–5."),
    ("review_count", "Reviews", 9, "214", "Optional."),
]

TEMPLATE_HEADERS = [label for _, label, _, _, _ in TEMPLATE_COLUMNS]
_TEMPLATE_BY_LABEL = {
    label: (field, example, hint) for field, label, _, example, hint in TEMPLATE_COLUMNS
}

_SOCIAL_ALIASES: dict[str, tuple[str, ...]] = {
    "instagram": ("instagram", "insta", "ig", "instagram profile"),
    "x": ("x", "x twitter", "twitter", "twitter x", "x profile", "twitter profile"),
    "tiktok": ("tiktok", "tik tok", "tiktok profile"),
    "youtube": ("youtube", "you tube", "yt", "youtube profile"),
    "facebook": ("facebook", "fb", "facebook profile"),
    "linkedin": ("linkedin", "linked in", "linkedin profile"),
}

_SOCIAL_LABELS = {
    "instagram": "Instagram",
    "x": "X",
    "tiktok": "TikTok",
    "youtube": "YouTube",
    "facebook": "Facebook",
    "linkedin": "LinkedIn",
}

_EXACT_FIELDS = {
    "business": "title",
    "business name": "title",
    "business title": "title",
    "company": "title",
    "company name": "title",
    "organization": "title",
    "organisation": "title",
    "account": "title",
    "lead": "title",
    "lead name": "title",
    "name": "title",
    "title": "title",
    "category": "category",
    "industry": "category",
    "business type": "category",
    "type": "category",
    "sector": "category",
    "niche": "category",
    "email": "email",
    "e mail": "email",
    "email address": "email",
    "mail": "email",
    "website": "website",
    "web site": "website",
    "web": "website",
    "site": "website",
    "url": "website",
    "domain": "website",
    "address": "address",
    "street": "address",
    "street address": "address",
    "location": "address",
    "city": "city",
    "town": "city",
    "locality": "city",
    "state": "state",
    "province": "state",
    "region": "state",
    "country": "country",
    "nation": "country",
    "postal code": "postal_code",
    "postcode": "postal_code",
    "post code": "postal_code",
    "zip": "postal_code",
    "zip code": "postal_code",
    "rating": "rating",
    "stars": "rating",
    "google rating": "rating",
    "reviews": "review_count",
    "review count": "review_count",
    "number of reviews": "review_count",
    "tags": "tags",
    "tag": "tags",
    "labels": "tags",
    "keywords": "tags",
    "notes": "notes",
    "note": "notes",
    "comments": "notes",
    "comment": "notes",
    "description": "notes",
    "status": "status",
    "crm status": "status",
    "stage": "status",
}

_FIELD_LABELS = {
    "title": "Business name",
    "category": "Category",
    "phones": "Phone",
    "email": "Email",
    "website": "Website",
    "address": "Address",
    "city": "City",
    "state": "State",
    "country": "Country",
    "postal_code": "Postal code",
    "rating": "Rating",
    "review_count": "Reviews",
    "status": "Status",
    "tags": "Tags",
    "notes": "Notes",
}

_STATUS_VALUES = {
    "new": "new",
    "open": "new",
    "contacted": "contacted",
    "called": "contacted",
    "interested": "interested",
    "warm": "interested",
    "not interested": "not_interested",
    "not_interested": "not_interested",
    "cold": "not_interested",
    "won": "won",
    "closed won": "won",
    "customer": "won",
    "lost": "lost",
    "closed lost": "lost",
    "do not call": "do_not_call",
    "do_not_call": "do_not_call",
    "dnc": "do_not_call",
}

_TAG_SPLIT_RE = re.compile(r"\s*[,;|]\s*")
_HANDLE_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")

_SOCIAL_DOMAINS: dict[str, tuple[str, ...]] = {
    "instagram": ("instagram.com",),
    "x": ("x.com", "twitter.com"),
    "tiktok": ("tiktok.com",),
    "youtube": ("youtube.com", "youtu.be"),
    "facebook": ("facebook.com", "fb.com"),
    "linkedin": ("linkedin.com",),
}


class LeadImportError(Exception):
    """A spreadsheet could not be read as a lead table."""


@dataclass
class ParsedRow:
    sheet: str
    row_number: int
    values: dict[str, Any]
    issues: list[str]


@dataclass
class ParsedImport:
    file_name: str
    sheets: list[str]
    rows: list[ParsedRow]
    mapped_headers: dict[str, str]
    ignored_headers: list[str]


@dataclass
class ImportRow:
    sheet: str
    row_number: int
    lead: dict[str, Any] | None
    issues: list[str]


@dataclass
class PreparedImport:
    rows: list[ImportRow]
    leads: list[dict[str, Any]]
    total_rows: int
    skipped_rows: int
    duplicate_rows: int
    warnings: list[str]


def _slug(text: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split())


def canonical_field(header: Any) -> str | None:
    """Map a spreadsheet header to a lead field, or None when unknown."""
    slug = _slug(header)
    if not slug:
        return None
    for platform, aliases in _SOCIAL_ALIASES.items():
        if slug in aliases:
            return f"social:{platform}"
    if slug in _EXACT_FIELDS:
        return _EXACT_FIELDS[slug]
    # Free-form headers like "Business Phone" or "Primary Mobile" still carry
    # the same meaning; the word rules avoid requiring an exact alias.
    if "phone" in slug or "mobile" in slug or "whatsapp" in slug or "telephone" in slug:
        return "phones"
    if "email" in slug or "e mail" in slug:
        return "email"
    if "website" in slug or "web site" in slug or "domain" in slug:
        return "website"
    if "address" in slug or "street" in slug:
        return "address"
    if "postal" in slug or "postcode" in slug or "zip" in slug:
        return "postal_code"
    if "city" in slug or "town" in slug:
        return "city"
    if "state" in slug or "province" in slug:
        return "state"
    if "country" in slug or "nation" in slug:
        return "country"
    if "category" in slug or "industry" in slug or "sector" in slug:
        return "category"
    if "rating" in slug or "stars" in slug:
        return "rating"
    if "review" in slug:
        return "review_count"
    if "status" in slug or "stage" in slug:
        return "status"
    if slug in {"tags", "tag", "labels", "keywords"}:
        return "tags"
    if slug in {"notes", "note", "comments", "comment", "description"}:
        return "notes"
    return None


def _field_label(field: str) -> str:
    if field.startswith("social:"):
        platform = field.split(":", 1)[1]
        return _SOCIAL_LABELS.get(platform, platform.title())
    return _FIELD_LABELS.get(field, field)


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _decode_csv(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _csv_matrix(content: bytes) -> list[list[str]]:
    text = _decode_csv(content)
    if not text.strip():
        raise LeadImportError("The file is empty.")
    delimiter = _sniff_delimiter(text[:4096])
    return [list(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _xlsx_matrices(content: bytes) -> list[tuple[str, list[list[Any]]]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - pinned in requirements
        raise LeadImportError(
            "Excel support is not installed on this deployment."
        ) from exc

    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise LeadImportError(
            "That file could not be opened as an Excel workbook."
        ) from exc
    try:
        return [
            (sheet.title, [list(row) for row in sheet.iter_rows(values_only=True)])
            for sheet in workbook.worksheets
        ]
    finally:
        workbook.close()


def _find_header(
    matrix: list[list[Any]],
) -> tuple[int, dict[int, str]] | None:
    """Locate the header row; it must name a business (or company) column."""
    best: tuple[int, dict[int, str]] | None = None
    limit = min(len(matrix), 15)
    for index in range(limit):
        cells = [_cell_text(cell) for cell in matrix[index]]
        mapping = {}
        for column, header in enumerate(cells):
            # Real headers are short labels. Long cells are prose from a notes
            # or instructions sheet and must not be mistaken for a header.
            if len(header) > 40:
                continue
            field = canonical_field(header)
            if field:
                mapping[column] = field
        if "title" not in mapping.values() or len(mapping) < 2:
            continue
        if best is None or len(mapping) > len(best[1]):
            best = (index, mapping)
    return best


def _empty_row(cells: list[Any]) -> bool:
    return not any(_cell_text(cell) for cell in cells)


def _matrix_rows(matrix: list[list[Any]], *, start: int) -> list[tuple[int, list[Any]]]:
    rows: list[tuple[int, list[Any]]] = []
    # ``start`` is the zero-based header index, so the first data row is the
    # next line and its one-based spreadsheet number is ``start + 2``.
    for offset, cells in enumerate(matrix[start + 1 :], start=start + 2):
        if _empty_row(cells):
            continue
        rows.append((offset, list(cells)))
        if len(rows) > LEADS_IMPORT_MAX_ROWS:
            raise LeadImportError(
                f"This sheet has more than {LEADS_IMPORT_MAX_ROWS} leads. "
                "Split it and import the parts one after another."
            )
    return rows


def _social_link(platform: str, value: Any) -> str | None:
    text = _cell_text(value)
    if not text:
        return None
    domains = _SOCIAL_DOMAINS[platform]

    def host_matches(host: str) -> bool:
        host = host.lower()
        return any(host == domain or host.endswith(f".{domain}") for domain in domains)

    if "://" in text or re.match(r"^(?:www\.)?[a-z0-9-]+\.[a-z0-9.-]+/", text, re.I):
        candidate = text if "://" in text else f"https://{text}"
        parsed = urlparse(candidate)
        host = parsed.hostname or ""
        if parsed.scheme not in {"http", "https"} or not host_matches(host):
            return None
        path = parsed.path.rstrip("/")
        return f"https://{host}{path}" if path else f"https://{host}"

    if "@" in text and "." in text:
        # Looks like an email address, not a profile.
        return None
    handle = text.lstrip("@").strip().strip("/")
    if not handle or not _HANDLE_RE.match(handle):
        return None

    if platform == "instagram":
        return f"https://www.instagram.com/{handle}/"
    if platform == "x":
        return f"https://x.com/{handle}"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{handle}"
    if platform == "youtube":
        if handle.upper().startswith("UC"):
            return f"https://www.youtube.com/channel/{handle}"
        return f"https://www.youtube.com/@{handle}"
    if platform == "facebook":
        return f"https://www.facebook.com/{handle}"
    if platform == "linkedin":
        return (
            f"https://www.linkedin.com/{handle}"
            if "/" in handle
            else (f"https://www.linkedin.com/in/{handle}")
        )
    return None


def _normalize_status(value: Any) -> str:
    text = _cell_text(value)
    if not text:
        return "new"
    slug = _slug(text).replace(" ", "_")
    return _STATUS_VALUES.get(slug) or _STATUS_VALUES.get(_slug(text)) or "new"


def _parse_tags(value: Any) -> list[str]:
    seen: list[str] = []
    for part in _TAG_SPLIT_RE.split(_cell_text(value)):
        tag = part.strip()[:64]
        if tag and tag.lower() not in {existing.lower() for existing in seen}:
            seen.append(tag)
        if len(seen) >= 20:
            break
    return seen


def _float(value: Any) -> float | None:
    text = _cell_text(value).replace(",", "")
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _int(value: Any) -> int:
    parsed = _float(value)
    return int(parsed) if parsed is not None else 0


def _website_domain(website: str | None) -> str | None:
    if not website:
        return None
    try:
        host = (urlparse(website).hostname or "").lower().strip(".")
    except ValueError:
        return None
    if not host:
        return None
    return host[4:] if host.startswith("www.") else host


def import_dedupe_key(lead: dict[str, Any]) -> str:
    """Stable identity for a spreadsheet row.

    A phone number survives formatting changes, so it wins; the website domain
    identifies a business even when the sheet has no phone; the email is the
    weakest of the three because one inbox can front several locations.
    """
    phone = lead.get("phone_e164")
    if phone:
        return f"phone:{phone}"[:191]
    domain = _website_domain(lead.get("website"))
    if domain:
        return f"web:{domain}"[:191]
    email = lead.get("email")
    if email:
        return f"email:{email.lower()}"[:191]
    seed = "|".join(
        (
            (lead.get("title") or "").lower(),
            (lead.get("city") or "").lower(),
            (lead.get("state") or "").lower(),
        )
    )
    return f"hash:{hashlib.sha256(seed.encode()).hexdigest()}"[:191]


def parse_lead_import(filename: str, content: bytes) -> ParsedImport:
    """Read a CSV/XLSX upload into canonical, header-mapped rows."""
    lowered = (filename or "").lower()
    if lowered.endswith(".xlsx"):
        matrices = [
            (sheet, matrix) for sheet, matrix in _xlsx_matrices(content) if matrix
        ]
    elif lowered.endswith(".csv"):
        matrices = [("CSV", _csv_matrix(content))]
    else:
        raise LeadImportError("Upload an .xlsx or .csv file from the Bolo template.")

    rows: list[ParsedRow] = []
    mapped_headers: dict[str, str] = {}
    ignored: list[str] = []
    sheets_used: list[str] = []

    for sheet_name, matrix in matrices:
        header = _find_header(matrix)
        if header is None:
            continue
        header_index, mapping = header
        sheets_used.append(sheet_name)
        header_cells = [_cell_text(cell) for cell in matrix[header_index]]
        for column, field in mapping.items():
            label = header_cells[column] or f"Column {column + 1}"
            mapped_headers.setdefault(label, _field_label(field))
        for column, label in enumerate(header_cells):
            if label and column not in mapping and label not in ignored:
                ignored.append(label)

        for row_number, cells in _matrix_rows(matrix, start=header_index):
            values: dict[str, Any] = {"phones": [], "socials": {}}
            for column, field in mapping.items():
                text = _cell_text(cells[column] if column < len(cells) else None)
                if not text:
                    continue
                if field == "phones":
                    values["phones"].append(text)
                elif field.startswith("social:"):
                    values["socials"].setdefault(field.split(":", 1)[1], text)
                elif field not in values:
                    values[field] = text
            rows.append(
                ParsedRow(
                    sheet=sheet_name,
                    row_number=row_number,
                    values=values,
                    issues=[],
                )
            )

    if not sheets_used:
        raise LeadImportError(
            "No lead table was found. Keep a header row that includes a "
            "business name column."
        )
    if not rows:
        raise LeadImportError(
            "The header row was found but there are no leads below it."
        )

    return ParsedImport(
        file_name=filename or "upload",
        sheets=sheets_used,
        rows=rows,
        mapped_headers=mapped_headers,
        ignored_headers=ignored,
    )


def _country_hint_for(parsed: ParsedImport, explicit: str | None) -> str | None:
    hint = normalize_country_code(explicit)
    if hint:
        return hint
    counts = Counter(
        code for row in parsed.rows if (code := lead_country(row.values.get("country")))
    )
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def _normalize_row(
    row: ParsedRow, *, file_name: str, country_hint: str | None
) -> ImportRow:
    issues = list(row.issues)
    values = row.values
    title = _cell_text(values.get("title"))
    if not title:
        return ImportRow(
            sheet=row.sheet,
            row_number=row.row_number,
            lead=None,
            issues=[*issues, "Missing business name"],
        )

    candidates = [phone for phone in values.get("phones", []) if phone]
    hint = lead_country(values.get("country")) or country_hint
    chosen_phone = next(
        (
            candidate
            for candidate in candidates
            if normalize_lead_phone(candidate, hint) is not None
        ),
        candidates[0] if candidates else "",
    )

    website = normalize_website(_cell_text(values.get("website")) or None)
    if values.get("website") and website is None:
        issues.append("Website could not be read and was skipped")

    provider_row: dict[str, Any] = {
        "title": title,
        "category": _cell_text(values.get("category")),
        "phone": chosen_phone,
        "email": _cell_text(values.get("email")),
        "website": website or "",
        "address": _cell_text(values.get("address")),
        "city": _cell_text(values.get("city")),
        "state": _cell_text(values.get("state")),
        "country": _cell_text(values.get("country")),
        "postal_code": _cell_text(values.get("postal_code")),
        "rating": values.get("rating"),
        "review_count": values.get("review_count"),
    }
    lead = normalize_lead_row(provider_row, country_hint=hint, source="import")
    if lead is None:
        return ImportRow(
            sheet=row.sheet,
            row_number=row.row_number,
            lead=None,
            issues=[*issues, "Missing business name"],
        )

    if candidates and lead.get("phone_e164") is None:
        issues.append("Phone could not be converted to an international number")

    socials: dict[str, str] = {}
    for platform, raw in values.get("socials", {}).items():
        if platform not in SOCIAL_PLATFORMS:
            continue
        link = _social_link(platform, raw)
        if link:
            socials[platform] = link
        else:
            issues.append(
                f"{_SOCIAL_LABELS.get(platform, platform.title())} value is not "
                "a profile link"
            )

    status = _normalize_status(values.get("status"))
    if (
        values.get("status")
        and status == "new"
        and _slug(values["status"])
        not in {
            "new",
            "open",
        }
    ):
        issues.append(f"Status {values['status']!r} is not recognised; saved as New")

    lead.update(
        {
            "source": "import",
            "social_links": socials,
            "crm_status": status,
            "tags": _parse_tags(values.get("tags")),
            "notes": _cell_text(values.get("notes"))[:20000] or None,
            "rating": _float(values.get("rating")),
            "review_count": _int(values.get("review_count")),
            "dedupe_key": "",
            "raw_data": {
                "import": {
                    "file": file_name,
                    "sheet": row.sheet,
                    "row": row.row_number,
                }
            },
        }
    )
    lead["dedupe_key"] = import_dedupe_key(lead)
    return ImportRow(
        sheet=row.sheet,
        row_number=row.row_number,
        lead=lead,
        issues=issues,
    )


def prepare_import(
    parsed: ParsedImport, *, country_hint: str | None = None
) -> PreparedImport:
    hint = _country_hint_for(parsed, country_hint)
    normalized = [
        _normalize_row(row, file_name=parsed.file_name, country_hint=hint)
        for row in parsed.rows
    ]

    warnings: list[str] = []
    if parsed.ignored_headers:
        warnings.append("Ignored columns: " + ", ".join(parsed.ignored_headers[:8]))
    for row in normalized:
        for issue in row.issues:
            if len(warnings) >= 20:
                break
            warnings.append(f"Sheet {row.sheet}, row {row.row_number}: {issue}")
    if len(warnings) >= 20:
        warnings.append("More row notes were omitted.")

    seen: set[str] = set()
    leads: list[dict[str, Any]] = []
    duplicate_rows = 0
    for row in normalized:
        lead = row.lead
        if lead is None:
            continue
        key = lead["dedupe_key"]
        if key in seen:
            duplicate_rows += 1
            continue
        seen.add(key)
        leads.append(lead)

    return PreparedImport(
        rows=normalized,
        leads=leads,
        total_rows=len(normalized),
        skipped_rows=sum(1 for row in normalized if row.lead is None),
        duplicate_rows=duplicate_rows,
        warnings=warnings,
    )


def build_import_template_xlsx() -> bytes:
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    workbook = Workbook()
    leads_sheet = workbook.active
    leads_sheet.title = "Leads"

    header_fill = PatternFill("solid", fgColor=TEAL)
    header_font = Font(bold=True, color="FFFFFF")
    example_font = Font(italic=True, color="5B6B67")

    for index, (field, label, width, example, hint) in enumerate(
        TEMPLATE_COLUMNS, start=1
    ):
        cell = leads_sheet.cell(row=1, column=index, value=label)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(vertical="center")
        leads_sheet.column_dimensions[get_column_letter(index)].width = width
        if hint:
            cell.comment = Comment(hint, "Bolo")

    example_rows = [
        {
            "title": "Bluebird Dental (example — delete this row)",
            "category": "Dentist",
            "phones": "+1 512 555 0142",
            "email": "hello@bluebirddental.example",
            "website": "https://bluebirddental.example",
            "address": "1200 Main St",
            "city": "Austin",
            "state": "TX",
            "country": "US",
            "postal_code": "78701",
            "social:instagram": "@bluebirddental",
            "status": "New",
            "tags": "dental, austin",
            "notes": "Example row: replace it with your own leads.",
            "rating": "4.7",
            "review_count": "214",
        },
        {
            "title": "Cedar Works (example — delete this row)",
            "category": "Carpenter",
            "phones": "5125550199",
            "email": "team@cedarworks.example",
            "website": "cedarworks.example",
            "city": "Round Rock",
            "state": "TX",
            "country": "US",
            "social:facebook": "facebook.com/cedarworks",
            "status": "Contacted",
        },
    ]
    for row_offset, example_row in enumerate(example_rows, start=2):
        for index, (field, *_rest) in enumerate(TEMPLATE_COLUMNS, start=1):
            value = example_row.get(field)
            if value is None:
                continue
            cell = leads_sheet.cell(row=row_offset, column=index, value=value)
            cell.font = example_font

    leads_sheet.freeze_panes = "A2"
    leads_sheet.auto_filter.ref = f"A1:{get_column_letter(len(TEMPLATE_COLUMNS))}1"

    status_column = next(
        index
        for index, (field, *_rest) in enumerate(TEMPLATE_COLUMNS, start=1)
        if field == "status"
    )
    status_letter = get_column_letter(status_column)
    validation = DataValidation(
        type="list",
        formula1='"New,Contacted,Interested,Not interested,Won,Lost,Do not call"',
        allow_blank=True,
    )
    validation.error = "Choose one of the statuses from the list."
    validation.promptTitle = "Lead status"
    validation.prompt = "Optional. New is used when this is blank."
    leads_sheet.add_data_validation(validation)
    validation.add(f"{status_letter}2:{status_letter}5000")

    guide = workbook.create_sheet("How to fill this in")
    title_cell = guide.cell(row=1, column=1, value="How to import your own leads")
    title_cell.font = Font(bold=True, size=14, color=INK)
    intro = [
        "Fill the Leads sheet, keep the header row, then upload the file in "
        "Bolo under Leads → Import.",
        "One business per row. Only Business name is required.",
        "Delete the two example rows before uploading — they are not real businesses.",
        "Duplicate rows inside the file are merged. A lead that already exists "
        "(same phone, website or email) is updated, and your CRM status, tags "
        "and notes are never overwritten.",
        "Valid phone numbers get a WhatsApp icon automatically. Social columns "
        "accept a full profile link or an @handle.",
    ]
    row = 3
    for text in intro:
        guide.cell(row=row, column=1, value=text)
        guide.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        row += 1

    row += 1
    guide.cell(row=row, column=1, value="Column").font = Font(bold=True)
    guide.cell(row=row, column=2, value="What it does").font = Font(bold=True)
    row += 1
    for field, label, _width, example, hint in TEMPLATE_COLUMNS:
        required = "Required" if field == "title" else "Optional"
        text = f"{required}. Example: {example or '—'}"
        if hint:
            text = f"{text} {hint}"
        guide.cell(row=row, column=1, value=label)
        guide.cell(row=row, column=2, value=text)
        row += 1

    guide.column_dimensions["A"].width = 22
    guide.column_dimensions["B"].width = 100
    for row in guide.iter_rows(min_row=1):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    guide.sheet_view.showGridLines = False

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def build_import_template_csv() -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(TEMPLATE_HEADERS)
    writer.writerow(
        [
            "Bluebird Dental (example — delete this row)",
            "Dentist",
            "+1 512 555 0142",
            "hello@bluebirddental.example",
            "https://bluebirddental.example",
            "1200 Main St",
            "Austin",
            "TX",
            "US",
            "78701",
            "@bluebirddental",
            "",
            "",
            "",
            "",
            "",
            "New",
            "dental, austin",
            "Example row: replace it with your own leads.",
            "4.7",
            "214",
        ]
    )
    writer.writerow(
        [
            "Cedar Works (example — delete this row)",
            "Carpenter",
            "5125550199",
            "team@cedarworks.example",
            "cedarworks.example",
            "",
            "Round Rock",
            "TX",
            "US",
            "",
            "",
            "",
            "facebook.com/cedarworks",
            "",
            "",
            "",
            "Contacted",
            "",
            "",
            "",
            "",
        ]
    )
    # Excel opens UTF-8 CSV correctly only when the file starts with a BOM.
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")
