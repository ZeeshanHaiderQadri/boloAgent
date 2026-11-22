"""Lead import: template, spreadsheet parsing and CRM upsert coverage."""

import io
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from api.routes.leads import preview_lead_import, router
from api.services.auth.depends import get_user
from api.services.leads.imports import (
    TEMPLATE_HEADERS,
    build_import_template_csv,
    build_import_template_xlsx,
    parse_lead_import,
    prepare_import,
)
from api.services.leads.normalization import normalize_lead_row

USER = SimpleNamespace(id=3, selected_organization_id=11)


def _csv(text: str) -> bytes:
    return text.encode("utf-8")


def _prepare(text: str, *, country_hint: str | None = "US"):
    parsed = parse_lead_import("leads.csv", _csv(text))
    return parsed, prepare_import(parsed, country_hint=country_hint)


async def _org(db_client):
    user, _ = await db_client.get_or_create_user_by_provider_id(
        f"leads-import-{uuid.uuid4().hex}"
    )
    org, _ = await db_client.get_or_create_organization_by_provider_id(
        f"leads-import-{uuid.uuid4().hex}", user.id
    )
    return user, org


def test_template_has_leads_sheet_guidance_and_examples():
    content = build_import_template_xlsx()
    workbook = load_workbook(io.BytesIO(content))

    assert workbook.sheetnames == ["Leads", "How to fill this in"]
    sheet = workbook["Leads"]
    assert [cell.value for cell in sheet[1]] == TEMPLATE_HEADERS
    assert sheet.freeze_panes == "A2"
    assert "Bluebird Dental" in str(sheet["A2"].value)
    assert workbook["How to fill this in"]["A1"].value == (
        "How to import your own leads"
    )


def test_csv_template_round_trips_through_the_parser():
    content = build_import_template_csv()
    assert content.startswith(b"\xef\xbb\xbf")

    parsed = parse_lead_import("bolo_leads_template.csv", content)
    prepared = prepare_import(parsed, country_hint=None)

    assert parsed.mapped_headers["Business name"] == "Business name"
    assert prepared.total_rows == 2
    assert len(prepared.leads) == 2
    first = prepared.leads[0]
    assert first["phone_e164"] == "+15125550142"
    assert first["social_links"]["instagram"] == (
        "https://www.instagram.com/bluebirddental/"
    )


def test_aliases_phones_and_duplicate_rows_are_handled():
    parsed, prepared = _prepare(
        "Company Name,Business Phone,E-mail,Website,Instagram,LinkedIn,Status\n"
        "Acme Roofing,+1 512-555-0100,Hello@AcmeRoof.com,acmeroof.com,"
        "@acmeroof,https://www.linkedin.com/company/acme,Contacted\n"
        "Acme Roofing (dup),5125550100,hello@acmeroof.com,,,@acmeroof,\n"
        ",,+1 512 555 0199,no-title@acme.com,,,,\n"
    )

    assert parsed.mapped_headers == {
        "Company Name": "Business name",
        "Business Phone": "Phone",
        "E-mail": "Email",
        "Website": "Website",
        "Instagram": "Instagram",
        "LinkedIn": "LinkedIn",
        "Status": "Status",
    }
    assert prepared.total_rows == 3
    assert prepared.skipped_rows == 1
    assert prepared.duplicate_rows == 1
    assert len(prepared.leads) == 1

    lead = prepared.leads[0]
    assert lead["title"] == "Acme Roofing"
    assert lead["phone_e164"] == "+15125550100"
    assert lead["email"] == "hello@acmeroof.com"
    assert lead["website"] == "https://acmeroof.com"
    assert lead["crm_status"] == "contacted"
    assert lead["social_links"] == {
        "instagram": "https://www.instagram.com/acmeroof/",
        "linkedin": "https://www.linkedin.com/company/acme",
    }


def test_first_dialable_phone_wins_and_invalid_socials_warn():
    prepared = prepare_import(
        parse_lead_import(
            "leads.csv",
            _csv(
                "Business name,Phone,Mobile,Country,Instagram,Facebook\n"
                "London Bakery,n/a,+44 20 7946 0958,GB,@bakery,not a profile!!\n"
            ),
        ),
        country_hint=None,
    )

    lead = prepared.leads[0]
    assert lead["phone"] == "+44 20 7946 0958"
    assert lead["phone_e164"] == "+442079460958"
    assert lead["country"] == "GB"
    assert lead["social_links"] == {"instagram": "https://www.instagram.com/bakery/"}
    assert prepared.warnings == [
        "Sheet CSV, row 2: Facebook value is not a profile link"
    ]


def test_header_can_sit_below_a_title_row_of_an_xlsx_sheet():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Leads"
    sheet.append(["My 2026 prospect list"])
    sheet.append([])
    sheet.append(["Business name", "Phone", "Country"])
    sheet.append(["Acme Roofing", "+1 512 555 0100", "US"])

    notes = workbook.create_sheet("Read me")
    notes.append(["How to prepare your list"])
    notes.append(
        ["Business name", "Fill this column for every row, one row per business."]
    )
    notes.append(["Phone", "Use the international format when you can."])

    buffer = io.BytesIO()
    workbook.save(buffer)
    parsed = parse_lead_import("leads.xlsx", buffer.getvalue())
    prepared = prepare_import(parsed, country_hint=None)

    assert parsed.sheets == ["Leads"]
    assert prepared.total_rows == 1
    assert prepared.rows[0].row_number == 4
    assert prepared.leads[0]["title"] == "Acme Roofing"


def test_template_download_and_preview_route():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user] = lambda: USER
    client = TestClient(app)

    template = client.get("/leads/import/template", params={"format": "csv"})
    assert template.status_code == 200
    assert template.headers["content-disposition"].endswith(
        'filename="bolo_leads_template.csv"'
    )
    assert template.content.startswith(b"\xef\xbb\xbfBusiness name")

    preview = client.post(
        "/leads/import/preview",
        files={
            "file": (
                "leads.csv",
                _csv("Business name,Phone\nAcme Roofing,+1 512 555 0100\n"),
                "text/csv",
            )
        },
        data={"country_code": "US"},
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["ready_rows"] == 1
    assert body["leads"][0]["phone_e164"] == "+15125550100"


@pytest.mark.asyncio
async def test_import_creates_updates_and_preserves_crm_fields(
    db_session, async_session
):
    user, org = await _org(db_session)
    search = await db_session.create_lead_search(
        organization_id=org.id,
        created_by=user.id,
        name="Roofers in Austin",
        category="Roofers",
        location="Austin, TX",
        country_code="US",
        language="en",
        provider="demo",
        params={"depth": 8},
    )
    scraper_row = normalize_lead_row(
        {
            "title": "Acme Roofing",
            "category": "Roofing",
            "phone": "+1 512 555 0142",
            "email": "old@acmeroof-demo.com",
            "website": "https://acmeroof.example",
            "city": "Austin",
            "state": "TX",
            "country": "US",
            "place_id": "place-import-1",
        },
        country_hint="US",
        source="demo",
    )
    assert scraper_row is not None
    await db_session.upsert_leads_for_search(
        search_id=search.id, organization_id=org.id, leads=[scraper_row]
    )
    leads, _ = await db_session.list_leads(org.id)
    existing = leads[0]
    await db_session.update_lead(
        existing.id,
        org.id,
        crm_status="contacted",
        tags=["vip"],
        notes="keep me",
    )

    _, prepared = _prepare(
        "Business name,Phone,Email,Instagram\n"
        "Acme Roofing Import,+1 512-555-0142,new@acmeroof-demo.com,@acmeroof\n"
    )
    summary = await db_session.upsert_imported_leads(
        organization_id=org.id,
        leads=prepared.leads,
        owner_user_id=user.id,
    )
    assert summary == {"created": 0, "updated": 1, "duplicates": 0}

    # The test fixture shares one session, so refresh the instance before
    # reading the row Core SQL just updated.
    await async_session.refresh(existing)
    lead = existing
    assert lead.title == "Acme Roofing Import"
    assert lead.email == "new@acmeroof-demo.com"
    assert lead.website == "https://acmeroof.example"
    assert lead.social_links == {"instagram": "https://www.instagram.com/acmeroof/"}
    assert lead.socials_checked_at is not None
    assert lead.crm_status == "contacted"
    assert lead.tags == ["vip"]
    assert lead.notes == "keep me"
    assert lead.source == "demo"
    assert lead.owner_user_id is None
    assert "import" not in (lead.raw_data or {})

    _, second = _prepare(
        "Business name,Phone,Email,Website\n"
        "Cedar Works,+1 512 555 0199,team@cedar.example,cedar.example\n"
    )
    second_summary = await db_session.upsert_imported_leads(
        organization_id=org.id,
        leads=second.leads,
        owner_user_id=user.id,
    )
    assert second_summary == {"created": 1, "updated": 0, "duplicates": 0}

    org_leads, _ = await db_session.list_leads(org.id)
    cedar = next(row for row in org_leads if row.title == "Cedar Works")
    assert cedar.source == "import"
    assert cedar.owner_user_id == user.id
    assert cedar.phone_e164 == "+15125550199"
    assert cedar.website == "https://cedar.example"

    repeat = await db_session.upsert_imported_leads(
        organization_id=org.id,
        leads=[*prepared.leads, *second.leads],
        owner_user_id=user.id,
    )
    assert repeat == {"created": 0, "updated": 2, "duplicates": 0}

    other_user, other_org = await _org(db_session)
    other_summary = await db_session.upsert_imported_leads(
        organization_id=other_org.id,
        leads=second.leads,
        owner_user_id=other_user.id,
    )
    assert other_summary == {"created": 1, "updated": 0, "duplicates": 0}


@pytest.mark.asyncio
async def test_preview_counts_leads_already_in_the_crm(db_session):
    from starlette.datastructures import Headers, UploadFile

    user, org = await _org(db_session)
    _, seeded = _prepare("Business name,Phone\nAcme Roofing,+1 512 555 0100\n")
    await db_session.upsert_imported_leads(
        organization_id=org.id,
        leads=seeded.leads,
        owner_user_id=user.id,
    )

    upload = UploadFile(
        file=io.BytesIO(
            _csv(
                "Business name,Phone\n"
                "Acme Roofing,+1 512 555 0100\n"
                "Cedar Works,+1 512 555 0199\n"
            )
        ),
        filename="leads.csv",
        headers=Headers({"content-type": "text/csv"}),
    )
    result = await preview_lead_import(
        file=upload,
        country_code="US",
        user=SimpleNamespace(id=user.id, selected_organization_id=org.id),
    )

    assert result.ready_rows == 2
    assert result.existing_rows == 1
    rows = {row.title: row for row in result.leads}
    assert rows["Acme Roofing"].existing is True
    assert rows["Cedar Works"].existing is False
