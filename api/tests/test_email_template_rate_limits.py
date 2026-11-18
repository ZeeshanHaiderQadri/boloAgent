"""Hourly abuse limits for template previews and test sends."""

from unittest.mock import AsyncMock

import pytest

from api.services.call_concurrency.rate_limiter import (
    RateLimiter,
    RateLimiterUnavailable,
)
from api.services.email import template_rate_limits as limits


def _patch(monkeypatch, *results):
    mock = AsyncMock(side_effect=list(results))
    monkeypatch.setattr(limits.rate_limiter, "acquire_token_in_window", mock)
    return mock


@pytest.mark.asyncio
async def test_test_send_checks_org_then_user_with_ten_per_hour(monkeypatch):
    mock = _patch(monkeypatch, True, True)

    assert await limits.allow_template_test_send(user_id=7, organization_id=3)

    calls = mock.await_args_list
    assert [call.args for call in calls] == [
        ("email_template:test_send:org:3",),
        ("email_template:test_send:user:7",),
    ]
    assert [call.kwargs for call in calls] == [
        {"limit": 10, "window_seconds": 3600},
        {"limit": 10, "window_seconds": 3600},
    ]


@pytest.mark.asyncio
async def test_org_limit_stops_before_charging_the_user(monkeypatch):
    mock = _patch(monkeypatch, False)

    assert not await limits.allow_template_test_send(user_id=7, organization_id=3)
    assert mock.await_count == 1


@pytest.mark.asyncio
async def test_user_limit_denies_even_when_the_org_has_room(monkeypatch):
    mock = _patch(monkeypatch, True, False)

    assert not await limits.allow_template_test_send(user_id=7, organization_id=3)
    assert mock.await_count == 2


@pytest.mark.asyncio
async def test_preview_allows_sixty_per_hour_per_user(monkeypatch):
    mock = _patch(monkeypatch, True)

    assert await limits.allow_template_preview(user_id=7)

    call = mock.await_args
    assert call.args == ("email_template:preview:user:7",)
    assert call.kwargs == {"limit": 60, "window_seconds": 3600}


class _CapturingRedis:
    def __init__(self, result):
        self.result = result
        self.args = None

    async def eval(self, script, numkeys, *args):
        self.args = args
        return self.result


class _BrokenRedis:
    async def eval(self, *args, **kwargs):
        raise ConnectionError("redis down")


@pytest.mark.asyncio
async def test_window_limiter_passes_limit_window_and_ttl():
    limiter = RateLimiter()
    limiter.redis_client = _CapturingRedis(1)

    allowed = await limiter.acquire_token_in_window(
        "email_template:preview:user:7", limit=60, window_seconds=3600
    )

    assert allowed is True
    key, _now, _window_start, limit, ttl, _member = limiter.redis_client.args
    assert key == "rate_limit:email_template:preview:user:7"
    assert limit == 60
    assert ttl == 3660


@pytest.mark.asyncio
async def test_window_limiter_reports_unavailable_on_redis_errors():
    limiter = RateLimiter()
    limiter.redis_client = _BrokenRedis()

    with pytest.raises(RateLimiterUnavailable):
        await limiter.acquire_token_in_window(
            "email_template:test_send:user:7", limit=10, window_seconds=3600
        )
