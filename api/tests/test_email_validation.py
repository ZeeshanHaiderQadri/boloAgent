import pytest
from pydantic import ValidationError

from api.schemas.leads import LeadUpdateRequest
from api.utils.email_validation import (
    classify_email,
    is_valid_email,
    normalize_email,
    validate_email_address,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("sales@apex-software.com", "sales@apex-software.com"),
        ("  Sales@Apex-Software.COM  ", "sales@apex-software.com"),
        ("<hello+leads@apex-software.com>", "hello+leads@apex-software.com"),
        ("mailto:hello@apex-software.com", "hello@apex-software.com"),
        ("first.last@sub.apex-software.com", "first.last@sub.apex-software.com"),
        ("o'brien@apex-software.com", "o'brien@apex-software.com"),
        ("user@example.xn--p1ai", "user@example.xn--p1ai"),
        ("User@Sub.Example.XN--P1AI", "user@sub.example.xn--p1ai"),
    ],
)
def test_valid_addresses_are_normalized(raw, expected):
    assert validate_email_address(raw) == expected
    assert normalize_email(raw) == expected
    assert is_valid_email(raw)


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("logo@2x.webp", "file_extension"),
        ("photo@cdn.png", "file_extension"),
        ("info@domain.com", "placeholder_domain"),
        ("info@example.com", "placeholder_domain"),
        ("info@mail.example.com", "placeholder_domain"),
        ("info@test.com", "placeholder_domain"),
        ("user@localhost", "reserved_domain"),
        ("user@printer.local", "reserved_domain"),
        ("not-an-email", "invalid_syntax"),
        ("two@at@apex-software.com", "invalid_syntax"),
        ("user@apex-software", "invalid_domain"),
        ("user@-apex-software.com", "invalid_domain"),
        (".user@apex-software.com", "invalid_local_part"),
        ("user..name@apex-software.com", "invalid_local_part"),
        ("user name@apex-software.com", "invalid_local_part"),
        ("", "empty"),
        (None, "empty"),
        ("a" * 65 + "@apex-software.com", "invalid_local_part"),
    ],
)
def test_invalid_addresses_report_a_reason(raw, reason):
    normalized, actual_reason = classify_email(raw)

    assert normalized is None
    assert actual_reason == reason
    assert normalize_email(raw) is None
    assert not is_valid_email(raw)


def test_scraped_punycode_tld_is_not_truncated():
    from api.utils.email_validation import EMAIL_PATTERN

    assert EMAIL_PATTERN.findall("Write to sales@example.xn--p1ai today") == [
        "sales@example.xn--p1ai"
    ]


def test_too_long_address_is_rejected_before_parsing():
    _, reason = classify_email("a" * 321 + "@apex-software.com")

    assert reason == "too_long"


def test_manual_lead_edit_accepts_a_valid_address():
    request = LeadUpdateRequest(email="  Sales@Apex-Software.COM ")

    assert request.email == "sales@apex-software.com"


def test_manual_lead_edit_rejects_image_and_placeholder_addresses():
    for raw in ("logo@2x.webp", "info@domain.com"):
        with pytest.raises(ValidationError):
            LeadUpdateRequest(email=raw)


def test_manual_lead_edit_allows_clearing_the_address():
    assert LeadUpdateRequest(email="   ").email == ""
