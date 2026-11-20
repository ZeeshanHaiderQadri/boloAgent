"""Tests for planning a search as one query per place."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.leads import fanout


def _search(**overrides):
    values = {
        "id": 11,
        "name": "Software Companies in California",
        "category": "Software Companies",
        "location": "California",
        "provider": "google_maps",
        "target_results": 60,
        "params": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_places_needed_scales_with_the_target():
    assert fanout.places_needed(10) == 0
    assert fanout.places_needed(15) == 0
    assert fanout.places_needed(16) == 1
    assert fanout.places_needed(60) == 3
    # Bounded by the query cap so a huge target still fits one scraper job.
    assert fanout.places_needed(1_000_000) == fanout.LEADS_FANOUT_MAX_QUERIES - 1


@pytest.mark.asyncio
async def test_small_target_keeps_a_single_query(monkeypatch):
    lookup = AsyncMock(return_value=["Fresno"])
    monkeypatch.setattr(fanout.geo, "area_places", lookup)

    assert await fanout.plan(_search(target_results=10)) is None
    lookup.assert_not_called()


@pytest.mark.asyncio
async def test_demo_provider_is_never_split(monkeypatch):
    lookup = AsyncMock(return_value=["Fresno"])
    monkeypatch.setattr(fanout.geo, "area_places", lookup)

    assert await fanout.plan(_search(provider="demo")) is None
    lookup.assert_not_called()


@pytest.mark.asyncio
async def test_plan_adds_one_query_per_place(monkeypatch):
    lookup = AsyncMock(return_value=["Fresno", "Bakersfield", "Riverside"])
    monkeypatch.setattr(fanout.geo, "area_places", lookup)

    plan = await fanout.plan(_search(target_results=60))

    assert plan is not None
    assert plan.keywords == [
        "Software Companies in California",
        "Software Companies in Fresno, California",
        "Software Companies in Bakersfield, California",
        "Software Companies in Riverside, California",
    ]
    assert plan.used_places == ["Fresno", "Bakersfield", "Riverside"]


@pytest.mark.asyncio
async def test_extend_skips_places_that_were_already_searched(monkeypatch):
    lookup = AsyncMock(return_value=["Fresno", "Bakersfield", "Riverside"])
    monkeypatch.setattr(fanout.geo, "area_places", lookup)

    plan = await fanout.plan(
        _search(
            target_results=60,
            params={"fanout_places": ["Fresno", "Bakersfield"]},
        )
    )

    assert plan is not None
    # The original query and the first two places already ran; only Riverside
    # is new, and the base query is not repeated.
    assert plan.keywords == ["Software Companies in Riverside, California"]
    assert plan.used_places == ["Fresno", "Bakersfield", "Riverside"]


@pytest.mark.asyncio
async def test_exhausted_coverage_falls_back_to_the_base_query(monkeypatch):
    lookup = AsyncMock(return_value=["Fresno"])
    monkeypatch.setattr(fanout.geo, "area_places", lookup)

    plan = await fanout.plan(
        _search(target_results=16, params={"fanout_places": ["Fresno"]})
    )

    assert plan is not None
    assert plan.keywords == ["Software Companies in California"]


@pytest.mark.asyncio
async def test_missing_geocoding_keeps_the_single_query(monkeypatch):
    lookup = AsyncMock(return_value=[])
    monkeypatch.setattr(fanout.geo, "area_places", lookup)

    plan = await fanout.plan(_search(target_results=200))

    assert plan is not None
    assert plan.keywords == ["Software Companies in California"]


def test_place_query_drops_a_repeated_location():
    assert fanout.place_query("Gyms", "London", "London, UK") == "Gyms in London, UK"
    assert fanout.place_query("Gyms", "Camden", "London, UK") == (
        "Gyms in Camden, London, UK"
    )
    assert fanout.place_query("Gyms", "London", "London") == "Gyms in London"
