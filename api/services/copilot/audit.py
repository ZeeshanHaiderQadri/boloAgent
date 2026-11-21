"""Append-only audit for copilot tool calls, shared by chat and voice."""

from __future__ import annotations

from typing import Any

from loguru import logger

from api.db import db_client

from .tools import CopilotContext


async def audit_tool_call(
    ctx: CopilotContext,
    tool_name: str,
    risk: str,
    status: str,
    arguments: dict,
    *,
    result: Any = None,
    error: str | None = None,
    duration_ms: int | None = None,
) -> None:
    try:
        await db_client.record_copilot_tool_call(
            organization_id=ctx.organization_id,
            conversation_id=ctx.conversation_id,
            tool_name=tool_name,
            risk=risk,
            status=status,
            arguments=arguments,
            result=result,
            error=error,
            duration_ms=duration_ms,
        )
    except Exception:
        # Audit is best-effort: a ledger outage must not hide the tool result.
        logger.exception(
            "Failed to record copilot tool call for conversation {}",
            ctx.conversation_id,
        )
