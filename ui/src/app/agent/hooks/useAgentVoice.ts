"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { client } from "@/client/client.gen";
import {
  getTurnCredentialsApiV1TurnCredentialsGet,
  voiceStatusApiV1CopilotVoiceGet,
} from "@/client/sdk.gen";
import type { CopilotVoiceStatus } from "@/client/types.gen";
import { useAppConfig } from "@/context/AppConfigContext";
import { resolveBrowserBackendUrl } from "@/lib/apiClient";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

export type AgentCallState = "idle" | "connecting" | "live";

const CALL_ERROR_MESSAGES: Record<string, string> = {
  voice_not_configured:
    "Connect your own LLM, speech-to-text and voice services before starting a call.",
  concurrency_limit_exceeded:
    "Another call is already active in this workspace. End it before starting a new one.",
  voice_pipeline_failed:
    "The call ended before it connected. Check your model and voice connections.",
  conversation_not_found: "That conversation no longer exists.",
};

function newPeerId() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return (
    "PC-" +
    Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")
  );
}

function startFailureMessage(cause: unknown) {
  if (cause instanceof DOMException) {
    if (cause.name === "NotAllowedError") {
      return "Microphone access was blocked. Allow the microphone and try again.";
    }
    if (cause.name === "NotFoundError") {
      return "No microphone found. Connect one and try again.";
    }
  }
  return "Could not start the voice call. Try again.";
}

export function useAgentVoice() {
  const { user, loading: authLoading, getAccessToken } = useAuth();
  const { config: appConfig } = useAppConfig();

  const [voiceStatus, setVoiceStatus] = useState<CopilotVoiceStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [callState, setCallState] = useState<AgentCallState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const pcIdRef = useRef<string | null>(null);
  const liveRef = useRef(false);
  const endingRef = useRef(false);

  const authHeaders = useCallback(
    async () => ({ Authorization: `Bearer ${await getAccessToken()}` }),
    [getAccessToken],
  );

  const refreshStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      const response = await voiceStatusApiV1CopilotVoiceGet({
        headers: await authHeaders(),
      });
      if (response.error) {
        setVoiceStatus({
          ready: false,
          message: detailFromError(response.error, "Voice is not available."),
        });
        return;
      }
      setVoiceStatus(response.data ?? null);
    } catch {
      setVoiceStatus({ ready: false, message: "Could not check voice readiness." });
    } finally {
      setStatusLoading(false);
    }
  }, [authHeaders]);

  const statusLoaded = useRef(false);
  useEffect(() => {
    if (authLoading || !user || statusLoaded.current) return;
    statusLoaded.current = true;
    void refreshStatus();
  }, [authLoading, user, refreshStatus]);

  const teardown = useCallback(() => {
    const ws = wsRef.current;
    if (
      ws &&
      ws.readyState !== WebSocket.CLOSED &&
      ws.readyState !== WebSocket.CLOSING
    ) {
      ws.close(1000, "hang up");
    }
    wsRef.current = null;

    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;

    const pc = pcRef.current;
    if (pc) {
      pc.getTransceivers().forEach((transceiver) => {
        try {
          transceiver.stop();
        } catch {
          // Already stopped.
        }
      });
      pc.getSenders().forEach((sender) => sender.track?.stop());
      if (pc.signalingState !== "closed") pc.close();
    }
    pcRef.current = null;

    if (audioRef.current) audioRef.current.srcObject = null;
    liveRef.current = false;
    setCallState("idle");
  }, []);

  useEffect(() => {
    return () => {
      endingRef.current = true;
      teardown();
    };
  }, [teardown]);

  const fail = useCallback(
    (message: string) => {
      teardown();
      setError(message);
    },
    [teardown],
  );

  const endCall = useCallback(
    (message?: string) => {
      teardown();
      setNotice(message ?? null);
    },
    [teardown],
  );

  const start = useCallback(
    async (conversationId: number) => {
      if (callState !== "idle") return;
      if (!voiceStatus?.ready) {
        setError(voiceStatus?.message || "Voice is not ready yet.");
        return;
      }

      setError(null);
      setNotice(null);
      setCallState("connecting");
      endingRef.current = false;

      const pcId = newPeerId();
      pcIdRef.current = pcId;

      try {
        const iceServers: RTCIceServer[] = [];
        if (!appConfig?.forceTurnRelay) {
          iceServers.push({ urls: "stun:stun.l.google.com:19302" });
        }
        if (appConfig?.turnEnabled !== false) {
          try {
            const turn = await getTurnCredentialsApiV1TurnCredentialsGet({
              headers: await authHeaders(),
            });
            if (turn.data?.uris?.length) {
              iceServers.push({
                urls: turn.data.uris,
                username: turn.data.username,
                credential: turn.data.password,
              });
            }
          } catch {
            // STUN-only fallback; the call can still connect on a LAN.
          }
        }

        const pc = new RTCPeerConnection({
          iceServers,
          ...(appConfig?.forceTurnRelay
            ? { iceTransportPolicy: "relay" as const }
            : {}),
        });
        pcRef.current = pc;

        pc.addEventListener("icecandidate", (event) => {
          const ws = wsRef.current;
          if (ws?.readyState !== WebSocket.OPEN) return;
          ws.send(
            JSON.stringify({
              type: "ice-candidate",
              payload: {
                pc_id: pcId,
                candidate: event.candidate
                  ? {
                      candidate: event.candidate.candidate,
                      sdpMid: event.candidate.sdpMid,
                      sdpMLineIndex: event.candidate.sdpMLineIndex,
                    }
                  : null,
              },
            }),
          );
        });

        pc.addEventListener("track", (event) => {
          if (event.track.kind !== "audio" || !audioRef.current) return;
          audioRef.current.srcObject = event.streams[0];
          void audioRef.current.play().catch(() => {
            // Autoplay can be blocked; the call still works once audio resumes.
          });
        });

        pc.addEventListener("connectionstatechange", () => {
          if (pcRef.current !== pc) return;
          if (pc.connectionState === "failed") {
            fail("The call dropped before audio connected.");
          }
        });

        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        streamRef.current = stream;
        stream.getTracks().forEach((track) => pc.addTrack(track, stream));

        const baseUrl = client.getConfig().baseUrl || resolveBrowserBackendUrl();
        const token = await getAccessToken();
        const ws = new WebSocket(
          `${baseUrl.replace(/^http/, "ws")}/api/v1/copilot/voice/signaling/${conversationId}?token=${token}`,
        );
        wsRef.current = ws;

        ws.onopen = () => {
          void (async () => {
            try {
              const offer = await pc.createOffer();
              await pc.setLocalDescription(offer);
              ws.send(
                JSON.stringify({
                  type: "offer",
                  payload: {
                    sdp: offer.sdp,
                    type: offer.type,
                    pc_id: pcId,
                  },
                }),
              );
            } catch {
              fail("Could not negotiate the voice connection.");
            }
          })();
        };

        ws.onmessage = (event) => {
          void (async () => {
            let message: {
              type?: string;
              payload?: {
                sdp?: string;
                candidate?: {
                  candidate: string;
                  sdpMid: string | null;
                  sdpMLineIndex: number | null;
                } | null;
                error_type?: string;
                message?: string;
                reason?: string;
              };
            };
            try {
              message = JSON.parse(event.data);
            } catch {
              return;
            }

            if (message.type === "answer" && message.payload?.sdp) {
              try {
                await pc.setRemoteDescription({
                  type: "answer",
                  sdp: message.payload.sdp,
                });
                liveRef.current = true;
                setCallState("live");
              } catch {
                fail("Could not negotiate the voice connection.");
              }
              return;
            }

            if (message.type === "ice-candidate" && message.payload?.candidate) {
              try {
                await pc.addIceCandidate({
                  candidate: message.payload.candidate.candidate,
                  sdpMid: message.payload.candidate.sdpMid,
                  sdpMLineIndex: message.payload.candidate.sdpMLineIndex,
                });
              } catch {
                // Trickled candidates can arrive after the call ends.
              }
              return;
            }

            if (message.type === "error") {
              const errorType = message.payload?.error_type ?? "voice_error";
              fail(
                message.payload?.message ||
                  CALL_ERROR_MESSAGES[errorType] ||
                  "The voice call failed.",
              );
              return;
            }

            if (message.type === "call-ended") {
              const reason = message.payload?.reason;
              if (reason === "pipeline_failed") {
                endCall();
              } else {
                endCall("Call ended.");
              }
            }
          })();
        };

        ws.onclose = (event) => {
          if (wsRef.current !== ws) return;
          wsRef.current = null;
          if (endingRef.current || event.reason === "call ended") return;
          if (liveRef.current) {
            endCall("The call ended.");
          } else {
            fail("Could not start the voice call. Try again.");
          }
        };
      } catch (cause) {
        teardown();
        setError(startFailureMessage(cause));
      }
    },
    [appConfig, authHeaders, callState, endCall, fail, getAccessToken, teardown, voiceStatus],
  );

  const stop = useCallback(() => {
    if (callState === "idle") return;
    endingRef.current = true;
    teardown();
    setNotice(null);
  }, [callState, teardown]);

  return {
    audioRef,
    callState,
    voiceStatus,
    statusLoading,
    error,
    notice,
    start,
    stop,
    refreshStatus,
    clearError: () => setError(null),
    clearNotice: () => setNotice(null),
  };
}
