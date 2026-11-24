"""Voice surface for the workspace copilot.

The caller talks to the same agent as the chat page, but over WebRTC and with
read-only tools only. Writes stay behind the chat's approval card: there is no
spoken approval path, so the voice agent explains what it would do and points
at the chat instead of changing anything.

As everywhere else in the copilot, inference is BYOK. The pipeline is built
from the organization's own text LLM, speech-to-text and voice connections;
Bolo-hosted services are refused rather than silently substituted.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from loguru import logger
from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    MetricsFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.metrics.metrics import TTFBMetricsData
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import FunctionCallParams
from pipecat.turns.user_start.transcription_user_turn_start_strategy import (
    TranscriptionUserTurnStartStrategy,
)
from pipecat.turns.user_start.vad_user_turn_start_strategy import (
    VADUserTurnStartStrategy,
)
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.utils.errors import ErrorCategory
from pydantic import ValidationError

from api.enums import WorkflowRunMode
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.avatar_profile import apply_agent_voice, get_avatar_profile
from api.services.configuration.ai_model_configuration import (
    get_resolved_ai_model_configuration,
)
from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.audio_config import create_audio_config
from api.services.pipecat.pipeline_builder import create_pipeline_task
from api.services.pipecat.service_factory import (
    create_llm_service,
    create_stt_service,
    create_tts_service,
)
from api.services.pipecat.transport_setup import create_webrtc_transport
from api.services.pipecat.wav_container_stripper import WavContainerStripper
from api.services.pipecat.worker_runner import (
    create_worker_runner,
    run_worker_runner,
)

from . import activity
from .audit import audit_tool_call
from .prompts import greeting_for, voice_system_prompt
from .service import record_voice_turn
from .tools import TOOLS, CopilotContext, CopilotTool
from .voice_language import apply_voice_language

# Speech endpoints that expose an OpenAI-style ``/models`` list, so a
# misconfigured model name can be caught before the call starts.
_PROBED_SPEECH_PROVIDERS = {
    ServiceProviders.SPEACHES.value,
    ServiceProviders.OPENAI.value,
}

# The turn pipeline greets immediately. Starting it before ICE finishes writes
# audio into a connection that is still negotiating, and ten failed writes
# cancel the pipeline.
PEER_CONNECT_TIMEOUT_SECS = 20.0

# TTS playout gives up on an audio context after this much silence. The default
# is 3s, which a routed or self-hosted TTS can exceed while it synthesizes: the
# context closes before its first audio exists and the audio that arrives later
# is dropped, so the caller hears the opening sentence and then nothing.
VOICE_TTS_IDLE_TIMEOUT_SECS = 15.0


class VoiceConfigurationError(Exception):
    """The organization cannot run a voice session on its own connections."""


def _provider(connection: Any) -> str:
    return str(getattr(connection.provider, "value", connection.provider))


def _label(connection: Any) -> str:
    return f"{_provider(connection)} / {connection.model}"


async def _wait_for_peer(
    connection: Any, timeout: float = PEER_CONNECT_TIMEOUT_SECS
) -> None:
    """Hold the pipeline until the peer connection is up.

    The greeting is spoken as soon as the pipeline starts. Starting it while
    ICE is still gathering writes audio into a connection that is not ready,
    and the output transport cancels the pipeline after ten failed writes.
    """
    if connection.is_connected():
        return
    connected = asyncio.Event()

    @connection.event_handler("connected")
    async def _on_connected(_connection):
        connected.set()

    try:
        await asyncio.wait_for(connected.wait(), timeout=timeout)
    except TimeoutError as exc:
        raise VoiceConfigurationError(
            "The call reached the server but its audio channel did not open. "
            "Check your network and try again."
        ) from exc


def _is_permanent_error(frame: ErrorFrame) -> bool:
    if frame.fatal:
        return True
    return frame.category is not None and frame.category.is_permanent


class _VoiceErrorRelay(FrameProcessor):
    """Report pipeline failures to the call UI.

    Speech failures arrive as ``ErrorFrame``s whether or not the service can
    recover. Ending the call for a transient one drops a session that would
    have answered on the next turn, so only errors that cannot clear on their
    own end it; the rest become a warning, so the caller learns why the agent
    went quiet without losing the call.
    """

    def __init__(
        self,
        on_error: Callable[[str], Awaitable[None]],
        on_warning: Callable[[str], Awaitable[None]] | None = None,
    ):
        super().__init__()
        self._on_error = on_error
        self._on_warning = on_warning
        self._reported: set[str] = set()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, ErrorFrame):
            message = " ".join(str(frame.error or "").split())
            if message and message not in self._reported:
                self._reported.add(message)
                if _is_permanent_error(frame):
                    await self._on_error(message)
                else:
                    logger.warning(
                        "Copilot voice pipeline error (recoverable): {}", message
                    )
                    if self._on_warning is not None:
                        await self._on_warning(
                            voice_warning_message(message, frame.category)
                        )
        await self.push_frame(frame, direction)


class _VoiceTranscript:
    """Writes finished turns into the conversation's chat transcript.

    Turns are queued and written in order by one task so a DB hiccup cannot
    stall the audio path and two turns cannot race for the same checkpoint.
    """

    def __init__(self, ctx: CopilotContext):
        self._ctx = ctx
        self._turns: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._drain(), name="copilot_voice_transcript")

    async def record(self, role: str, text: str) -> None:
        text = " ".join(text.split())
        if text:
            await self._turns.put((role, text))

    async def _drain(self) -> None:
        while True:
            turn = await self._turns.get()
            if turn is None:
                return
            role, text = turn
            try:
                await record_voice_turn(
                    organization_id=self._ctx.organization_id,
                    user_id=self._ctx.user_id,
                    conversation_id=self._ctx.conversation_id,
                    role=role,
                    content=text,
                )
            except Exception:
                logger.exception(
                    "Could not save a voice turn for conversation {}",
                    self._ctx.conversation_id,
                )

    async def close(self) -> None:
        if self._task is None:
            return
        await self._turns.put(None)
        await self._task
        self._task = None


class _UserSpeechRecorder(FrameProcessor):
    """Saves the caller's final transcripts as chat messages."""

    def __init__(self, transcript: _VoiceTranscript):
        super().__init__()
        self._transcript = transcript

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            await self._transcript.record("user", frame.text)
        await self.push_frame(frame, direction)


class _TurnLatency:
    """Collects one turn's service timings and forwards each update.

    A new transcription (``stt``) starts a turn, so the caller's next utterance
    does not inherit the previous reply's numbers. Everything else only adds to
    the current turn.
    """

    def __init__(self, on_update: Callable[[dict[str, int]], Awaitable[None]] | None):
        self._on_update = on_update
        self._values: dict[str, int] = {}
        self.llm_requested_at: float | None = None

    async def mark(self, kind: str, value_ms: int) -> None:
        if kind == "stt":
            self._values = {}
        self._values[kind] = value_ms
        if self._on_update is not None:
            try:
                await self._on_update(dict(self._values))
            except Exception as exc:
                logger.debug(f"Could not report voice latency: {exc}")

    async def mark_llm_first_token(self) -> None:
        started = self.llm_requested_at
        self.llm_requested_at = None
        if started is not None:
            await self.mark("llm", int((time.monotonic() - started) * 1000))


class _TurnMarker(FrameProcessor):
    """Stamps the moment the user's turn is handed to the LLM."""

    def __init__(self, latency: _TurnLatency):
        super().__init__()
        self._latency = latency

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            self._latency.llm_requested_at = time.monotonic()
        await self.push_frame(frame, direction)


class _ReplyRecorder(FrameProcessor):
    """Saves each completed spoken reply and times the LLM's first token."""

    def __init__(self, transcript: _VoiceTranscript, latency: _TurnLatency):
        super().__init__()
        self._transcript = transcript
        self._latency = latency
        self._parts: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame):
            # A new reply starts: drop anything left by an interrupted one.
            self._parts.clear()
        elif isinstance(frame, LLMTextFrame):
            if self._latency.llm_requested_at is not None:
                await self._latency.mark_llm_first_token()
            self._parts.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            text = "".join(self._parts)
            self._parts.clear()
            await self._transcript.record("assistant", text)
        await self.push_frame(frame, direction)


class _LatencyReporter(FrameProcessor):
    """Forward STT and TTS time-to-first-byte as they pass toward the output.

    The services emit their metrics downstream, so sitting just before the
    transport sees each service's measurement without becoming a second source
    of truth for the numbers.
    """

    def __init__(self, latency: _TurnLatency, *, stt_name: str, tts_name: str):
        super().__init__()
        self._latency = latency
        self._kinds = {stt_name: "stt", tts_name: "tts"}

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, MetricsFrame):
            for data in frame.data:
                kind = self._kinds.get(getattr(data, "processor", ""))
                if kind is None or not isinstance(data, TTFBMetricsData):
                    continue
                if data.value > 0:
                    await self._latency.mark(kind, int(data.value * 1000))
        await self.push_frame(frame, direction)


def voice_warning_message(error: str, category: ErrorCategory | None = None) -> str:
    """A short, actionable note for a speech failure that did not end the call."""
    text = " ".join(str(error or "").split())
    if category in (ErrorCategory.RATE_LIMIT, ErrorCategory.QUOTA):
        # Retries against an exhausted provider are what make the reply late
        # and fragmented, so name the quota instead of the symptom.
        return (
            "The speech provider is rate-limited or out of quota, so replies "
            "are late, cut short, or missing. Raise the provider's quota or "
            "switch the speech connection in Model connections. "
            f"({text[:220]})"
        )
    return (
        "A speech service failed, so the agent may have missed or skipped a "
        "reply. Check the speech connections in Model connections. "
        f"({text[:220]})"
    )


def voice_error_message(error: str) -> str:
    """A short, actionable sentence for the call UI."""
    text = " ".join(str(error or "").split())
    lowered = text.lower()
    if "speech error" in lowered or "audio model" in lowered:
        return (
            "The voice provider rejected speech synthesis. Check the TTS model "
            f"in Model connections. ({text[:220]})"
        )
    return f"The voice session stopped on an audio error. ({text[:220]})"


async def _speech_problem(role: str, connection: Any) -> str | None:
    """Why a self-hosted speech endpoint would leave the call silent, if it would.

    The readiness check used to verify presence only, so an unreachable
    Speaches endpoint produced a connected call with no audio and no clue. We
    now probe any custom OpenAI-compatible endpoint and require it to serve the
    configured model, which is how a router rejecting the model gets caught
    before the call instead of during it.
    """
    if _provider(connection) not in _PROBED_SPEECH_PROVIDERS:
        return None
    base_url = (getattr(connection, "base_url", None) or "").rstrip("/")
    if not base_url:
        return None
    key = getattr(connection, "api_key", None)
    if isinstance(key, list):
        key = key[0] if key else None
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{base_url}/models", headers=headers)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        host = urlsplit(base_url).hostname
        hint = (
            " If Bolo runs in Docker and the service on your machine, use "
            "host.docker.internal instead of localhost."
            if host in {"localhost", "127.0.0.1", "0.0.0.0"}
            else ""
        )
        return f"The {role} service does not answer at {base_url}.{hint}"

    model = getattr(connection, "model", None)
    if not model or model == "auto":
        return None
    items = payload.get("data", []) if isinstance(payload, dict) else []
    offered = {
        str(item["id"]) for item in items if isinstance(item, dict) and item.get("id")
    }
    if offered and model not in offered:
        return (
            f"The {role} endpoint at {base_url} does not serve model "
            f"'{model}'. Pick 'auto' or a model this endpoint lists in Model "
            "connections."
        )
    return None


async def voice_configuration(
    organization_id: int,
    user_id: int | None = None,
) -> EffectiveAIModelConfiguration:
    """Resolve and validate the BYOK services a voice session needs.

    With ``user_id``, the caller's avatar voice is applied when it matches the
    organization's TTS provider. Workflows never pass a user, so they keep the
    configured voice.
    """
    resolved = await get_resolved_ai_model_configuration(
        organization_id=organization_id
    )
    config = resolved.effective

    if config.is_realtime:
        raise VoiceConfigurationError(
            "Bolo Agent voice needs a text LLM with speech-to-text and voice "
            "providers. Your default is a realtime model; choose a text model "
            "in Model connections."
        )
    if config.llm is None:
        raise VoiceConfigurationError(
            "Bolo Agent voice needs your own text LLM. Add a provider "
            "connection in Model connections."
        )
    if config.stt is None:
        raise VoiceConfigurationError(
            "Bolo Agent voice needs a speech-to-text provider. Add one in "
            "Model connections."
        )
    if config.tts is None:
        raise VoiceConfigurationError(
            "Bolo Agent voice needs a voice provider for speech. Add one in "
            "Model connections."
        )
    for label, connection in (
        ("LLM", config.llm),
        ("speech-to-text", config.stt),
        ("voice", config.tts),
    ):
        if _provider(connection) == ServiceProviders.BOLO.value:
            raise VoiceConfigurationError(
                f"Bolo Agent voice runs on your own {label} connection, not "
                "Bolo-hosted services. Add your provider keys in Model "
                "connections."
            )
    problems = [
        problem
        for problem in await asyncio.gather(
            _speech_problem("speech-to-text", config.stt),
            _speech_problem("voice", config.tts),
        )
        if problem
    ]
    if problems:
        raise VoiceConfigurationError(problems[0])
    if user_id is not None:
        config = apply_agent_voice(config, await get_avatar_profile(user_id))
    return config


def voice_status(config: EffectiveAIModelConfiguration) -> dict[str, str]:
    """Labels for the readiness endpoint. Never includes keys."""
    return {
        "llm": _label(config.llm),
        "stt": _label(config.stt),
        "tts": _label(config.tts),
    }


def voice_tools() -> tuple[CopilotTool, ...]:
    """Every read tool; writes are chat-only because they need approval."""
    return tuple(tool for tool in TOOLS.values() if not tool.requires_approval)


async def _record_voice_activity(
    ctx: CopilotContext,
    turn_id: str,
    kind: str,
    tool: CopilotTool,
    *,
    status: str | None = None,
    arguments: Any = None,
    result: Any = None,
    error: str | None = None,
    duration_ms: int | None = None,
) -> None:
    await activity.record(
        organization_id=ctx.organization_id,
        conversation_id=ctx.conversation_id,
        turn_id=turn_id,
        kind=kind,
        tool_name=tool.name,
        risk=tool.risk,
        status=status,
        arguments=arguments,
        result=result,
        error=error,
        duration_ms=duration_ms,
    )


def _voice_handler(
    ctx: CopilotContext, tool: CopilotTool
) -> Callable[[FunctionCallParams], Awaitable[None]]:
    async def handle(params: FunctionCallParams) -> None:
        started = time.monotonic()
        raw_arguments = dict(params.arguments or {})
        # The workspace pane follows this conversation's activity events, so a
        # spoken lookup has to publish the same rows as a typed one for the
        # page it reads to open mid-call. One synthetic turn per tool call
        # keeps running/finished state self-contained.
        turn_id = f"voice-{uuid4().hex[:16]}"
        await activity.record(
            organization_id=ctx.organization_id,
            conversation_id=ctx.conversation_id,
            turn_id=turn_id,
            kind=activity.TURN_STARTED,
        )
        await _record_voice_activity(
            ctx, turn_id, activity.TOOL_STARTED, tool, arguments=raw_arguments
        )

        async def finish(
            status: str, *, error: str | None = None, result: Any = None
        ) -> None:
            await _record_voice_activity(
                ctx,
                turn_id,
                activity.TOOL_FINISHED,
                tool,
                status=status,
                arguments=raw_arguments,
                result=result,
                error=error,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            await activity.record(
                organization_id=ctx.organization_id,
                conversation_id=ctx.conversation_id,
                turn_id=turn_id,
                kind=activity.TURN_FINISHED,
                status="error" if status == "error" else "ok",
            )

        try:
            arguments = tool.args_schema.model_validate(raw_arguments)
        except ValidationError as exc:
            await audit_tool_call(
                ctx,
                tool.name,
                tool.risk,
                "error",
                raw_arguments,
                error="validation",
            )
            await finish("error", error="Invalid arguments")
            await params.result_callback(
                {
                    "error": (
                        f"Invalid arguments: {exc.error_count()} problem(s). "
                        "Ask the caller for the missing detail."
                    )
                }
            )
            return

        try:
            result = await tool.handler(ctx, arguments)
        except ValueError as exc:
            await audit_tool_call(
                ctx, tool.name, tool.risk, "error", raw_arguments, error=str(exc)
            )
            await finish("error", error=str(exc))
            await params.result_callback({"error": str(exc)})
            return
        except Exception:
            logger.exception(
                "Copilot voice tool {} failed for conversation {}",
                tool.name,
                ctx.conversation_id,
            )
            await audit_tool_call(
                ctx,
                tool.name,
                tool.risk,
                "error",
                raw_arguments,
                error="The tool failed before completing.",
            )
            await finish("error", error="The tool failed before completing.")
            await params.result_callback(
                {"error": "The lookup failed before completing."}
            )
            return

        await audit_tool_call(
            ctx,
            tool.name,
            tool.risk,
            "executed",
            raw_arguments,
            result=result,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        await finish("executed", result=result)
        await params.result_callback(result)

    return handle


def voice_tools_schema(ctx: CopilotContext) -> ToolsSchema:
    """Advertise read tools with handlers bound to this conversation."""
    schemas: list[FunctionSchema] = []
    for tool in voice_tools():
        schema = tool.args_schema.model_json_schema()
        schemas.append(
            FunctionSchema(
                name=tool.name,
                description=(
                    f"{tool.description} Speak the answer in one or two short "
                    "sentences."
                ),
                properties=schema.get("properties", {}),
                required=schema.get("required", []),
                handler=_voice_handler(ctx, tool),
            )
        )
    return ToolsSchema(standard_tools=schemas)


def voice_user_turn_params() -> LLMUserAggregatorParams:
    """Aggregator settings for caller turns.

    Whisper-style STT transcribes only between VAD speech events, so the
    aggregator owns VAD; without it the caller's speech never becomes a
    transcript and the agent answers nothing after its greeting.
    """
    return LLMUserAggregatorParams(
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
        user_turn_strategies=UserTurnStrategies(
            start=[
                TranscriptionUserTurnStartStrategy(),
                VADUserTurnStartStrategy(),
            ],
            stop=[SpeechTimeoutUserTurnStopStrategy()],
        ),
    )


async def run_voice_pipeline(
    webrtc_connection,
    ctx: CopilotContext,
    on_error: Callable[[str], Awaitable[None]] | None = None,
    on_warning: Callable[[str], Awaitable[None]] | None = None,
    on_metrics: Callable[[dict[str, int]], Awaitable[None]] | None = None,
) -> None:
    """Run one voice session until the peer connection closes."""
    config = await voice_configuration(ctx.organization_id, ctx.user_id)
    config, language_warning = apply_voice_language(config, ctx.language)
    if language_warning and on_warning is not None:
        await on_warning(language_warning)
    audio_config = create_audio_config(WorkflowRunMode.SMALLWEBRTC.value)

    transport = await create_webrtc_transport(
        webrtc_connection, ctx.conversation_id, audio_config
    )
    stt = create_stt_service(config, audio_config)
    tts = create_tts_service(
        config,
        audio_config,
        organization_id=ctx.organization_id,
        stop_frame_timeout_s=VOICE_TTS_IDLE_TIMEOUT_SECS,
    )
    llm = create_llm_service(config)

    context = LLMContext(
        messages=[
            {
                "role": "system",
                "content": voice_system_prompt(
                    model_label=_label(config.llm), language=ctx.language
                ),
            }
        ],
        tools=voice_tools_schema(ctx),
    )
    # Whisper-style STT only transcribes between VAD speech events, so the
    # aggregator must run its own VAD or the caller's speech never becomes a
    # transcript and the agent answers nothing after its greeting.
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=voice_user_turn_params(),
    )
    transcript = _VoiceTranscript(ctx)
    latency = _TurnLatency(on_metrics)

    processors = [transport.input()]
    if on_error is not None:
        # Errors travel upstream, so the relay must sit below the services it
        # watches: STT and LLM failures from here never reach the output.
        processors.append(_VoiceErrorRelay(on_error, on_warning))
    processors.extend(
        [
            stt,
            _UserSpeechRecorder(transcript),
            aggregators.user(),
            _TurnMarker(latency),
            llm,
            _ReplyRecorder(transcript, latency),
            tts,
            # Routers that ignore ``response_format=pcm`` answer with a WAV
            # file, whose header and trailer are audible when played as samples.
            WavContainerStripper(),
            _LatencyReporter(latency, stt_name=stt.name, tts_name=tts.name),
            transport.output(),
            aggregators.assistant(),
        ]
    )

    pipeline = Pipeline(processors)
    task = create_pipeline_task(
        pipeline,
        ctx.conversation_id,
        audio_config,
        conversation_type="copilot_voice",
        # The caller may read the transcript or think for minutes between
        # turns. The peer connection and signaling socket already end the
        # session when the call does, so the idle watchdog only cuts it short.
        idle_timeout_secs=None,
    )

    @task.event_handler("on_pipeline_started")
    async def _greet(_worker, _frame):
        # The transport's connect() runs as the StartFrame reaches the output,
        # and only then does the connection's "connected" event exist. Waiting
        # here keeps the greeting from being written into a peer that is still
        # negotiating, which the output cancels after ten failed writes.
        try:
            await _wait_for_peer(webrtc_connection)
        except VoiceConfigurationError as exc:
            if on_error is not None:
                await on_error(str(exc))
            return
        # Speaking the greeting directly guarantees the caller hears something
        # before any model turn, and appends it so the LLM knows it greeted.
        await task.queue_frames(
            [TTSSpeakFrame(greeting_for(ctx.language), append_to_context=True)]
        )

    transcript.start()
    runner = create_worker_runner()
    try:
        await run_worker_runner(runner, task)
    finally:
        await transcript.close()
