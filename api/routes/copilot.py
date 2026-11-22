"""Workspace copilot API: conversations, turns, and approvals."""

from fastapi import APIRouter, Depends, Query, Response

from api.db.models import UserModel
from api.schemas.copilot import (
    CopilotApprovalRequest,
    CopilotConversationCreate,
    CopilotConversationSummary,
    CopilotMessageRequest,
    CopilotRegenerateRequest,
    CopilotSnapshot,
)
from api.services.auth.depends import get_user
from api.services.copilot import service

router = APIRouter(prefix="/copilot", tags=["copilot"])


@router.post("/conversations", response_model=CopilotConversationSummary)
async def create_conversation(
    request: CopilotConversationCreate,
    user: UserModel = Depends(get_user),
):
    return await service.create_conversation(user, request.title)


@router.get("/conversations", response_model=list[CopilotConversationSummary])
async def list_conversations(
    limit: int = Query(default=50, ge=1, le=100),
    user: UserModel = Depends(get_user),
):
    return await service.list_conversations(user, limit)


@router.get("/conversations/{conversation_id}", response_model=CopilotSnapshot)
async def get_conversation(
    conversation_id: int,
    user: UserModel = Depends(get_user),
):
    return await service.get_snapshot(conversation_id, user)


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: int,
    user: UserModel = Depends(get_user),
):
    await service.delete_conversation(conversation_id, user)
    return Response(status_code=204)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=CopilotSnapshot,
)
async def send_message(
    conversation_id: int,
    request: CopilotMessageRequest,
    user: UserModel = Depends(get_user),
):
    return await service.send_message(
        conversation_id,
        user,
        request.message,
        request.client_request_id,
        attachments=request.attachments,
    )


@router.post(
    "/conversations/{conversation_id}/regenerate",
    response_model=CopilotSnapshot,
)
async def regenerate_reply(
    conversation_id: int,
    request: CopilotRegenerateRequest,
    user: UserModel = Depends(get_user),
):
    return await service.regenerate(
        conversation_id,
        user,
        request.client_request_id,
    )


@router.post(
    "/conversations/{conversation_id}/approvals",
    response_model=CopilotSnapshot,
)
async def decide_approval(
    conversation_id: int,
    request: CopilotApprovalRequest,
    user: UserModel = Depends(get_user),
):
    return await service.decide_approval(
        conversation_id,
        user,
        request.approval_id,
        request.decision,
        request.client_request_id,
    )
