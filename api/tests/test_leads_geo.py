"""Unit tests for the OpenStreetMap place lookup."""

from unittest.mock import patch

import pytest

from api.services.leads import geo


class _Response:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _Client:
    def __init__(
        self,
        *,
        get_payload=None,
        post_payload=None,
        reverse_payloads=None,
        sparql_payload=None,
    ):
        self.get_payload = get_payload
        self.post_payload = post_payload
        self.reverse_payloads = list(reverse_payloads or [])
        self.sparql_payload = sparql_payload or {"results": {"bindings": []}}
        self.calls: list[tuple[str, str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None, headers=None):
        self.calls.append(("GET", url, params or {}))
        if "sparql" in url:
            return _Response(self.sparql_payload)
        if "/reverse" in url:
            payload = self.reverse_payloads.pop(0) if self.reverse_payloads else {}
            return _Response(payload)
        return _Response(self.get_payload)

    async def post(self, url, data=None):
        self.calls.append(("POST", url, data or {}))
        return _Response(self.post_payload)


_CALIFORNIA = [
    {
        "osm_type": "relation",
        "osm_id": 165475,
        "lat": "36.7014631",
        "lon": "-118.7559970",
        "boundingbox": ["32.53", "42.01", "-124.48", "-114.13"],
        "extratags": {"wikidata": "Q99"},
    }
]

_SAN_FRANCISCO = [
    {
        "osm_type": "relation",
        "osm_id": 111968,
        "lat": "37.7790262",
        "lon": "-122.4199061",
        "boundingbox": ["37.70", "37.83", "-122.53", "-122.35"],
    }
]


def _node(name, *, place="city", population=None):
    tags = {"name": name, "place": place}
    if population is not None:
        tags["population"] = str(population)
    return {"type": "node", "tags": tags}


def test_place_names_ranks_by_population_and_dedupes():
    elements = [
        _node("Fresno", population=542107),
        _node("Bakersfield", population=403455),
        _node("fresno", population=1),
        _node("", population=0),
    ]

    assert geo.place_names(elements, limit=5) == ["Fresno", "Bakersfield"]


def test_place_names_prefers_districts_when_population_is_missing():
    elements = [
        _node("Sunset District", place="neighbourhood"),
        _node("Mission", place="quarter"),
        _node("SoMa", place="suburb"),
    ]

    assert geo.place_names(elements, limit=2) == ["SoMa", "Mission"]


def test_overpass_query_uses_area_id_for_boundaries():
    area = geo.Area(
        osm_type="relation",
        osm_id=165475,
        bbox=(32.53, 42.01, -124.48, -114.13),
        lat=36.7,
        lon=-118.7,
    )

    query = geo._overpass_query(area, geo._CITY_KINDS)

    assert "area(id:3600165475)->.a;" in query
    assert "(area.a)" in query
    assert '"^(city|town|village)$"' in query


def test_overpass_query_falls_back_to_bounding_box():
    area = geo.Area(
        osm_type="node",
        osm_id=1,
        bbox=(10.0, 11.0, 20.0, 21.0),
        lat=10.5,
        lon=20.5,
    )

    query = geo._overpass_query(area, geo._CITY_KINDS)

    assert "(10.0,20.0,11.0,21.0)" in query


def test_area_km2_distinguishes_states_from_cities():
    state = geo._area_from_nominatim(_CALIFORNIA)
    city = geo._area_from_nominatim(_SAN_FRANCISCO)

    assert state is not None and city is not None
    assert geo._area_km2(state) > geo._CITY_AREA_KM2
    assert geo._area_km2(city) < geo._CITY_AREA_KM2


@pytest.mark.asyncio
async def test_area_places_lists_cities_for_a_state():
    client = _Client(
        get_payload=_CALIFORNIA,
        post_payload={
            "elements": [
                _node("Los Angeles", population=3898747),
                _node("San Diego", population=1386932),
                _node("Fresno", population=542107),
            ]
        },
    )
    with patch.object(geo.httpx, "AsyncClient", lambda **kwargs: client):
        places = await geo.area_places("California", limit=2)

    assert places == ["Los Angeles", "San Diego"]
    assert client.calls[0][0] == "GET"
    assert client.calls[0][2]["q"] == "California"
    assert client.calls[1][0] == "POST"


@pytest.mark.asyncio
async def test_area_places_uses_districts_for_a_city():
    client = _Client(
        get_payload=_SAN_FRANCISCO,
        post_payload={
            "elements": [
                _node("Richmond District", place="neighbourhood"),
                _node("Mission", place="quarter"),
            ]
        },
    )
    with patch.object(geo.httpx, "AsyncClient", lambda **kwargs: client):
        places = await geo.area_places("San Francisco", limit=5)

    assert places == ["Mission", "Richmond District"]
    assert "suburb|quarter|neighbourhood" in client.calls[1][2]["data"]


@pytest.mark.asyncio
async def test_area_places_returns_empty_when_geocoding_fails():
    client = _Client(get_payload=[])

    with patch.object(geo.httpx, "AsyncClient", lambda **kwargs: client):
        assert await geo.area_places("Nowhere", limit=5) == []


def test_grid_points_stay_inside_the_area():
    area = geo._area_from_nominatim(_CALIFORNIA)
    assert area is not None

    points = geo._grid_points(area, points=6)

    south, north, west, east = area.bbox
    assert len(points) == 6
    assert all(south < lat < north and west < lon < east for lat, lon in points)


@pytest.mark.asyncio
async def test_area_places_falls_back_to_wikidata_when_overpass_is_down():
    client = _Client(
        get_payload=_CALIFORNIA,
        post_payload={"elements": []},
        sparql_payload={
            "results": {
                "bindings": [
                    {"itemLabel": {"value": "Fresno"}},
                    {"itemLabel": {"value": "Bakersfield"}},
                    {"itemLabel": {"value": "fresno"}},
                ]
            }
        },
    )

    with patch.object(geo.httpx, "AsyncClient", lambda **kwargs: client):
        places = await geo.area_places("California", limit=5)

    assert places == ["Fresno", "Bakersfield"]
    sparql_calls = [call for call in client.calls if "sparql" in call[1]]
    assert sparql_calls
    assert "wd:Q99" in sparql_calls[0][2]["query"]


@pytest.mark.asyncio
async def test_area_places_falls_back_to_reverse_geocoding(monkeypatch):
    monkeypatch.setattr(geo, "_REVERSE_GRID_DELAY_SECONDS", 0)
    client = _Client(
        get_payload=_CALIFORNIA,
        post_payload={"elements": []},
        reverse_payloads=[
            {"name": "Huntington Beach", "address": {"city": "Huntington Beach"}},
            {"name": "Fresno County", "address": {"county": "Fresno County"}},
            {"error": "Unable to geocode"},
            {"name": "United States", "address": {"country": "United States"}},
            {"name": "Bakersfield", "address": {"city": "Bakersfield"}},
        ],
    )

    with patch.object(geo.httpx, "AsyncClient", lambda **kwargs: client):
        places = await geo.area_places("California", limit=5)

    assert places == ["Huntington Beach", "Bakersfield"]
    reverse_calls = [call for call in client.calls if "/reverse" in call[1]]
    assert reverse_calls
    assert reverse_calls[0][2]["zoom"] == geo._REVERSE_GRID_ZOOM_SETTLEMENT


def test_settlement_from_address_prefers_the_most_specific_component():
    assert geo._settlement_from_address({"city": "Fresno"}) == "Fresno"
    assert (
        geo._settlement_from_address({"suburb": "Mission", "city": "San Francisco"})
        == "Mission"
    )
    assert geo._settlement_from_address({"country": "United States"}) == ""


@pytest.mark.asyncio
async def test_reverse_grid_uses_district_zoom_for_a_city(monkeypatch):
    monkeypatch.setattr(geo, "_REVERSE_GRID_DELAY_SECONDS", 0)
    client = _Client(
        get_payload=_SAN_FRANCISCO,
        post_payload={"elements": []},
        reverse_payloads=[{"name": "Mission District"}],
    )

    with patch.object(geo.httpx, "AsyncClient", lambda **kwargs: client):
        places = await geo.area_places("San Francisco", limit=5)

    assert places == ["Mission District"]
    reverse_calls = [call for call in client.calls if "/reverse" in call[1]]
    assert reverse_calls[0][2]["zoom"] == geo._REVERSE_GRID_ZOOM_DISTRICT
