"""Abuse limits for email-template preview and test sends.

These buckets are isolated from voice-call admission. Limits are hourly and
fail closed: if Redis is unavailable the check raises
:class:`~api.services.call_concurrency.rate_limiter.RateLimiterUnavailable`,
the route answers 503 instead of 429, and nothing is sent. A test send reaches
a real inbox, so an unavailable limiter must not become an open door.

There are no campaign-level caps here. Campaign sending has its own admission
and sender limits.
"""

from api.services.call_concurrency.rate_limiter import rate_limiter

TEST_SEND_LIMIT_PER_HOUR = 10
PREVIEW_LIMIT_PER_HOUR = 60

_WINDOW_SECONDS = 3600


async def allow_template_test_send(*, user_id: int, organization_id: int) -> bool:
    """Allow at most ten test sends per user and per organization per hour.

    The organization bucket is checked first so one noisy member cannot spend
    every other member's allowance. The user bucket is only charged when the
    organization still has room; when the user is over the limit the
    organization token for that attempt has already been spent, which is the
    conservative direction.
    """
    if not await rate_limiter.acquire_token_in_window(
        f"email_template:test_send:org:{organization_id}",
        limit=TEST_SEND_LIMIT_PER_HOUR,
        window_seconds=_WINDOW_SECONDS,
    ):
        return False
    return await rate_limiter.acquire_token_in_window(
        f"email_template:test_send:user:{user_id}",
        limit=TEST_SEND_LIMIT_PER_HOUR,
        window_seconds=_WINDOW_SECONDS,
    )


async def allow_template_preview(*, user_id: int) -> bool:
    """Allow sixty previews per user per hour."""
    return await rate_limiter.acquire_token_in_window(
        f"email_template:preview:user:{user_id}",
        limit=PREVIEW_LIMIT_PER_HOUR,
        window_seconds=_WINDOW_SECONDS,
    )
