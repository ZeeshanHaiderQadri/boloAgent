"""Unit tests for the copilot voice surface."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from pipecat.frames.frames import (
    ErrorFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    MetricsFrame,
    TranscriptionFrame,
)
from pipecat.metrics.metrics import TTFAMetricsData, TTFBMetricsData
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.utils.errors import ErrorCategory
from pydantic import BaseModel
from starlette.websockets import WebSocketState

from api.routes import copilot_voice
from api.services.call_concurrency import CallConcurrencyLimitError
from api.services.copilot import service as copilot_service
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


class _ModelsResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _ModelsClient:
    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None):
        return _ModelsResponse(self._payload)


@pytest.mark.asyncio
async def test_voice_configuration_rejects_a_model_the_endpoint_does_not_serve(
    monkeypatch,
):
    config = _effective(
        tts=_Config(
            provider="openai",
            model="gpt-4o-mini-tts",
            base_url="http://host.docker.internal:31415/v1",
        )
    )
    monkeypatch.setattr(
        voice.httpx,
        "AsyncClient",
        lambda **kwargs: _ModelsClient(
            {"data": [{"id": "auto"}, {"id": "llama-3.3-70b"}]}
        ),
    )
    with patch.object(
        voice,
        "get_resolved_ai_model_configuration",
        AsyncMock(return_value=_resolved(config)),
    ):
        with pytest.raises(voice.VoiceConfigurationError) as excinfo:
            await voice.voice_configuration(42)

    message = str(excinfo.value)
    assert "does not serve model 'gpt-4o-mini-tts'" in message
    assert "auto" in message


@pytest.mark.asyncio
async def test_voice_configuration_accepts_auto_and_listed_custom_models(monkeypatch):
    monkeypatch.setattr(
        voice.httpx,
        "AsyncClient",
        lambda **kwargs: _ModelsClient({"data": [{"id": "auto"}, {"id": "tts-model"}]}),
    )
    for model in ("auto", "tts-model"):
        config = _effective(
            tts=_Config(provider="openai", model=model, base_url="http://router/v1")
        )
        with patch.object(
            voice,
            "get_resolved_ai_model_configuration",
            AsyncMock(return_value=_resolved(config)),
        ):
            resolved = await voice.voice_configuration(42)
        assert resolved is config


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
    run.assert_awaited_once()
    run_args, run_kwargs = run.await_args
    assert run_args == (connection, session.ctx)
    assert callable(run_kwargs["on_error"])
    assert session.ctx == CTX
    assert session.websocket.sent[0]["type"] == "answer"
    assert session.websocket.sent[0]["payload"]["pc_id"] == "pc-1"
    assert connection.disconnected is True
    release.assert_awaited_once_with(slot)


@pytest.mark.asyncio
async def test_offer_pins_the_requested_language_for_the_pipeline():
    session = _session()
    connection = _FakeConnection()

    with (
        patch.object(
            copilot_voice, "SmallWebRTCConnection", lambda **kwargs: connection
        ),
        patch.object(copilot_voice, "get_ice_servers", lambda **_kw: []),
        patch.object(copilot_voice, "filter_outbound_sdp", lambda sdp: sdp),
        patch.object(
            copilot_voice.call_concurrency,
            "acquire_org_slot",
            AsyncMock(return_value=object()),
        ),
        patch.object(copilot_voice.call_concurrency, "release_slot", AsyncMock()),
        patch.object(copilot_voice.voice, "run_voice_pipeline", AsyncMock()) as run,
    ):
        await session._handle_offer(
            {
                "pc_id": "pc-1",
                "sdp": "offer-sdp",
                "type": "offer",
                "language": "ur-PK",
            }
        )
        await asyncio.sleep(0)
        await session._cleanup()

    run_args, run_kwargs = run.await_args
    assert run_args[1].language == "ur"
    assert callable(run_kwargs["on_warning"])
    assert callable(run_kwargs["on_metrics"])


@pytest.mark.asyncio
async def test_warning_is_sent_without_ending_the_call():
    session = _session()

    await session._on_voice_warning("Sarvam voices have no Urdu.")

    message = session.websocket.sent[-1]
    assert message["type"] == "warning"
    assert message["payload"]["warning_type"] == "voice"
    assert message["payload"]["message"] == "Sarvam voices have no Urdu."
    assert session.closed is False


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


class _PeerConnection:
    def __init__(self, ready: bool = False):
        self.ready = ready
        self.handlers = {}

    def is_connected(self) -> bool:
        return self.ready

    def event_handler(self, name):
        def register(handler):
            self.handlers[name] = handler
            return handler

        return register

    async def fire(self, name):
        await self.handlers[name](self)


@pytest.mark.asyncio
async def test_wait_for_peer_returns_when_already_connected():
    await voice._wait_for_peer(_PeerConnection(ready=True), timeout=0.01)


@pytest.mark.asyncio
async def test_wait_for_peer_blocks_until_the_connection_event():
    connection = _PeerConnection()
    waiter = asyncio.create_task(voice._wait_for_peer(connection, timeout=1))
    await asyncio.sleep(0)

    assert not waiter.done()
    await connection.fire("connected")
    await asyncio.wait_for(waiter, timeout=1)


@pytest.mark.asyncio
async def test_wait_for_peer_times_out_with_a_clear_error():
    with pytest.raises(voice.VoiceConfigurationError) as excinfo:
        await voice._wait_for_peer(_PeerConnection(), timeout=0.01)

    assert "audio channel did not open" in str(excinfo.value)


@pytest.mark.asyncio
async def test_voice_error_relay_reports_the_first_permanent_error_once():
    reported = AsyncMock()
    relay = voice._VoiceErrorRelay(reported)
    raw = "Unknown error occurred: Error code: 400 - speech error: Unknown audio model 'gpt-4o-mini-tts'."

    await relay.process_frame(
        ErrorFrame(error=raw, category=ErrorCategory.INVALID_REQUEST),
        FrameDirection.DOWNSTREAM,
    )
    await relay.process_frame(
        ErrorFrame(error=f"  {raw}  ", category=ErrorCategory.INVALID_REQUEST),
        FrameDirection.DOWNSTREAM,
    )

    reported.assert_awaited_once()
    assert "gpt-4o-mini-tts" in reported.await_args.args[0]


@pytest.mark.asyncio
async def test_voice_error_relay_warns_once_on_recoverable_errors():
    reported = AsyncMock()
    warned = AsyncMock()
    relay = voice._VoiceErrorRelay(reported, warned)

    for _ in range(2):
        await relay.process_frame(
            ErrorFrame(
                error="Error code: 429 - speech error: rate limited",
                category=ErrorCategory.RATE_LIMIT,
            ),
            FrameDirection.DOWNSTREAM,
        )

    reported.assert_not_awaited()
    warned.assert_awaited_once()
    assert "rate-limited or out of quota" in warned.await_args.args[0]


@pytest.mark.asyncio
async def test_voice_error_relay_warns_with_a_generic_note_for_other_failures():
    reported = AsyncMock()
    warned = AsyncMock()
    relay = voice._VoiceErrorRelay(reported, warned)

    await relay.process_frame(
        ErrorFrame(error="TTS request timed out", category=ErrorCategory.CONNECTIVITY),
        FrameDirection.DOWNSTREAM,
    )

    assert "may have missed or skipped a reply" in warned.await_args.args[0]


@pytest.mark.asyncio
async def test_audio_errors_end_the_call_with_an_actionable_message():
    session = _session()

    await session._on_audio_error(
        "Unknown error occurred: Error code: 400 - speech error: "
        "Unknown audio model 'gpt-4o-mini-tts'."
    )

    assert [m["type"] for m in session.websocket.sent] == ["error", "call-ended"]
    error = session.websocket.sent[0]["payload"]
    assert error["error_type"] == "voice_pipeline_failed"
    assert "rejected speech synthesis" in error["message"]
    assert "gpt-4o-mini-tts" in error["message"]


def test_user_turns_run_vad_for_segmented_speech_to_text():
    params = voice.voice_user_turn_params()

    assert params.vad_analyzer is not None
    assert [type(item).__name__ for item in params.user_turn_strategies.start] == [
        "TranscriptionUserTurnStartStrategy",
        "VADUserTurnStartStrategy",
    ]
    assert [type(item).__name__ for item in params.user_turn_strategies.stop] == [
        "SpeechTimeoutUserTurnStopStrategy"
    ]


@pytest.mark.asyncio
async def test_user_speech_recorder_saves_final_transcripts_only():
    transcript = SimpleNamespace(record=AsyncMock())
    recorder = voice._UserSpeechRecorder(transcript)

    await recorder.process_frame(
        TranscriptionFrame(text=" find dentists ", user_id="u", timestamp="t"),
        FrameDirection.DOWNSTREAM,
    )
    await recorder.process_frame(
        LLMTextFrame(text="not speech"), FrameDirection.DOWNSTREAM
    )

    transcript.record.assert_awaited_once_with("user", " find dentists ")


@pytest.mark.asyncio
async def test_reply_recorder_joins_one_reply_and_drops_interrupted_text():
    transcript = SimpleNamespace(record=AsyncMock())
    recorder = voice._ReplyRecorder(transcript, voice._TurnLatency(None))

    await recorder.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    await recorder.process_frame(LLMTextFrame(text="Hi "), FrameDirection.DOWNSTREAM)
    await recorder.process_frame(LLMTextFrame(text="there."), FrameDirection.DOWNSTREAM)
    await recorder.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
    transcript.record.assert_awaited_once_with("assistant", "Hi there.")

    transcript.record.reset_mock()
    await recorder.process_frame(
        LLMTextFrame(text="Cut off"), FrameDirection.DOWNSTREAM
    )
    await recorder.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    await recorder.process_frame(LLMTextFrame(text="Fresh."), FrameDirection.DOWNSTREAM)
    await recorder.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
    transcript.record.assert_awaited_once_with("assistant", "Fresh.")


@pytest.mark.asyncio
async def test_transcript_writer_saves_turns_in_order():
    writer = voice._VoiceTranscript(CTX)
    with patch.object(voice, "record_voice_turn", AsyncMock()) as save:
        writer.start()
        await writer.record("user", "  find dentists\nin Austin ")
        await writer.record("assistant", "Found 12 dentists.")
        await writer.close()

    assert [call.kwargs["role"] for call in save.await_args_list] == [
        "user",
        "assistant",
    ]
    assert save.await_args_list[0].kwargs["content"] == "find dentists in Austin"
    assert save.await_args_list[0].kwargs["conversation_id"] == 5
    assert save.await_args_list[0].kwargs["organization_id"] == 42


@pytest.mark.asyncio
async def test_voice_turns_are_appended_to_the_chat_thread(monkeypatch):
    conversation = SimpleNamespace(
        id=5, organization_id=42, title=copilot_service.DEFAULT_TITLE
    )
    graph = SimpleNamespace(aupdate_state=AsyncMock())

    class _Saver:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(
        copilot_service.db_client,
        "get_copilot_conversation",
        AsyncMock(return_value=conversation),
    )
    monkeypatch.setattr(
        copilot_service.db_client,
        "copilot_checkpointer",
        lambda thread, readonly=False: _Saver(),
    )
    monkeypatch.setattr(
        copilot_service.db_client, "touch_copilot_conversation", AsyncMock()
    )
    monkeypatch.setattr(
        copilot_service, "build_copilot_graph", lambda *_args, **_kw: graph
    )

    await copilot_service.record_voice_turn(
        organization_id=42,
        user_id=9,
        conversation_id=5,
        role="user",
        content="  find dentists ",
    )
    config, update = graph.aupdate_state.await_args.args
    assert config["configurable"]["thread_id"] == "copilot:42:9:5"
    assert isinstance(update["messages"][0], HumanMessage)
    assert update["messages"][0].content == "find dentists"
    copilot_service.db_client.touch_copilot_conversation.assert_awaited_once_with(
        conversation_id=5, organization_id=42, title="find dentists"
    )

    await copilot_service.record_voice_turn(
        organization_id=42,
        user_id=9,
        conversation_id=5,
        role="assistant",
        content="Found 12 dentists.",
    )
    _, update = graph.aupdate_state.await_args.args
    assert isinstance(update["messages"][0], AIMessage)
    assert (
        copilot_service.db_client.touch_copilot_conversation.await_args.kwargs["title"]
        is None
    )


def _latency_reports():
    reports: list[dict[str, int]] = []

    async def collect(values):
        reports.append(values)

    return reports, collect


@pytest.mark.asyncio
async def test_turn_latency_starts_a_fresh_turn_on_speech_to_text():
    reports, collect = _latency_reports()
    latency = voice._TurnLatency(collect)

    await latency.mark("stt", 120)
    await latency.mark("tts", 300)
    await latency.mark("stt", 90)

    assert reports == [{"stt": 120}, {"stt": 120, "tts": 300}, {"stt": 90}]


@pytest.mark.asyncio
async def test_turn_marker_times_the_llm_first_token_once(monkeypatch):
    reports, collect = _latency_reports()
    latency = voice._TurnLatency(collect)
    marker = voice._TurnMarker(latency)
    recorder = voice._ReplyRecorder(SimpleNamespace(record=AsyncMock()), latency)
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(voice, "time", SimpleNamespace(monotonic=lambda: clock.now))

    await marker.process_frame(LLMContextFrame(LLMContext()), FrameDirection.DOWNSTREAM)
    clock.now = 100.7
    await recorder.process_frame(LLMTextFrame(text="Hello"), FrameDirection.DOWNSTREAM)
    clock.now = 100.9
    await recorder.process_frame(LLMTextFrame(text=" there"), FrameDirection.DOWNSTREAM)

    assert reports == [{"llm": 700}]


@pytest.mark.asyncio
async def test_latency_reporter_forwards_only_ttfb_from_the_speech_services():
    reports, collect = _latency_reports()
    latency = voice._TurnLatency(collect)
    reporter = voice._LatencyReporter(latency, stt_name="stt", tts_name="tts")

    frame = MetricsFrame(
        data=[
            TTFBMetricsData(processor="stt", value=0.22),
            TTFBMetricsData(processor="tts", value=0.31),
            TTFBMetricsData(processor="other", value=9.0),
            TTFBMetricsData(processor="tts", value=0.0),
            TTFAMetricsData(processor="tts", ttfa=0.4, ttfb=0.3, leading_silence=0.1),
        ]
    )
    await reporter.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert reports == [{"stt": 220}, {"stt": 220, "tts": 310}]


@pytest.mark.asyncio
async def test_turn_metrics_are_forwarded_to_the_browser():
    session = _session()

    await session._on_turn_metrics({"stt": 220, "llm": 700, "tts": 300})

    assert session.websocket.sent == [
        {"type": "metrics", "payload": {"stt": 220, "llm": 700, "tts": 300}}
    ]
    assert session.closed is False
