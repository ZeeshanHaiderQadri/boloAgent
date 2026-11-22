"""Deleting a copilot conversation also removes its transcript and audit rows."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from api.db.models import CheckpointModel, CopilotToolCallModel


@pytest.mark.asyncio
async def test_delete_conversation_removes_checkpoints_and_tool_calls(
    db_session, async_session
):
    user, _ = await db_session.get_or_create_user_by_provider_id(str(uuid4()))
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        str(uuid4()), user.id
    )
    conversation = await db_session.create_copilot_conversation(
        organization_id=org.id, user_id=user.id
    )
    thread = f"copilot:{org.id}:{user.id}:{conversation.id}"
    async_session.add(
        CheckpointModel(
            thread_id=thread,
            checkpoint_id="checkpoint-1",
            checkpoint={},
            checkpoint_metadata={},
        )
    )
    async_session.add(
        CopilotToolCallModel(
            organization_id=org.id,
            conversation_id=conversation.id,
            tool_name="business_overview",
            risk="read",
            status="ok",
            arguments={},
        )
    )
    await async_session.flush()

    assert (
        await db_session.delete_copilot_conversation(conversation.id, org.id, thread)
        is True
    )
    assert (
        await async_session.scalar(
            select(CheckpointModel).where(CheckpointModel.thread_id == thread)
        )
        is None
    )
    assert (
        await async_session.scalar(
            select(CopilotToolCallModel).where(
                CopilotToolCallModel.conversation_id == conversation.id
            )
        )
        is None
    )
    # A second delete is a no-op, not an error.
    assert (
        await db_session.delete_copilot_conversation(conversation.id, org.id, thread)
        is False
    )
