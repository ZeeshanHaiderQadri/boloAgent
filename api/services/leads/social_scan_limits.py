"""Admission controls for outbound lead-website scans.

Every scan is a request to a third-party site, so both the manual refresh
endpoint and the background enricher need hard limits. The endpoint fails
closed when Redis cannot answer (503), exactly like the email template
endpoints; the background job simply defers.

The lease helpers are deliberately generic: they only count live tokens in a
sorted set, without touching call concurrency or fleet metrics.
"""

from __future__ import annotations

import time
import uuid

from loguru import logger

from api.services.call_concurrency.rate_limiter import (
    RateLimiterUnavailable,
    rate_limiter,
)

REFRESH_SCANS_PER_USER_HOUR = 20
REFRESH_SCANS_PER_ORG_HOUR = 60
ORG_CONCURRENT_SCANS = 2
ORG_SCAN_LEASE_SECONDS = 60

ENRICH_JOB_LEASE_KEY = "lead_socials:enrich_job"
ENRICH_JOB_LEASE_SECONDS = 180

_WINDOW_SECONDS = 3600

_ACQUIRE_LEASE = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local token = ARGV[3]
local ttl = tonumber(ARGV[4])

redis.call('ZREMRANGEBYSCORE', key, 0, now)
if redis.call('ZCARD', key) >= limit then
    return 0
end
redis.call('ZADD', key, now + ttl, token)
redis.call('EXPIRE', key, ttl + 60)
return 1
"""


async def try_acquire_lease(
    redis, key: str, *, limit: int, ttl_seconds: int
) -> str | None:
    """Take one of ``limit`` live leases, or return None when all are held.

    Raises :class:`RateLimiterUnavailable` when Redis cannot answer, so a
    request-triggered scan fails closed instead of silently skipping limits.
    """
    token = uuid.uuid4().hex
    now = time.time()
    try:
        result = await redis.eval(
            _ACQUIRE_LEASE, 1, key, now, limit, token, ttl_seconds
        )
    except Exception as exc:
        raise RateLimiterUnavailable(key) from exc
    return token if result else None


async def release_lease(redis, key: str, token: str | None) -> None:
    if not token:
        return
    try:
        await redis.zrem(key, token)
    except Exception as exc:
        # The lease expires on its own; failing to release only delays the
        # next scan by at most the TTL.
        logger.warning("Could not release social scan lease {}: {}", key, exc)


async def allow_refresh_socials(*, user_id: int, organization_id: int) -> bool:
    """Twenty manual refreshes per user and sixty per organization per hour.

    The organization bucket is checked first so one noisy member cannot spend
    everyone else's allowance.
    """
    if not await rate_limiter.acquire_token_in_window(
        f"lead_socials:refresh:org:{organization_id}",
        limit=REFRESH_SCANS_PER_ORG_HOUR,
        window_seconds=_WINDOW_SECONDS,
    ):
        return False
    return await rate_limiter.acquire_token_in_window(
        f"lead_socials:refresh:user:{user_id}",
        limit=REFRESH_SCANS_PER_USER_HOUR,
        window_seconds=_WINDOW_SECONDS,
    )


async def acquire_org_scan_slot(organization_id: int) -> str | None:
    redis = await rate_limiter.get_redis()
    return await try_acquire_lease(
        redis,
        f"lead_socials:org_scan:{organization_id}",
        limit=ORG_CONCURRENT_SCANS,
        ttl_seconds=ORG_SCAN_LEASE_SECONDS,
    )


async def release_org_scan_slot(organization_id: int, token: str | None) -> None:
    redis = await rate_limiter.get_redis()
    await release_lease(redis, f"lead_socials:org_scan:{organization_id}", token)


async def acquire_enrichment_job_lease(redis) -> str | None:
    """One enrichment job fleet-wide, so scans cannot consume the job pool."""
    return await try_acquire_lease(
        redis,
        ENRICH_JOB_LEASE_KEY,
        limit=1,
        ttl_seconds=ENRICH_JOB_LEASE_SECONDS,
    )


async def release_enrichment_job_lease(redis, token: str | None) -> None:
    await release_lease(redis, ENRICH_JOB_LEASE_KEY, token)
