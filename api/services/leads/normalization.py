"""Turn raw provider rows into the flat lead records stored by Bolo."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from typing import Any

from api.utils.email_validation import EMAIL_PATTERN, normalize_email
from api.utils.telephony_address import normalize_telephony_address

_PHONE_SPLIT_RE = re.compile(r"\s*(?:[/;|,]|\bor\b)\s*", re.IGNORECASE)


def parse_google_maps_csv(text: str, *, partial: bool = False) -> list[dict[str, Any]]:
    """Parse the scraper's CSV export into dictionaries keyed by header name.

    While a scrape is running the CSV grows a row at a time, so a download can
    end mid-row. With ``partial=True`` the trailing incomplete line is ignored
    (the next poll picks it up) instead of surfacing a half-written business.
    """
    if not text:
        return []
    if partial:
        # Only parse complete physical lines; the scraper terminates every row
        # with a newline before starting the next one.
        if not text.endswith(("\n", "\r")):
            cut = max(text.rfind("\n"), text.rfind("\r"))
            if cut < 0:
                return []
            text = text[: cut + 1]
        if not text.strip():
            return []

    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, Any]] = []
    try:
        for row in reader:
            # Fewer columns than the header (or extra ones under ``None``)
            # means the row is truncated; wait for the next poll.
            if None in row or None in row.values():
                continue
            if not any((value or "").strip() for value in row.values()):
                continue
            rows.append({str(k).strip(): v for k, v in row.items() if k is not None})
    except csv.Error:
        if not partial:
            raise
    return rows


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _json_value(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return default
    return default


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        text = _text(value)
        if not text:
            return default
        parsed = float(text)
        # Ignore NaN/Infinity so JSON/DB writes stay valid.
        if math.isnan(parsed) or math.isinf(parsed):
            return default
        return parsed
    except (ValueError, TypeError):
        return default


def _int(value: Any, default: int = 0) -> int:
    parsed = _float(value)
    return int(parsed) if parsed is not None else default


def _first_email(*values: Any) -> str | None:
    for value in values:
        if not value:
            continue
        if isinstance(value, list):
            candidates = [str(item) for item in value]
        else:
            parsed = _json_value(value, None)
            if isinstance(parsed, list):
                candidates = [str(item) for item in parsed]
            else:
                candidates = [str(value)]
        for candidate in candidates:
            for match in EMAIL_PATTERN.finditer(candidate):
                # Use the same validator as manual edits and campaign audiences:
                # image filenames and placeholder domains are not addresses.
                normalized = normalize_email(match.group(0))
                if normalized:
                    return normalized
    return None


def _normalize_phone(raw: Any, country_hint: str | None) -> str | None:
    text = _text(raw)
    if not text:
        return None
    # Provider rows sometimes list a switchboard and a mobile number.
    first = _PHONE_SPLIT_RE.split(text)[0].strip()
    if not first:
        return None
    try:
        normalized = normalize_telephony_address(first, country_hint)
    except ValueError:
        return None
    if normalized.address_type != "pstn":
        return None
    if not normalized.canonical.startswith("+") or len(normalized.canonical) < 8:
        return None
    return normalized.canonical[:32]


def _raw_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    """Keep the provider row for later use without unbounded JSON growth."""
    snapshot: dict[str, Any] = {}
    for key, value in row.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            snapshot[key] = value
            continue
        text = str(value)
        snapshot[key] = text[:2000]
    return snapshot


def _dedupe_key(
    *, place_id: str, cid: str, data_id: str, title: str, address: str, phone: str
) -> str:
    if place_id:
        return f"place:{place_id}"[:191]
    if cid:
        return f"cid:{cid}"[:191]
    if data_id:
        return f"data:{data_id}"[:191]
    digest = hashlib.sha256(
        f"{title.lower()}|{address.lower()}|{phone}".encode()
    ).hexdigest()
    return f"hash:{digest}"[:191]


def normalize_lead_row(
    row: dict[str, Any],
    *,
    country_hint: str | None,
    source: str,
) -> dict[str, Any] | None:
    """Map a provider row to lead columns. Returns None for unusable rows."""
    title = _text(row.get("title") or row.get("name"))
    if not title:
        return None

    complete_address = _json_value(row.get("complete_address"), {})
    if not isinstance(complete_address, dict):
        complete_address = {}

    city = _text(complete_address.get("city")) or _text(row.get("city")) or None
    state = _text(complete_address.get("state")) or _text(row.get("state")) or None
    country = (
        _text(complete_address.get("country")) or _text(row.get("country")) or None
    )
    postal_code = (
        _text(complete_address.get("postal_code"))
        or _text(row.get("postal_code"))
        or None
    )
    street = _text(complete_address.get("street"))
    address = _text(row.get("address")) or street or None
    if address and street and street not in address:
        address = f"{street}, {address}"

    hint = (country_hint or country or "").strip().upper()
    if len(hint) != 2:
        hint = None

    phone_raw = _text(row.get("phone"))
    phone_e164 = _normalize_phone(phone_raw, hint)
    email = _first_email(row.get("emails"), row.get("email"))
    website = _text(row.get("website") or row.get("web_site"))[:1024] or None
    place_id = _text(row.get("place_id"))
    cid = _text(row.get("cid"))
    data_id = _text(row.get("data_id"))

    dedupe_key = _dedupe_key(
        place_id=place_id,
        cid=cid,
        data_id=data_id,
        title=title,
        address=address or "",
        phone=phone_e164 or phone_raw,
    )

    raw_data = _raw_snapshot(row)
    open_hours = _json_value(row.get("open_hours"), {})
    if not isinstance(open_hours, dict):
        open_hours = {}

    return {
        "dedupe_key": dedupe_key,
        "title": title[:512],
        "category": (_text(row.get("category")) or None),
        "address": address,
        "city": city,
        "state": state,
        "country": country,
        "postal_code": postal_code[:32] if postal_code else None,
        "phone": phone_raw[:64] or None,
        "phone_e164": phone_e164,
        "email": email,
        "website": website,
        "place_id": place_id[:255] or None,
        "cid": cid[:64] or None,
        "rating": _float(row.get("review_rating") or row.get("rating")),
        "review_count": _int(row.get("review_count")),
        "latitude": _float(row.get("latitude")),
        "longitude": _float(
            row.get("longitude") or row.get("longtitude") or row.get("lng")
        ),
        "open_hours": open_hours,
        "raw_data": raw_data,
        "source": source,
    }


def normalize_rows(
    rows: list[dict[str, Any]],
    *,
    country_hint: str | None,
    source: str,
) -> list[dict[str, Any]]:
    normalized = []
    for row in rows:
        lead = normalize_lead_row(row, country_hint=country_hint, source=source)
        if lead is not None:
            normalized.append(lead)
    return normalized


def normalize_lead_phone(raw: Any, country_hint: str | None = None) -> str | None:
    """Public E.164 normalizer for inline lead edits.

    Returns ``None`` when the value cannot be dialled, so callers can store the
    human-entered text while leaving the lead out of callable lists.
    """
    return _normalize_phone(raw, country_hint)
