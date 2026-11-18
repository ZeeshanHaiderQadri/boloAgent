"""Rules that keep an email-consent decision attached to a specific address.

A consent decision is evidence about the address the person actually agreed to.
If the address later changes — through a manual CRM edit or a scraper refresh —
the old decision says nothing about the new mailbox, so it must be reset and the
reason recorded on the lead timeline.
"""

from typing import Any


def normalize_email_address(value: str | None) -> str:
    return (value or "").strip().lower()


def email_address_changed(previous: str | None, new: str | None) -> bool:
    """True when the stored address and the incoming address are different.

    Case and surrounding whitespace are ignored: ``Bob@Example.com `` is the
    same mailbox as ``bob@example.com`` and must not reset consent.
    """
    return normalize_email_address(previous) != normalize_email_address(new)


def consent_reset_values() -> dict[str, Any]:
    """Columns that clear a consent decision back to unknown."""
    return {
        "email_consent": "unknown",
        "email_consent_source": None,
        "email_consent_at": None,
    }


def has_address_bound_decision(
    previous_email: str | None, previous_consent: str
) -> bool:
    """True only when a decision existed and it was attached to an address.

    That is the case where the timeline can honestly say consent was reset.
    Consent recorded before any address was known is still cleared when an
    address arrives, but the entry describes a plain address change.
    """
    return bool(previous_email) and previous_consent != "unknown"


def email_change_activity(
    *,
    organization_id: int,
    lead_id: int,
    user_id: int | None,
    previous_email: str | None,
    new_email: str | None,
    previous_consent: str,
    source: str,
) -> dict[str, Any]:
    """Audit row explaining why consent was reset.

    ``previous_consent`` is recorded even when it was already ``unknown`` so the
    timeline always shows what the reset replaced.
    """
    return {
        "organization_id": organization_id,
        "lead_id": lead_id,
        "user_id": user_id,
        "activity_type": "email_consent",
        "title": "Email consent reset: address changed",
        "body": (
            f"Email address changed from {previous_email or 'none'} to "
            f"{new_email or 'none'}. Consent recorded for the old address no "
            "longer applies, so it was reset to unknown."
        ),
        "extra_metadata": {
            "from": previous_consent,
            "to": "unknown",
            "source": source,
            "reason": "email_changed",
            "previous_email": previous_email,
            "new_email": new_email,
        },
    }


def plain_email_change_activity(
    *,
    organization_id: int,
    lead_id: int,
    user_id: int | None,
    previous_email: str | None,
    new_email: str | None,
    source: str,
) -> dict[str, Any]:
    """Timeline entry for an address change with no consent to reset."""
    return {
        "organization_id": organization_id,
        "lead_id": lead_id,
        "user_id": user_id,
        "activity_type": "lead_edit",
        "title": "Email address changed",
        "body": (
            f"Email address changed from {previous_email or 'none'} to "
            f"{new_email or 'none'}."
        ),
        "extra_metadata": {
            "previous_email": previous_email,
            "new_email": new_email,
            "source": source,
        },
    }
