from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from api.routes import model_connections as model_connection_routes
from api.schemas.model_catalog import ModelsResponse
from api.services.auth.depends import get_user_with_selected_organization
from api.services.configuration import model_catalog
from api.services.configuration.model_connections import model_connection_catalog

HttpClient = httpx.AsyncClient


def connection_row(**overrides):
    values = {
        "uuid": "openai-connection",
        "organization_id": 42,
        "provider": "openai",
        "is_active": True,
        "credentials": {"api_key": "private-openai-key"},
        "connection_settings": {"base_url": "https://router.example.test/v1"},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def connection(monkeypatch):
    row = connection_row()

    async def scoped_lookup(organization_id, uuid):
        if (
            row.organization_id == organization_id
            and row.uuid == uuid
            and row.is_active
        ):
            return row
        return None

    monkeypatch.setattr(
        model_catalog.db_client,
        "get_provider_connection",
        AsyncMock(side_effect=scoped_lookup),
    )
    return row


@pytest.fixture
def upstream(monkeypatch):
    requests = []

    def install(handler):
        def record(request):
            requests.append(request)
            return handler(request)

        monkeypatch.setattr(
            model_catalog.httpx,
            "AsyncClient",
            lambda **kwargs: HttpClient(
                transport=httpx.MockTransport(record), **kwargs
            ),
        )
        return requests

    return install


@pytest.mark.asyncio
async def test_lists_models_with_the_connection_key_and_deduplicates(
    connection, upstream, monkeypatch
):
    connection.credentials = {"api_key": ["first-key", "second-key"]}
    monkeypatch.setattr(model_catalog, "choice", lambda keys: keys[1])
    requests = upstream(
        lambda request: httpx.Response(
            200,
            json={
                "data": [
                    {"id": "auto", "name": "Auto router"},
                    {"id": "auto", "name": "Duplicate"},
                    {"id": "gpt-5-mini"},
                    "manual-model",
                    {"id": "piper-ur", "task": "text-to-speech"},
                    {"name": "Missing id"},
                    {"id": "  "},
                ]
            },
        )
    )

    response = await model_catalog.get_connection_models(42, connection.uuid)

    assert [model.id for model in response.models] == [
        "auto",
        "gpt-5-mini",
        "manual-model",
        "piper-ur",
    ]
    assert response.models[0].name == "Auto router"
    assert response.models[1].name is None
    # Speech tasks travel with the model so role pickers can filter them.
    assert response.models[3].task == "text-to-speech"
    assert response.models[2].task is None
    assert response.provider == "openai"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://router.example.test/v1/models"
    assert requests[0].headers["Authorization"] == "Bearer second-key"


@pytest.mark.asyncio
async def test_falls_back_to_the_provider_default_base_url(connection, upstream):
    connection.connection_settings = {}
    requests = upstream(lambda request: httpx.Response(200, json={"data": []}))

    await model_catalog.get_connection_models(42, connection.uuid)

    assert str(requests[0].url) == "https://api.openai.com/v1/models"


@pytest.mark.asyncio
async def test_connection_must_belong_to_the_selected_organization(connection):
    with pytest.raises(HTTPException) as error:
        await model_catalog.get_connection_models(7, connection.uuid)

    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_unsupported_provider_is_rejected_without_a_request(connection, upstream):
    connection.provider = "google"
    requests = upstream(lambda request: httpx.Response(200, json={"data": []}))

    with pytest.raises(HTTPException) as error:
        await model_catalog.get_connection_models(42, connection.uuid)

    assert error.value.status_code == 400
    assert requests == []


@pytest.mark.asyncio
async def test_rejected_key_is_reported_without_echoing_it(connection, upstream):
    upstream(lambda request: httpx.Response(401, json={"error": "denied"}))

    with pytest.raises(HTTPException) as error:
        await model_catalog.get_connection_models(42, connection.uuid)

    assert error.value.status_code == 400
    assert "private-openai-key" not in error.value.detail


@pytest.mark.asyncio
async def test_unreachable_loopback_endpoint_suggests_the_docker_host(
    connection, upstream
):
    connection.connection_settings = {"base_url": "http://localhost:11434/v1"}

    def unreachable(request):
        raise httpx.ConnectError("connection refused", request=request)

    upstream(unreachable)

    with pytest.raises(HTTPException) as error:
        await model_catalog.get_connection_models(42, connection.uuid)

    assert error.value.status_code == 502
    assert "Could not connect to http://localhost:11434/v1" in error.value.detail
    assert "host.docker.internal" in error.value.detail


@pytest.mark.asyncio
async def test_unexpected_payload_is_rejected(connection, upstream):
    upstream(lambda request: httpx.Response(200, json={"data": "nope"}))

    with pytest.raises(HTTPException) as error:
        await model_catalog.get_connection_models(42, connection.uuid)

    assert error.value.status_code == 502


@pytest.mark.asyncio
async def test_route_scopes_the_lookup_to_the_selected_organization(monkeypatch):
    lookup = AsyncMock(return_value=ModelsResponse(provider="openai", models=[]))
    monkeypatch.setattr(model_connection_routes, "get_connection_models", lookup)
    app = FastAPI()
    app.include_router(model_connection_routes.router)
    app.dependency_overrides[get_user_with_selected_organization] = lambda: (
        SimpleNamespace(selected_organization_id=42)
    )
    async with HttpClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/model-connections/provider-connections/openai-connection/models"
        )

    assert response.status_code == 200
    lookup.assert_awaited_once_with(42, "openai-connection")


def test_catalog_marks_only_listable_model_fields():
    services = model_connection_catalog()["services"]

    assert (
        services["llm"]["openai"]["settings_schema"]["properties"]["model"][
            "model_catalog"
        ]
        is True
    )
    assert (
        services["stt"]["speaches"]["settings_schema"]["properties"]["model"].get(
            "model_catalog"
        )
        is True
    )
    assert (
        "model_catalog"
        not in services["llm"]["google"]["settings_schema"]["properties"]["model"]
    )
