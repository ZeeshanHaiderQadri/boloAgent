"use client";

import {
  AlertTriangle,
  Check,
  CheckCircle2,
  CircleDashed,
  Loader2,
  Radio,
  ShieldCheck,
  Sparkles,
  Wrench,
  XCircle,
} from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { CopilotActivityEvent } from "@/client/types.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import type { AuthHeaders } from "../hooks/useAgentActivity";
import { toolLabel } from "../lib/labels";
import { deriveTaskView } from "../lib/taskView";
import {
  deriveArtifacts,
  formatDuration,
  GenericResultCard,
  LeadSearchTracker,
  LeadsTableCard,
  OverviewCard,
} from "./WorkspaceCards";
import { WorkspacePageView } from "./WorkspacePageView";

const STREAM_LIMIT = 40;

type Props = {
  conversationId: number | null;
  events: CopilotActivityEvent[];
  loading: boolean;
  error: string | null;
  busy: boolean;
  deciding: boolean;
  pendingCount: number;
  onReviewApproval: () => void;
  authHeaders: AuthHeaders;
};

function formatClock(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function isTurnRunning(events: CopilotActivityEvent[]) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const kind = events[index].kind;
    if (kind === "turn_finished" || kind === "turn_failed") return false;
    if (
      kind === "turn_started" ||
      kind === "tool_started" ||
      kind === "approval_required"
    ) {
      return true;
    }
  }
  return false;
}

function ActivityRow({ event }: { event: CopilotActivityEvent }) {
  const duration = formatDuration(event.duration_ms);
  let icon = <CircleDashed className="h-3 w-3 text-muted-foreground" />;
  let text = "";
  let detail: ReactNode = null;

  switch (event.kind) {
    case "turn_started":
      icon = <Sparkles className="h-3 w-3 text-cta" />;
      text = "Thinking about your request";
      break;
    case "tool_started":
      icon = <Loader2 className="h-3 w-3 animate-spin text-cta" />;
      text = toolLabel(event.tool);
      break;
    case "tool_finished":
      if (event.status === "executed") {
        icon = <Check className="h-3 w-3 text-emerald-500" />;
        text = toolLabel(event.tool);
        if (duration) detail = <span className="tabular-nums">{duration}</span>;
      } else if (event.status === "declined") {
        icon = <XCircle className="h-3 w-3 text-muted-foreground" />;
        text = `${toolLabel(event.tool)} — declined`;
      } else {
        icon = <AlertTriangle className="h-3 w-3 text-destructive" />;
        text = `${toolLabel(event.tool)} — failed`;
        if (event.error) detail = <span className="text-destructive">{event.error}</span>;
      }
      break;
    case "approval_required":
      icon = <ShieldCheck className="h-3 w-3 text-amber-500" />;
      text = "Asked for your approval";
      break;
    case "approval_decided":
      icon =
        event.status === "approve" ? (
          <CheckCircle2 className="h-3 w-3 text-emerald-500" />
        ) : (
          <XCircle className="h-3 w-3 text-muted-foreground" />
        );
      text = event.status === "approve" ? "You approved the action" : "You declined the action";
      break;
    case "turn_finished":
      icon = <Check className="h-3 w-3 text-emerald-500" />;
      text =
        event.status === "waiting_for_approval"
          ? "Paused for your approval"
          : "Turn complete";
      break;
    case "turn_failed":
      icon = <AlertTriangle className="h-3 w-3 text-destructive" />;
      text = "Turn failed";
      if (event.error) detail = <span className="text-destructive">{event.error}</span>;
      break;
  }

  return (
    <li className="relative flex gap-2.5 pb-3 pl-0 last:pb-0">
      <span className="relative z-10 mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-border/60 bg-background">
        {icon}
      </span>
      <span className="absolute bottom-0 left-2.5 top-5 w-px bg-border/50" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <p className="min-w-0 flex-1 truncate text-xs text-foreground/90">{text}</p>
          <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground/70">
            {formatClock(event.created_at)}
          </span>
        </div>
        {detail && <p className="mt-0.5 text-[11px] text-muted-foreground">{detail}</p>}
      </div>
    </li>
  );
}

export function LiveWorkspace({
  conversationId,
  events,
  loading,
  error,
  busy,
  deciding,
  pendingCount,
  onReviewApproval,
  authHeaders,
}: Props) {
  const streamEndRef = useRef<HTMLDivElement | null>(null);
  const [space, setSpace] = useState<"page" | "activity">("page");
  const running = busy || deciding || isTurnRunning(events);
  const latest = events[events.length - 1] ?? null;
  const artifacts = useMemo(() => deriveArtifacts(events), [events]);
  const taskView = useMemo(() => deriveTaskView(events), [events]);
  const stream = events.slice(-STREAM_LIMIT);
  const viewPath = taskView?.path ?? null;

  // Adjust state during render (not in an effect) so a path change lands in
  // the same commit that renders it — an effect would flush after a user's
  // tab click in the same batch and snap the pane back to the page view.
  const [shownPath, setShownPath] = useState<string | null>(viewPath);
  if (viewPath !== shownPath) {
    setShownPath(viewPath);
    if (viewPath) setSpace("page");
  }
  const [wasRunning, setWasRunning] = useState(running);
  if (running !== wasRunning) {
    setWasRunning(running);
    if (running && viewPath) setSpace("page");
  }

  useEffect(() => {
    const target = streamEndRef.current;
    if (!target || typeof target.scrollIntoView !== "function") return;
    target.scrollIntoView({
      block: "nearest",
      behavior: prefersReducedMotion() ? "auto" : "smooth",
    });
  }, [events.length]);

  const lastStartedTool = [...events]
    .reverse()
    .find((event) => event.kind === "tool_started");
  const nowTitle = pendingCount
    ? "Waiting for your approval"
    : running
      ? lastStartedTool
        ? `Running: ${toolLabel(lastStartedTool.tool)}`
        : "Thinking about your request"
      : latest?.kind === "turn_failed"
        ? "That turn hit an error"
        : events.length > 0
          ? "All caught up"
          : "Nothing running yet";
  const showPage = taskView !== null && space === "page";
  const statusTone = pendingCount
    ? "text-amber-500"
    : latest?.kind === "turn_failed"
      ? "text-destructive"
      : running
        ? "text-cta"
        : "text-muted-foreground";
  const statusHint = latest
    ? latest.kind === "turn_failed"
      ? (latest.error ?? undefined)
      : `Last update at ${formatClock(latest.created_at)}`
    : undefined;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-3 border-b px-5 py-2.5">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <Radio
            className={cn("h-4 w-4 shrink-0", running ? "text-cta" : "text-muted-foreground")}
          />
          <p className="shrink-0 text-sm font-semibold">Live workspace</p>
          <span className="text-muted-foreground/40" aria-hidden>
            ·
          </span>
          <span
            className={cn("min-w-0 truncate text-xs", statusTone)}
            title={statusHint}
          >
            {nowTitle}
          </span>
          {latest && !running && latest.kind !== "turn_failed" && (
            <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground/60">
              {formatClock(latest.created_at)}
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {pendingCount > 0 && (
            <Button size="sm" variant="outline" onClick={onReviewApproval}>
              Review
            </Button>
          )}
          <Badge
            variant="outline"
            className={cn(
              "gap-1.5 transition-colors duration-500",
              running && "border-cta/60 text-cta",
            )}
          >
            <span
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                running
                  ? "animate-pulse bg-cta motion-reduce:animate-none"
                  : "bg-muted-foreground/50",
              )}
            />
            {running ? "Live" : "Idle"}
          </Badge>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col">
        {conversationId === null ? (
          <div className="flex flex-1 flex-col items-center justify-center px-5 text-center">
            <span className="mb-3 flex h-11 w-11 items-center justify-center rounded-2xl bg-cta/15 text-cta shadow-[0_0_28px_-14px_var(--cta)]">
              <Wrench className="h-5 w-5" />
            </span>
            <p className="text-sm font-medium">The workspace shows the work</p>
            <p className="mt-1 max-w-[260px] text-xs text-muted-foreground">
              Start a conversation and every search, list and approval appears
              here as it happens — no need to open another tab.
            </p>
          </div>
        ) : loading && events.length === 0 ? (
          <div className="space-y-2 px-5 py-4">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-10 w-3/4" />
            <Skeleton className="h-10 w-2/3" />
          </div>
        ) : (
          <>
            {error && (
              <p className="flex shrink-0 items-center gap-1.5 border-b border-destructive/20 bg-destructive/5 px-5 py-1.5 text-[11px] text-destructive">
                <AlertTriangle className="h-3 w-3 shrink-0" />
                {error}
              </p>
            )}

            {taskView && (
              <div
                role="tablist"
                aria-label="Workspace view"
                className="mx-5 mt-3 flex shrink-0 items-center gap-1 rounded-xl border border-border/70 bg-muted/30 p-1 transition-colors duration-500"
              >
                {(
                  [
                    { key: "page", label: taskView.title },
                    { key: "activity", label: "Activity" },
                  ] as const
                ).map(({ key, label }) => (
                  <button
                    key={key}
                    type="button"
                    role="tab"
                    aria-selected={space === key}
                    onClick={() => setSpace(key)}
                    className={cn(
                      "flex flex-1 items-center justify-center gap-1.5 truncate rounded-lg px-2 py-1.5 text-xs transition-colors duration-300",
                      space === key
                        ? "bg-cta/15 font-medium text-foreground"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    <span className="truncate">{label}</span>
                    {key === "activity" && running && (
                      <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-cta motion-reduce:animate-none" />
                    )}
                    {key === "page" && pendingCount > 0 && (
                      <span className="flex h-4 min-w-4 shrink-0 items-center justify-center rounded-full bg-amber-500/20 px-1 text-[10px] font-semibold text-amber-500">
                        {pendingCount}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}

            {showPage && taskView ? (
              <div className="mt-3 flex min-h-0 flex-1 flex-col border-t border-border/60">
                <WorkspacePageView view={taskView} />
              </div>
            ) : (
              <div className="mt-3 flex-1 space-y-4 overflow-y-auto px-5 pb-4 pt-1">
                {artifacts.length > 0 && (
                  <div className="space-y-2.5">
                    {artifacts.map((artifact) => {
                      if (artifact.kind === "lead_search") {
                        return (
                          <LeadSearchTracker
                            key={artifact.key}
                            searchId={artifact.searchId}
                            name={artifact.name}
                            authHeaders={authHeaders}
                          />
                        );
                      }
                      if (artifact.kind === "leads") {
                        return <LeadsTableCard key={artifact.key} result={artifact.result} />;
                      }
                      if (artifact.kind === "overview") {
                        return <OverviewCard key={artifact.key} result={artifact.result} />;
                      }
                      return (
                        <GenericResultCard
                          key={artifact.key}
                          tool={artifact.tool}
                          result={artifact.result}
                          error={artifact.error}
                        />
                      );
                    })}
                  </div>
                )}

                {stream.length > 0 && (
                  <div>
                    <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                      Activity
                    </p>
                    <ol className="space-y-0">
                      {stream.map((event) => (
                        <ActivityRow key={event.id} event={event} />
                      ))}
                    </ol>
                  </div>
                )}

                <div ref={streamEndRef} />
              </div>
            )}
          </>
        )}
      </div>

      <p className="border-t px-5 py-2.5 text-[11px] text-muted-foreground/70">
        {showPage
          ? "Live dashboard · the same page as your sidebar"
          : `Updates automatically · keeps the last ${STREAM_LIMIT} steps`}
      </p>
    </div>
  );
}
