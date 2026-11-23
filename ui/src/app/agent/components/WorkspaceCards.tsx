"use client";

import {
  ArrowUpRight,
  CircleAlert,
  ExternalLink,
  Loader2,
  MapPin,
  Search,
  Star,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { getLeadSearchApiV1LeadsSearchesSearchIdGet } from "@/client/sdk.gen";
import type {
  CopilotActivityEvent,
  LeadSearchResponse,
} from "@/client/types.gen";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import type { AuthHeaders } from "../hooks/useAgentActivity";
import { toolLabel } from "../lib/labels";

const ACTIVE_SEARCH_STATUSES = new Set(["queued", "running", "pending"]);
const TRACKER_POLL_MS = 2500;

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatDuration(durationMs: number | null | undefined) {
  if (!durationMs || durationMs < 0) return null;
  if (durationMs < 1000) return `${durationMs}ms`;
  return `${(durationMs / 1000).toFixed(1)}s`;
}

function CardShell({
  title,
  icon,
  link,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  link?: { href: string; label: string };
  children: React.ReactNode;
}) {
  return (
    <div className="animate-in fade-in slide-in-from-bottom-2 rounded-2xl border border-border/60 bg-background/50 p-3.5 duration-500">
      <div className="mb-2.5 flex items-center gap-2">
        <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-cta/15 text-cta">
          {icon}
        </span>
        <p className="min-w-0 flex-1 truncate text-sm font-medium">{title}</p>
        {link && (
          <Link
            href={link.href}
            className="flex shrink-0 items-center gap-0.5 text-xs text-muted-foreground transition-colors duration-300 hover:text-cta"
          >
            {link.label}
            <ArrowUpRight className="h-3 w-3" />
          </Link>
        )}
      </div>
      {children}
    </div>
  );
}

export function LeadSearchTracker({
  searchId,
  name,
  authHeaders,
}: {
  searchId: number;
  name: string | null;
  authHeaders: AuthHeaders;
}) {
  const [search, setSearch] = useState<LeadSearchResponse | null>(null);
  const authHeadersRef = useRef(authHeaders);

  useEffect(() => {
    authHeadersRef.current = authHeaders;
  }, [authHeaders]);

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    let controller: AbortController | null = null;

    const poll = async () => {
      if (stopped) return;
      controller = new AbortController();
      let active = true;
      try {
        const response = await getLeadSearchApiV1LeadsSearchesSearchIdGet({
          path: { search_id: searchId },
          headers: await authHeadersRef.current(),
          signal: controller.signal,
        });
        if (stopped) return;
        if (response.data) {
          setSearch(response.data);
          active = ACTIVE_SEARCH_STATUSES.has(response.data.status);
        }
      } catch {
        // A transient tracker miss must not break the panel; the next poll retries.
      } finally {
        if (!stopped && active) {
          timer = window.setTimeout(poll, TRACKER_POLL_MS);
        }
      }
    };

    void poll();

    return () => {
      stopped = true;
      controller?.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [searchId]);

  const status = search?.status ?? "queued";
  const running = ACTIVE_SEARCH_STATUSES.has(status);
  const target = search?.target_results ?? null;
  const results = search?.results_count ?? 0;
  const progress =
    target && target > 0 ? Math.min(100, Math.round((results / target) * 100)) : null;

  return (
    <CardShell
      title={name ?? search?.name ?? "Lead search"}
      icon={<Search className="h-3.5 w-3.5" />}
      link={{ href: "/leads/searches", label: "Searches" }}
    >
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        {running ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-cta" />
        ) : status === "completed" ? (
          <span className="h-2 w-2 rounded-full bg-emerald-500" />
        ) : (
          <CircleAlert className="h-3.5 w-3.5 text-destructive" />
        )}
        <span className="capitalize">{status.replaceAll("_", " ")}</span>
        <span className="ml-auto tabular-nums">
          {results}
          {target ? ` / ${target}` : ""} found
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-700",
            running ? "bg-cta/70 animate-pulse" : "bg-emerald-500",
          )}
          style={{ width: `${progress ?? (running ? 15 : 100)}%` }}
        />
      </div>
      <div className="mt-2 flex items-center justify-between text-[11px] text-muted-foreground">
        <span>{search?.new_leads_count ?? 0} new leads added</span>
        {search?.error_message ? (
          <span className="text-destructive">{search.error_message}</span>
        ) : null}
      </div>
    </CardShell>
  );
}

type LeadRow = {
  title?: unknown;
  city?: unknown;
  rating?: unknown;
  crm_status?: unknown;
};

export function LeadsTableCard({
  result,
}: {
  result: Record<string, unknown>;
}) {
  const leads = Array.isArray(result.leads) ? (result.leads as LeadRow[]) : [];
  if (leads.length === 0) return null;
  const total = num(result.total) ?? leads.length;
  return (
    <CardShell
      title={`Leads · ${total} match${total === 1 ? "" : "es"}`}
      icon={<MapPin className="h-3.5 w-3.5" />}
      link={{ href: "/leads", label: "Open CRM" }}
    >
      <ul className="divide-y divide-border/50">
        {leads.slice(0, 6).map((lead, index) => {
          const rating = num(lead.rating);
          return (
            <li
              key={index}
              className="flex items-center gap-2 py-1.5 text-xs transition-colors duration-300"
            >
              <span className="min-w-0 flex-1 truncate text-foreground/90">
                {str(lead.title) ?? "Untitled lead"}
              </span>
              {str(lead.city) && (
                <span className="shrink-0 text-muted-foreground">
                  {str(lead.city)}
                </span>
              )}
              {rating !== null && (
                <span className="flex shrink-0 items-center gap-0.5 text-amber-500">
                  <Star className="h-3 w-3 fill-current" />
                  {rating.toFixed(1)}
                </span>
              )}
              {str(lead.crm_status) && (
                <Badge
                  variant="outline"
                  className="shrink-0 px-1.5 py-0 text-[10px] capitalize"
                >
                  {String(lead.crm_status).replaceAll("_", " ")}
                </Badge>
              )}
            </li>
          );
        })}
      </ul>
      {leads.length > 6 && (
        <p className="mt-1.5 text-[11px] text-muted-foreground">
          +{leads.length - 6} more in this view
        </p>
      )}
    </CardShell>
  );
}

export function OverviewCard({ result }: { result: Record<string, unknown> }) {
  const leads = asRecord(result.leads) ?? {};
  const email = asRecord(result.email_campaigns) ?? {};
  const tiles = [
    { label: "Leads", value: num(leads.total) },
    { label: "With email", value: num(leads.with_email) },
    { label: "New", value: num(leads.new) },
    { label: "Emails sent", value: num(email.sent) },
  ];
  return (
    <CardShell
      title="Workspace overview"
      icon={<Star className="h-3.5 w-3.5" />}
      link={{ href: "/overview", label: "Overview" }}
    >
      <div className="grid grid-cols-2 gap-2">
        {tiles.map((tile) => (
          <div
            key={tile.label}
            className="rounded-xl border border-border/50 bg-muted/20 px-2.5 py-2"
          >
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
              {tile.label}
            </p>
            <p className="text-lg font-semibold tabular-nums">
              {tile.value ?? "—"}
            </p>
          </div>
        ))}
      </div>
    </CardShell>
  );
}

function genericLink(result: Record<string, unknown>) {
  if (str(result.campaign_uuid)) {
    return { href: "/email/campaigns", label: "Email campaigns" };
  }
  if (num(result.campaign_id)) {
    return { href: "/campaigns", label: "Campaigns" };
  }
  return undefined;
}

export function GenericResultCard({
  tool,
  result,
  error,
}: {
  tool: string | null;
  result: unknown;
  error?: string | null;
}) {
  const record = asRecord(result);
  if (!record && !error) return null;
  return (
    <CardShell
      title={toolLabel(tool)}
      icon={<ExternalLink className="h-3.5 w-3.5" />}
      link={record ? genericLink(record) : undefined}
    >
      {error ? (
        <p className="text-xs text-destructive">{error}</p>
      ) : (
        <details className="group">
          <summary className="cursor-pointer list-none text-xs text-muted-foreground transition-colors duration-300 hover:text-foreground">
            Show result
          </summary>
          <pre className="mt-2 max-h-44 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted/40 p-2 text-[11px] text-muted-foreground">
            {JSON.stringify(record, null, 2)}
          </pre>
        </details>
      )}
    </CardShell>
  );
}

export type WorkspaceArtifact =
  | { kind: "lead_search"; key: string; searchId: number; name: string | null }
  | { kind: "leads"; key: string; result: Record<string, unknown> }
  | { kind: "overview"; key: string; result: Record<string, unknown> }
  | {
      kind: "generic";
      key: string;
      tool: string | null;
      result: unknown;
      error: string | null;
    };

const MAX_ARTIFACTS = 4;

export function deriveArtifacts(events: CopilotActivityEvent[]): WorkspaceArtifact[] {
  const artifacts: WorkspaceArtifact[] = [];
  const seen = new Set<string>();
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.kind !== "tool_finished") continue;
    const record = asRecord(event.result);
    const tool = event.tool ?? null;
    let artifact: WorkspaceArtifact | null = null;
    const searchId = num(record?.search_id);
    if (
      searchId !== null &&
      (tool === "start_lead_search" || tool === "lead_search_status")
    ) {
      artifact = {
        kind: "lead_search",
        key: `search:${searchId}`,
        searchId,
        name: str(record?.name),
      };
    } else if (tool === "search_leads" && record) {
      artifact = { kind: "leads", key: "leads", result: record };
    } else if (tool === "business_overview" && record) {
      artifact = { kind: "overview", key: "overview", result: record };
    } else if (tool && (record || event.status === "error")) {
      artifact = {
        kind: "generic",
        key: `tool:${tool}`,
        tool,
        result: event.result,
        error: event.status === "executed" ? null : (event.error ?? null),
      };
    }
    if (artifact && !seen.has(artifact.key)) {
      seen.add(artifact.key);
      artifacts.push(artifact);
    }
    if (artifacts.length >= MAX_ARTIFACTS) break;
  }
  return artifacts;
}

export { formatDuration };
