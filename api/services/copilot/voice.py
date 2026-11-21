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

import time
from typing import Any, Awaitable, Callable

from loguru import logger
from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
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


class VoiceConfigurationError(Exception):
    """The organization cannot run a voice session on its own connections."""


def _provider(connection: Any) -> str:
    return str(getattr(connection.provider, "value", connection.provider))


def _label(connection: Any) -> str:
    return f"{_provider(connection)} / {connection.model}"


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
) -> None:
    """Run one voice session until the peer connection closes."""
    config = await voice_configuration(ctx.organization_id)
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

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregators.user(),
            llm,
            tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )
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
