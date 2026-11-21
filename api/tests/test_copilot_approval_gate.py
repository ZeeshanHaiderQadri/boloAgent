"""Tests for the copilot's approval gate around write tools.

Runs the real compiled graph with an in-memory checkpointer and a fake model,
so the interrupt/resume path is exercised without a provider or database.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel

from api.services.copilot import agent as agent_module
from api.services.copilot.tools import RISK_READ, RISK_WRITE, CopilotTool


class _Args(BaseModel):
    lead_ids: list[int] = []


def _write_tool() -> CopilotTool:
    return CopilotTool(
        name="tag_test",
        description="Tag leads",
        args_schema=_Args,
        risk=RISK_WRITE,
        handler=AsyncMock(return_value={"updated": 1}),
    )


def _read_tool() -> CopilotTool:
    return CopilotTool(
        name="read_test",
        description="Read something",
        args_schema=_Args,
        risk=RISK_READ,
        handler=AsyncMock(return_value={"ok": True}),
    )


class _BoundModel:
    def __init__(self, replies):
        self.replies = replies

    async def ainvoke(self, messages):
        return self.replies.pop(0)


class _FakeModel:
    def __init__(self, replies):
        self.replies = list(replies)

    def bind_tools(self, tools):
        return _BoundModel(self.replies)

    async def describe(self):
        return "fake / model"


def _initial_state():
    return {
        "messages": [HumanMessage(content="tag lead 1")],
        "organization_id": 11,
        "user_id": 22,
        "conversation_id": 33,
    }


@asynccontextmanager
async def _copilot(replies, tools):
    with (
        patch.object(agent_module, "TOOLS", tools),
        patch.object(agent_module, "db_client") as db,
    ):
        db.record_copilot_tool_call = AsyncMock()
        graph = agent_module.build_copilot_graph(_FakeModel(replies), InMemorySaver())
        config = {"configurable": {"thread_id": "thread-1"}}
        await graph.ainvoke(_initial_state(), config)
        yield graph, config, db


@pytest.mark.asyncio
async def test_write_tool_pauses_before_any_side_effect():
    write = _write_tool()
    async with _copilot(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "tag_test", "args": {"lead_ids": [1]}, "id": "c1"}
                ],
            ),
            AIMessage(content="Waiting for your approval."),
        ],
        {"tag_test": write},
    ) as (graph, config, _):
        state = await graph.aget_state(config)

    assert len(state.interrupts) == 1
    assert state.interrupts[0].value["kind"] == "tool_approval"
    assert state.interrupts[0].value["calls"] == [
        {"tool": "tag_test", "risk": RISK_WRITE, "arguments": {"lead_ids": [1]}}
    ]
    write.handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_declining_never_runs_the_write_and_is_audited():
    write = _write_tool()
    async with _copilot(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "tag_test", "args": {"lead_ids": [1]}, "id": "c1"}
                ],
            ),
            AIMessage(content="Okay, nothing changed."),
        ],
        {"tag_test": write},
    ) as (graph, config, db):
        state = await graph.aget_state(config)
        approval_id = state.interrupts[0].id
        await graph.ainvoke(
            Command(resume={approval_id: {"decision": "decline"}}), config
        )
        final = await graph.aget_state(config)
        statuses = [
            call.kwargs["status"]
            for call in db.record_copilot_tool_call.await_args_list
        ]

    write.handler.assert_not_awaited()
    assert statuses == ["declined"]
    assert final.interrupts == ()


@pytest.mark.asyncio
async def test_approving_runs_the_write_with_the_authenticated_context():
    write = _write_tool()
    async with _copilot(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "tag_test", "args": {"lead_ids": [1]}, "id": "c1"}
                ],
            ),
            AIMessage(content="Tagged 1 lead."),
        ],
        {"tag_test": write},
    ) as (graph, config, db):
        state = await graph.aget_state(config)
        approval_id = state.interrupts[0].id
        await graph.ainvoke(
            Command(resume={approval_id: {"decision": "approve"}}), config
        )
        statuses = [
            call.kwargs["status"]
            for call in db.record_copilot_tool_call.await_args_list
        ]

    write.handler.assert_awaited_once()
    ctx, args = write.handler.await_args.args
    assert ctx.organization_id == 11
    assert ctx.conversation_id == 33
    assert args.lead_ids == [1]
    assert statuses == ["executed"]


@pytest.mark.asyncio
async def test_read_tools_run_without_an_interrupt():
    read = _read_tool()
    async with _copilot(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "read_test", "args": {"lead_ids": []}, "id": "c1"}
                ],
            ),
            AIMessage(content="Here is what I found."),
        ],
        {"read_test": read},
    ) as (graph, config, _):
        state = await graph.aget_state(config)

    assert state.interrupts == ()
    read.handler.assert_awaited_once()
