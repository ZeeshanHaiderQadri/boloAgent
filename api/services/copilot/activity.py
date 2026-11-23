"""UI-facing activity log for the agent workspace canvas.

``copilot_tool_calls`` stays the security ledger; these are summarized rows
that the workspace panel renders live and after a reload. Recording is
best-effort: a failed insert must never fail the turn that produced it.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from api.db import db_client
from api.db.models import CopilotConversationModel

TURN_STARTED = "turn_started"
TURN_FINISHED = "turn_finished"
TURN_FAILED = "turn_failed"
APPROVAL_REQUIRED = "approval_required"
APPROVAL_DECIDED = "approval_decided"
TOOL_STARTED = "tool_started"
TOOL_FINISHED = "tool_finished"

MAX_STRING_CHARS = 800
MAX_LIST_ITEMS = 30
MAX_DICT_ITEMS = 50
MAX_DEPTH = 6
MAX_SERIALIZED_CHARS = 32000
MAX_ERROR_CHARS = 1000


def _compact(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        return "…"
    if isinstance(value, str):
        if len(value) <= MAX_STRING_CHARS:
            return value
        return value[:MAX_STRING_CHARS] + "…"
    if isinstance(value, (list, tuple)):
        items = [_compact(item, depth + 1) for item in value[:MAX_LIST_ITEMS]]
        if len(value) > MAX_LIST_ITEMS:
            items.append(f"…{len(value) - MAX_LIST_ITEMS} more")
        return items
    if isinstance(value, dict):
        entries = list(value.items())[:MAX_DICT_ITEMS]
        compacted = {str(key): _compact(item, depth + 1) for key, item in entries}
        if len(value) > MAX_DICT_ITEMS:
            compacted["…"] = f"{len(value) - MAX_DICT_ITEMS} more keys"
        return compacted
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _compact(str(value), depth)


def summarize(value: Any) -> Any:
    """Bound a tool payload so one event cannot bloat the table."""
    compacted = _compact(value)
    try:
        encoded = json.dumps(compacted, default=str)
    except (TypeError, ValueError):
        return {"unserializable": True}
    if len(encoded) > MAX_SERIALIZED_CHARS:
        return {
            "truncated": True,
            "characters": len(encoded),
            "preview": encoded[:MAX_SERIALIZED_CHARS],
        }
    return compacted


async def record(
    *,
    organization_id: int,
    conversation_id: int,
    turn_id: str,
    kind: str,
    tool_name: str | None = None,
    risk: str | None = None,
    status: str | None = None,
    arguments: Any = None,
    result: Any = None,
    error: str | None = None,
    duration_ms: int | None = None,
) -> None:
    try:
        await db_client.record_copilot_activity(
            organization_id=organization_id,
            conversation_id=conversation_id,
            turn_id=turn_id[:64],
            kind=kind,
            tool_name=tool_name[:64] if tool_name else None,
            risk=risk,
            status=status[:16] if status else None,
            arguments=summarize(arguments) if arguments is not None else None,
            result=summarize(result) if result is not None else None,
            error=error[:MAX_ERROR_CHARS] if error else None,
            duration_ms=duration_ms,
        )
    except Exception:
        logger.exception(
            "Copilot activity write failed (conversation={}, kind={})",
            conversation_id,
            kind,
        )


async def turn_started(conversation: CopilotConversationModel, turn_id: str) -> None:
    await record(
        organization_id=conversation.organization_id,
        conversation_id=conversation.id,
        turn_id=turn_id,
        kind=TURN_STARTED,
    )


async def turn_finished(
    conversation: CopilotConversationModel, turn_id: str, status: str
) -> None:
    await record(
        organization_id=conversation.organization_id,
        conversation_id=conversation.id,
        turn_id=turn_id,
        kind=TURN_FINISHED,
        status=status,
    )


async def turn_failed(
    conversation: CopilotConversationModel, turn_id: str, error: str
) -> None:
    await record(
        organization_id=conversation.organization_id,
        conversation_id=conversation.id,
        turn_id=turn_id,
        kind=TURN_FAILED,
        status="error",
        error=error,
    )


async def approval_required(
    conversation: CopilotConversationModel, turn_id: str, calls: list[dict]
) -> None:
    await record(
        organization_id=conversation.organization_id,
        conversation_id=conversation.id,
        turn_id=turn_id,
        kind=APPROVAL_REQUIRED,
        tool_name=calls[0].get("tool") if calls else None,
        status="pending",
        arguments={"calls": calls},
    )


async def approval_decided(
    conversation: CopilotConversationModel, turn_id: str, decision: str
) -> None:
    await record(
        organization_id=conversation.organization_id,
        conversation_id=conversation.id,
        turn_id=turn_id,
        kind=APPROVAL_DECIDED,
        status=decision,
    )
