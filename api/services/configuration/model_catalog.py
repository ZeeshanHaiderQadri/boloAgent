"""Model discovery using an organization's saved provider connection.

Model names change with every provider release, so the catalog is read from
the provider's own ``GET /models`` endpoint instead of a list in our code.
Only OpenAI-compatible providers are queried; the rest fall back to manual
entry in the UI.
"""

import asyncio
from random import choice
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from api.db import db_client
from api.schemas.model_catalog import ModelInfo, ModelsResponse
from api.services.configuration.registry import ServiceProviders
from api.utils.url_security import validate_user_configured_service_url

# OpenAI-compatible providers that list models at ``{base_url}/models``.
OPENAI_COMPATIBLE_BASE_URLS = {
    ServiceProviders.OPENAI.value: "https://api.openai.com/v1",
    ServiceProviders.OPENROUTER.value: "https://openrouter.ai/api/v1",
    ServiceProviders.GROQ.value: "https://api.groq.com/openai/v1",
    ServiceProviders.ATLASCLOUD.value: "https://api.atlascloud.ai/v1",
    ServiceProviders.HOPPER.value: "https://api.withhopper.com/v1",
    ServiceProviders.SPEACHES.value: "http://localhost:11434/v1",
}


async def get_connection_models(
    organization_id: int, connection_uuid: str
) -> ModelsResponse:
    connection = await db_client.get_provider_connection(
        organization_id, connection_uuid
    )
    if connection is None:
        raise HTTPException(status_code=404, detail="Provider connection not found")
    provider = connection.provider
    default_base_url = OPENAI_COMPATIBLE_BASE_URLS.get(provider)
    if default_base_url is None:
        raise HTTPException(
            status_code=400,
            detail="Model discovery is not supported for this provider",
        )

    base_url = (
        connection.connection_settings.get("base_url") or default_base_url
    ).rstrip("/")
    try:
        await asyncio.to_thread(
            validate_user_configured_service_url, base_url, field_name="base_url"
        )
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Invalid provider connection base URL"
        ) from None

    keys = connection.credentials.get("api_key")
    api_key = choice(keys) if isinstance(keys, list) and keys else keys
    headers = (
        {"Authorization": f"Bearer {api_key}"}
        if isinstance(api_key, str) and api_key.strip()
        else {}
    )

    try:
        async with (
            asyncio.timeout(20),
            httpx.AsyncClient(timeout=10, follow_redirects=False) as client,
        ):
            response = await client.get(f"{base_url}/models", headers=headers)
            response.raise_for_status()
            payload = response.json()
    except (TimeoutError, httpx.TimeoutException):
        raise HTTPException(
            status_code=504,
            detail="The provider took too long to list models. Try again.",
        ) from None
    except httpx.ConnectError:
        # The usual self-host trap: the API runs in Docker, the provider on the
        # host, so loopback points at the API container and can never connect.
        host = urlsplit(base_url).hostname
        hint = (
            " If Bolo runs in Docker and the provider on your machine, use "
            "host.docker.internal instead of localhost."
            if host in {"localhost", "127.0.0.1", "0.0.0.0"}
            else ""
        )
        raise HTTPException(
            status_code=502,
            detail=f"Could not connect to {base_url}.{hint}",
        ) from None
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403}:
            raise HTTPException(
                status_code=400,
                detail=(
                    "The provider rejected the connection's API key. "
                    "Check the key and try again."
                ),
            ) from None
        if exc.response.status_code == 429:
            raise HTTPException(
                status_code=429,
                detail="The provider rate limited model requests. Try again shortly.",
            ) from None
        raise HTTPException(
            status_code=502, detail="Could not load models from the provider."
        ) from None
    except (httpx.HTTPError, ValueError):
        # Provider bodies can contain account details; never surface them.
        raise HTTPException(
            status_code=502, detail="Could not load models from the provider."
        ) from None

    entries = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise HTTPException(
            status_code=502, detail="The provider returned an unexpected model list."
        )
    models: dict[str, ModelInfo] = {}
    for entry in entries:
        if isinstance(entry, str):
            model_id, name = entry, None
        elif isinstance(entry, dict) and isinstance(entry.get("id"), str):
            model_id = entry["id"]
            name = entry.get("name") if isinstance(entry.get("name"), str) else None
        else:
            continue
        model_id = model_id.strip()
        if model_id and model_id not in models:
            models[model_id] = ModelInfo(id=model_id, name=name)
    return ModelsResponse(provider=provider, models=list(models.values()))
