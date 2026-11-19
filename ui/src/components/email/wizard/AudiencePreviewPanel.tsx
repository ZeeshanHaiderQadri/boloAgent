"use client";

import { AlertCircle, Check, Loader2 } from "lucide-react";

import type { CampaignCountsResponse, EmailCampaignResponse } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { CountUp } from "@/components/ui/count-up";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface Bucket {
    key: keyof CampaignCountsResponse;
    label: string;
    tone?: "eligible";
}

const BUCKETS: Bucket[] = [
    { key: "eligible_count", label: "Eligible", tone: "eligible" },
    { key: "excluded_no_email_count", label: "No email" },
    { key: "excluded_invalid_email_count", label: "Invalid email" },
    { key: "excluded_suppressed_count", label: "Suppressed" },
    { key: "excluded_opted_out_count", label: "Opted out" },
    { key: "excluded_unknown_count", label: "Unknown consent" },
    { key: "excluded_manual_count", label: "Manually excluded" },
    { key: "duplicates_collapsed_count", label: "Duplicates collapsed" },
];

interface Props {
    campaign: EmailCampaignResponse;
    /** True while the real preview request is in flight. */
    previewing?: boolean;
    error?: string | null;
    onRetry?: () => void;
}

/**
 * Shows the audience preview as it actually resolves. The API returns every
 * bucket in one response, so while the request is running this shows a single
 * honest "resolving / checking" state; the counts then animate from the real
 * numbers the response carried. There are no timers that advance the UI.
 */
export function AudiencePreviewPanel({ campaign, previewing = false, error, onRetry }: Props) {
    const counts = campaign.counts as CampaignCountsResponse;
    const total = counts.recipient_count;
    const eligible = counts.eligible_count;
    const excluded = Math.max(0, total - eligible);
    const percent = total > 0 ? Math.round((eligible / total) * 100) : 0;
    const bucketSignature = previewing
        ? "pending"
        : BUCKETS.map((bucket) => counts[bucket.key] ?? 0).join("-");

    return (
        <section
            aria-busy={previewing}
            aria-label="Audience preview"
            className="space-y-3 rounded-xl border bg-muted/20 p-4"
        >
            <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-semibold text-foreground">Audience preview</h3>
                <span className="text-xs font-medium text-muted-foreground">
                    {previewing ? "Updating…" : error ? "Could not resolve" : "Live from the last preview"}
                </span>
            </div>

            {error ? (
                <div
                    role="alert"
                    className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive"
                >
                    <span className="flex items-start gap-2">
                        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                        {error}
                    </span>
                    {onRetry && (
                        <Button variant="outline" size="sm" onClick={onRetry}>
                            Retry preview
                        </Button>
                    )}
                </div>
            ) : previewing ? (
                <div className="space-y-3" role="status">
                    <ul className="space-y-1.5 text-sm">
                        {["Resolving audience", "Checking suppression and consent", "Counting recipients"].map(
                            (label) => (
                                <li key={label} className="flex items-center gap-2 text-muted-foreground">
                                    <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" aria-hidden />
                                    {label}
                                </li>
                            ),
                        )}
                    </ul>
                    <Skeleton className="h-2 w-full rounded-full" />
                    <div className="grid gap-2 sm:grid-cols-2">
                        {[1, 2, 3, 4].map((item) => (
                            <Skeleton key={item} className="h-12 w-full rounded-lg" />
                        ))}
                    </div>
                    <span className="sr-only">Resolving the audience. Counts will appear when the request finishes.</span>
                </div>
            ) : total === 0 ? (
                <div className="rounded-lg border border-dashed p-4 text-center">
                    <p className="text-sm font-medium text-foreground">No recipients resolved yet</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                        This audience matched nothing, or the preview has not run. Change the audience or retry the preview.
                    </p>
                    {onRetry && (
                        <Button variant="outline" size="sm" className="mt-3" onClick={onRetry}>
                            Run preview
                        </Button>
                    )}
                </div>
            ) : (
                <div className="space-y-3">
                    <div className="flex items-baseline justify-between gap-3">
                        <p className="text-sm text-muted-foreground">
                            <CountUp value={eligible} className="text-base font-semibold text-foreground" />{" "}
                            of <CountUp value={total} className="font-semibold text-foreground" /> recipients
                            will receive this campaign
                        </p>
                        <span className="text-xs font-medium text-muted-foreground tabular-nums">{percent}%</span>
                    </div>
                    <Progress value={percent} aria-label={`${percent}% of matched recipients are eligible`} />
                    <p className="sr-only" aria-live="polite">
                        {eligible.toLocaleString()} eligible, {excluded.toLocaleString()} excluded,{" "}
                        {total.toLocaleString()} matched in total.
                    </p>
                    <div key={bucketSignature} className="grid gap-2 sm:grid-cols-2">
                        {BUCKETS.map((bucket, index) => {
                            const value = counts[bucket.key] ?? 0;
                            return (
                                <div
                                    key={bucket.key}
                                    style={{ animationDelay: `${Math.min(index, 8) * 40}ms` }}
                                    className={cn(
                                        "list-enter flex items-center justify-between gap-2 rounded-lg border bg-card px-3 py-2",
                                        bucket.tone === "eligible" &&
                                            "border-emerald-500/30 bg-emerald-500/5",
                                    )}
                                >
                                    <span className="text-xs font-medium text-muted-foreground">
                                        {bucket.label}
                                    </span>
                                    <CountUp
                                        value={value}
                                        className={cn(
                                            "text-sm font-semibold tabular-nums",
                                            bucket.tone === "eligible"
                                                ? "text-emerald-700 dark:text-emerald-400"
                                                : "text-foreground",
                                        )}
                                    />
                                </div>
                            );
                        })}
                    </div>
                    <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <Check className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" aria-hidden />
                        Resolved from the audience rule above. Nothing is stored until you save or launch.
                    </p>
                </div>
            )}
        </section>
    );
}
