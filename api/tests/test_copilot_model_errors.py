"""A failed turn must not be replayed as if the copilot had said it."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.services.copilot import agent as agent_module
from api.services.copilot.model import CopilotUpstreamError


class _BoundModel:
    def __init__(self, model):
        self.model = model

    async def ainvoke(self, messages):
        self.model.seen.append(messages)
        if self.model.failures:
            raise CopilotUpstreamError(self.model.failures.pop(0))
        return AIMessage(content="Working fine.")


class _FakeModel:
    def __init__(self, failures=()):
        self.failures = list(failures)
        self.seen = []

    def bind_tools(self, tools):
        return _BoundModel(self)

    async def describe(self):
        return "fake / model"


def _config(thread):
    return {"configurable": {"thread_id": thread}}


def _turn(text):
    return {
        "messages": [HumanMessage(content=text)],
        "organization_id": 1,
        "user_id": 2,
        "conversation_id": 3,
    }


def _replayed_ai_messages(model):
    return [m.content for m in model.seen[-1] if isinstance(m, AIMessage)]


@pytest.mark.asyncio
async def test_a_failed_turn_is_not_replayed_to_the_model():
    model = _FakeModel(failures=["Your LLM provider returned an error (400)."])
    graph = agent_module.build_copilot_graph(model, InMemorySaver())

    await graph.ainvoke(_turn("hello"), _config("thread"))
    await graph.ainvoke(_turn("try again"), _config("thread"))

    assert _replayed_ai_messages(model) == []
    state = await graph.aget_state(_config("thread"))
    transcript = [
        m.content for m in state.values["messages"] if isinstance(m, AIMessage)
    ]
    # The transcript keeps the failure; only the model's view drops it.
    assert any("error (400)" in content for content in transcript)


@pytest.mark.asyncio
async def test_checkpoints_written_before_the_flag_still_hide_errors():
    model = _FakeModel()
    graph = agent_module.build_copilot_graph(model, InMemorySaver())
    await graph.aupdate_state(
        _config("legacy"),
        {
            "messages": [
                HumanMessage(content="hello"),
                AIMessage(
                    content="Could not reach your LLM provider. Check your network."
                ),
            ]
        },
        as_node="agent",
    )

    await graph.ainvoke(_turn("try again"), _config("legacy"))

    assert _replayed_ai_messages(model) == []
