"""Unit tests for the offline WhatsApp reachability hint."""

from api.services.whatsapp.reachability import (
    WHATSAPP_REACHABILITY_LIKELY,
    WHATSAPP_REACHABILITY_UNLIKELY,
    WHATSAPP_REACHABILITY_UNVERIFIED,
    whatsapp_reachability,
)


def test_nanp_toll_free_numbers_are_unlikely():
    for number in ["+18005550100", "+18885550100", "+18335550100"]:
        assert whatsapp_reachability(number) == WHATSAPP_REACHABILITY_UNLIKELY


def test_other_nanp_numbers_are_unverified_because_they_may_be_landlines():
    assert whatsapp_reachability("+15125550100") == WHATSAPP_REACHABILITY_UNVERIFIED
    assert whatsapp_reachability("+14165550100") == WHATSAPP_REACHABILITY_UNVERIFIED


def test_mobile_first_markets_are_likely():
    assert whatsapp_reachability("+923001234567") == WHATSAPP_REACHABILITY_LIKELY
    assert whatsapp_reachability("+442079460018") == WHATSAPP_REACHABILITY_LIKELY


def test_unreadable_numbers_are_unverified():
    assert whatsapp_reachability(None) == WHATSAPP_REACHABILITY_UNVERIFIED
    assert whatsapp_reachability("") == WHATSAPP_REACHABILITY_UNVERIFIED
