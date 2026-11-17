"""Discovery providers: the real Google Maps scraper and a deterministic demo.

The provider contract deliberately passes the ``LeadSearchModel`` into every
call. It keeps the demo provider stateless (safe across worker restarts) and
lets the Google Maps provider read ``search.external_job_id`` without extra
state of its own.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx
from loguru import logger

from api.constants import (
    LEADS_SCRAPER_TIMEOUT_SECONDS,
    LEADS_SCRAPER_URL,
)
from api.db.models import LeadSearchModel

SCRAPER_REQUEST_TIMEOUT = httpx.Timeout(30.0, connect=10.0, read=180.0)


@dataclass
class ProviderJobStatus:
    status: str  # pending | running | completed | failed
    result_count: int = 0
    error: str | None = None


class LeadProviderError(RuntimeError):
    """Raised when the discovery provider cannot complete a job."""


class LeadProvider(Protocol):
    name: str

    async def submit(self, search: LeadSearchModel) -> str: ...

    async def poll(self, search: LeadSearchModel) -> ProviderJobStatus: ...

    async def fetch_results(
        self, search: LeadSearchModel, *, final: bool = False
    ) -> list[dict]: ...

    async def cancel(self, search: LeadSearchModel) -> None: ...


def search_query(search: LeadSearchModel) -> str:
    category = (search.category or "").strip()
    location = (search.location or "").strip()
    if category and location:
        return f"{category} in {location}"
    return category or location or search.name


def _http_client() -> httpx.AsyncClient:
    """Client for the internal scraper service.

    ``trust_env=False`` keeps corporate proxy settings (including malformed
    ``NO_PROXY`` entries such as ``[::1]``) from breaking calls to a service
    that is expected to be reachable directly.
    """
    return httpx.AsyncClient(timeout=SCRAPER_REQUEST_TIMEOUT, trust_env=False)


class GoogleMapsScraperProvider:
    """Adapter for gosom/google-maps-scraper's unauthenticated web API.

    Contract verified against upstream ``web/web.go``:
      POST /api/v1/jobs            -> {"id": "<uuid>"}
      GET  /api/v1/jobs/{id}       -> {"Status": "pending|working|ok|failed", ...}
      GET  /api/v1/jobs/{id}/download -> CSV
    """

    name = "google_maps"

    def __init__(self, base_url: str = LEADS_SCRAPER_URL):
        self.base_url = base_url.rstrip("/")

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    async def submit(self, search: LeadSearchModel) -> str:
        params = search.params or {}
        payload = {
            "name": search.name,
            "keywords": [search_query(search)],
            "lang": search.language or "en",
            "zoom": int(params.get("zoom", 15)),
            "lat": str(params.get("lat", "") or ""),
            "lon": str(params.get("lon", "") or ""),
            "fast_mode": bool(params.get("fast_mode", False)),
            "radius": int(params.get("radius_km", 10)),
            "depth": int(params.get("depth", 10)),
            "email": bool(params.get("email", False)),
            "extra_reviews": False,
            "max_time": min(
                int(params.get("max_time_seconds", LEADS_SCRAPER_TIMEOUT_SECONDS)),
                LEADS_SCRAPER_TIMEOUT_SECONDS,
            ),
            "proxies": [],
        }
        async with _http_client() as client:
            try:
                response = await client.post(self._url("/api/v1/jobs"), json=payload)
            except httpx.HTTPError as exc:
                raise LeadProviderError(
                    "Could not reach the Google Maps scraper service. "
                    "Start it with `docker compose --profile leads up -d` or set "
                    "LEADS_SCRAPER_URL."
                ) from exc
            if response.status_code >= 400:
                raise LeadProviderError(
                    f"Scraper rejected the search ({response.status_code}): "
                    f"{response.text[:500]}"
                )
            data = response.json()
        job_id = data.get("id") or data.get("ID")
        if not job_id:
            raise LeadProviderError("Scraper did not return a job id")
        return str(job_id)

    async def _get_job(self, job_id: str) -> dict:
        async with _http_client() as client:
            response = await client.get(self._url(f"/api/v1/jobs/{job_id}"))
        if response.status_code == 404:
            raise LeadProviderError("The scraper lost this job (container restarted?)")
        if response.status_code >= 400:
            raise LeadProviderError(
                f"Scraper job lookup failed ({response.status_code})"
            )
        return response.json()

    async def poll(self, search: LeadSearchModel) -> ProviderJobStatus:
        if not search.external_job_id:
            raise LeadProviderError("Search has no external scraper job id")
        data = await self._get_job(search.external_job_id)
        # The simple web API marshals Go fields with capitalised keys.
        raw_status = str(data.get("Status") or data.get("status") or "").lower()
        mapping = {
            "pending": "pending",
            "working": "running",
            "ok": "completed",
            "completed": "completed",
            "failed": "failed",
        }
        status = mapping.get(raw_status, "running")
        result_count = int(data.get("ResultCount") or data.get("result_count") or 0)
        return ProviderJobStatus(status=status, result_count=result_count)

    async def fetch_results(
        self, search: LeadSearchModel, *, final: bool = False
    ) -> list[dict]:
        if not search.external_job_id:
            raise LeadProviderError("Search has no external scraper job id")
        async with _http_client() as client:
            response = await client.get(
                self._url(f"/api/v1/jobs/{search.external_job_id}/download")
            )
        if response.status_code == 404:
            # While the job is starting the CSV does not exist yet. Once the
            # job reports success a missing file is a real failure.
            if not final:
                return []
            raise LeadProviderError("Scraper job finished without a results file")
        if response.status_code >= 400:
            raise LeadProviderError(
                f"Could not download scraper results ({response.status_code})"
            )
        from api.services.leads.normalization import parse_google_maps_csv

        # The file is written incrementally by the scraper, so mid-scrape
        # downloads can end with a partially written row.
        return parse_google_maps_csv(response.text, partial=not final)

    async def cancel(self, search: LeadSearchModel) -> None:
        if not search.external_job_id:
            return
        try:
            async with _http_client() as client:
                await client.delete(self._url(f"/api/v1/jobs/{search.external_job_id}"))
        except httpx.HTTPError:
            logger.warning("Could not cancel scraper job {}", search.external_job_id)


# ---------------------------------------------------------------------------
# Demo provider
# ---------------------------------------------------------------------------

_PREFIXES = (
    "Apex",
    "Blue Harbor",
    "Northstar",
    "Cedar",
    "Brightline",
    "Summit",
    "Harborview",
    "Golden Gate",
    "Ironwood",
    "Lakeside",
    "Maple",
    "Nova",
    "Oakline",
    "Pioneer",
    "Redwood",
    "Silverline",
    "Stonebridge",
    "Sunrise",
    "Trailhead",
    "Westfield",
)

_SUFFIXES = (
    "Group",
    "Labs",
    "Partners",
    "Solutions",
    "Studio",
    "Works",
    "Collective",
    "Co.",
    "Digital",
    "Consulting",
)

_CITY_PROFILES = {
    "london": ("London", "England", "GB", "+4420", "EC1A"),
    "uk": ("London", "England", "GB", "+4420", "EC1A"),
    "california": ("San Francisco", "CA", "US", "+1415", "94105"),
    "san francisco": ("San Francisco", "CA", "US", "+1415", "94105"),
    "los angeles": ("Los Angeles", "CA", "US", "+1213", "90012"),
    "new york": ("New York", "NY", "US", "+1212", "10001"),
    "texas": ("Austin", "TX", "US", "+1512", "78701"),
    "austin": ("Austin", "TX", "US", "+1512", "78701"),
    "toronto": ("Toronto", "ON", "CA", "+1416", "M5H"),
    "dubai": ("Dubai", "Dubai", "AE", "+9714", "00000"),
    "sydney": ("Sydney", "NSW", "AU", "+612", "2000"),
    "berlin": ("Berlin", "Berlin", "DE", "+4930", "10115"),
    "singapore": ("Singapore", "Singapore", "SG", "+656", "018956"),
}  # type: ignore[assignment]

_STREETS = (
    "Market Street",
    "Main Street",
    "High Street",
    "Oak Avenue",
    "Pine Street",
    "King Street",
    "Queen Street",
    "Broadway",
    "Union Square",
    "Commerce Way",
)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:40] or "business"


def _city_profile(location: str, country_code: str | None):
    lowered = (location or "").lower()
    for key, profile in _CITY_PROFILES.items():
        if key in lowered:
            return profile
    country = (country_code or "US").upper()
    if country == "GB":
        return ("London", "England", "GB", "+4420", "EC1A")
    if country == "CA":
        return ("Toronto", "ON", "CA", "+1416", "M5H")
    if country == "AU":
        return ("Sydney", "NSW", "AU", "+612", "2000")
    if country == "AE":
        return ("Dubai", "Dubai", "AE", "+9714", "00000")
    if country == "DE":
        return ("Berlin", "Berlin", "DE", "+4930", "10115")
    if country == "SG":
        return ("Singapore", "Singapore", "SG", "+656", "018956")
    return ("San Francisco", "CA", "US", "+1415", "94105")


class DemoLeadProvider:
    """Deterministic sample businesses so the feature works without Chromium.

    Rows are revealed gradually (about two per second) so the dashboard shows
    the same progressive behaviour as a real Google Maps scrape.
    """

    name = "demo"

    def _total(self, search: LeadSearchModel) -> int:
        params = search.params or {}
        target = getattr(search, "target_results", None) or params.get("target_results")
        if target:
            # Only a share of demo businesses have (or lack) a website, so
            # generate extra rows to leave the worker enough matches.
            mode = params.get("website_filter", "any")
            factor = {"no_website": 4, "has_website": 2}.get(mode, 1)
            return max(1, min(500, int(target) * factor))
        depth = max(1, int(params.get("depth", 8)))
        return min(60, max(12, depth * 6))

    def _visible(self, search: LeadSearchModel, total: int) -> int:
        started_at = getattr(search, "started_at", None)
        if started_at is None:
            return total
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        elapsed = max(0.0, (datetime.now(UTC) - started_at).total_seconds())
        return min(total, int(elapsed * 2) + 1)

    async def submit(self, search: LeadSearchModel) -> str:
        # A short delay makes the UI progress state visible without slowing the
        # worker down; results themselves are generated on fetch.
        await asyncio.sleep(1.2)
        return f"demo-{search.id}"

    async def poll(self, search: LeadSearchModel) -> ProviderJobStatus:
        total = self._total(search)
        visible = self._visible(search, total)
        return ProviderJobStatus(
            status="completed" if visible >= total else "running",
            result_count=visible,
        )

    async def fetch_results(
        self, search: LeadSearchModel, *, final: bool = False
    ) -> list[dict]:
        await asyncio.sleep(0.2)
        params = search.params or {}
        total = self._total(search)
        count = total if final else self._visible(search, total)
        seed = int(
            hashlib.sha256(
                f"{search.id}:{search.category}:{search.location}".encode()
            ).hexdigest()[:8],
            16,
        )
        rng = random.Random(seed)
        city, state, country, dial, postal = _city_profile(
            search.location or "", search.country_code
        )
        category = (search.category or "Local Business").strip().title()
        rows: list[dict] = []
        for index in range(count):
            prefix = _PREFIXES[index % len(_PREFIXES)]
            suffix = _SUFFIXES[(index * 3) % len(_SUFFIXES)]
            title = f"{prefix} {category} {suffix}"
            street_number = 100 + index * 7
            address = f"{street_number} {_STREETS[index % len(_STREETS)]}"
            has_phone = rng.random() > 0.08
            phone = f"{dial} 555-{1000 + index:04d}" if has_phone else None
            # Real Google Maps results include businesses without a website, so
            # the demo reflects that mix (and the "no website" filter is usable).
            has_website = rng.random() > 0.3
            domain = f"{_slug(title)}.example.com" if has_website else ""
            rating = round(rng.uniform(3.4, 4.9), 1)
            review_count = rng.randint(8, 640)
            rows.append(
                {
                    "title": title,
                    "category": category,
                    "address": f"{address}, {city}, {state} {postal}, {country}",
                    "phone": phone,
                    "emails": (
                        f"hello@{domain}" if has_website and rng.random() > 0.25 else ""
                    ),
                    "website": f"https://{domain}" if has_website else "",
                    "place_id": f"demo-{search.id}-{index}",
                    "cid": f"demo-cid-{search.id}-{index}",
                    "review_rating": rating,
                    "review_count": review_count,
                    "latitude": round(
                        rng.uniform(37.70, 37.81)
                        if country == "US"
                        else rng.uniform(-33.9, 51.5),
                        6,
                    ),
                    "longitude": round(
                        rng.uniform(-122.52, -122.35)
                        if country == "US"
                        else rng.uniform(-0.3, 151.2),
                        6,
                    ),
                    "complete_address": {
                        "street": address,
                        "city": city,
                        "state": state,
                        "postal_code": postal,
                        "country": country,
                    },
                    "open_hours": {
                        "Monday": ["9 AM-6 PM"],
                        "Tuesday": ["9 AM-6 PM"],
                        "Wednesday": ["9 AM-6 PM"],
                        "Thursday": ["9 AM-6 PM"],
                        "Friday": ["9 AM-5 PM"],
                    },
                    "link": f"https://maps.google.com/?q={_slug(title)}",
                    "data_id": f"demo-data-{search.id}-{index}",
                }
            )
        return rows

    async def cancel(self, search: LeadSearchModel) -> None:
        return None


_PROVIDERS: dict[str, LeadProvider] = {
    "google_maps": GoogleMapsScraperProvider(),
    "demo": DemoLeadProvider(),
}


def available_providers(include_demo: bool) -> list[str]:
    return [name for name in ("google_maps", "demo") if include_demo or name != "demo"]


def get_provider(name: str, *, include_demo: bool = True) -> LeadProvider:
    provider = _PROVIDERS.get(name)
    if provider is None:
        raise LeadProviderError(f"Unknown discovery provider: {name}")
    if name == "demo" and not include_demo:
        raise LeadProviderError("The demo provider is disabled on this deployment")
    return provider
