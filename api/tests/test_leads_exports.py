import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import openpyxl
import pytest

from api.services.leads.exports import build_export


def _lead(**overrides):
    values = {
        "id": 1,
        "title": "Apex Software Labs",
        "category": "Software company",
        "phone": "(415) 555-0142",
        "phone_e164": "+14155550142",
        "email": "hello@apex.example.com",
        "website": "https://apex.example.com",
        "address": "123 Market St",
        "city": "San Francisco",
        "state": "CA",
        "country": "US",
        "postal_code": "94105",
        "place_id": "abc123",
        "rating": 4.6,
        "review_count": 128,
        "latitude": 37.79,
        "longitude": -122.4,
        "crm_status": "new",
        "owner_user_id": None,
        "tags": ["hot", "q4"],
        "notes": None,
        "source": "google_maps",
        "last_contacted_at": None,
        "next_follow_up_at": None,
        "created_at": datetime(2026, 11, 17, 12, 0, tzinfo=UTC),
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_csv_export_is_utf8_bom_with_expected_columns():
    content, filename, media = build_export(
        [_lead()], "csv", title="Software in California", subtitle="1 lead"
    )

    assert filename.endswith(".csv")
    assert media == "text/csv; charset=utf-8"
    text = content.decode("utf-8-sig")
    header, row = text.splitlines()[:2]
    assert header.startswith("Business,Category,Phone")
    assert "Apex Software Labs" in row
    assert "+14155550142" in row


def test_xlsx_export_is_a_real_workbook_with_frozen_header():
    content, filename, media = build_export(
        [_lead(), _lead(id=2, title="Cedar Works")],
        "xlsx",
        title="Leads",
        subtitle="2 leads",
    )

    assert filename.endswith(".xlsx")
    assert media.endswith("spreadsheetml.sheet")
    workbook = openpyxl.load_workbook(io.BytesIO(content))
    sheet = workbook.active
    assert sheet["A1"].value == "Business"
    assert sheet["A2"].value == "Apex Software Labs"
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref is not None


def test_json_export_carries_metadata_and_leads():
    content, filename, media = build_export(
        [_lead()], "json", title="Leads", subtitle="1 lead"
    )

    assert filename.endswith(".json")
    assert media == "application/json"
    payload = json.loads(content)
    assert payload["title"] == "Leads"
    assert payload["count"] == 1
    assert payload["leads"][0]["title"] == "Apex Software Labs"


def test_pdf_and_png_exports_have_real_signatures():
    pdf, pdf_name, pdf_media = build_export(
        [_lead()], "pdf", title="Leads", subtitle="1 lead"
    )
    png, png_name, png_media = build_export(
        [_lead()], "png", title="Leads", subtitle="1 lead"
    )

    assert pdf.startswith(b"%PDF")
    assert pdf_name.endswith(".pdf")
    assert pdf_media == "application/pdf"
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert png_name.endswith(".png")
    assert png_media == "image/png"


def test_unknown_export_format_is_rejected():
    with pytest.raises(ValueError):
        build_export([_lead()], "docx", title="Leads", subtitle="")
