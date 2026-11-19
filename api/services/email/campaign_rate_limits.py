"""Abuse limits for campaign audience previews and launch attempts.

Preview walks a potentially large filter, so it is rate limited per user and
organization. Launch attempts are limited too; both fail closed: if Redis is
unavailable the check raises ``RateLimiterUnavailable`` and the route answers
503 instead of opening the door. Nothing is sent from any of these endpoints.
"""

from api.services.call_concurrency.rate_limiter import rate_limiter

PREVIEW_LIMIT_PER_HOUR = 120
LAUNCH_ATTEMPT_LIMIT_PER_HOUR = 20

_WINDOW_SECONDS = 3600


async def allow_campaign_preview(*, user_id: int, organization_id: int) -> bool:
    if not await rate_limiter.acquire_token_in_window(
        f"email_campaign:preview:org:{organization_id}",
        limit=PREVIEW_LIMIT_PER_HOUR,
        window_seconds=_WINDOW_SECONDS,
    ):
        return False
    return await rate_limiter.acquire_token_in_window(
        f"email_campaign:preview:user:{user_id}",
        limit=PREVIEW_LIMIT_PER_HOUR,
        window_seconds=_WINDOW_SECONDS,
    )


async def allow_campaign_launch_attempt(*, user_id: int, organization_id: int) -> bool:
    if not await rate_limiter.acquire_token_in_window(
        f"email_campaign:launch:org:{organization_id}",
        limit=LAUNCH_ATTEMPT_LIMIT_PER_HOUR,
        window_seconds=_WINDOW_SECONDS,
    ):
        return False
    return await rate_limiter.acquire_token_in_window(
        f"email_campaign:launch:user:{user_id}",
        limit=LAUNCH_ATTEMPT_LIMIT_PER_HOUR,
        window_seconds=_WINDOW_SECONDS,
    )
