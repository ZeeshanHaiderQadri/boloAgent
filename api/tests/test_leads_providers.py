"""Contract tests for the Google Maps scraper adapter."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from api.services.leads import providers


class _Response:
    def __init__(self, *, status_code: int = 200, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class _Client:
    def __init__(self, *, response: _Response | None = None):
        self.response = response
        self.requests: list[tuple[str, str, dict | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None):
        self.requests.append(("POST", url, json))
        return self.response

    async def get(self, url):
        self.requests.append(("GET", url, None))
        return self.response

    async def delete(self, url):
        self.requests.append(("DELETE", url, None))
        return self.response


def _search(**overrides):
    values = {
        "id": 7,
        "name": "Gyms in London",
        "category": "Gyms",
        "location": "London, UK",
        "country_code": "GB",
        "language": "en",
        "external_job_id": "job-1",
        "params": {
            "depth": 2,
            "zoom": 12,
            "radius_km": 7,
            "email": True,
            "max_time_seconds": 300,
        },
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_scraper_client_bypasses_proxy_environment():
    with patch.object(providers.httpx, "AsyncClient") as client_cls:
        providers._http_client()

    assert client_cls.call_args.kwargs["trust_env"] is False


@pytest.mark.asyncio
async def test_submit_sends_expected_job_payload():
    client = _Client(response=_Response(payload={"id": "job-42"}))
    provider = providers.GoogleMapsScraperProvider("http://scraper.test")

    with patch.object(providers.httpx, "AsyncClient", MagicMock(return_value=client)):
        job_id = await provider.submit(_search())

    assert job_id == "job-42"
    method, url, payload = client.requests[0]
    assert (method, url) == ("POST", "http://scraper.test/api/v1/jobs")
    assert payload["keywords"] == ["Gyms in London, UK"]
    assert payload["depth"] == 2
    assert payload["zoom"] == 12
    assert payload["radius"] == 7
    assert payload["email"] is True
    assert payload["max_time"] == 300


@pytest.mark.asyncio
async def test_poll_reads_capitalised_go_fields():
    client = _Client(response=_Response(payload={"Status": "ok", "ResultCount": 3}))
    provider = providers.GoogleMapsScraperProvider("http://scraper.test")

    with patch.object(providers.httpx, "AsyncClient", MagicMock(return_value=client)):
        status = await provider.poll(_search())

    assert status.status == "completed"
    assert status.result_count == 3


@pytest.mark.asyncio
async def test_fetch_results_parses_downloaded_csv():
    csv_text = "title,address,phone\nApex Coffee,123 Main St,+14155550142\n"
    client = _Client(response=_Response(text=csv_text))
    provider = providers.GoogleMapsScraperProvider("http://scraper.test")

    with patch.object(providers.httpx, "AsyncClient", MagicMock(return_value=client)):
        rows = await provider.fetch_results(_search())

    assert rows == [
        {"title": "Apex Coffee", "address": "123 Main St", "phone": "+14155550142"}
    ]
    assert client.requests[0][1].endswith("/api/v1/jobs/job-1/download")


@pytest.mark.asyncio
async def test_download_during_scrape_returns_empty_until_file_exists():
    client = _Client(response=_Response(status_code=404, text="not found"))
    provider = providers.GoogleMapsScraperProvider("http://scraper.test")

    with patch.object(providers.httpx, "AsyncClient", MagicMock(return_value=client)):
        assert await provider.fetch_results(_search()) == []
        with pytest.raises(providers.LeadProviderError, match="without a results file"):
            await provider.fetch_results(_search(), final=True)


@pytest.mark.asyncio
async def test_demo_provider_generates_the_requested_number_of_leads():
    provider = providers.DemoLeadProvider()
    search = _search(id=9, target_results=4, started_at=None, params={})

    rows = await provider.fetch_results(search)

    assert len(rows) == 4
    assert len({row["place_id"] for row in rows}) == 4


@pytest.mark.asyncio
async def test_demo_provider_mixes_businesses_with_and_without_websites():
    provider = providers.DemoLeadProvider()
    search = _search(id=11, target_results=60, started_at=None, params={})

    rows = await provider.fetch_results(search)

    assert any(row["website"] for row in rows)
    assert any(not row["website"] for row in rows)
