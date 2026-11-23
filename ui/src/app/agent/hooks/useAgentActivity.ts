"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getActivityApiV1CopilotConversationsConversationIdActivityGet } from "@/client/sdk.gen";
import type { CopilotActivityEvent } from "@/client/types.gen";

const FAST_INTERVAL_MS = 1200;
const SLOW_INTERVAL_MS = 4000;
const HIDDEN_INTERVAL_MS = 10000;
const INITIAL_LIMIT = 500;
const INCREMENTAL_LIMIT = 200;
const MAX_EVENTS = 400;

type AuthHeaders = () => Promise<Record<string, string>>;

export type { AuthHeaders };

type Options = {
  conversationId: number | null;
  enabled: boolean;
  /** Poll quickly while a turn is in flight or a tracker is running. */
  fast: boolean;
  authHeaders: AuthHeaders;
};

function mergeEvents(
  current: CopilotActivityEvent[],
  incoming: CopilotActivityEvent[],
) {
  if (incoming.length === 0) return current;
  const seen = new Set(current.map((event) => event.id));
  const next = [...current];
  for (const event of incoming) {
    if (!seen.has(event.id)) {
      seen.add(event.id);
      next.push(event);
    }
  }
  next.sort((a, b) => a.id - b.id);
  return next.length > MAX_EVENTS ? next.slice(-MAX_EVENTS) : next;
}

export function useAgentActivity({
  conversationId,
  enabled,
  fast,
  authHeaders,
}: Options) {
  const [events, setEvents] = useState<CopilotActivityEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const lastIdRef = useRef(0);
  const fastRef = useRef(fast);
  const authHeadersRef = useRef(authHeaders);
  const refreshRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    fastRef.current = fast;
  }, [fast]);

  useEffect(() => {
    authHeadersRef.current = authHeaders;
  }, [authHeaders]);

  const refresh = useCallback(() => {
    refreshRef.current();
  }, []);

  useEffect(() => {
    refreshRef.current = () => undefined;
    setEvents([]);
    setError(null);
    lastIdRef.current = 0;
    if (conversationId === null || !enabled) {
      setLoading(false);
      return;
    }

    let stopped = false;
    let timer: number | undefined;
    let controller: AbortController | null = null;
    let pending = false;
    setLoading(true);

    const schedule = (delay: number) => {
      if (!stopped) timer = window.setTimeout(() => void poll(), delay);
    };

    const poll = async () => {
      if (stopped || pending) return;
      pending = true;
      if (typeof document !== "undefined" && document.hidden) {
        pending = false;
        schedule(HIDDEN_INTERVAL_MS);
        return;
      }
      controller = new AbortController();
      const afterId = lastIdRef.current;
      try {
        const response =
          await getActivityApiV1CopilotConversationsConversationIdActivityGet({
            path: { conversation_id: conversationId },
            query: {
              after_id: afterId,
              limit: afterId === 0 ? INITIAL_LIMIT : INCREMENTAL_LIMIT,
            },
            headers: await authHeadersRef.current(),
            signal: controller.signal,
          });
        if (stopped) return;
        if (response.error) {
          setError("Could not load live activity.");
        } else {
          setError(null);
          const incoming = response.data?.events ?? [];
          if (incoming.length > 0) {
            lastIdRef.current = incoming[incoming.length - 1].id;
            setEvents((current) => mergeEvents(current, incoming));
          }
        }
      } catch (cause) {
        if (
          !stopped &&
          !(cause instanceof DOMException && cause.name === "AbortError")
        ) {
          setError("Could not load live activity.");
        }
      } finally {
        pending = false;
        if (!stopped) {
          setLoading(false);
          schedule(fastRef.current ? FAST_INTERVAL_MS : SLOW_INTERVAL_MS);
        }
      }
    };

    refreshRef.current = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      timer = undefined;
      if (!pending) void poll();
    };
    void poll();

    const onVisibility = () => {
      if (!document.hidden && !stopped) {
        if (timer !== undefined) window.clearTimeout(timer);
        timer = undefined;
        if (!pending) void poll();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      stopped = true;
      document.removeEventListener("visibilitychange", onVisibility);
      refreshRef.current = () => undefined;
      if (timer !== undefined) window.clearTimeout(timer);
      controller?.abort();
    };
  }, [conversationId, enabled]);

  return { events, loading, error, refresh };
}

export type AgentActivityState = ReturnType<typeof useAgentActivity>;
