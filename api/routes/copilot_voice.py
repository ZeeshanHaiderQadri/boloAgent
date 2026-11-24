"""Voice surface for the workspace copilot: readiness and WebRTC signaling.

The WebSocket contract matches the workflow test-call endpoint (offer,
ice-candidate, renegotiate; answer, error, warning, metrics, call-ended), so a
client written for one can talk to the other. The difference is scope: this
session is bound to one copilot conversation, runs read-only tools, and takes an
org concurrency slot like any other call.

The offer may name a ``language`` (``en``, ``ur`` or ``hi`` to pin the call;
anything else keeps auto-detect). A provider that cannot serve the requested
language does not fail the call: the caller gets a ``warning`` message and the
session runs in the configured language. Recoverable speech failures are
reported the same way, so a reply that never played is not mistaken for
silence.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from aiortc.sdp import candidate_from_sdp
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from loguru import logger
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.utils.run_context import set_current_org_id, set_current_run_id
from starlette.websockets import WebSocketState

from api.db.models import CopilotConversationModel, UserModel
from api.schemas.copilot import CopilotVoiceStatus
from api.services.auth.depends import get_user, get_user_ws
from api.services.call_concurrency import CallConcurrencyLimitError, call_concurrency
from api.services.copilot import voice
from api.services.copilot.service import load_conversation
from api.services.copilot.tools import CopilotContext
from api.services.copilot.voice_language import normalize_voice_language
from api.services.pipecat.webrtc_ice import (
    ICE_INBOUND_POLICY,
    _keep_candidate,
    filter_outbound_sdp,
    get_ice_servers,
)

router = APIRouter(prefix="/copilot/voice", tags=["copilot"])


@router.get("", response_model=CopilotVoiceStatus)
async def voice_status(user: UserModel = Depends(get_user)):
    organization_id = user.selected_organization_id
    if organization_id is None:
        raise HTTPException(400, "Select an organization first")
    try:
        config = await voice.voice_configuration(organization_id, user.id)
    except voice.VoiceConfigurationError as exc:
        return CopilotVoiceStatus(ready=False, message=str(exc))
    return CopilotVoiceStatus(ready=True, **voice.voice_status(config))


class CopilotVoiceSession:
    """One WebSocket's signaling and peer-connection state."""

    def __init__(
        self,
        websocket: WebSocket,
        user: UserModel,
        conversation: CopilotConversationModel,
    ):
        self.websocket = websocket
        self.user = user
        self.conversation = conversation
        self.ctx = CopilotContext(
            organization_id=conversation.organization_id,
            user_id=user.id,
            conversation_id=conversation.id,
        )
        self.connection: SmallWebRTCConnection | None = None
        self.pc_id: str | None = None
        self.pipeline_task: asyncio.Task | None = None
        self.slot = None
        self.closed = False

    async def run(self) -> None:
        await self.websocket.accept()
        try:
            while True:
                await self._handle(await self.websocket.receive_json())
        except WebSocketDisconnect:
            logger.info(
                f"Copilot voice signaling disconnected for conversation "
                f"{self.conversation.id}"
            )
        except Exception:
            logger.exception(
                "Copilot voice signaling failed for conversation {}",
                self.conversation.id,
            )
        finally:
            await self._cleanup()

    async def _send(self, message: dict) -> bool:
        if self.websocket.application_state != WebSocketState.CONNECTED:
            return False
        try:
            await self.websocket.send_json(message)
            return True
        except Exception as exc:
            logger.debug(f"Failed to send copilot voice message: {exc}")
            return False

    async def _send_error(self, message: str, error_type: str = "voice_error") -> None:
        await self._send(
            {"type": "error", "payload": {"error_type": error_type, "message": message}}
        )

    async def _handle(self, message: dict) -> None:
        msg_type = message.get("type")
        payload = message.get("payload") or {}
        if msg_type == "offer":
            await self._handle_offer(payload)
        elif msg_type == "ice-candidate":
            await self._handle_ice_candidate(payload)
        elif msg_type == "renegotiate":
            await self._handle_renegotiation(payload)

    async def _handle_offer(self, payload: dict) -> None:
        pc_id = payload.get("pc_id")
        sdp = payload.get("sdp")
        type_ = payload.get("type")
        if not pc_id or not sdp or not type_:
            await self._send_error("Missing offer fields")
            return

        if self.connection is not None:
            if pc_id != self.pc_id:
                await self._send_error("Peer connection already open")
                return
            await self.connection.renegotiate(sdp=sdp, type=type_, restart_pc=False)
            answer = self.connection.get_answer()
            await self._send_answer(answer, pc_id)
            return

        try:
            self.slot = await call_concurrency.acquire_org_slot(
                self.ctx.organization_id, source="copilot_voice", timeout=0
            )
        except CallConcurrencyLimitError:
            await self._send_error(
                "Concurrent call limit reached. Finish another call and try again.",
                error_type="concurrency_limit_exceeded",
            )
            return

        try:
            # org_id before initialize() so aiortc's tasks inherit it.
            set_current_run_id(self.conversation.id)
            set_current_org_id(self.ctx.organization_id)

            self.ctx = replace(
                self.ctx, language=normalize_voice_language(payload.get("language"))
            )

            connection = SmallWebRTCConnection(
                ice_servers=get_ice_servers(user_id=str(self.user.id)),
                connection_timeout_secs=60,
            )
            connection._pc_id = pc_id
            await connection.initialize(sdp=sdp, type=type_)

            self.connection = connection
            self.pc_id = pc_id

            @connection.event_handler("closed")
            async def _on_closed(_connection: SmallWebRTCConnection):
                await self._notify_call_ended("peer_connection_closed")

            self.pipeline_task = asyncio.create_task(
                voice.run_voice_pipeline(
                    connection,
                    self.ctx,
                    on_error=self._on_audio_error,
                    on_warning=self._on_voice_warning,
                    on_metrics=self._on_turn_metrics,
                )
            )
            self.pipeline_task.add_done_callback(self._on_pipeline_done)

            await self._send_answer(connection.get_answer(), pc_id)
        except voice.VoiceConfigurationError as exc:
            await self._send_error(str(exc), error_type="voice_not_configured")
            await self._cleanup()
        except Exception:
            logger.exception(
                "Could not start copilot voice for conversation {}",
                self.conversation.id,
            )
            await self._send_error(
                "The voice session could not start. Check that your LLM, "
                "speech-to-text and voice connections are set up in Model "
                "connections."
            )
            await self._cleanup()

    async def _send_answer(self, answer: dict, pc_id: str) -> None:
        await self._send(
            {
                "type": "answer",
                "payload": {
                    "sdp": filter_outbound_sdp(answer["sdp"]),
                    "type": answer["type"],
                    "pc_id": pc_id,
                },
            }
        )

    async def _handle_ice_candidate(self, payload: dict) -> None:
        pc_id = payload.get("pc_id")
        if not self.connection or pc_id != self.pc_id:
            logger.warning(f"Ignoring ICE candidate for unknown pc_id: {pc_id}")
            return

        candidate_data = payload.get("candidate")
        if not candidate_data:
            return
        candidate_str = candidate_data.get("candidate", "")
        if not _keep_candidate(candidate_str, ICE_INBOUND_POLICY):
            logger.debug(
                f"Dropping inbound copilot voice candidate per policy "
                f"({ICE_INBOUND_POLICY.value}): {candidate_str[:50]}..."
            )
            return

        try:
            candidate = candidate_from_sdp(candidate_str)
            candidate.sdpMid = candidate_data.get("sdpMid")
            candidate.sdpMLineIndex = candidate_data.get("sdpMLineIndex")
            await self.connection.add_ice_candidate(candidate)
        except Exception as exc:
            logger.error(f"Failed to add copilot voice ICE candidate: {exc}")

    async def _handle_renegotiation(self, payload: dict) -> None:
        pc_id = payload.get("pc_id")
        if not self.connection or pc_id != self.pc_id:
            await self._send_error("Peer connection not found")
            return
        await self.connection.renegotiate(
            sdp=payload.get("sdp"),
            type=payload.get("type"),
            restart_pc=payload.get("restart_pc", False),
        )
        await self._send_answer(self.connection.get_answer(), pc_id)

    def _on_pipeline_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        if isinstance(exc, voice.VoiceConfigurationError):
            message = str(exc)
        else:
            logger.error(
                f"Copilot voice pipeline failed for conversation "
                f"{self.conversation.id}: {exc!r}"
            )
            message = (
                "The voice session stopped. Check your LLM, speech-to-text "
                "and voice connections in Model connections."
            )
        asyncio.create_task(self._fail_session(message))

    async def _on_audio_error(self, error: str) -> None:
        """A speech service failed mid-call; the caller would otherwise sit in silence."""
        if self.closed:
            return
        logger.warning(
            f"Copilot voice audio error for conversation "
            f"{self.conversation.id}: {error}"
        )
        await self._fail_session(voice.voice_error_message(error))

    async def _on_voice_warning(self, message: str) -> None:
        """A provider cannot serve part of the call; the call continues."""
        if self.closed:
            return
        logger.info(
            f"Copilot voice warning for conversation "
            f"{self.conversation.id} (language: {self.ctx.language}): {message}"
        )
        await self._send(
            {
                "type": "warning",
                "payload": {"warning_type": "voice", "message": message},
            }
        )

    async def _on_turn_metrics(self, values: dict[str, int]) -> None:
        """Per-turn service latency in milliseconds, for the voice bar."""
        if self.closed:
            return
        await self._send({"type": "metrics", "payload": values})

    async def _fail_session(self, message: str) -> None:
        await self._send_error(message, error_type="voice_pipeline_failed")
        await self._notify_call_ended("pipeline_failed")

    async def _notify_call_ended(self, reason: str) -> None:
        if self.closed:
            return
        self.closed = True
        logger.info(
            f"Copilot voice call ended for conversation {self.conversation.id} "
            f"(reason: {reason}, language: {self.ctx.language})"
        )
        await self._send(
            {
                "type": "call-ended",
                "payload": {
                    "conversation_id": self.conversation.id,
                    "pc_id": self.pc_id,
                    "reason": reason,
                },
            }
        )
        try:
            await self.websocket.close(code=1000, reason="call ended")
        except Exception as exc:
            logger.debug(f"Failed to close copilot voice WebSocket: {exc}")

    async def _cleanup(self) -> None:
        task = self.pipeline_task
        self.pipeline_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        connection = self.connection
        self.connection = None
        if connection is not None:
            try:
                await connection.disconnect()
            except Exception as exc:
                logger.debug(f"Failed to disconnect copilot voice peer: {exc}")

        slot = self.slot
        self.slot = None
        if slot is not None:
            await call_concurrency.release_slot(slot)


@router.websocket("/signaling/{conversation_id}")
async def copilot_voice_signaling(
    websocket: WebSocket,
    conversation_id: int,
    user: UserModel = Depends(get_user_ws),
):
    """WebRTC signaling for a voice session on one copilot conversation."""
    if user.selected_organization_id is None:
        await websocket.accept()
        await websocket.close(code=1008, reason="No organization selected")
        return

    try:
        conversation = await load_conversation(conversation_id, user)
    except HTTPException as exc:
        await websocket.accept()
        await websocket.send_json(
            {
                "type": "error",
                "payload": {
                    "error_type": "conversation_not_found",
                    "message": str(exc.detail),
                },
            }
        )
        await websocket.close(code=1008)
        return

    try:
        await voice.voice_configuration(conversation.organization_id, user.id)
    except voice.VoiceConfigurationError as exc:
        await websocket.accept()
        await websocket.send_json(
            {
                "type": "error",
                "payload": {
                    "error_type": "voice_not_configured",
                    "message": str(exc),
                },
            }
        )
        await websocket.close(code=1008)
        return

    await CopilotVoiceSession(websocket, user, conversation).run()
