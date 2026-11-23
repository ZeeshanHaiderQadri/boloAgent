"""Local model listing, install guards and removal, without real servers."""

import asyncio

import httpx
import pytest

from api.schemas.local_models import LocalModelInfo
from api.services import local_models


def installed(family, kind, model_id, size=None):
    return LocalModelInfo(
        family=family,
        id=model_id,
        kind=kind,
        title=model_id,
        installed=True,
        size_bytes=size,
    )


class FakeRedis:
    def __init__(self):
        self.keys = {}

    async def exists(self, key):
        return int(key in self.keys)

    async def set(self, key, value, ex=None):
        self.keys[key] = value

    async def hgetall(self, key):
        return {}

    async def hset(self, key, mapping):
        self.keys[key] = mapping

    async def expire(self, key, seconds):
        return True

    async def delete(self, *keys):
        for key in keys:
            self.keys.pop(key, None)


@pytest.fixture
def servers(monkeypatch):
    monkeypatch.setattr(local_models, "LOCAL_OLLAMA_URL", "http://ollama.test")
    monkeypatch.setattr(local_models, "LOCAL_SPEACHES_URL", "http://speaches.test")
    return monkeypatch


async def test_lists_the_catalog_with_installed_models_and_download_progress(servers):
    async def ollama():
        return {
            "lfm3:1.7b-q4_K_M": installed("ollama", "llm", "lfm3:1.7b-q4_K_M", 1000),
            "custom:latest": installed("ollama", "llm", "custom:latest", 2000),
        }

    async def speaches():
        return {
            "Systran/faster-whisper-base": installed(
                "speaches", "stt", "Systran/faster-whisper-base"
            )
        }

    async def running_job(family, model):
        if model == "speaches-ai/Kokoro-82M-v1.0-ONNX":
            return {"status": "running", "completed": 50, "total": 100}
        return None

    servers.setattr(local_models, "_ollama_installed", ollama)
    servers.setattr(local_models, "_speaches_installed", speaches)
    servers.setattr(local_models, "_running_job", running_job)

    result = await local_models.list_local_models()
    assert result.servers == ["ollama", "speaches"]
    models = {(model.family, model.id): model for model in result.models}
    # The catalog keeps its friendly title but takes the size from the server.
    base = models[("ollama", "lfm3:1.7b-q4_K_M")]
    assert base.installed and base.size_bytes == 1000
    assert base.title == "LFM 3 · fast replies"
    # Models installed by other means stay visible so they can be removed.
    assert models[("ollama", "custom:latest")].installed
    # A model downloading now reports how far along it is.
    kokoro = models[("speaches", "speaches-ai/Kokoro-82M-v1.0-ONNX")]
    assert kokoro.downloading and kokoro.progress == 0.5
    assert not models[("speaches", "Keshavji/piper-ur_PK-fasih-medium")].installed


async def test_skips_a_server_that_is_not_responding(servers):
    async def unreachable():
        return None

    async def speaches():
        return {
            "speaches-ai/Kokoro-82M-v1.0-ONNX": installed(
                "speaches", "tts", "speaches-ai/Kokoro-82M-v1.0-ONNX"
            )
        }

    servers.setattr(local_models, "_ollama_installed", unreachable)
    servers.setattr(local_models, "_speaches_installed", speaches)

    async def no_job(family, model):
        return None

    servers.setattr(local_models, "_running_job", no_job)

    result = await local_models.list_local_models()
    assert result.servers == ["speaches"]
    assert all(model.family == "speaches" for model in result.models)


async def test_installs_a_model_the_user_picked_in_the_editor(servers):
    async def none_installed():
        return {}

    started = []

    async def run_download(job_id, family, model):
        started.append((family, model))

    servers.setattr(local_models, "_ollama_installed", none_installed)
    servers.setattr(local_models, "_redis", lambda: FakeRedis())
    servers.setattr(local_models, "_run_download", run_download)

    response = await local_models.download_model("ollama", "qwen2.5:7b")
    await asyncio.sleep(0)
    assert (response.family, response.model) == ("ollama", "qwen2.5:7b")
    assert started == [("ollama", "qwen2.5:7b")]


async def test_names_a_speech_model_the_voice_server_does_not_offer(servers):
    class Response:
        status_code = 404

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "not found",
                request=httpx.Request("POST", "http://speaches.test"),
                response=self,
            )

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url):
            return Response()

    servers.setattr(local_models.httpx, "AsyncClient", lambda **kwargs: Client())
    with pytest.raises(local_models.LocalModelError) as error:
        await local_models._pull_speaches("someone/unknown-model")
    assert "does not offer" in str(error.value)


async def test_refuses_an_unsafe_model_name(servers):
    with pytest.raises(local_models.LocalModelInvalid):
        await local_models.download_model("speaches", "../etc/passwd")


async def test_refuses_to_install_a_model_that_is_already_there(servers):
    async def ollama():
        return {"lfm3:1.7b-q4_K_M": installed("ollama", "llm", "lfm3:1.7b-q4_K_M")}

    servers.setattr(local_models, "_ollama_installed", ollama)
    with pytest.raises(local_models.LocalModelConflict):
        await local_models.download_model("ollama", "lfm3:1.7b-q4_K_M")


async def test_reports_unavailable_when_no_local_server_is_configured(servers):
    servers.setattr(local_models, "LOCAL_OLLAMA_URL", "")
    with pytest.raises(local_models.LocalModelUnavailable):
        await local_models.download_model("ollama", "lfm3:1.7b-q4_K_M")


async def test_only_removes_installed_models(servers):
    async def ollama():
        return {"lfm3:1.7b-q4_K_M": installed("ollama", "llm", "lfm3:1.7b-q4_K_M")}

    servers.setattr(local_models, "_ollama_installed", ollama)
    servers.setattr(local_models, "_redis", lambda: FakeRedis())
    with pytest.raises(local_models.LocalModelNotFound):
        await local_models.remove_model("ollama", "custom:latest")


async def test_does_not_remove_a_model_that_is_downloading(servers):
    redis = FakeRedis()
    redis.keys["bolo:local_model:active:ollama:lfm3:1.7b-q4_K_M"] = "job"
    servers.setattr(local_models, "_redis", lambda: redis)
    with pytest.raises(local_models.LocalModelConflict):
        await local_models.remove_model("ollama", "lfm3:1.7b-q4_K_M")
