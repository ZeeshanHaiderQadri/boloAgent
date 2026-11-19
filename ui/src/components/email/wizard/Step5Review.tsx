"use client";

import { AlertTriangle, CheckCircle2, Lock, XCircle } from "lucide-react";

import type { EmailCampaignResponse } from "@/client/types.gen";
import { Button } from "@/components/ui/button";

const BUCKET_LABELS: Array<[string, string]> = [
    ["recipient_count", "Total matched"],
    ["excluded_no_email_count", "No email"],
    ["excluded_invalid_email_count", "Invalid email"],
    ["excluded_suppressed_count", "Suppressed"],
    ["excluded_opted_out_count", "Opted out"],
    ["excluded_unknown_count", "Unknown consent (excluded)"],
    ["excluded_manual_count", "Manually excluded"],
    ["duplicates_collapsed_count", "Duplicates collapsed"],
    ["eligible_count", "Eligible"],
];

interface ReadinessItem {
    ok: boolean;
    label: string;
}

function buildChecklist(campaign: EmailCampaignResponse): ReadinessItem[] {
    const items: ReadinessItem[] = [];

    items.push({
        ok: Boolean(campaign.sender),
        label: campaign.sender ? `Sender: ${campaign.sender.name}` : "No sender selected",
    });
    items.push({
        ok: campaign.sender?.status === "verified",
        label:
            campaign.sender?.status === "verified"
                ? "Sender is verified"
                : "Sender is not verified",
    });
    items.push({
        ok: Boolean(campaign.template),
        label: campaign.template ? `Template: ${campaign.template.name}` : "No template selected",
    });
    items.push({
        ok: campaign.template?.has_unsubscribe_placeholder !== false,
        label:
            campaign.template?.has_unsubscribe_placeholder !== false
                ? "Template has unsubscribe link"
                : "Template is missing unsubscribe link — launch will be blocked",
    });
    items.push({
        ok: campaign.counts.eligible_count > 0,
        label:
            campaign.counts.eligible_count > 0
                ? `${campaign.counts.eligible_count.toLocaleString()} eligible recipients`
                : "No eligible recipients — run a preview first",
    });
    if (campaign.consent_policy === "include_unknown") {
        items.push({
            ok: Boolean(campaign.consent_override_reason?.trim()),
            label: campaign.consent_override_reason?.trim()
                ? "Consent override reason recorded"
                : "Include-unknown requires a written reason (Step 3)",
        });
    }

    return items;
}

interface Props {
    campaign: EmailCampaignResponse;
    onBack: () => void;
}

export function Step5Review({ campaign, onBack }: Props) {
    const checklist = buildChecklist(campaign);
    const guidance = campaign.sender?.guidance ?? "—";

    return (
        <div className="space-y-5">
            <div>
                <h2 className="text-base font-semibold">Step 5 — Review and Launch</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                    Review the campaign readiness before launching. Sending is currently
                    disabled; the Launch button is permanently disabled in this release.
                </p>
            </div>

            {/* Sending disabled banner */}
            <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-400">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <p>
                    <code className="text-xs">EMAIL_CAMPAIGN_SENDING_ENABLED</code> is off.
                    No email will be sent.
                </p>
            </div>

            <div className="grid gap-6 lg:grid-cols-2">
                {/* Readiness checklist */}
                <div className="space-y-2">
                    <h3 className="text-sm font-semibold">Readiness checklist</h3>
                    <ul className="space-y-2">
                        {checklist.map((item, i) => (
                            <li key={i} className="flex items-start gap-2 text-sm">
                                {item.ok ? (
                                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
                                ) : (
                                    <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                                )}
                                <span className={item.ok ? "" : "text-destructive"}>
                                    {item.label}
                                </span>
                            </li>
                        ))}
                    </ul>
                </div>

                {/* Stored counts */}
                <div className="space-y-2">
                    <h3 className="text-sm font-semibold">Stored counts</h3>
                    <div className="grid grid-cols-2 gap-2">
                        {BUCKET_LABELS.map(([key, label]) => {
                            const counts = campaign.counts as unknown as Record<string, number>;
                            const value = counts[key] ?? 0;
                            return (
                                <div
                                    key={key}
                                    className={`rounded-lg border p-2 ${
                                        key === "eligible_count"
                                            ? "border-emerald-500/30 bg-emerald-500/5"
                                            : "bg-muted/30"
                                    }`}
                                >
                                    <p className="text-xs text-muted-foreground">{label}</p>
                                    <p className="mt-0.5 text-base font-semibold tabular-nums">
                                        {value.toLocaleString()}
                                    </p>
                                </div>
                            );
                        })}
                    </div>
                    <p className="text-xs text-muted-foreground">
                        Daily sending estimate: {guidance}
                    </p>
                </div>
            </div>

            <div className="flex gap-3">
                <Button variant="outline" size="sm" onClick={onBack}>
                    Back
                </Button>
                <Button
                    size="sm"
                    disabled
                    title="Sending is currently disabled"
                    className="cursor-not-allowed"
                >
                    <Lock className="mr-1.5 h-3.5 w-3.5" />
                    Launch
                </Button>
            </div>
        </div>
    );
}
