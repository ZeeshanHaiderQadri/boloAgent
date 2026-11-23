"""API schemas for the workspace copilot."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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
    # Knowledge-base document uuids the user attached to this turn. The agent
    # reads them on demand through the read_document tool.
    attachments: list[str] = Field(default_factory=list, max_length=10)
    # Retries with the same id return the stored snapshot instead of running
    # the graph (and any tool side effects) twice.
    client_request_id: str | None = Field(default=None, max_length=64)


class CopilotApprovalRequest(BaseModel):
    approval_id: str = Field(min_length=1, max_length=64)
    decision: CopilotDecision
    client_request_id: str | None = Field(default=None, max_length=64)


class CopilotRegenerateRequest(BaseModel):
    # Retries with the same id return the stored snapshot instead of running
    # the graph (and any tool side effects) twice.
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


CopilotActivityKind = Literal[
    "turn_started",
    "turn_finished",
    "turn_failed",
    "approval_required",
    "approval_decided",
    "tool_started",
    "tool_finished",
]


class CopilotActivityEvent(BaseModel):
    id: int
    conversation_id: int
    turn_id: str
    kind: CopilotActivityKind
    tool: str | None = None
    risk: str | None = None
    status: str | None = None
    arguments: dict | None = None
    result: Any | None = None
    error: str | None = None
    duration_ms: int | None = None
    created_at: datetime


class CopilotActivityResponse(BaseModel):
    events: list[CopilotActivityEvent]


CopilotWebProviderName = Literal["exa", "tavily", "firecrawl", "serper", "serpapi"]


class CopilotWebProvider(BaseModel):
    name: CopilotWebProviderName
    label: str
    keyless: bool
    enabled: bool
    configured: bool
    supports_search: bool
    supports_fetch: bool
    docs_url: str


class CopilotWebAccessSettings(BaseModel):
    providers: list[CopilotWebProvider]
    any_enabled: bool


class CopilotWebProviderUpdate(BaseModel):
    name: CopilotWebProviderName
    enabled: bool | None = None
    # A new key replaces the stored one; it is encrypted before storage and
    # never returned by the read endpoint.
    api_key: str | None = Field(default=None, max_length=400)
    clear_key: bool = False


class CopilotWebAccessUpdate(BaseModel):
    providers: list[CopilotWebProviderUpdate] = Field(min_length=1, max_length=5)


class CopilotVoiceStatus(BaseModel):
    """Whether the organization's own services can run a voice session."""

    ready: bool
    message: str | None = None
    llm: str | None = None
    stt: str | None = None
    tts: str | None = None
