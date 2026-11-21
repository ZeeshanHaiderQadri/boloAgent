"""Populated places inside a location, from OpenStreetMap.

Google Maps ends a text query's result list after roughly one page (~20
businesses) and the scraper's web API has no grid mode, so a large target is
collected by sending one query per place inside the requested area. Nominatim
resolves the location's boundary, Overpass lists the cities/towns (or, for
city-sized areas, neighbourhoods) inside it.

The public Overpass mirrors are often at capacity ("all query workers are
busy"). When they are, the area's Wikidata id (read from the geocoder's
extratags) is used to list its settlements from the Wikidata Query Service,
which stores the same facts. A coarse reverse-geocoded grid is the last
resort for city-sized areas, where districts are small enough to hit.

Every call here is best effort: timeouts and missing OSM data return an empty
list and the caller keeps the original single query.
"""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass

import httpx
from loguru import logger

from api.constants import (
    LEADS_GEOCODER_URL,
    LEADS_GEOCODER_USER_AGENT,
    LEADS_OVERPASS_URLS,
    LEADS_WIKIDATA_SPARQL_URL,
)

# Above this the location is a state/country-sized area where cities are the
# useful unit; below it the location is a city and its districts dig deeper.
_CITY_AREA_KM2 = 20_000.0
_CITY_KINDS = ("city", "town", "village")
_DISTRICT_KINDS = ("suburb", "quarter", "neighbourhood", "borough")
_KIND_PRIORITY = {kind: index for index, kind in enumerate(_DISTRICT_KINDS)}

# Reverse-geocoding fallback. Nominatim's usage policy allows at most one
# request per second, so the grid is capped and paced.
_REVERSE_GRID_MAX_POINTS = 18
_REVERSE_GRID_DELAY_SECONDS = 1.05
_REVERSE_GRID_ZOOM_SETTLEMENT = 12
_REVERSE_GRID_ZOOM_DISTRICT = 15
_NOT_A_PLACE_SUFFIXES = (
    " county",
    " parish",
    " region",
    " province",
    " prefecture",
    " canton",
    " department",
    " municipality",
)

# Settlement classes: city, town, village, hamlet, municipality,
# census-designated place and California's specific "city" class.
_SETTLEMENT_CLASSES = (
    "Q515",
    "Q3957",
    "Q532",
    "Q5084",
    "Q15284",
    "Q4989906",
    "Q1093829",
)
_QID_RE = re.compile(r"^Q\d+$")


@dataclass(frozen=True)
class Area:
    osm_type: str | None
    osm_id: int | None
    # south, north, west, east
    bbox: tuple[float, float, float, float]
    lat: float
    lon: float
    wikidata: str | None = None


def _area_from_nominatim(rows: list[dict]) -> Area | None:
    if not rows:
        return None
    row = rows[0]
    try:
        south, north, west, east = (float(value) for value in row["boundingbox"])
        lat = float(row["lat"])
        lon = float(row["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    osm_id = row.get("osm_id")
    extratags = row.get("extratags") or {}
    wikidata = str(extratags.get("wikidata") or "")
    return Area(
        osm_type=row.get("osm_type"),
        osm_id=int(osm_id) if osm_id is not None else None,
        bbox=(south, north, west, east),
        lat=lat,
        lon=lon,
        wikidata=wikidata if _QID_RE.match(wikidata) else None,
    )


def _area_km2(area: Area) -> float:
    south, north, west, east = area.bbox
    height_km = (north - south) * 111.32
    width_km = (east - west) * 111.32 * max(0.05, math.cos(math.radians(abs(area.lat))))
    return abs(height_km * width_km)


def _overpass_query(area: Area, kinds: tuple[str, ...]) -> str:
    scope = ""
    # Relations and ways expose a stable area id to Overpass; a plain bbox is
    # the fallback (and is rectangular, so it can include neighbouring places).
    if area.osm_type == "relation" and area.osm_id:
        scope = f"area(id:{3_600_000_000 + area.osm_id})->.a;"
        where = "(area.a)"
    elif area.osm_type == "way" and area.osm_id:
        scope = f"area(id:{2_400_000_000 + area.osm_id})->.a;"
        where = "(area.a)"
    else:
        south, north, west, east = area.bbox
        where = f"({south},{west},{north},{east})"
    pattern = "^(" + "|".join(kinds) + ")$"
    return (
        f'[out:json][timeout:25];{scope}node["place"~"{pattern}"]{where};out tags 800;'
    )


def _population(tags: dict) -> int:
    try:
        return max(0, int(str(tags.get("population", "0")).replace(",", "")))
    except ValueError:
        return 0


def place_names(elements: list[dict], *, limit: int) -> list[str]:
    """Pick the most useful distinct place names from Overpass elements."""
    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()
    for element in elements:
        tags = element.get("tags") or {}
        name = " ".join(str(tags.get("name") or "").split())
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        kind = str(tags.get("place") or "")
        # Population first: businesses cluster where people are. Districts do
        # not carry a population, so their kind ordering decides.
        score = _population(tags) * 100 + (10 - _KIND_PRIORITY.get(kind, 10))
        ranked.append((score, name))
    ranked.sort(key=lambda item: (-item[0], item[1].lower()))
    return [name for _, name in ranked[:limit]]


def _client(read_seconds: float = 40.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(read_seconds, connect=10.0),
        headers={"User-Agent": LEADS_GEOCODER_USER_AGENT},
    )


async def _resolve_area(location: str) -> Area | None:
    async with _client() as client:
        response = await client.get(
            f"{LEADS_GEOCODER_URL}/search",
            params={
                "q": location,
                "format": "jsonv2",
                "limit": 1,
                "accept-language": "en",
                "extratags": 1,
            },
        )
    if response.status_code >= 400:
        logger.warning("Geocoder rejected {} ({})", location, response.status_code)
        return None
    return _area_from_nominatim(response.json())


async def _places_in_area(
    area: Area, kinds: tuple[str, ...], limit: int
) -> tuple[list[str], bool]:
    """Overpass place names, plus whether any mirror answered at all.

    A mirror that was reached but refused the query (capacity/error) still
    counts as reachable; only transport failures mark Overpass as down.
    """
    query = _overpass_query(area, kinds)
    reached = False
    for url in LEADS_OVERPASS_URLS:
        try:
            async with _client(read_seconds=15.0) as client:
                response = await client.post(url, data={"data": query})
        except httpx.HTTPError as exc:
            # Public Overpass mirrors time out individually; try the next one.
            logger.warning("Overpass mirror {} failed ({})", url, type(exc).__name__)
            continue
        reached = True
        if response.status_code >= 400:
            logger.warning("Overpass mirror {} returned {}", url, response.status_code)
            continue
        elements = response.json().get("elements") or []
        return place_names(elements, limit=limit), True
    return [], reached


def _wikidata_query(qid: str, *, limit: int) -> str:
    classes = " ".join(f"wd:{code}" for code in _SETTLEMENT_CLASSES)
    # Two hops: a state's cities sit inside its counties, a country's towns
    # inside its districts, and so on.
    return (
        "SELECT DISTINCT ?item ?itemLabel ?pop WHERE {"
        " ?item wdt:P131 ?parent ."
        f" ?parent wdt:P131 wd:{qid} ."
        " ?item wdt:P31 ?type ."
        f" VALUES ?type {{ {classes} }}"
        " ?item wdt:P1082 ?pop ."
        " FILTER(?pop > 2000)"
        ' SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }'
        " } ORDER BY DESC(?pop)"
        f" LIMIT {limit}"
    )


async def _wikidata_places(area: Area, *, limit: int) -> list[str]:
    """Settlements inside the area, from the Wikidata Query Service."""
    if not area.wikidata:
        return []
    async with _client(read_seconds=25.0) as client:
        response = await client.get(
            LEADS_WIKIDATA_SPARQL_URL,
            params={"query": _wikidata_query(area.wikidata, limit=limit)},
            # Without this header the service answers SPARQL XML.
            headers={"Accept": "application/sparql-results+json"},
        )
    if response.status_code >= 400:
        logger.warning(
            "Wikidata rejected the place query for {} ({})",
            area.wikidata,
            response.status_code,
        )
        return []
    bindings = (response.json().get("results") or {}).get("bindings") or []
    names: list[str] = []
    seen: set[str] = set()
    for binding in bindings:
        name = _clean_place_name(((binding.get("itemLabel") or {}).get("value")) or "")
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        names.append(name)
        if len(names) >= limit:
            break
    return names


def _grid_points(area: Area, *, points: int) -> list[tuple[float, float]]:
    """Cell centres of a roughly square grid over the area's bounding box."""
    south, north, west, east = area.bbox
    height = max(north - south, 1e-6)
    width = max(
        (east - west) * max(0.05, math.cos(math.radians(abs(area.lat)))),
        1e-6,
    )
    rows = max(1, round(math.sqrt(points * height / width)))
    cols = max(1, math.ceil(points / rows))
    lat_step = height / rows
    lon_step = (east - west) / cols
    return [
        (south + (row + 0.5) * lat_step, west + (col + 0.5) * lon_step)
        for row in range(rows)
        for col in range(cols)
    ]


def _clean_place_name(value: str) -> str:
    name = " ".join(str(value or "").split())
    # Reverse geocoding in rural cells resolves to counties and regions,
    # which are not useful as query areas.
    if not name or name.lower().endswith(_NOT_A_PLACE_SUFFIXES):
        return ""
    return name


def _settlement_from_address(address: dict) -> str:
    for key in (
        "neighbourhood",
        "suburb",
        "quarter",
        "hamlet",
        "village",
        "town",
        "city",
    ):
        name = " ".join(str(address.get(key) or "").split())
        if name:
            return name
    return ""


async def _reverse_grid_places(area: Area, *, zoom: int, limit: int) -> list[str]:
    """Sample the area with Nominatim reverse lookups, one settlement per point."""
    points = min(_REVERSE_GRID_MAX_POINTS, max(6, limit + 4))
    names: list[str] = []
    seen: set[str] = set()
    async with _client(read_seconds=15.0) as client:
        for index, (lat, lon) in enumerate(_grid_points(area, points=points)):
            if index:
                await asyncio.sleep(_REVERSE_GRID_DELAY_SECONDS)
            try:
                response = await client.get(
                    f"{LEADS_GEOCODER_URL}/reverse",
                    params={
                        "lat": f"{lat:.5f}",
                        "lon": f"{lon:.5f}",
                        "format": "jsonv2",
                        "zoom": zoom,
                        "addressdetails": 1,
                        "accept-language": "en",
                    },
                )
            except httpx.HTTPError as exc:
                logger.warning("Reverse geocoding failed ({})", type(exc).__name__)
                continue
            if response.status_code >= 400:
                continue
            payload = response.json() or {}
            # The top-level name can be a country for points in open water;
            # the address hierarchy names the actual settlement around it.
            # When an address is present but has no settlement at all, the
            # point is in unincorporated land and is not useful.
            address = payload.get("address") or {}
            if address:
                name = _settlement_from_address(address)
            else:
                name = _clean_place_name(payload.get("name") or "")
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            names.append(name)
            if len(names) >= limit:
                break
    return names


async def area_places(location: str, *, limit: int) -> list[str]:
    """Return up to ``limit`` populated place names inside ``location``."""
    location = " ".join((location or "").split())
    if not location or limit <= 0:
        return []
    try:
        area = await _resolve_area(location)
        if area is None:
            return []
        if _area_km2(area) > _CITY_AREA_KM2:
            places, _ = await _places_in_area(area, _CITY_KINDS, limit)
            if not places:
                places = await _wikidata_places(area, limit=limit)
            if not places:
                places = await _reverse_grid_places(
                    area, zoom=_REVERSE_GRID_ZOOM_SETTLEMENT, limit=limit
                )
            return places
        places, overpass_reachable = await _places_in_area(area, _DISTRICT_KINDS, limit)
        if not places and overpass_reachable:
            # Some cities have no mapped districts; fall back to nearby towns.
            places, _ = await _places_in_area(area, _CITY_KINDS, limit)
        if not places:
            places = await _reverse_grid_places(
                area, zoom=_REVERSE_GRID_ZOOM_DISTRICT, limit=limit
            )
        if not places:
            places = await _reverse_grid_places(
                area, zoom=_REVERSE_GRID_ZOOM_SETTLEMENT, limit=limit
            )
        return places
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning(
            "Place lookup for {} failed ({}): {}",
            location,
            type(exc).__name__,
            exc,
        )
        return []
