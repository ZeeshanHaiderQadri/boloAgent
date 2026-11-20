"""Unit tests for reading lead phone numbers under a known country."""

from api.services.whatsapp.countries import (
    dial_code,
    dial_code_for_e164,
    international_digits,
    lead_country,
    normalize_country_code,
    resolve_lead_phone,
    resolve_local,
)


def test_sender_country_validation_accepts_iso_codes_only():
    assert normalize_country_code(" us ") == "US"
    assert normalize_country_code("gb") == "GB"
    for value in ["UK", "EU", "ZZ", "XK", "USA", "", None]:
        assert normalize_country_code(value) is None


def test_lead_country_understands_names_and_aliases():
    assert lead_country("GB") == "GB"
    assert lead_country("United Kingdom") == "GB"
    assert lead_country("united states of america") == "US"
    assert lead_country("Japan") == "JP"
    assert lead_country("Türkiye") == "TR"
    assert lead_country("Atlantis") is None
    assert lead_country(None) is None


def test_local_numbers_take_the_known_country():
    assert resolve_local("020 7946 0018", "GB") == "+442079460018"
    assert resolve_local("(512) 555-0100", "US") == "+15125550100"
    assert resolve_local("1 512 555 0100", "US") == "+15125550100"
    assert resolve_local("04 227 4666", "AE") == "+97142274666"
    assert resolve_local("08-123 456 78", "SE") == "+46812345678"
    assert resolve_local("447554626761", "GB") == "+447554626761"


def test_local_numbers_that_do_not_fit_the_country_are_refused():
    # A Tokyo landline starts with a trunk zero, which NANP area codes cannot.
    assert resolve_local("03-1234-5678", "US") is None
    assert resolve_local("555-0100", "US") is None
    assert resolve_local("03-1234-5678", "JP") == "+81312345678"
    assert resolve_local("020 7946 0018", "ZZ") is None


def test_international_prefixes_are_recognised():
    assert international_digits("+44 20 7946 0018") == "442079460018"
    assert international_digits("0044 20 7946 0018") == "442079460018"
    assert international_digits("(512) 555-0100") is None
    assert international_digits("+1") is None


def test_dial_codes_come_from_the_number_itself():
    assert dial_code("GB") == "44"
    assert dial_code("uk") is None
    assert dial_code_for_e164("+15125550100") == "1"
    assert dial_code_for_e164("+442079460018") == "44"
    assert dial_code_for_e164("5125550100") is None


def test_plus_number_is_used_unchanged_even_when_stored_disagrees():
    resolved = resolve_lead_phone("+44 20 7946 0018", "+15125550199", "GB", "US")
    assert resolved.phone_e164 == "+442079460018"
    assert resolved.country == "GB"
    assert resolved.failure is None


def test_lead_country_wins_over_the_default_sender():
    resolved = resolve_lead_phone("020 7946 0018", None, "GB", "US")
    assert resolved.phone_e164 == "+442079460018"
    assert resolved.country == "GB"


def test_japanese_local_number_is_never_written_as_a_us_number():
    resolved = resolve_lead_phone("03-1234-5678", None, "Japan", "US")
    assert resolved.phone_e164 == "+81312345678"

    unknown = resolve_lead_phone("03-1234-5678", None, None, "US")
    assert unknown.phone_e164 is None
    assert unknown.failure == "unrecognized"


def test_no_country_and_no_sender_builds_no_link():
    resolved = resolve_lead_phone("5125550177", "+15125550177", None, None)
    assert resolved.phone_e164 is None
    assert resolved.failure == "country_unknown"
    assert resolve_lead_phone(None, None, "US").failure == "missing"


def test_stored_e164_is_ignored_when_it_contradicts_the_lead_country():
    resolved = resolve_lead_phone("020 7946 0018", "+1202079460018", "GB", None)
    assert resolved.phone_e164 == "+442079460018"

    # No raw number left to repair: the contradicted value is not used either.
    contradicted = resolve_lead_phone(None, "+1202079460018", "GB", None)
    assert contradicted.phone_e164 is None
    assert contradicted.failure == "unrecognized"


def test_stored_e164_is_used_when_there_is_no_raw_number():
    resolved = resolve_lead_phone(None, "+15125550177", "US", None)
    assert resolved.phone_e164 == "+15125550177"
    assert resolved.country == "US"
