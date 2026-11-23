"""Install and remove model files on the local Ollama and Speaches servers.

A self-hosted install runs both servers next to the API and points
``LOCAL_OLLAMA_URL`` / ``LOCAL_SPEACHES_URL`` at them. The servers do the
downloads; job progress is kept in Redis so any API worker can report it.
"""

import asyncio
import json
import re
import uuid
from typing import Literal

import httpx
import redis.asyncio as aioredis
from loguru import logger

from api.constants import LOCAL_OLLAMA_URL, LOCAL_SPEACHES_URL, REDIS_URL
from api.schemas.local_models import (
    LocalModelDownloadResponse,
    LocalModelDownloadStatus,
    LocalModelFamily,
    LocalModelInfo,
    LocalModelsResponse,
)

_READ_TIMEOUT = 30.0
_JOB_TTL_SECONDS = 3600
_SPEACHES_POLL_SECONDS = 2
_SPEACHES_POLL_ATTEMPTS = 900
# Ollama tags and Hugging Face repo ids, one path segment at a time. Free of
# dots-only or empty segments, so a name can never walk out of the URL path.
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*(/[A-Za-z0-9][A-Za-z0-9._:-]*)*$")

# What a non-technical user can add in one click, per server. Titles and
# descriptions say what the model does for a voice agent, not how it works.
CATALOG: tuple[dict, ...] = (
    {
        "family": "ollama",
        "model": "lfm3:1.7b-q4_K_M",
        "kind": "llm",
        "title": "LFM 3 · fast replies",
        "description": "Small chat model for quick answers on a laptop.",
    },
    {
        "family": "ollama",
        "model": "lfm3-omni:2b-q4_K_M",
        "kind": "llm",
        "title": "LFM 3 Omni · audio aware",
        "description": "Chat model that also understands audio input.",
    },
    {
        "family": "speaches",
        "model": "Systran/faster-whisper-base",
        "kind": "stt",
        "title": "Whisper base · listening",
        "description": "Transcribes English, Urdu and Hindi in real time.",
    },
    {
        "family": "speaches",
        "model": "speaches-ai/Kokoro-82M-v1.0-ONNX",
        "kind": "tts",
        "title": "Kokoro · English and Hindi voice",
        "description": "Natural English and Hindi voices.",
    },
    {
        "family": "speaches",
        "model": "Keshavji/piper-ur_PK-fasih-medium",
        "kind": "tts",
        "title": "Piper · Urdu voice",
        "description": "Free Urdu voice for calls.",
    },
    {
        "family": "speaches",
        "model": "Systran/faster-whisper-large-v3",
        "kind": "stt",
        "title": "Whisper large v3 · accurate listening",
        "description": "More accurate than base, but heavy on a laptop CPU.",
    },
)


class LocalModelError(Exception):
    """Base for local model management failures."""


class LocalModelInvalid(LocalModelError):
    pass


class LocalModelConflict(LocalModelError):
    pass


class LocalModelUnavailable(LocalModelError):
    pass


class LocalModelNotFound(LocalModelError):
    pass


_redis_client: aioredis.Redis | None = None
# Downloads run in this worker; keeping a reference stops the GC cancelling them.
_tasks: set[asyncio.Task] = set()


def _redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


def _server_url(family: LocalModelFamily) -> str:
    return LOCAL_OLLAMA_URL if family == "ollama" else LOCAL_SPEACHES_URL


def _job_key(job_id: str) -> str:
    return f"bolo:local_model:job:{job_id}"


def _active_key(family: str, model: str) -> str:
    return f"bolo:local_model:active:{family}:{model}"


def _valid_model_id(model: str) -> bool:
    return bool(model) and len(model) <= 200 and bool(_MODEL_ID.match(model))


async def _save_job(job_id: str, **fields) -> None:
    redis = _redis()
    await redis.hset(
        _job_key(job_id),
        mapping={key: str(value) for key, value in fields.items() if value is not None},
    )
    await redis.expire(_job_key(job_id), _JOB_TTL_SECONDS)


async def _read_job(job_id: str) -> dict | None:
    data = await _redis().hgetall(_job_key(job_id))
    if not data:
        return None
    return {
        "family": data.get("family"),
        "model": data.get("model"),
        "status": data.get("status", "running"),
        "completed": int(data["completed"]) if data.get("completed") else None,
        "total": int(data["total"]) if data.get("total") else None,
        "detail": data.get("detail"),
    }


async def _ollama_installed() -> dict[str, LocalModelInfo] | None:
    """Installed chat models, or None when the server is not reachable."""
    if not LOCAL_OLLAMA_URL:
        return {}
    try:
        async with httpx.AsyncClient(timeout=_READ_TIMEOUT) as client:
            response = await client.get(f"{LOCAL_OLLAMA_URL}/api/tags")
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        logger.warning("Local Ollama model list is unavailable")
        return None
    installed = {}
    for entry in payload.get("models", []) if isinstance(payload, dict) else []:
        name = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not _valid_model_id(name):
            continue
        size = entry.get("size")
        installed[name] = LocalModelInfo(
            family="ollama",
            id=name,
            kind="llm",
            title=name,
            installed=True,
            size_bytes=size if isinstance(size, int) else None,
        )
    return installed


async def _speaches_installed() -> dict[str, LocalModelInfo] | None:
    """Installed speech models, or None when the server is not reachable."""
    if not LOCAL_SPEACHES_URL:
        return {}
    try:
        async with httpx.AsyncClient(timeout=_READ_TIMEOUT) as client:
            response = await client.get(f"{LOCAL_SPEACHES_URL}/v1/models")
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        logger.warning("Local Speaches model list is unavailable")
        return None
    entries = payload.get("data") if isinstance(payload, dict) else payload
    installed = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        if not isinstance(model_id, str) or not _valid_model_id(model_id):
            continue
        installed[model_id] = LocalModelInfo(
            family="speaches",
            id=model_id,
            kind="tts" if entry.get("task") == "text-to-speech" else "stt",
            title=model_id,
            installed=True,
        )
    return installed


async def _installed(family: LocalModelFamily) -> dict[str, LocalModelInfo] | None:
    return await (_ollama_installed() if family == "ollama" else _speaches_installed())


async def _running_job(family: str, model: str) -> dict | None:
    job_id = await _redis().get(_active_key(family, model))
    return await _read_job(job_id) if job_id else None


async def list_local_models() -> LocalModelsResponse:
    servers: list[str] = []
    installed: dict[tuple[str, str], LocalModelInfo] = {}
    for family in ("ollama", "speaches"):
        found = await _installed(family)
        if found is None:
            continue
        servers.append(family)
        installed.update({(family, key): value for key, value in found.items()})

    models: list[LocalModelInfo] = []
    for entry in CATALOG:
        key = (entry["family"], entry["model"])
        if entry["family"] not in servers:
            continue
        found = installed.get(key)
        models.append(
            LocalModelInfo(
                family=entry["family"],
                id=entry["model"],
                kind=entry["kind"],
                title=entry["title"],
                description=entry["description"],
                installed=found is not None,
                size_bytes=found.size_bytes if found else None,
            )
        )
    known = {(entry["family"], entry["model"]) for entry in CATALOG}
    # Models the user installed by other means stay removable from here.
    for (family, model_id), found in installed.items():
        if (family, model_id) not in known:
            models.append(found)

    try:
        for info in models:
            job = await _running_job(info.family, info.id)
            if job and job.get("status") == "running":
                info.downloading = True
                total, completed = job.get("total"), job.get("completed")
                if total and completed is not None:
                    info.progress = min(completed / total, 1.0)
    except (aioredis.RedisError, OSError):
        logger.warning("Local model download state is unavailable")
    return LocalModelsResponse(servers=servers, models=models)


async def download_status(job_id: str) -> LocalModelDownloadStatus | None:
    job = await _read_job(job_id)
    if job is None:
        return None
    return LocalModelDownloadStatus(
        status=job["status"],
        model=job.get("model") or "",
        completed=job.get("completed"),
        total=job.get("total"),
        detail=job.get("detail"),
    )


async def download_model(
    family: LocalModelFamily, model: str
) -> LocalModelDownloadResponse:
    # Any well-formed id is accepted, not just the catalog: the editor offers to
    # install whichever model the user picked. The server decides if it exists.
    model = model.strip()
    if not _valid_model_id(model):
        raise LocalModelInvalid("Choose a valid model name.")
    if not _server_url(family):
        raise LocalModelUnavailable(
            "No local model server is configured for this deployment."
        )
    installed = await _installed(family)
    if installed is None:
        raise LocalModelUnavailable("The local model server is not responding.")
    if model in installed:
        raise LocalModelConflict("That model is already installed.")
    if await _redis().exists(_active_key(family, model)):
        raise LocalModelConflict("That model is already downloading.")

    job_id = uuid.uuid4().hex
    await _save_job(
        job_id, family=family, model=model, status="running", completed=0, total=0
    )
    await _redis().set(_active_key(family, model), job_id, ex=_JOB_TTL_SECONDS)
    task = asyncio.create_task(_run_download(job_id, family, model))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return LocalModelDownloadResponse(job_id=job_id, family=family, model=model)


async def remove_model(family: LocalModelFamily, model: str) -> None:
    model = model.strip()
    if not _valid_model_id(model):
        raise LocalModelInvalid("Choose a valid model name.")
    if not _server_url(family):
        raise LocalModelUnavailable(
            "No local model server is configured for this deployment."
        )
    if await _redis().exists(_active_key(family, model)):
        raise LocalModelConflict(
            "Wait for the download to finish before removing that model."
        )
    installed = await _installed(family)
    if installed is None:
        raise LocalModelUnavailable("The local model server is not responding.")
    if model not in installed:
        raise LocalModelNotFound("That model is not installed.")
    try:
        async with httpx.AsyncClient(timeout=_READ_TIMEOUT) as client:
            if family == "ollama":
                response = await client.request(
                    "DELETE", f"{_server_url(family)}/api/delete", json={"model": model}
                )
            else:
                response = await client.delete(
                    f"{_server_url(family)}/v1/models/{model}"
                )
            response.raise_for_status()
    except httpx.HTTPError:
        raise LocalModelError(
            "The local model server could not remove that model."
        ) from None


async def _run_download(
    job_id: str, family: Literal["ollama", "speaches"], model: str
) -> None:
    try:
        if family == "ollama":
            await _pull_ollama(job_id, model)
        else:
            await _pull_speaches(model)
        await _save_job(job_id, status="done")
    except LocalModelError as exc:
        await _save_job(job_id, status="error", detail=str(exc))
    except Exception:
        logger.exception("Local model download failed")
        await _save_job(job_id, status="error", detail="The download failed.")
    finally:
        await _redis().delete(_active_key(family, model))


async def _pull_ollama(job_id: str, model: str) -> None:
    timeout = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            f"{LOCAL_OLLAMA_URL}/api/pull",
            json={"model": model, "stream": True},
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue
                if payload.get("error"):
                    raise LocalModelError(str(payload["error"])[:200])
                completed, total = payload.get("completed"), payload.get("total")
                if isinstance(completed, int) and isinstance(total, int) and total > 0:
                    await _save_job(job_id, completed=completed, total=total)


async def _pull_speaches(model: str) -> None:
    timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(f"{LOCAL_SPEACHES_URL}/v1/models/{model}")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise LocalModelError(
                    "The local voice server does not offer that model."
                ) from None
            raise LocalModelError(
                "The local voice server could not start the download."
            ) from None
    # Speaches downloads in the background; wait for the model to appear.
    for _ in range(_SPEACHES_POLL_ATTEMPTS):
        await asyncio.sleep(_SPEACHES_POLL_SECONDS)
        installed = await _speaches_installed()
        if installed is None:
            raise LocalModelError("The local voice server stopped responding.")
        if model in installed:
            return
    raise LocalModelError("The download took too long. Check the local voice server.")
