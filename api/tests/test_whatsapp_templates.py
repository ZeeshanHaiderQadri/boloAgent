"""Pure unit tests for WhatsApp template rendering and wa.me links."""

import pytest

from api.services.whatsapp.countries import resolve_lead_phone
from api.services.whatsapp.templates import (
    WhatsAppTemplateError,
    build_wa_me_url,
    extract_variables,
    lead_values,
    normalize_category,
    normalize_language,
    render_body,
    sample_values,
    validate_template_body,
)


def test_extract_variables_keeps_first_use_order_without_duplicates():
    body = "Hi {{business_name}}, you in {{city}}? {{business_name}} again."
    assert extract_variables(body) == ["business_name", "city"]


def test_validate_rejects_unknown_placeholders():
    with pytest.raises(WhatsAppTemplateError) as exc:
        validate_template_body("Hi {{first_name}}")
    assert "{{first_name}}" in str(exc.value)


def test_validate_rejects_empty_and_oversized_bodies():
    with pytest.raises(WhatsAppTemplateError):
        validate_template_body("   ")
    with pytest.raises(WhatsAppTemplateError):
        validate_template_body("x" * 5000)


def test_validate_returns_cleaned_body_and_variables():
    body, variables = validate_template_body("  Hi {{business_name}}  \n")
    assert body == "Hi {{business_name}}"
    assert variables == ["business_name"]


def test_render_fills_values_and_drops_double_spaces_from_missing_ones():
    rendered = render_body(
        "Hi {{business_name}}, you near  {{city}}?",
        {"business_name": "Apex Labs", "city": None},
    )
    assert rendered == "Hi Apex Labs, you near ?"


def test_render_keeps_newlines():
    rendered = render_body(
        "{{business_name}}\n{{category}}",
        lead_values(business_name="Apex", category="Salon", city=None),
    )
    assert rendered == "Apex\nSalon"


def test_build_wa_me_url_uses_digits_only_and_encodes_text():
    url = build_wa_me_url("+15125550100", "Hi Apex, come by?")
    assert url.startswith("https://wa.me/15125550100?text=")
    assert "Hi%20Apex%2C%20come%20by%3F" in url


@pytest.mark.parametrize(
    "raw_phone", ["(510) 835-4483", "+1 510-835-4483", "+15108354483"]
)
def test_resolved_us_lead_phone_builds_digits_only_wa_me_url(raw_phone):
    phone = resolve_lead_phone(
        raw_phone=raw_phone, stored_e164=None, lead_country_value="US"
    )
    assert phone.phone_e164 == "+15108354483"

    url = build_wa_me_url(phone.phone_e164, "")
    assert url == "https://wa.me/15108354483"
    assert url.removeprefix("https://wa.me/").isdigit()


def test_build_wa_me_url_never_guesses_a_country():
    for value in ["5125550100", "", "+1"]:
        with pytest.raises(WhatsAppTemplateError):
            build_wa_me_url(value, "Hi")


def test_language_and_category_validation():
    assert normalize_language("EN-us") == "EN-us"
    assert normalize_category(" Marketing ") == "marketing"
    with pytest.raises(WhatsAppTemplateError):
        normalize_language("english language")
    with pytest.raises(WhatsAppTemplateError):
        normalize_category("promo")


def test_sample_values_cover_every_supported_variable():
    assert set(sample_values()) == {"business_name", "category", "city"}
