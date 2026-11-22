"""Bolo Agent web access: settings storage, provider dispatch and safety."""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.copilot import router
from api.services.auth.depends import get_user
from api.services.copilot import web_access
from api.services.copilot.agent import _active_tools, _tools_node
from api.services.copilot.prompts import system_prompt
from api.services.copilot.tools import TOOLS

USER = SimpleNamespace(id=3, selected_organization_id=11)


class _Store:
    def __init__(self) -> None:
        self.rows: dict[tuple[int, str], dict] = {}

    async def get_configuration(self, organization_id, key):
        value = self.rows.get((organization_id, key))
        return SimpleNamespace(value=value) if value is not None else None

    async def upsert_configuration(self, organization_id, key, value):
        self.rows[(organization_id, key)] = value
        return SimpleNamespace(value=value)


@pytest.fixture
def store(monkeypatch):
    fake = _Store()
    monkeypatch.setattr(
        web_access.db_client, "get_configuration", fake.get_configuration
    )
    monkeypatch.setattr(
        web_access.db_client, "upsert_configuration", fake.upsert_configuration
    )
    return fake


@pytest.mark.asyncio
async def test_keyless_exa_is_on_by_default_and_keys_stay_off(store):
    settings = await web_access.public_settings(11)
    by_name = {item["name"]: item for item in settings["providers"]}

    assert by_name["exa"]["enabled"] is True
    assert by_name["exa"]["configured"] is True
    assert by_name["tavily"]["enabled"] is False
    assert by_name["tavily"]["configured"] is False
    assert settings["any_enabled"] is True


@pytest.mark.asyncio
async def test_saving_a_key_encrypts_it_and_never_returns_it(store):
    settings = await web_access.update_settings(
        11,
        [
            {"name": "tavily", "enabled": True, "api_key": "tvly-secret-value"},
        ],
    )
    by_name = {item["name"]: item for item in settings["providers"]}

    assert by_name["tavily"]["enabled"] is True
    assert by_name["tavily"]["configured"] is True
    assert "api_key" not in by_name["tavily"]

    stored = store.rows[(11, web_access.CONFIG_KEY)]
    serialized = json.dumps(stored)
    assert "tvly-secret-value" not in serialized
    credentials = stored["providers"]["tavily"]["credentials"]
    assert credentials["alg"] == "fernet"
    assert credentials["ciphertext"].startswith("gAAAA")

    cleared = await web_access.update_settings(
        11, [{"name": "tavily", "clear_key": True}]
    )
    cleared_by_name = {item["name"]: item for item in cleared["providers"]}
    assert cleared_by_name["tavily"]["configured"] is False


@pytest.mark.asyncio
async def test_settings_are_isolated_per_organization(store):
    await web_access.update_settings(
        11, [{"name": "tavily", "enabled": True, "api_key": "org-eleven"}]
    )

    other = await web_access.public_settings(22)
    other_by_name = {item["name"]: item for item in other["providers"]}
    assert other_by_name["tavily"]["enabled"] is False
    assert other_by_name["tavily"]["configured"] is False


@pytest.mark.asyncio
async def test_web_tools_are_off_until_a_provider_is_usable(store):
    await web_access.update_settings(
        11,
        [
            {"name": "exa", "enabled": False},
            {"name": "tavily", "enabled": True},
        ],
    )

    assert await web_access.enabled_provider_labels(11) == []
    with pytest.raises(web_access.WebAccessError, match="Web access is off"):
        await web_access.search(11, "anything")


@pytest.mark.asyncio
async def test_search_falls_back_to_the_next_enabled_provider(store, monkeypatch):
    await web_access.update_settings(
        11, [{"name": "tavily", "enabled": True, "api_key": "tvly-key"}]
    )

    async def failing_exa(query, limit):
        raise web_access.WebAccessError("rate limited")

    async def working_tavily(api_key, query, limit):
        assert api_key == "tvly-key"
        return [{"title": "Example", "url": "https://example.com/a", "snippet": "hi"}]

    monkeypatch.setattr(web_access, "_exa_search", failing_exa)
    monkeypatch.setattr(web_access, "_tavily_search", working_tavily)

    result = await web_access.search(11, "example")

    assert result["provider"] == "Tavily"
    assert result["results"][0]["url"] == "https://example.com/a"


@pytest.mark.asyncio
async def test_fetch_rejects_private_and_local_urls(store, monkeypatch):
    async def failing_exa(url):
        raise web_access.WebAccessError("unavailable")

    monkeypatch.setattr(web_access, "_exa_fetch", failing_exa)

    with pytest.raises(web_access.WebAccessError):
        await web_access.fetch(11, "http://127.0.0.1:8000/admin")
    with pytest.raises(web_access.WebAccessError):
        await web_access.fetch(11, "http://localhost/")


@pytest.mark.asyncio
async def test_fetch_uses_firecrawl_when_it_is_enabled(store, monkeypatch):
    await web_access.update_settings(
        11, [{"name": "firecrawl", "enabled": True, "api_key": "fc-key"}]
    )

    async def firecrawl(api_key, url):
        assert api_key == "fc-key"
        return "# Heading\nUseful page text"

    monkeypatch.setattr(web_access, "_firecrawl_fetch", firecrawl)

    result = await web_access.fetch(11, "https://example.com/page")

    assert result["provider"] == "Firecrawl"
    assert "Useful page text" in result["content"]


def test_exa_markdown_results_are_parsed():
    text = (
        "Title: First result\n"
        "URL: https://example.com/one\n"
        "Published: N/A\n"
        "Author: N/A\n"
        "Highlights:\n"
        "The first snippet.\n"
        "Title: Second result\n"
        "URL: https://example.com/two\n"
        "Highlights:\n"
        "The second snippet.\n"
    )

    results = web_access._parse_exa_results(text)

    assert [item["url"] for item in results] == [
        "https://example.com/one",
        "https://example.com/two",
    ]
    assert results[0]["title"] == "First result"
    assert "first snippet" in results[0]["snippet"]


def test_web_tools_are_hidden_when_web_access_is_off():
    names_off = {tool.name for tool in _active_tools(False)}
    names_on = {tool.name for tool in _active_tools(True)}

    assert {"web_search", "web_fetch"}.isdisjoint(names_off)
    assert {"web_search", "web_fetch"} <= names_on
    assert {"web_search", "web_fetch"} <= set(TOOLS)


def test_prompt_states_whether_web_access_is_available():
    on = system_prompt(model_label="test", web_access="Exa")
    off = system_prompt(model_label="test")

    assert "Web access is on through Exa" in on
    assert "Web access is off" in off


@pytest.mark.asyncio
async def test_tools_node_rejects_web_calls_when_disabled():
    node = _tools_node(False)
    state = {
        "messages": [
            SimpleNamespace(
                tool_calls=[
                    {"id": "call-1", "name": "web_search", "args": {"query": "x"}}
                ]
            )
        ],
        "organization_id": 11,
        "user_id": 3,
        "conversation_id": 1,
    }

    result = await node(state)

    assert "Unknown tool" in result["messages"][0].content


def test_route_returns_and_updates_settings(store):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_user] = lambda: USER
    client = TestClient(app)

    response = client.get("/api/v1/copilot/web-access")
    assert response.status_code == 200
    assert response.json()["any_enabled"] is True

    response = client.put(
        "/api/v1/copilot/web-access",
        json={
            "providers": [{"name": "serper", "enabled": True, "api_key": "serper-key"}]
        },
    )
    assert response.status_code == 200
    by_name = {item["name"]: item for item in response.json()["providers"]}
    assert by_name["serper"]["enabled"] is True
    assert by_name["serper"]["configured"] is True
    assert "api_key" not in by_name["serper"]


def test_route_requires_a_selected_organization(store):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=3, selected_organization_id=None
    )
    client = TestClient(app)

    response = client.get("/api/v1/copilot/web-access")

    assert response.status_code == 400
