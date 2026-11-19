"""Shared recipient bucket names and ordering for email campaigns.

Both the audience resolver (``api.services.email.audience``) and the
recipients reader (``api.db.email_audience_client``) classify leads into the
same buckets, so the names must live somewhere neither layer owns.
"""

from __future__ import annotations

# Same precedence the resolver applies, most-excluded first.
EXCLUSION_ORDER: tuple[str, ...] = (
    "no_email",
    "invalid_email",
    "suppressed",
    "opted_out",
    "unknown",
    "manually_excluded",
    "eligible",
)
