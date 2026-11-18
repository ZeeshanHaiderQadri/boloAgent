"""Sending limits per sender, with conservative provider defaults.

Senders may override both values. Until an operator confirms the real quota in
the provider dashboard, the defaults keep a freshly connected account well
below what it can actually send — an over-limit sender gets throttled or
blocked by the provider, and for a brand-new mailbox that is hard to undo.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.enums import EmailProvider


@dataclass(frozen=True)
class SenderLimits:
    daily_cap: int
    rate_per_second: float


# Deliberately conservative:
# - Hostinger's shared SMTP is the tightest common case, so universal SMTP
#   starts at 100/day and 1 message/second.
# - A new SES account sits in the sandbox (200/day), so SES also starts there.
# - Mailgun and Mandrill starter plans are raised explicitly once verified.
PROVIDER_DEFAULTS: dict[EmailProvider, SenderLimits] = {
    EmailProvider.SMTP: SenderLimits(daily_cap=100, rate_per_second=1.0),
    EmailProvider.SES: SenderLimits(daily_cap=200, rate_per_second=1.0),
    EmailProvider.MAILGUN: SenderLimits(daily_cap=100, rate_per_second=1.0),
    EmailProvider.MANDRILL: SenderLimits(daily_cap=100, rate_per_second=1.0),
}


def provider_limits(provider: EmailProvider | str) -> SenderLimits:
    """Return the default limits for a provider value as stored in the database."""
    return PROVIDER_DEFAULTS[EmailProvider(provider)]


def effective_limits(sender: object) -> SenderLimits:
    """Resolve a sender row's limits, falling back to the provider defaults.

    ``sender`` is an ``EmailSenderModel`` or any object with ``provider``,
    ``daily_cap`` and ``rate_per_second`` attributes.
    """
    defaults = provider_limits(getattr(sender, "provider"))
    daily_cap = getattr(sender, "daily_cap", None)
    rate_per_second = getattr(sender, "rate_per_second", None)
    return SenderLimits(
        daily_cap=defaults.daily_cap if daily_cap is None else int(daily_cap),
        rate_per_second=(
            defaults.rate_per_second
            if rate_per_second is None
            else float(rate_per_second)
        ),
    )
