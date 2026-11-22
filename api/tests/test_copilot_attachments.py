"""Tests for attachments in copilot turns."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import HumanMessage

from api.services.copilot import service as copilot_service
from api.services.copilot.service import (
    ATTACHMENTS_MARKER,
    _attachment_note,
    _present,
)


@pytest.mark.asyncio
async def test_attachment_note_lists_only_known_workspace_documents():
    document = SimpleNamespace(filename="menu.txt", document_uuid="u1")

    async def lookup(document_uuid: str, organization_id: int):
        assert organization_id == 42
        return document if document_uuid == "u1" else None

    with patch.object(
        copilot_service.db_client,
        "get_document_by_uuid",
        AsyncMock(side_effect=lookup),
    ):
        note = await _attachment_note(42, ["u1", "u1", "foreign"])

    assert note == ATTACHMENTS_MARKER + "- menu.txt (document_uuid=u1)\n"


@pytest.mark.asyncio
async def test_attachment_note_is_none_without_matching_documents():
    with patch.object(
        copilot_service.db_client,
        "get_document_by_uuid",
        AsyncMock(return_value=None),
    ):
        assert await _attachment_note(42, ["foreign"]) is None


def test_attachment_marker_stays_out_of_the_visible_transcript():
    content = "Summarize this" + ATTACHMENTS_MARKER + "- menu.txt (document_uuid=u1)\n"
    state = SimpleNamespace(
        values={"messages": [HumanMessage(content=content)]}, interrupts=()
    )

    snapshot = _present(state, SimpleNamespace(id=1, title="t"))

    assert [m.content for m in snapshot.messages] == ["Summarize this"]
