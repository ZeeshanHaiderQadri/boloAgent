"""Split a large lead search into one Google Maps query per place.

A single query ends after roughly one page (~20 businesses) no matter how many
pages the scraper scrolls. The scraper's web API does run the keywords of one
job concurrently, so a large target is planned as a query for the original
location plus one query per city/town (or neighbourhood, for city-sized
locations) inside it. Places already used by an earlier run of the same
search are skipped, which is what makes "Extend" collect new businesses
instead of repeating the first query.

The plan is stored on the search's ``params`` (``fanout_places``) so an
extended search continues from where it stopped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from loguru import logger

from api.constants import (
    LEADS_FANOUT_MAX_QUERIES,
    LEADS_FANOUT_RESULTS_PER_QUERY,
)
from api.db.models import LeadSearchModel
from api.services.leads import geo
from api.services.leads.providers import search_query, split_query


@dataclass(frozen=True)
class FanoutPlan:
    keywords: list[str]
    used_places: list[str]


def places_needed(target_results: int) -> int:
    """How many place queries a target of this size plans for."""
    if target_results <= LEADS_FANOUT_RESULTS_PER_QUERY:
        return 0
    queries = math.ceil(target_results / max(1, LEADS_FANOUT_RESULTS_PER_QUERY))
    return min(max(0, queries - 1), max(0, LEADS_FANOUT_MAX_QUERIES - 1))


def place_query(category: str, place: str, location: str) -> str:
    if not category:
        return place
    known = location.lower()
    if not place or place.lower() in known:
        return f"{category} in {location}"
    return f"{category} in {place}, {location}"


def _used_places(search: LeadSearchModel) -> list[str]:
    places = (search.params or {}).get("fanout_places") or []
    return [place for place in places if isinstance(place, str) and place.strip()]


async def plan(search: LeadSearchModel) -> FanoutPlan | None:
    """Build the keyword list for the next scraper job of ``search``.

    Returns ``None`` to keep the single-query behaviour (small target, demo
    provider, or a location that cannot be geocoded).
    """
    if search.provider != "google_maps":
        return None
    category, location = split_query(search)
    needed = places_needed(int(search.target_results or 0))
    if needed <= 0 or not location:
        return None

    used = _used_places(search)
    remaining = needed - len(used)
    keywords: list[str] = [] if used else [search_query(search)]
    if remaining > 0:
        # Ask for a little headroom: some names are rejected as duplicates of
        # the location itself and must be replaced.
        candidates = await geo.area_places(location, limit=needed + 5)
        known = {place.lower() for place in used}
        location_key = location.lower()
        new_places = [
            place
            for place in candidates
            if place.lower() not in known and place.lower() not in location_key
        ][:remaining]
        keywords.extend(place_query(category, place, location) for place in new_places)
        used = [*used, *new_places]
        logger.info(
            "Lead search {}: {} queries planned over {} places for target {}",
            search.id,
            len(keywords),
            len(used),
            search.target_results,
        )

    if not keywords:
        # Every planned place was already searched; repeat the original query
        # so extending an exhausted search still returns a valid (if empty) job.
        keywords = [search_query(search)]
    return FanoutPlan(keywords=keywords, used_places=used)
