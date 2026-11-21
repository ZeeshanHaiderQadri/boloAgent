"use client";

import {
  AlertTriangle,
  ArrowUp,
  Loader2,
  MessageSquarePlus,
  ShieldCheck,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  createConversationApiV1CopilotConversationsPost,
  decideApprovalApiV1CopilotConversationsConversationIdApprovalsPost,
  getConversationApiV1CopilotConversationsConversationIdGet,
  listConversationsApiV1CopilotConversationsGet,
  sendMessageApiV1CopilotConversationsConversationIdMessagesPost,
} from "@/client/sdk.gen";
import type {
  CopilotConversationSummary,
  CopilotPendingApproval,
  CopilotSnapshot,
  CopilotTranscriptMessage,
} from "@/client/types.gen";
import { PageShell } from "@/components/layout/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const TOOL_LABELS: Record<string, string> = {
  tag_leads: "Tag leads",
  start_lead_search: "Start lead search",
  create_email_campaign_draft: "Create email draft",
  launch_email_campaign: "Launch email campaign",
  send_whatsapp_message: "Send WhatsApp message",
  start_voice_campaign: "Start voice campaign",
  pause_voice_campaign: "Pause voice campaign",
  resume_voice_campaign: "Resume voice campaign",
};

const SUGGESTIONS = [
  "How did my campaigns do this week?",
  "Find 25 dentists in Austin",
  "Draft a promo email for my VIP leads",
  "Which leads have phone numbers but no email?",
];

function toolLabel(tool: string) {
  return TOOL_LABELS[tool] ?? tool.replaceAll("_", " ");
}

function requestId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

function formatWhen(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function ApprovalCard({
  approval,
  deciding,
  onDecide,
}: {
  approval: CopilotPendingApproval;
  deciding: string | null;
  onDecide: (approvalId: string, decision: "approve" | "decline") => void;
}) {
  return (
    <div className="rounded-2xl border border-amber-500/40 bg-amber-500/5 p-4 transition-colors duration-500">
      <div className="mb-3 flex items-start gap-3">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-amber-500/15 text-amber-500">
          <ShieldCheck className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold">Approval needed</p>
          <p className="text-sm text-muted-foreground">
            The agent wants to make changes in your workspace. Nothing runs
            until you approve.
          </p>
        </div>
      </div>
      <div className="space-y-3">
        {approval.calls.map((call, index) => (
          <div
            key={`${call.tool}-${index}`}
            className="rounded-xl border border-border/60 bg-background/60 p-3"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium">{toolLabel(call.tool)}</span>
              <Badge variant="outline" className="text-[10px] uppercase tracking-wide">
                {call.risk}
              </Badge>
            </div>
            <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted/40 p-2 text-xs text-muted-foreground">
              {JSON.stringify(call.arguments, null, 2)}
            </pre>
          </div>
        ))}
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button
          size="sm"
          onClick={() => onDecide(approval.id, "approve")}
          disabled={deciding !== null}
        >
          {deciding === approval.id ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <ThumbsUp className="mr-2 h-4 w-4" />
          )}
          Approve
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => onDecide(approval.id, "decline")}
          disabled={deciding !== null}
        >
          <ThumbsDown className="mr-2 h-4 w-4" />
          Decline
        </Button>
      </div>
    </div>
  );
}

export default function AgentPage() {
  const { user, loading: authLoading, getAccessToken, redirectToLogin } = useAuth();

  const [conversations, setConversations] = useState<
    CopilotConversationSummary[] | null
  >(null);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [snapshot, setSnapshot] = useState<CopilotSnapshot | null>(null);
  const [optimistic, setOptimistic] = useState<CopilotTranscriptMessage[]>([]);
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [deciding, setDeciding] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const hasLoaded = useRef(false);
  const endRef = useRef<HTMLDivElement | null>(null);

  const authHeaders = useCallback(
    async () => ({ Authorization: `Bearer ${await getAccessToken()}` }),
    [getAccessToken],
  );

  useEffect(() => {
    if (!authLoading && !user) {
      redirectToLogin();
    }
  }, [authLoading, user, redirectToLogin]);

  const openConversation = useCallback(
    async (conversationId: number) => {
      setActiveId(conversationId);
      setSnapshotLoading(true);
      setError(null);
      try {
        const response =
          await getConversationApiV1CopilotConversationsConversationIdGet({
            path: { conversation_id: conversationId },
            headers: await authHeaders(),
          });
        if (response.error) {
          setError(detailFromError(response.error, "Could not open that conversation."));
          return;
        }
        setSnapshot(response.data ?? null);
        setOptimistic([]);
      } finally {
        setSnapshotLoading(false);
      }
    },
    [authHeaders],
  );

  useEffect(() => {
    if (authLoading || !user || hasLoaded.current) return;
    hasLoaded.current = true;

    const load = async () => {
      setConversations([]);
      const response = await listConversationsApiV1CopilotConversationsGet({
        headers: await authHeaders(),
      });
      if (response.error) {
        setConversations([]);
        setError(detailFromError(response.error, "Could not load conversations."));
        return;
      }
      const list = response.data ?? [];
      setConversations(list);
      if (list.length > 0) {
        await openConversation(list[0].id);
      }
    };

    void load();
  }, [authLoading, user, authHeaders, openConversation]);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end", behavior: "smooth" });
  }, [snapshot, optimistic, busy]);

  const createConversation = useCallback(async () => {
    const response = await createConversationApiV1CopilotConversationsPost({
      body: {},
      headers: await authHeaders(),
    });
    if (response.error || !response.data) {
      throw new Error(
        detailFromError(response.error, "Could not start a conversation."),
      );
    }
    const created = response.data;
    setConversations((current) => [created, ...(current ?? [])]);
    setActiveId(created.id);
    setSnapshot({
      conversation_id: created.id,
      title: created.title,
      status: "idle",
      messages: [],
      pending_approvals: [],
    });
    return created;
  }, [authHeaders]);

  const handleSend = async () => {
    const text = draft.trim();
    if (!text || busy) return;

    setError(null);
    setBusy(true);
    setDraft("");
    setOptimistic([{ role: "user", content: text }]);

    try {
      let conversationId = activeId;
      if (conversationId === null) {
        conversationId = (await createConversation()).id;
      }
      const response =
        await sendMessageApiV1CopilotConversationsConversationIdMessagesPost({
          path: { conversation_id: conversationId },
          body: { message: text, client_request_id: requestId() },
          headers: await authHeaders(),
        });
      if (response.error || !response.data) {
        setError(detailFromError(response.error, "The agent could not reply."));
        setDraft(text);
        setOptimistic([]);
        return;
      }
      setSnapshot(response.data);
      setOptimistic([]);
      const refreshed = await listConversationsApiV1CopilotConversationsGet({
        headers: await authHeaders(),
      });
      if (refreshed.data) setConversations(refreshed.data);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "The agent could not reply. Try again.",
      );
      setDraft(text);
      setOptimistic([]);
    } finally {
      setBusy(false);
    }
  };

  const handleDecide = async (
    approvalId: string,
    decision: "approve" | "decline",
  ) => {
    if (activeId === null || deciding !== null) return;
    setDeciding(approvalId);
    setError(null);
    try {
      const response =
        await decideApprovalApiV1CopilotConversationsConversationIdApprovalsPost({
          path: { conversation_id: activeId },
          body: {
            approval_id: approvalId,
            decision,
            client_request_id: requestId(),
          },
          headers: await authHeaders(),
        });
      if (response.error || !response.data) {
        setError(detailFromError(response.error, "Could not send your decision."));
        return;
      }
      setSnapshot(response.data);
      setOptimistic([]);
    } catch {
      setError("Could not send your decision. Try again.");
    } finally {
      setDeciding(null);
    }
  };

  const startFreshConversation = async () => {
    if (busy) return;
    setError(null);
    setBusy(true);
    try {
      await createConversation();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Could not start a conversation.",
      );
    } finally {
      setBusy(false);
    }
  };

  const messages = [...(snapshot?.messages ?? []), ...optimistic];
  const pending = snapshot?.pending_approvals ?? [];
  const hasConversation = activeId !== null;
  const visibleConversations = conversations ?? [];

  return (
    <PageShell className="flex flex-col">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="mb-1 flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-cta/15 text-cta">
              <Sparkles className="h-4 w-4" />
            </span>
            <h1 className="text-3xl font-bold">Bolo Agent</h1>
          </div>
          <p className="text-muted-foreground">
            Your workspace operator. It reads your leads, campaigns and calls,
            and asks before it changes anything.
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => void startFreshConversation()}
          disabled={busy}
        >
          <MessageSquarePlus className="mr-2 h-4 w-4" />
          New conversation
        </Button>
      </div>

      {error && (
        <div className="mb-4 flex items-start gap-3 rounded-xl border border-destructive/40 bg-destructive/10 p-3 text-sm">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <span className="min-w-0 flex-1">{error}</span>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-2"
            onClick={() => setError(null)}
          >
            Dismiss
          </Button>
        </div>
      )}

      <div className="grid min-h-[560px] gap-6 lg:grid-cols-[260px_minmax(0,1fr)]">
        <Card className="h-fit max-h-[calc(100dvh-14rem)] overflow-hidden lg:sticky lg:top-4">
          <CardContent className="p-2">
            <p className="px-2 py-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Conversations
            </p>
            <div className="max-h-60 overflow-y-auto lg:max-h-[calc(100dvh-20rem)]">
              {conversations === null ? (
                <div className="space-y-2 p-2">
                  <Skeleton className="h-9 w-full" />
                  <Skeleton className="h-9 w-full" />
                  <Skeleton className="h-9 w-full" />
                </div>
              ) : visibleConversations.length === 0 ? (
                <p className="px-2 py-3 text-sm text-muted-foreground">
                  No conversations yet. Send the first message to start one.
                </p>
              ) : (
                <ul className="space-y-1">
                  {visibleConversations.map((conversation) => (
                    <li key={conversation.id}>
                      <button
                        type="button"
                        onClick={() => void openConversation(conversation.id)}
                        className={cn(
                          "w-full rounded-lg px-2 py-2 text-left transition-colors duration-300 hover:bg-accent",
                          conversation.id === activeId &&
                            "bg-cta/15 font-medium text-foreground",
                        )}
                      >
                        <span className="block truncate text-sm">
                          {conversation.title}
                        </span>
                        <span className="block truncate text-xs text-muted-foreground">
                          {formatWhen(conversation.updated_at)}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </CardContent>
        </Card>

        <Card className="flex h-[calc(100dvh-14rem)] min-h-[560px] flex-col overflow-hidden">
          <div className="flex items-center justify-between gap-3 border-b px-5 py-3">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">
                {snapshot?.title ?? "New conversation"}
              </p>
              <p className="text-xs text-muted-foreground">
                Runs on your workspace model connection.
              </p>
            </div>
            <Badge
              variant="outline"
              className={cn(
                "shrink-0 transition-colors duration-500",
                pending.length > 0 && "border-amber-500/50 text-amber-500",
              )}
            >
              {pending.length > 0 ? "Awaiting approval" : "Ready"}
            </Badge>
          </div>

          <div className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
            {snapshotLoading ? (
              <div className="space-y-3">
                <Skeleton className="h-16 w-3/4" />
                <Skeleton className="ml-auto h-10 w-1/2" />
              </div>
            ) : !hasConversation ? (
              <div className="flex h-full flex-col items-center justify-center text-center">
                <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-cta/15 text-cta">
                  <Sparkles className="h-6 w-6" />
                </span>
                <h2 className="text-lg font-semibold">What should we work on?</h2>
                <p className="mt-1 max-w-md text-sm text-muted-foreground">
                  Ask about your leads, campaigns or calls. Writes show up as
                  approval cards before anything runs.
                </p>
                <div className="mt-5 flex max-w-lg flex-wrap justify-center gap-2">
                  {SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      onClick={() => setDraft(suggestion)}
                      className="rounded-full border border-border/70 px-3 py-1.5 text-xs text-muted-foreground transition-colors duration-300 hover:border-cta/60 hover:text-foreground"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            ) : messages.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Say hello, or ask what the agent can do.
              </p>
            ) : (
              messages.map((message, index) => (
                <div
                  key={`${message.role}-${index}`}
                  className={cn(
                    "flex",
                    message.role === "user" ? "justify-end" : "justify-start",
                  )}
                >
                  <div
                    className={cn(
                      "max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed transition-colors duration-300",
                      message.role === "user"
                        ? "bg-cta text-cta-foreground"
                        : "border border-border/60 bg-muted/40",
                    )}
                  >
                    {message.content}
                  </div>
                </div>
              ))
            )}

            {busy && !pending.length && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Thinking…
              </div>
            )}

            {pending.length > 0 && (
              <div className="space-y-3">
                {pending.map((approval) => (
                  <ApprovalCard
                    key={approval.id}
                    approval={approval}
                    deciding={deciding}
                    onDecide={(id, decision) => void handleDecide(id, decision)}
                  />
                ))}
              </div>
            )}

            <div ref={endRef} />
          </div>

          <div className="border-t px-5 py-4">
            <div className="flex items-end gap-2">
              <Textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void handleSend();
                  }
                }}
                placeholder="Ask the agent to find leads, check a campaign, draft an email…"
                rows={2}
                maxLength={8000}
                disabled={busy}
                className="min-h-[52px] resize-none"
                aria-label="Message Bolo Agent"
              />
              <Button
                size="icon"
                onClick={() => void handleSend()}
                disabled={busy || draft.trim().length === 0}
                aria-label="Send message"
                className="h-[52px] w-[52px] shrink-0 rounded-xl"
              >
                {busy ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <ArrowUp className="h-4 w-4" />
                )}
              </Button>
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              Enter to send. Shift+Enter for a new line. Writes wait for your
              approval.
            </p>
          </div>
        </Card>
      </div>
    </PageShell>
  );
}
