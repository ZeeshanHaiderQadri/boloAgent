"""Activity log behind the agent workspace: summarization, scoping, events."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel

from api.services.copilot import activity, service
from api.services.copilot import agent as agent_module
from api.services.copilot.model import CopilotUpstreamError
from api.services.copilot.tools import RISK_READ, RISK_WRITE, CopilotTool


class _Args(BaseModel):
    lead_ids: list[int] = []


def _read_tool(handler=None) -> CopilotTool:
    return CopilotTool(
        name="read_test",
        description="Read something",
        args_schema=_Args,
        risk=RISK_READ,
        handler=handler or AsyncMock(return_value={"ok": True}),
    )


def _write_tool() -> CopilotTool:
    return CopilotTool(
        name="tag_test",
        description="Tag leads",
        args_schema=_Args,
        risk=RISK_WRITE,
        handler=AsyncMock(return_value={"updated": 1}),
    )


class _BoundModel:
    def __init__(self, replies):
        self.replies = replies

    async def ainvoke(self, messages):
        if isinstance(self.replies, Exception):
            raise self.replies
        return self.replies.pop(0)


class _FakeModel:
    def __init__(self, replies):
        self.replies = list(replies)

    def bind_tools(self, tools):
        return _BoundModel(self.replies)

    async def describe(self):
        return "fake / model"


class _FailingModel(_FakeModel):
    def bind_tools(self, tools):
        return _BoundModel(CopilotUpstreamError("The provider is unavailable."))


def test_summarize_truncates_long_strings():
    payload = activity.summarize({"text": "x" * (activity.MAX_STRING_CHARS + 50)})
    assert payload["text"].endswith("…")
    assert len(payload["text"]) == activity.MAX_STRING_CHARS + 1


def test_summarize_caps_lists_and_reports_the_remainder():
    payload = activity.summarize(list(range(activity.MAX_LIST_ITEMS + 5)))
    assert len(payload) == activity.MAX_LIST_ITEMS + 1
    assert payload[-1] == "…5 more"


def test_summarize_replaces_an_oversized_payload_with_a_preview():
    huge = {f"k{index}": "y" * 800 for index in range(50)}
    payload = activity.summarize(huge)
    assert payload["truncated"] is True
    assert len(payload["preview"]) == activity.MAX_SERIALIZED_CHARS


@pytest.mark.asyncio
async def test_activity_rows_are_org_scoped_and_cursor_paginated(db_session):
    user, _ = await db_session.get_or_create_user_by_provider_id(str(uuid4()))
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        str(uuid4()), user.id
    )
    other_org, _ = await db_session.get_or_create_organization_by_provider_id(
        str(uuid4()), user.id
    )
    conversation = await db_session.create_copilot_conversation(
        organization_id=org.id, user_id=user.id
    )
    first = await db_session.record_copilot_activity(
        organization_id=org.id,
        conversation_id=conversation.id,
        turn_id="turn-1",
        kind=activity.TURN_STARTED,
    )
    second = await db_session.record_copilot_activity(
        organization_id=org.id,
        conversation_id=conversation.id,
        turn_id="turn-1",
        kind=activity.TOOL_STARTED,
        tool_name="search_leads",
        risk=RISK_READ,
        arguments={"query": "dentists"},
    )
    await db_session.record_copilot_activity(
        organization_id=other_org.id,
        conversation_id=conversation.id,
        turn_id="turn-1",
        kind=activity.TURN_FINISHED,
        status="idle",
    )

    rows = await db_session.list_copilot_activity(conversation.id, org.id)
    assert [row.id for row in rows] == [first.id, second.id]

    newer = await db_session.list_copilot_activity(
        conversation.id, org.id, after_id=first.id
    )
    assert [row.kind for row in newer] == [activity.TOOL_STARTED]
    assert newer[0].arguments == {"query": "dentists"}


@pytest.mark.asyncio
async def test_tool_events_record_start_and_finish_with_the_turn_id():
    read = _read_tool()
    record = AsyncMock()
    with (
        patch.object(agent_module, "TOOLS", {"read_test": read}),
        patch.object(agent_module, "audit_tool_call", AsyncMock()),
        patch.object(agent_module.activity, "record", record),
    ):
        graph = agent_module.build_copilot_graph(
            _FakeModel(
                [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "read_test",
                                "args": {"lead_ids": [2]},
                                "id": "c1",
                            }
                        ],
                    ),
                    AIMessage(content="Found it."),
                ]
            ),
            InMemorySaver(),
        )
        await _wait(
            graph.ainvoke(
                {
                    "messages": [HumanMessage(content="hi")],
                    "organization_id": 11,
                    "user_id": 22,
                    "conversation_id": 33,
                    "turn_id": "turn-1",
                },
                {"configurable": {"thread_id": "thread-tools"}},
            )
        )

    calls = record.await_args_list
    assert [call.kwargs["kind"] for call in calls] == [
        activity.TOOL_STARTED,
        activity.TOOL_FINISHED,
    ]
    started, finished = calls
    assert started.kwargs["turn_id"] == "turn-1"
    assert started.kwargs["tool_name"] == "read_test"
    assert started.kwargs["arguments"] == {"lead_ids": [2]}
    assert finished.kwargs["status"] == "executed"
    assert finished.kwargs["result"] == {"ok": True}
    assert finished.kwargs["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_declined_tool_is_recorded_without_a_start_event():
    write = _write_tool()
    record = AsyncMock()
    config = {"configurable": {"thread_id": "thread-decline"}}
    with (
        patch.object(agent_module, "TOOLS", {"tag_test": write}),
        patch.object(agent_module, "audit_tool_call", AsyncMock()),
        patch.object(agent_module.activity, "record", record),
    ):
        graph = agent_module.build_copilot_graph(
            _FakeModel(
                [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "tag_test",
                                "args": {"lead_ids": [1]},
                                "id": "c1",
                            }
                        ],
                    ),
                    AIMessage(content="Nothing changed."),
                ]
            ),
            InMemorySaver(),
        )
        await _wait(
            graph.ainvoke(
                {
                    "messages": [HumanMessage(content="tag lead 1")],
                    "organization_id": 11,
                    "user_id": 22,
                    "conversation_id": 33,
                    "turn_id": "turn-2",
                },
                config,
            )
        )
        state = await graph.aget_state(config)
        await _wait(
            graph.ainvoke(
                Command(resume={state.interrupts[0].id: {"decision": "decline"}}),
                config,
            )
        )

    write.handler.assert_not_awaited()
    (event,) = record.await_args_list
    assert event.kwargs["kind"] == activity.TOOL_FINISHED
    assert event.kwargs["status"] == "declined"
    assert event.kwargs["turn_id"] == "turn-2"


@asynccontextmanager
async def _checkpointer(thread, *, readonly=False):
    yield InMemorySaver()


def _conversation():
    return SimpleNamespace(
        id=3,
        organization_id=11,
        title="New conversation",
        last_request_id=None,
        last_response=None,
    )


def _user():
    return SimpleNamespace(id=22, selected_organization_id=11)


@asynccontextmanager
async def _service_run(graph, record):
    with (
        patch.object(
            service, "load_conversation", AsyncMock(return_value=_conversation())
        ),
        patch.object(service, "_graph_for", AsyncMock(return_value=graph)),
        patch.object(service.db_client, "copilot_checkpointer", _checkpointer),
        patch.object(service.db_client, "save_copilot_snapshot", AsyncMock()),
        patch.object(service.db_client, "record_copilot_activity", record),
        patch.object(agent_module, "TOOLS", {}),
        patch.object(agent_module, "audit_tool_call", AsyncMock()),
    ):
        yield


def _wait(coroutine):
    return asyncio.wait_for(coroutine, timeout=10)


@pytest.mark.asyncio
async def test_a_turn_records_start_and_finish():
    record = AsyncMock()
    graph = agent_module.build_copilot_graph(
        _FakeModel([AIMessage(content="Hello there.")]), InMemorySaver()
    )
    async with _service_run(graph, record):
        snapshot = await _wait(service.send_message(3, _user(), "hi", "req-1"))

    kinds = [call.kwargs["kind"] for call in record.await_args_list]
    assert kinds == [activity.TURN_STARTED, activity.TURN_FINISHED]
    turn_ids = {call.kwargs["turn_id"] for call in record.await_args_list}
    assert len(turn_ids) == 1
    (turn_id,) = turn_ids
    # The label is server-generated so a client id cannot merge two turns.
    assert turn_id != "req-1"
    assert len(turn_id) == 32
    assert record.await_args_list[1].kwargs["status"] == "idle"
    assert snapshot.messages[-1].content == "Hello there."


@pytest.mark.asyncio
async def test_a_failed_turn_records_the_error_not_a_finish():
    record = AsyncMock()
    graph = agent_module.build_copilot_graph(_FailingModel([]), InMemorySaver())
    async with _service_run(graph, record):
        with pytest.raises(HTTPException):
            await _wait(service.send_message(3, _user(), "hi", "req-2"))

    kinds = [call.kwargs["kind"] for call in record.await_args_list]
    assert kinds == [activity.TURN_STARTED, activity.TURN_FAILED]
    assert record.await_args_list[1].kwargs["error"]


@pytest.mark.asyncio
async def test_get_activity_rejects_another_users_conversation(db_session):
    owner, _ = await db_session.get_or_create_user_by_provider_id(str(uuid4()))
    stranger, _ = await db_session.get_or_create_user_by_provider_id(str(uuid4()))
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        str(uuid4()), owner.id
    )
    conversation = await db_session.create_copilot_conversation(
        organization_id=org.id, user_id=owner.id
    )

    with pytest.raises(HTTPException) as excinfo:
        await service.get_activity(
            conversation.id,
            SimpleNamespace(id=stranger.id, selected_organization_id=org.id),
        )
    assert excinfo.value.status_code == 404
