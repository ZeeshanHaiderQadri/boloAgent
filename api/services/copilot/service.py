"""Turn orchestration for the workspace copilot.

A turn is one idempotent graph step: the conversation row carries the last
request id and snapshot for replay, while the transcript and any pending
approval live in durable LangGraph checkpoints.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from api.db import db_client
from api.db.models import CopilotConversationModel, UserModel
from api.db.workflow_builder import BuilderBusy
from api.schemas.copilot import (
    CopilotConversationSummary,
    CopilotPendingApproval,
    CopilotPendingCall,
    CopilotSnapshot,
    CopilotTranscriptMessage,
)
from api.services.copilot.agent import build_copilot_graph
from api.services.copilot.model import (
    CopilotChatModel,
    CopilotConfigurationError,
    CopilotUpstreamError,
)

DEFAULT_TITLE = "New conversation"
TURN_TIMEOUT_SECONDS = 180
RECURSION_LIMIT = 25


def _thread_id(organization_id: int, user_id: int, conversation_id: int) -> str:
    return f"copilot:{organization_id}:{user_id}:{conversation_id}"


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    return str(content or "")


def _present(snapshot, conversation: CopilotConversationModel) -> CopilotSnapshot:
    messages: list[CopilotTranscriptMessage] = []
    for message in snapshot.values.get("messages", []):
        if isinstance(message, HumanMessage):
            text = _content_text(message.content).strip()
            if text:
                messages.append(CopilotTranscriptMessage(role="user", content=text))
        elif isinstance(message, AIMessage):
            text = _content_text(message.content).strip()
            if text:
                messages.append(
                    CopilotTranscriptMessage(role="assistant", content=text)
                )

    pending: list[CopilotPendingApproval] = []
    for item in getattr(snapshot, "interrupts", ()) or ():
        value = item.value if isinstance(item.value, dict) else {}
        if value.get("kind") != "tool_approval":
            continue
        pending.append(
            CopilotPendingApproval(
                id=str(item.id),
                kind="tool_approval",
                calls=[
                    CopilotPendingCall(
                        tool=call.get("tool", ""),
                        risk=call.get("risk", ""),
                        arguments=call.get("arguments") or {},
                    )
                    for call in value.get("calls", [])
                ],
            )
        )

    return CopilotSnapshot(
        conversation_id=conversation.id,
        title=conversation.title,
        status="waiting_for_approval" if pending else "idle",
        messages=messages,
        pending_approvals=pending,
    )


def _auto_title(snapshot: CopilotSnapshot) -> str | None:
    first = next(
        (message.content for message in snapshot.messages if message.role == "user"),
        None,
    )
    return first[:60].strip() if first else None


def _graph_for(conversation: CopilotConversationModel, user: UserModel, saver):
    model = CopilotChatModel(
        organization_id=conversation.organization_id,
        user_id=str(user.id),
    )
    return build_copilot_graph(model, saver)


def _config(conversation: CopilotConversationModel, user: UserModel) -> dict:
    return {
        "configurable": {
            "thread_id": _thread_id(
                conversation.organization_id, user.id, conversation.id
            )
        },
        "recursion_limit": RECURSION_LIMIT,
    }


async def load_conversation(
    conversation_id: int, user: UserModel
) -> CopilotConversationModel:
    organization_id = user.selected_organization_id
    if organization_id is None:
        raise HTTPException(400, "Select an organization first")
    conversation = await db_client.get_copilot_conversation(
        conversation_id, organization_id
    )
    if conversation is None or (
        conversation.user_id is not None and conversation.user_id != user.id
    ):
        raise HTTPException(404, "Conversation not found")
    return conversation


async def create_conversation(
    user: UserModel, title: str | None
) -> CopilotConversationSummary:
    organization_id = user.selected_organization_id
    if organization_id is None:
        raise HTTPException(400, "Select an organization first")
    conversation = await db_client.create_copilot_conversation(
        organization_id=organization_id,
        user_id=user.id,
        title=(title or DEFAULT_TITLE)[:120],
    )
    return CopilotConversationSummary.model_validate(conversation, from_attributes=True)


async def list_conversations(
    user: UserModel, limit: int = 50
) -> list[CopilotConversationSummary]:
    organization_id = user.selected_organization_id
    if organization_id is None:
        return []
    conversations = await db_client.list_copilot_conversations(
        organization_id, user.id, limit=limit
    )
    return [
        CopilotConversationSummary.model_validate(item, from_attributes=True)
        for item in conversations
    ]


async def get_snapshot(conversation_id: int, user: UserModel) -> CopilotSnapshot:
    conversation = await load_conversation(conversation_id, user)
    thread = _thread_id(conversation.organization_id, user.id, conversation.id)
    async with db_client.copilot_checkpointer(thread, readonly=True) as saver:
        graph = _graph_for(conversation, user, saver)
        state = await graph.aget_state({"configurable": {"thread_id": thread}})
    return _present(state, conversation)


async def _save(conversation, user, request_id, snapshot: CopilotSnapshot):
    title = None
    if conversation.title == DEFAULT_TITLE:
        title = _auto_title(snapshot)
        if title:
            snapshot.title = title
    await db_client.save_copilot_snapshot(
        conversation_id=conversation.id,
        organization_id=conversation.organization_id,
        request_id=request_id,
        response=snapshot.model_dump(mode="json"),
        title=title,
    )


async def send_message(
    conversation_id: int,
    user: UserModel,
    message: str,
    client_request_id: str | None,
) -> CopilotSnapshot:
    conversation = await load_conversation(conversation_id, user)
    if (
        client_request_id
        and conversation.last_request_id == client_request_id
        and conversation.last_response
    ):
        return CopilotSnapshot.model_validate(conversation.last_response)

    thread = _thread_id(conversation.organization_id, user.id, conversation.id)
    config = _config(conversation, user)
    try:
        async with db_client.copilot_checkpointer(thread) as saver:
            graph = _graph_for(conversation, user, saver)
            state = await graph.aget_state(config)
            if getattr(state, "interrupts", ()):
                raise HTTPException(
                    409,
                    "Answer the pending approval before sending another message.",
                )
            async with asyncio.timeout(TURN_TIMEOUT_SECONDS):
                await graph.ainvoke(
                    {
                        "messages": [HumanMessage(content=message)],
                        "organization_id": conversation.organization_id,
                        "user_id": user.id,
                        "conversation_id": conversation.id,
                    },
                    config,
                )
            state = await graph.aget_state(config)
    except BuilderBusy as exc:
        raise HTTPException(
            409, "This conversation is already running. Try again in a moment."
        ) from exc
    except TimeoutError as exc:
        raise HTTPException(
            504, "The copilot took too long; try a narrower ask."
        ) from exc
    except CopilotConfigurationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except CopilotUpstreamError as exc:
        raise HTTPException(502, str(exc)) from exc

    kind = state.values.get("model_error_kind")
    if kind:
        detail = state.values.get("model_error") or (
            "The copilot could not reach your LLM."
        )
        raise HTTPException(400 if kind == "configuration" else 502, detail)

    snapshot = _present(state, conversation)
    await _save(conversation, user, client_request_id, snapshot)
    return snapshot


async def decide_approval(
    conversation_id: int,
    user: UserModel,
    approval_id: str,
    decision: str,
    client_request_id: str | None,
) -> CopilotSnapshot:
    conversation = await load_conversation(conversation_id, user)
    if (
        client_request_id
        and conversation.last_request_id == client_request_id
        and conversation.last_response
    ):
        return CopilotSnapshot.model_validate(conversation.last_response)

    thread = _thread_id(conversation.organization_id, user.id, conversation.id)
    config = _config(conversation, user)
    try:
        async with db_client.copilot_checkpointer(thread) as saver:
            graph = _graph_for(conversation, user, saver)
            state = await graph.aget_state(config)
            pending = {
                str(item.id) for item in (getattr(state, "interrupts", ()) or ())
            }
            if pending and pending != {approval_id}:
                raise HTTPException(
                    409, "The approval request changed; reload and try again."
                )
            if not pending:
                snapshot = _present(state, conversation)
                await _save(conversation, user, client_request_id, snapshot)
                return snapshot
            async with asyncio.timeout(TURN_TIMEOUT_SECONDS):
                await graph.ainvoke(
                    Command(resume={approval_id: {"decision": decision}}),
                    config,
                )
            state = await graph.aget_state(config)
    except BuilderBusy as exc:
        raise HTTPException(
            409, "This conversation is already running. Try again in a moment."
        ) from exc
    except TimeoutError as exc:
        raise HTTPException(504, "The approval took too long; try again.") from exc
    except CopilotConfigurationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except CopilotUpstreamError as exc:
        raise HTTPException(502, str(exc)) from exc

    snapshot = _present(state, conversation)
    await _save(conversation, user, client_request_id, snapshot)
    return snapshot
