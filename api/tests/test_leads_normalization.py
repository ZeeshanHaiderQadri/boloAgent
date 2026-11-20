import math

from api.services.leads.normalization import (
    normalize_lead_row,
    normalize_rows,
    parse_google_maps_csv,
)


def test_parse_scraper_csv_skips_blank_rows_and_strips_headers():
    text = (
        "title,address,phone\n"
        " Apex Labs ,1 Market St,+14155550100\n"
        ",,\n"
        "Cedar Works,2 High St,+442071234567\n"
    )

    rows = parse_google_maps_csv(text)

    assert [row["title"] for row in rows] == [" Apex Labs ", "Cedar Works"]
    assert all(" " not in key for row in rows for key in row)


def test_partial_parse_drops_an_unfinished_trailing_row():
    text = (
        "title,address,phone\n"
        "Apex Coffee,1 Market St,+14155550100\n"
        "Cedar Bakery,2 High St,+1415555010"
    )

    complete = parse_google_maps_csv(text, partial=True)
    assert [row["title"] for row in complete] == ["Apex Coffee"]

    # A row whose quoted field is still being written must not surface either.
    quoted = (
        "title,address,phone\n"
        "Apex Coffee,1 Market St,+14155550100\n"
        'Cedar Bakery,"2 High St'
    )
    assert [row["title"] for row in parse_google_maps_csv(quoted, partial=True)] == [
        "Apex Coffee"
    ]


def test_partial_parse_keeps_every_complete_row():
    text = "title,phone\nApex,+14155550100\nCedar,+14155550101\n"
    assert len(parse_google_maps_csv(text, partial=True)) == 2


def test_normalize_maps_business_fields_and_e164_phone():
    lead = normalize_lead_row(
        {
            "title": "Apex Software Labs",
            "category": "Software company",
            "address": "123 Market St, San Francisco, CA 94105, US",
            "phone": "(415) 555-0142",
            "emails": '["hello@apex-software.com"]',
            "website": "https://apex.example.com",
            "place_id": "abc123",
            "review_rating": "4.6",
            "review_count": "128",
            "latitude": "37.79",
            "longitude": "-122.4",
            "complete_address": (
                '{"street": "123 Market St", "city": "San Francisco", '
                '"state": "CA", "country": "US", "postal_code": "94105"}'
            ),
        },
        country_hint="US",
        source="google_maps",
    )

    assert lead is not None
    assert lead["dedupe_key"] == "place:abc123"
    assert lead["phone"] == "(415) 555-0142"
    assert lead["phone_e164"] == "+14155550142"
    assert lead["email"] == "hello@apex-software.com"
    assert (lead["city"], lead["state"], lead["country"]) == (
        "San Francisco",
        "CA",
        "US",
    )
    assert lead["rating"] == 4.6
    assert lead["review_count"] == 128
    assert lead["source"] == "google_maps"


def test_normalize_uses_first_number_when_provider_lists_two():
    lead = normalize_lead_row(
        {"title": "Two Lines Ltd", "phone": "020 7946 0018 / 07700 900123"},
        country_hint="GB",
        source="google_maps",
    )

    assert lead is not None
    assert lead["phone_e164"] == "+442079460018"


def test_normalize_prefers_the_rows_own_country_over_the_search_hint():
    lead = normalize_lead_row(
        {
            "title": "Thames Coffee",
            "phone": "020 7946 0018",
            "complete_address": {"country": "GB"},
        },
        country_hint="US",
        source="google_maps",
    )

    assert lead is not None
    assert lead["phone_e164"] == "+442079460018"


def test_normalize_accepts_a_country_name_from_the_row():
    lead = normalize_lead_row(
        {
            "title": "Thames Coffee",
            "phone": "020 7946 0018",
            "complete_address": {"country": "United Kingdom"},
        },
        country_hint="US",
        source="google_maps",
    )

    assert lead is not None
    assert lead["phone_e164"] == "+442079460018"


def test_normalize_leaves_an_international_number_alone():
    # A leading "+" is already international; the search hint must not
    # prepend a second country code to it.
    lead = normalize_lead_row(
        {"title": "No Country Ltd", "phone": "+442079460018"},
        country_hint="US",
        source="google_maps",
    )

    assert lead is not None
    assert lead["phone_e164"] == "+442079460018"


def test_normalize_stores_no_e164_when_the_number_contradicts_the_country():
    lead = normalize_lead_row(
        {
            "title": "Cross-Border Co",
            "phone": "+44 20 7946 0018",
            "complete_address": {"country": "US"},
        },
        country_hint="US",
        source="google_maps",
    )

    assert lead is not None
    assert lead["phone"] == "+44 20 7946 0018"
    assert lead["phone_e164"] is None


def test_normalize_treats_a_shared_dial_code_as_agreeing():
    # +1 covers the US and Canada; digits alone cannot say which one, so the
    # number is kept rather than thrown away.
    lead = normalize_lead_row(
        {
            "title": "Maple Co",
            "phone": "+1 416 555 0123",
            "complete_address": {"country": "US"},
        },
        country_hint="US",
        source="google_maps",
    )

    assert lead is not None
    assert lead["phone_e164"] == "+14165550123"


def test_normalize_drops_non_dialable_and_invalid_numbers():
    for raw in ("not a phone", "12345", ""):
        lead = normalize_lead_row(
            {"title": "No Phone Co", "phone": raw},
            country_hint="US",
            source="google_maps",
        )
        assert lead is not None
        assert lead["phone_e164"] is None


def test_normalize_guardrails_nan_and_rejects_titleless_rows():
    lead = normalize_lead_row(
        {"title": "NaN Cafe", "review_rating": "NaN", "latitude": math.inf},
        country_hint="US",
        source="google_maps",
    )

    assert lead is not None
    assert lead["rating"] is None
    assert lead["latitude"] is None
    assert normalize_lead_row({}, country_hint="US", source="demo") is None


def test_dedupe_prefers_place_then_cid_then_data_then_hash():
    base = {"title": "Same Name", "address": "1 Main St", "phone": "+14155550100"}

    assert (
        normalize_lead_row(
            {**base, "place_id": "p1", "cid": "c1"}, country_hint="US", source="demo"
        )["dedupe_key"]
        == "place:p1"
    )
    assert (
        normalize_lead_row(
            {**base, "cid": "c1", "data_id": "d1"}, country_hint="US", source="demo"
        )["dedupe_key"]
        == "cid:c1"
    )
    assert (
        normalize_lead_row({**base, "data_id": "d1"}, country_hint="US", source="demo")[
            "dedupe_key"
        ]
        == "data:d1"
    )
    hashed = normalize_lead_row(base, country_hint="US", source="demo")
    assert hashed["dedupe_key"].startswith("hash:")


def test_hash_dedupe_is_stable_and_case_insensitive():
    upper = normalize_lead_row(
        {"title": "SAME NAME", "address": "1 MAIN ST", "phone": "+14155550100"},
        country_hint="US",
        source="demo",
    )
    lower = normalize_lead_row(
        {"title": "same name", "address": "1 main st", "phone": "+14155550100"},
        country_hint="US",
        source="demo",
    )

    assert upper["dedupe_key"] == lower["dedupe_key"]


def test_normalize_rows_skips_unusable_rows():
    rows = [
        {"title": "Good Co", "phone": "+14155550100"},
        {"phone": "+14155550101"},
        {"title": "Also Good", "phone": "+14155550102"},
    ]

    normalized = normalize_rows(rows, country_hint="US", source="demo")

    assert [lead["title"] for lead in normalized] == ["Good Co", "Also Good"]


def test_normalize_skips_image_filenames_and_placeholder_domains():
    lead = normalize_lead_row(
        {
            "title": "Image Trap Co",
            "emails": ["logo@2x.webp", "info@domain.com", "sales@image-trap.com"],
        },
        country_hint="US",
        source="google_maps",
    )

    assert lead is not None
    assert lead["email"] == "sales@image-trap.com"


def test_normalize_returns_no_email_when_every_candidate_is_invalid():
    lead = normalize_lead_row(
        {"title": "No Real Email", "emails": ["logo@2x.webp", "info@domain.com"]},
        country_hint="US",
        source="google_maps",
    )

    assert lead is not None
    assert lead["email"] is None
