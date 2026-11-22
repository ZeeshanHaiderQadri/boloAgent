"""Conversation and tool-audit persistence for the workspace copilot.

The transcript is a LangGraph checkpoint, not a table here: the copilot reuses
the builder's durable, per-thread saver so a paused approval survives a
restart. This client owns the org-scoped conversation row and the append-only
audit of tool executions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select

from api.db.models import (
    CheckpointBlobModel,
    CheckpointModel,
    CheckpointWriteModel,
    CopilotConversationModel,
    CopilotToolCallModel,
)
from api.db.workflow_builder import WorkflowBuilderClient


class CopilotClient(WorkflowBuilderClient):
    def copilot_checkpointer(self, thread_id: str, *, readonly: bool = False):
        """Durable LangGraph checkpoints, shared with the workflow builder.

        The thread id is built by the copilot service from the org, user, and
        conversation id, so neither feature can address the other's threads.
        """
        return self.builder_checkpointer(thread_id, readonly=readonly)

    async def create_copilot_conversation(
        self, *, organization_id: int, user_id: int, title: str = "New conversation"
    ) -> CopilotConversationModel:
        async with self.async_session() as session:
            conversation = CopilotConversationModel(
                organization_id=organization_id,
                user_id=user_id,
                title=title,
            )
            session.add(conversation)
            await session.commit()
            await session.refresh(conversation)
            return conversation

    async def get_copilot_conversation(
        self, conversation_id: int, organization_id: int
    ) -> CopilotConversationModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(CopilotConversationModel).where(
                    CopilotConversationModel.id == conversation_id,
                    CopilotConversationModel.organization_id == organization_id,
                )
            )
            return result.scalar_one_or_none()

    async def delete_copilot_conversation(
        self, conversation_id: int, organization_id: int, thread_id: str
    ) -> bool:
        """Delete the conversation and its LangGraph thread in one transaction."""
        async with self.async_session() as session:
            conversation = await session.get(CopilotConversationModel, conversation_id)
            if conversation is None or conversation.organization_id != organization_id:
                return False
            for checkpoint in (
                CheckpointWriteModel,
                CheckpointBlobModel,
                CheckpointModel,
            ):
                await session.execute(
                    delete(checkpoint).where(checkpoint.thread_id == thread_id)
                )
            await session.delete(conversation)
            await session.commit()
            return True

    async def list_copilot_conversations(
        self, organization_id: int, user_id: int, limit: int = 50
    ) -> list[CopilotConversationModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(CopilotConversationModel)
                .where(
                    CopilotConversationModel.organization_id == organization_id,
                    CopilotConversationModel.user_id == user_id,
                )
                .order_by(CopilotConversationModel.updated_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def save_copilot_snapshot(
        self,
        *,
        conversation_id: int,
        organization_id: int,
        request_id: str | None,
        response: dict[str, Any],
        title: str | None = None,
    ) -> None:
        async with self.async_session() as session:
            conversation = await session.get(CopilotConversationModel, conversation_id)
            if conversation is None or conversation.organization_id != organization_id:
                return
            conversation.last_request_id = request_id
            conversation.last_response = response
            conversation.updated_at = datetime.now(UTC)
            if title:
                conversation.title = title[:120]
            await session.commit()

    async def record_copilot_tool_call(
        self,
        *,
        organization_id: int,
        conversation_id: int,
        tool_name: str,
        risk: str,
        status: str,
        arguments: dict[str, Any],
        result: Any = None,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        async with self.async_session() as session:
            session.add(
                CopilotToolCallModel(
                    organization_id=organization_id,
                    conversation_id=conversation_id,
                    tool_name=tool_name,
                    risk=risk,
                    status=status,
                    arguments=arguments,
                    result=result,
                    error=error,
                    duration_ms=duration_ms,
                )
            )
            await session.commit()

    async def list_copilot_tool_calls(
        self, conversation_id: int, organization_id: int, limit: int = 100
    ) -> list[CopilotToolCallModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(CopilotToolCallModel)
                .where(
                    CopilotToolCallModel.conversation_id == conversation_id,
                    CopilotToolCallModel.organization_id == organization_id,
                )
                .order_by(CopilotToolCallModel.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())
