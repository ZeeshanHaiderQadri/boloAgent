"use client";

import { CheckCircle2, Loader2, Rocket, XCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import {
    getEmailCampaignApiV1EmailCampaignsCampaignUuidGet,
    launchEmailCampaignApiV1EmailCampaignsCampaignUuidLaunchPost,
} from "@/client";
import type {
    EmailCampaignResponse,
    LaunchResponse,
} from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { CountUp } from "@/components/ui/count-up";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

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
                : campaign.consent_policy !== "include_unknown" &&
                    campaign.counts.excluded_unknown_count > 0
                  ? `${campaign.counts.excluded_unknown_count.toLocaleString()} lead(s) have no recorded consent. Set Step 3 to include unknown consent, or record consent on the leads.`
                  : "No eligible recipients match this audience",
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
    onCampaignChange: (campaign: EmailCampaignResponse) => void;
    onBack: () => void;
}

export function Step5Review({ campaign, onCampaignChange, onBack }: Props) {
    const { getAccessToken } = useAuth();
    const checklist = buildChecklist(campaign);
    const guidance = campaign.sender?.guidance ?? "—";
    const locallyReady = checklist.every((item) => item.ok);
    const launchable = locallyReady && (campaign.status === "draft" || campaign.status === "ready");

    const [launching, setLaunching] = useState(false);
    const [preconditions, setPreconditions] = useState<string[]>([]);
    const [result, setResult] = useState<LaunchResponse | null>(null);

    const handleLaunch = async () => {
        setLaunching(true);
        setPreconditions([]);
        setResult(null);
        try {
            const token = await getAccessToken();
            const response = await launchEmailCampaignApiV1EmailCampaignsCampaignUuidLaunchPost({
                headers: { Authorization: `Bearer ${token}` },
                path: { campaign_uuid: campaign.uuid },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Launch failed"));
                return;
            }
            const body = response.data;
            if (!body) return;
            if (!body.ready) {
                setPreconditions(body.preconditions);
                toast.error("Launch is blocked. Fix the listed items and retry.");
            } else {
                setResult(body);
                toast.success(body.message);
            }
            const refreshed = await getEmailCampaignApiV1EmailCampaignsCampaignUuidGet({
                headers: { Authorization: `Bearer ${token}` },
                path: { campaign_uuid: campaign.uuid },
            });
            if (refreshed.data) onCampaignChange(refreshed.data);
        } catch (cause) {
            toast.error(cause instanceof Error ? cause.message : "Launch failed");
        } finally {
            setLaunching(false);
        }
    };

    return (
        <div className="space-y-5">
            <div>
                <h2 className="text-base font-semibold">Step 5 — Review and Launch</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                    Launching freezes this audience and queues one email per eligible
                    recipient. Emails leave through the selected sender; campaign
                    sending must be enabled on the deployment.
                </p>
            </div>

            {result && (
                <div className="flex items-start gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm text-emerald-700 dark:text-emerald-400">
                    <Rocket className="mt-0.5 h-4 w-4 shrink-0" />
                    <p>{result.message}</p>
                </div>
            )}

            {preconditions.length > 0 && (
                <div className="rounded-lg border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive">
                    <p className="font-medium">Launch is blocked:</p>
                    <ul className="mt-1 list-disc space-y-1 pl-5">
                        {preconditions.map((failure) => (
                            <li key={failure}>{failure}</li>
                        ))}
                    </ul>
                </div>
            )}

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

                {/* Audience counts (from the latest preview) */}
                <div className="space-y-2">
                    <h3 className="text-sm font-semibold">Audience counts</h3>
                    {campaign.counts.recipient_count === 0 ? (
                        <div className="rounded-lg border bg-muted/30 p-4 text-center">
                            <p className="text-sm text-muted-foreground">
                                Not calculated yet.
                            </p>
                            <p className="mt-1 text-xs text-muted-foreground">
                                Go back to step 4 and continue to resolve the audience.
                            </p>
                        </div>
                    ) : (
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
                                        <CountUp
                                            value={value}
                                            className="mt-0.5 block text-base font-semibold"
                                        />
                                    </div>
                                );
                            })}
                        </div>
                    )}
                    <p className="text-xs text-muted-foreground">
                        Daily sending estimate: {guidance}
                    </p>
                </div>
            </div>

            <div className="flex gap-3">
                <Button variant="outline" size="sm" onClick={onBack} disabled={launching}>
                    Back
                </Button>
                <Button
                    size="sm"
                    onClick={() => void handleLaunch()}
                    disabled={!launchable || launching}
                    title={
                        launchable
                            ? "Freeze the audience and queue the campaign"
                            : "Fix the checklist items before launching"
                    }
                >
                    {launching ? (
                        <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                    ) : (
                        <Rocket className="mr-1.5 h-3.5 w-3.5" />
                    )}
                    {launching ? "Launching…" : "Launch campaign"}
                </Button>
                {!launchable && !launching && (
                    <p className="self-center text-xs text-muted-foreground">
                        {campaign.status === "draft" || campaign.status === "ready"
                            ? "Resolve the red checklist items first."
                            : `This campaign is ${campaign.status}.`}
                    </p>
                )}
            </div>
        </div>
    );
}
