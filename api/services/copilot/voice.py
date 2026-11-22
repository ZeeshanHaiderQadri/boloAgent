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

import httpx
from loguru import logger
from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import ErrorFrame, Frame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import FunctionCallParams
from pydantic import ValidationError

from api.enums import WorkflowRunMode
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
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
from api.services.pipecat.worker_runner import (
    create_worker_runner,
    run_worker_runner,
)

from .audit import audit_tool_call
from .prompts import voice_system_prompt
from .tools import TOOLS, CopilotContext, CopilotTool

VOICE_GREETING = "Hi, this is Bolo Agent. What would you like to look up?"

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


class _VoiceErrorRelay(FrameProcessor):
    """Report the first pipeline error to the call UI.

    Speech failures arrive as non-fatal ``ErrorFrame``s, so without this the
    call stays "live" and silent instead of telling the caller what went wrong.
    """

    def __init__(self, on_error: Callable[[str], Awaitable[None]]):
        super().__init__()
        self._on_error = on_error
        self._reported: str | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, ErrorFrame):
            message = " ".join(str(frame.error or "").split())
            if message and message != self._reported:
                self._reported = message
                await self._on_error(message)
        await self.push_frame(frame, direction)


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
) -> EffectiveAIModelConfiguration:
    """Resolve and validate the BYOK services a voice session needs."""
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


def _voice_handler(
    ctx: CopilotContext, tool: CopilotTool
) -> Callable[[FunctionCallParams], Awaitable[None]]:
    async def handle(params: FunctionCallParams) -> None:
        started = time.monotonic()
        raw_arguments = dict(params.arguments or {})
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


async def run_voice_pipeline(
    webrtc_connection,
    ctx: CopilotContext,
    on_error: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """Run one voice session until the peer connection closes."""
    config = await voice_configuration(ctx.organization_id)
    await _wait_for_peer(webrtc_connection)
    audio_config = create_audio_config(WorkflowRunMode.SMALLWEBRTC.value)

    transport = await create_webrtc_transport(
        webrtc_connection, ctx.conversation_id, audio_config
    )
    stt = create_stt_service(config, audio_config)
    tts = create_tts_service(config, audio_config, organization_id=ctx.organization_id)
    llm = create_llm_service(config)

    context = LLMContext(
        messages=[
            {
                "role": "system",
                "content": voice_system_prompt(model_label=_label(config.llm)),
            }
        ],
        tools=voice_tools_schema(ctx),
    )
    aggregators = LLMContextAggregatorPair(context)

    processors = [
        transport.input(),
        stt,
        aggregators.user(),
        llm,
        tts,
    ]
    if on_error is not None:
        processors.append(_VoiceErrorRelay(on_error))
    processors.extend([transport.output(), aggregators.assistant()])

    pipeline = Pipeline(processors)
    task = create_pipeline_task(
        pipeline,
        ctx.conversation_id,
        audio_config,
        conversation_type="copilot_voice",
    )

    @task.event_handler("on_pipeline_started")
    async def _greet(_worker, _frame):
        # Speaking the greeting directly guarantees the caller hears something
        # before any model turn, and appends it so the LLM knows it greeted.
        await task.queue_frames([TTSSpeakFrame(VOICE_GREETING, append_to_context=True)])

    runner = create_worker_runner()
    await run_worker_runner(runner, task)
