"""Coarse, offline hint about whether a number reaches consumer WhatsApp.

WhatsApp registers mobile SIMs, and nothing free tells us the line type or
registration of a number: NANP metadata marks every US/Canada number as
"fixed line or mobile", and wa.me answers registered and unregistered numbers
with the same page. This module therefore only encodes cheap heuristics:

- NANP toll-free numbers cannot be consumer WhatsApp accounts.
- Other NANP numbers may be a mobile or a business landline. Google Maps
  listings are often landlines, so treat them as unverified, not available.
- Outside NANP the number is still unverified, but mobile-first markets make a
  registered number much more likely.

A label here is not proof. Only a paid HLR/carrier lookup can tell whether a
number actually has WhatsApp.
"""

WHATSAPP_REACHABILITY_LIKELY = "likely"
WHATSAPP_REACHABILITY_UNVERIFIED = "unverified"
WHATSAPP_REACHABILITY_UNLIKELY = "unlikely"

_NANP_COUNTRY_CODE = "1"
_NANP_TOLL_FREE_AREA_CODES = frozenset(
    {"800", "833", "844", "855", "866", "877", "888"}
)


def whatsapp_reachability(phone_e164: str | None) -> str:
    """Classify one resolved E.164 number; unverified when unreadable."""
    digits = "".join(ch for ch in (phone_e164 or "") if ch.isdigit())
    if not digits:
        return WHATSAPP_REACHABILITY_UNVERIFIED
    if not digits.startswith(_NANP_COUNTRY_CODE):
        return WHATSAPP_REACHABILITY_LIKELY
    area_code = digits[len(_NANP_COUNTRY_CODE) :][:3]
    if area_code in _NANP_TOLL_FREE_AREA_CODES:
        return WHATSAPP_REACHABILITY_UNLIKELY
    return WHATSAPP_REACHABILITY_UNVERIFIED
