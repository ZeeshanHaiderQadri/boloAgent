"""LangGraph agent for the workspace copilot.

One agent node, one tool node. Read tools run inline; write tools pause the
graph with an ``interrupt`` before any side effect, so the resume decision is
bound to the exact arguments the user saw. The same patterns (durable
checkpoints, approval interrupts, replay-safe turns) are used by the workflow
builder agent, which is why both share a checkpointer.
"""

from __future__ import annotations

import json
import time
from typing import Annotated

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from loguru import logger
from pydantic import ValidationError
from typing_extensions import TypedDict

from .audit import audit_tool_call
from .model import (
    CopilotChatModel,
    CopilotConfigurationError,
    CopilotUpstreamError,
)
from .prompts import system_prompt
from .tools import TOOLS, CopilotContext, CopilotTool

MAX_TOOL_RESULT_CHARACTERS = 8000
MAX_TOOL_MESSAGE_CHARACTERS = 6000
HISTORY_WINDOW = 60
WEB_TOOL_NAMES = frozenset({"web_search", "web_fetch"})


class CopilotState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    organization_id: int
    user_id: int
    conversation_id: int
    # A model failure is converted into a normal assistant turn so the graph
    # never stops with a pending node. The service turns it into an HTTP error.
    model_error: str | None
    model_error_kind: str | None


COPILOT_ERROR_FLAG = "copilot_error"
# Failures become assistant messages so the graph ends cleanly, but replaying
# them would teach the model that it is offline. Checkpoints written before
# the flag existed are matched by their canned opening words instead.
MODEL_ERROR_PREFIXES = (
    "Your LLM provider",
    "Could not reach your LLM provider",
    "The copilot needs your own LLM",
    "The copilot could not reach your LLM",
    "The copilot runs on your own LLM key",
    "Add an API key for your LLM provider",
    "Add your Azure OpenAI endpoint",
    "No chat endpoint is configured",
    "Vertex AI connections are not supported",
)


def is_model_error_message(message: AnyMessage) -> bool:
    if not isinstance(message, AIMessage):
        return False
    if message.additional_kwargs.get(COPILOT_ERROR_FLAG):
        return True
    text = message.content if isinstance(message.content, str) else ""
    return text.startswith(MODEL_ERROR_PREFIXES)


def _bounded_history(messages: list[AnyMessage]) -> list[AnyMessage]:
    """Keep a valid provider sequence: start the window at a user turn."""
    messages = [message for message in messages if not is_model_error_message(message)]
    window = messages[-HISTORY_WINDOW:]
    start = next(
        (
            index
            for index, message in enumerate(window)
            if isinstance(message, HumanMessage)
        ),
        0,
    )
    bounded: list[AnyMessage] = []
    for message in window[start:]:
        if isinstance(message, ToolMessage) and isinstance(message.content, str):
            if len(message.content) > MAX_TOOL_MESSAGE_CHARACTERS:
                message = ToolMessage(
                    content=message.content[:MAX_TOOL_MESSAGE_CHARACTERS]
                    + "…[truncated; ask for a narrower query if you need more]",
                    tool_call_id=message.tool_call_id,
                    name=message.name,
                )
        bounded.append(message)
    return bounded


def _active_tools(web_enabled: bool) -> list[CopilotTool]:
    return [
        tool
        for name, tool in TOOLS.items()
        if web_enabled or name not in WEB_TOOL_NAMES
    ]


def _lc_tools(web_enabled: bool) -> list[StructuredTool]:
    """Schemas for the model. Execution goes through ``_run_tool`` instead."""

    def make(tool: CopilotTool) -> StructuredTool:
        async def unused(**_kwargs):
            raise RuntimeError("Copilot tools execute through the tools node")

        return StructuredTool.from_function(
            coroutine=unused,
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
        )

    return [make(tool) for tool in _active_tools(web_enabled)]


def _approval_payload(calls: list[dict]) -> dict:
    return {
        "kind": "tool_approval",
        "calls": [
            {
                "tool": call["name"],
                "risk": TOOLS[call["name"]].risk,
                "arguments": call.get("args") or {},
            }
            for call in calls
        ],
    }


async def _run_tool(ctx: CopilotContext, tool: CopilotTool, call: dict) -> ToolMessage:
    started = time.monotonic()
    try:
        arguments = tool.args_schema.model_validate(call.get("args") or {})
    except ValidationError as exc:
        message = (
            f"Invalid arguments: {exc.error_count()} problem(s). Check the schema."
        )
        await audit_tool_call(
            ctx,
            tool.name,
            tool.risk,
            "error",
            call.get("args") or {},
            error="validation",
        )
        return ToolMessage(content=message, tool_call_id=call["id"], name=tool.name)

    try:
        result = await tool.handler(ctx, arguments)
        status = "executed"
        error = None
    except ValueError as exc:
        result = None
        status = "error"
        error = str(exc)
    except Exception:
        logger.exception(
            "Copilot tool {} failed for conversation {}",
            tool.name,
            ctx.conversation_id,
        )
        result = None
        status = "error"
        error = "The tool failed before completing."

    duration_ms = int((time.monotonic() - started) * 1000)
    await audit_tool_call(
        ctx,
        tool.name,
        tool.risk,
        status,
        call.get("args") or {},
        result=result,
        error=error,
        duration_ms=duration_ms,
    )
    if status == "error":
        content = (
            f"{error or 'The tool failed.'} Tell the user the action did not "
            "complete and do not claim otherwise."
        )
    else:
        content = json.dumps(result, default=str)
        if len(content) > MAX_TOOL_RESULT_CHARACTERS:
            content = (
                content[:MAX_TOOL_RESULT_CHARACTERS]
                + "…[truncated; narrow the query for the rest]"
            )
    return ToolMessage(content=content, tool_call_id=call["id"], name=tool.name)


def _tools_node(web_enabled: bool):
    async def run(state: CopilotState) -> dict:
        last = state["messages"][-1]
        calls = list(getattr(last, "tool_calls", None) or [])
        ctx = CopilotContext(
            organization_id=state["organization_id"],
            user_id=state["user_id"],
            conversation_id=state["conversation_id"],
        )

        gated = [
            call
            for call in calls
            if call["name"] in TOOLS and TOOLS[call["name"]].requires_approval
        ]
        approved = True
        if gated:
            # Interrupt before any execution in this turn, so replaying the
            # node after resume cannot duplicate a side effect.
            decision = interrupt(_approval_payload(gated))
            approved = (
                isinstance(decision, dict) and decision.get("decision") == "approve"
            )

        results: list[ToolMessage] = []
        for call in calls:
            tool = TOOLS.get(call["name"])
            if tool is None or (tool.name in WEB_TOOL_NAMES and not web_enabled):
                results.append(
                    ToolMessage(
                        content=(
                            f"Unknown tool {call['name']!r}. Available: "
                            + ", ".join(t.name for t in _active_tools(web_enabled))
                        ),
                        tool_call_id=call["id"],
                        name=call["name"],
                    )
                )
                continue
            if tool.requires_approval and not approved:
                await audit_tool_call(
                    ctx,
                    tool.name,
                    tool.risk,
                    "declined",
                    call.get("args") or {},
                )
                results.append(
                    ToolMessage(
                        content=(
                            "The user declined this action, so nothing changed. "
                            "Do not retry it; ask what they want instead."
                        ),
                        tool_call_id=call["id"],
                        name=tool.name,
                    )
                )
                continue
            results.append(await _run_tool(ctx, tool, call))
        return {"messages": results}

    return run


def _agent_node(model: CopilotChatModel, bound_model, web_providers: list[str]):
    async def run(state: CopilotState) -> dict:
        try:
            label = await model.describe()
        except Exception:
            label = "your provider"
        try:
            reply = await bound_model.ainvoke(
                [
                    SystemMessage(
                        content=system_prompt(
                            model_label=label,
                            web_access=", ".join(web_providers) or None,
                        )
                    ),
                    *_bounded_history(state["messages"]),
                ]
            )
        except CopilotConfigurationError as exc:
            return {
                "messages": [
                    AIMessage(
                        content=str(exc), additional_kwargs={COPILOT_ERROR_FLAG: True}
                    )
                ],
                "model_error": str(exc),
                "model_error_kind": "configuration",
            }
        except CopilotUpstreamError as exc:
            return {
                "messages": [
                    AIMessage(
                        content=str(exc), additional_kwargs={COPILOT_ERROR_FLAG: True}
                    )
                ],
                "model_error": str(exc),
                "model_error_kind": "upstream",
            }
        return {"messages": [reply], "model_error": None, "model_error_kind": None}

    return run


def _route(state: CopilotState) -> str:
    if state.get("model_error"):
        return END
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else END


def build_copilot_graph(
    model: CopilotChatModel,
    checkpointer,
    *,
    web_providers: list[str] | None = None,
):
    providers = list(web_providers or [])
    graph = StateGraph(CopilotState)
    bound_model = model.bind_tools(_lc_tools(bool(providers)))
    graph.add_node("agent", _agent_node(model, bound_model, providers))
    graph.add_node("tools", _tools_node(bool(providers)))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", _route, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)
