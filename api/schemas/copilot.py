"""API schemas for the workspace copilot."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

CopilotDecision = Literal["approve", "decline"]
CopilotStatus = Literal["idle", "waiting_for_approval"]


class CopilotConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=120)


class CopilotConversationSummary(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime


class CopilotMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    # Retries with the same id return the stored snapshot instead of running
    # the graph (and any tool side effects) twice.
    client_request_id: str | None = Field(default=None, max_length=64)


class CopilotApprovalRequest(BaseModel):
    approval_id: str = Field(min_length=1, max_length=64)
    decision: CopilotDecision
    client_request_id: str | None = Field(default=None, max_length=64)


class CopilotTranscriptMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class CopilotPendingCall(BaseModel):
    tool: str
    risk: str
    arguments: dict


class CopilotPendingApproval(BaseModel):
    id: str
    kind: Literal["tool_approval"]
    calls: list[CopilotPendingCall]


class CopilotSnapshot(BaseModel):
    conversation_id: int
    title: str
    status: CopilotStatus
    messages: list[CopilotTranscriptMessage]
    pending_approvals: list[CopilotPendingApproval]
