"""Regenerating a reply replaces the last assistant turn instead of appending."""

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.checkpoint.memory import InMemorySaver

from api.services.copilot import agent as agent_module
from api.services.copilot.service import _reply_to_regenerate


class _BoundModel:
    def __init__(self, replies):
        self.replies = list(replies)

    async def ainvoke(self, messages):
        return self.replies.pop(0)


class _FakeModel:
    def __init__(self, replies):
        self.replies = list(replies)

    def bind_tools(self, tools):
        return _BoundModel(self.replies)

    async def describe(self):
        return "fake / model"


@pytest.mark.asyncio
async def test_regenerating_replaces_the_last_assistant_turn():
    graph = agent_module.build_copilot_graph(
        _FakeModel(
            [
                AIMessage(content="First answer."),
                AIMessage(content="Second answer."),
            ]
        ),
        InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "thread-regenerate"}}
    await graph.ainvoke(
        {
            "messages": [HumanMessage(content="hi")],
            "organization_id": 1,
            "user_id": 2,
            "conversation_id": 3,
        },
        config,
    )

    state = await graph.aget_state(config)
    previous = state.values["messages"][-1]
    assert previous.content == "First answer."

    await graph.ainvoke({"messages": [RemoveMessage(id=previous.id)]}, config)
    final = await graph.aget_state(config)

    answers = [
        message.content
        for message in final.values["messages"]
        if isinstance(message, AIMessage)
    ]
    assert answers == ["Second answer."]


def test_reply_to_regenerate_requires_an_assistant_turn():
    with pytest.raises(HTTPException):
        _reply_to_regenerate([HumanMessage(content="hi")])
    with pytest.raises(HTTPException):
        _reply_to_regenerate(
            [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "tag_leads", "args": {}, "id": "call-1"}],
                )
            ]
        )
    message = AIMessage(content="Done.", id="reply-1")
    assert _reply_to_regenerate([HumanMessage(content="hi"), message]) is message
