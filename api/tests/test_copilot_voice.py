"""Unit tests for the copilot voice surface."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException
from pydantic import BaseModel
from starlette.websockets import WebSocketState

from api.routes import copilot_voice
from api.services.call_concurrency import CallConcurrencyLimitError
from api.services.copilot import voice
from api.services.copilot.tools import (
    RISK_READ,
    RISK_WRITE,
    TOOLS,
    CopilotContext,
    CopilotTool,
)

CTX = CopilotContext(organization_id=42, user_id=9, conversation_id=5)


class _Config(BaseModel):
    provider: str
    model: str
    api_key: str = "sk-test"
    base_url: str = ""


def _effective(**overrides):
    values = {
        "is_realtime": False,
        "llm": _Config(provider="openai", model="gpt-4o-mini"),
        "stt": _Config(provider="deepgram", model="nova-3"),
        "tts": _Config(provider="cartesia", model="sonic-2"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _resolved(config):
    return SimpleNamespace(effective=config)


def test_voice_tools_are_exactly_the_read_tools():
    expected = {tool.name for tool in TOOLS.values() if tool.risk == RISK_READ}
    actual = {tool.name for tool in voice.voice_tools()}

    assert actual == expected
    assert expected
    for write_name in (
        "start_lead_search",
        "create_email_campaign_draft",
        "launch_email_campaign",
        "send_whatsapp_message",
        "tag_leads",
        "start_voice_campaign",
        "pause_voice_campaign",
        "resume_voice_campaign",
    ):
        assert TOOLS[write_name].risk == RISK_WRITE
        assert write_name not in actual


def test_voice_tool_schema_matches_the_pydantic_schema():
    schema = voice.voice_tools_schema(CTX)
    by_name = {tool.name: tool for tool in schema.standard_tools}

    assert set(by_name) == {tool.name for tool in voice.voice_tools()}
    search = by_name["search_leads"]
    assert "query" in search.properties
    assert search.required == []
    assert callable(search.handler)
    assert by_name["lead_search_status"].required == ["search_id"]
    assert "short" in search.description


@pytest.mark.asyncio
async def test_voice_configuration_requires_every_byok_connection():
    cases = [
        ({"llm": None}, "needs your own text LLM"),
        ({"stt": None}, "speech-to-text"),
        ({"tts": None}, "voice provider"),
        (
            {"tts": _Config(provider="bolo", model="managed")},
            "not Bolo-hosted services",
        ),
        ({"is_realtime": True}, "realtime model"),
    ]
    for overrides, message in cases:
        resolved = _resolved(_effective(**overrides))
        with patch.object(
            voice,
            "get_resolved_ai_model_configuration",
            AsyncMock(return_value=resolved),
        ):
            with pytest.raises(voice.VoiceConfigurationError) as excinfo:
                await voice.voice_configuration(42)
        assert message in str(excinfo.value)


@pytest.mark.asyncio
async def test_voice_configuration_accepts_own_text_stack():
    config = _effective()

    with patch.object(
        voice,
        "get_resolved_ai_model_configuration",
        AsyncMock(return_value=_resolved(config)),
    ):
        resolved = await voice.voice_configuration(42)

    assert resolved is config
    assert voice.voice_status(config) == {
        "llm": "openai / gpt-4o-mini",
        "stt": "deepgram / nova-3",
        "tts": "cartesia / sonic-2",
    }


@pytest.mark.asyncio
async def test_voice_configuration_reports_unreachable_speaches(monkeypatch):
    config = _effective(
        tts=_Config(
            provider="speaches",
            model="kokoro",
            base_url="http://localhost:11434/v1",
        )
    )

    class _Unreachable:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(voice.httpx, "AsyncClient", lambda **kwargs: _Unreachable())
    with patch.object(
        voice,
        "get_resolved_ai_model_configuration",
        AsyncMock(return_value=_resolved(config)),
    ):
        with pytest.raises(voice.VoiceConfigurationError) as excinfo:
            await voice.voice_configuration(42)

    message = str(excinfo.value)
    assert "The voice service does not answer at http://localhost:11434/v1" in message
    assert "host.docker.internal" in message


@pytest.mark.asyncio
async def test_voice_readiness_reports_ready_services():
    config = _effective()
    user = SimpleNamespace(selected_organization_id=42)

    with patch.object(
        copilot_voice.voice,
        "voice_configuration",
        AsyncMock(return_value=config),
    ):
        status = await copilot_voice.voice_status(user)

    assert status.ready is True
    assert status.message is None
    assert status.llm == "openai / gpt-4o-mini"
    assert status.stt == "deepgram / nova-3"
    assert status.tts == "cartesia / sonic-2"


@pytest.mark.asyncio
async def test_voice_readiness_reports_missing_connections():
    user = SimpleNamespace(selected_organization_id=42)

    with patch.object(
        copilot_voice.voice,
        "voice_configuration",
        AsyncMock(side_effect=voice.VoiceConfigurationError("needs your own text LLM")),
    ):
        status = await copilot_voice.voice_status(user)

    assert status.ready is False
    assert status.message == "needs your own text LLM"
    assert status.llm is None


@pytest.mark.asyncio
async def test_voice_readiness_requires_an_organization():
    with pytest.raises(HTTPException):
        await copilot_voice.voice_status(SimpleNamespace(selected_organization_id=None))


class _EchoArgs(BaseModel):
    text: str


@pytest.mark.asyncio
async def test_voice_handler_runs_scoped_tool_and_audits():
    handler = AsyncMock(return_value={"echo": "hi"})
    tool = CopilotTool(
        name="echo",
        description="Echo text.",
        args_schema=_EchoArgs,
        risk=RISK_READ,
        handler=handler,
    )
    params = SimpleNamespace(arguments={"text": "hi"}, result_callback=AsyncMock())

    with patch.object(voice, "audit_tool_call", AsyncMock()) as audit:
        await voice._voice_handler(CTX, tool)(params)

    handler.assert_awaited_once_with(CTX, _EchoArgs(text="hi"))
    params.result_callback.assert_awaited_once_with({"echo": "hi"})
    audit.assert_awaited_once()
    args, kwargs = audit.await_args
    assert args[:5] == (CTX, "echo", RISK_READ, "executed", {"text": "hi"})
    assert kwargs["result"] == {"echo": "hi"}
    assert isinstance(kwargs["duration_ms"], int)


@pytest.mark.asyncio
async def test_voice_handler_reports_validation_and_tool_errors():
    tool = CopilotTool(
        name="echo",
        description="Echo text.",
        args_schema=_EchoArgs,
        risk=RISK_READ,
        handler=AsyncMock(side_effect=ValueError("not in this workspace")),
    )

    invalid = SimpleNamespace(arguments={}, result_callback=AsyncMock())
    with patch.object(voice, "audit_tool_call", AsyncMock()) as audit:
        await voice._voice_handler(CTX, tool)(invalid)
    assert "Invalid arguments" in invalid.result_callback.await_args.args[0]["error"]
    assert audit.await_args.kwargs["error"] == "validation"

    failing = SimpleNamespace(arguments={"text": "hi"}, result_callback=AsyncMock())
    with patch.object(voice, "audit_tool_call", AsyncMock()) as audit:
        await voice._voice_handler(CTX, tool)(failing)
    failing.result_callback.assert_awaited_once_with({"error": "not in this workspace"})
    assert audit.await_args.kwargs["error"] == "not in this workspace"


class _FakeConnection:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.pc_id = None
        self.initialized = None
        self.disconnected = False
        self.candidates = []

    def event_handler(self, _name):
        def register(_handler):
            return _handler

        return register

    async def initialize(self, **kwargs):
        self.initialized = kwargs

    def get_answer(self):
        return {
            "sdp": "v=0\r\na=candidate:1 1 udp 1 192.168.1.2 5000 typ host\r\n",
            "type": "answer",
            "pc_id": self.pc_id,
        }

    async def renegotiate(self, **_kwargs):
        return None

    async def add_ice_candidate(self, candidate):
        self.candidates.append(candidate)

    async def disconnect(self):
        self.disconnected = True


class _FakeWebSocket:
    application_state = WebSocketState.CONNECTED

    def __init__(self):
        self.sent = []
        self.close = AsyncMock()

    async def send_json(self, message):
        self.sent.append(message)


def _session():
    user = SimpleNamespace(id=9)
    conversation = SimpleNamespace(
        id=5, organization_id=42, user_id=9, title="Voice test"
    )
    return copilot_voice.CopilotVoiceSession(_FakeWebSocket(), user, conversation)


@pytest.mark.asyncio
async def test_offer_starts_pipeline_with_org_slot_and_read_only_scope():
    session = _session()
    connection = _FakeConnection()
    slot = object()

    with (
        patch.object(
            copilot_voice, "SmallWebRTCConnection", lambda **kwargs: connection
        ),
        patch.object(copilot_voice, "get_ice_servers", lambda **_kw: []),
        patch.object(copilot_voice, "filter_outbound_sdp", lambda sdp: sdp),
        patch.object(
            copilot_voice.call_concurrency,
            "acquire_org_slot",
            AsyncMock(return_value=slot),
        ) as acquire,
        patch.object(
            copilot_voice.call_concurrency, "release_slot", AsyncMock()
        ) as release,
        patch.object(copilot_voice.voice, "run_voice_pipeline", AsyncMock()) as run,
    ):
        await session._handle_offer(
            {"pc_id": "pc-1", "sdp": "offer-sdp", "type": "offer"}
        )
        await asyncio.sleep(0)
        await session._cleanup()

    acquire.assert_awaited_once_with(42, source="copilot_voice", timeout=0)
    assert connection.initialized == {"sdp": "offer-sdp", "type": "offer"}
    run.assert_awaited_once_with(connection, session.ctx)
    assert session.ctx == CTX
    assert session.websocket.sent[0]["type"] == "answer"
    assert session.websocket.sent[0]["payload"]["pc_id"] == "pc-1"
    assert connection.disconnected is True
    release.assert_awaited_once_with(slot)


@pytest.mark.asyncio
async def test_offer_refuses_when_org_concurrency_is_full():
    session = _session()
    limit = CallConcurrencyLimitError(
        organization_id=42,
        source="copilot_voice",
        wait_time=0,
        max_concurrent=1,
    )

    with (
        patch.object(
            copilot_voice,
            "SmallWebRTCConnection",
            lambda **_kwargs: pytest.fail("connection must not be created"),
        ),
        patch.object(
            copilot_voice.call_concurrency,
            "acquire_org_slot",
            AsyncMock(side_effect=limit),
        ),
    ):
        await session._handle_offer(
            {"pc_id": "pc-1", "sdp": "offer-sdp", "type": "offer"}
        )

    error = session.websocket.sent[0]
    assert error["type"] == "error"
    assert error["payload"]["error_type"] == "concurrency_limit_exceeded"
    assert session.connection is None


@pytest.mark.asyncio
async def test_offer_rejects_missing_fields_and_second_peer():
    session = _session()

    await session._handle_offer({"pc_id": "pc-1", "sdp": "", "type": "offer"})
    assert session.websocket.sent[-1]["payload"]["error_type"] == "voice_error"

    session.connection = _FakeConnection()
    session.pc_id = "pc-1"
    await session._handle_offer({"pc_id": "pc-2", "sdp": "sdp", "type": "offer"})

    assert session.websocket.sent[-1]["type"] == "error"
    assert "already open" in session.websocket.sent[-1]["payload"]["message"]


_CANDIDATE = {
    "pc_id": "pc-1",
    "candidate": {"candidate": "candidate:1 1 udp 1 1.1.1.1 1 typ host"},
}


@pytest.mark.asyncio
async def test_ice_candidates_drop_unknown_peers_and_filtered_addresses():
    session = _session()

    await session._handle_ice_candidate({**_CANDIDATE, "pc_id": "nope"})
    assert session.websocket.sent == []

    connection = _FakeConnection()
    session.connection = connection
    session.pc_id = "pc-1"

    with patch.object(copilot_voice, "_keep_candidate", lambda *_a: False):
        await session._handle_ice_candidate(_CANDIDATE)
    assert connection.candidates == []

    with patch.object(copilot_voice, "_keep_candidate", lambda *_a: True):
        await session._handle_ice_candidate(_CANDIDATE)
    assert len(connection.candidates) == 1


@pytest.mark.asyncio
async def test_renegotiate_without_peer_reports_missing_connection():
    session = _session()

    await session._handle_renegotiation({"pc_id": "pc-1", "sdp": "sdp"})

    assert session.websocket.sent[-1]["type"] == "error"
    assert "not found" in session.websocket.sent[-1]["payload"]["message"]


@pytest.mark.asyncio
async def test_call_end_notification_closes_the_socket_once():
    session = _session()

    await session._notify_call_ended("peer_connection_closed")
    await session._notify_call_ended("pipeline_failed")

    assert [m["type"] for m in session.websocket.sent] == ["call-ended"]
    assert session.websocket.sent[0]["payload"]["reason"] == "peer_connection_closed"
    assert session.websocket.close.await_count == 1
    assert session.websocket.close.await_args.kwargs["code"] == 1000
