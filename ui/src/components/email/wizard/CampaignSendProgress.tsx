"use client";

import { Loader2, Pause, Play, RotateCcw, Square, Users } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    getEmailCampaignApiV1EmailCampaignsCampaignUuidGet,
    listCampaignMessagesApiV1EmailCampaignsCampaignUuidMessagesGet,
    pauseEmailCampaignApiV1EmailCampaignsCampaignUuidPausePost,
    restartEmailCampaignApiV1EmailCampaignsCampaignUuidRestartPost,
    resumeEmailCampaignApiV1EmailCampaignsCampaignUuidResumePost,
    stopEmailCampaignApiV1EmailCampaignsCampaignUuidStopPost,
} from "@/client";
import type {
    CampaignMessagesResponse,
    EmailCampaignResponse,
} from "@/client/types.gen";
import {
    AlertDialog,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CountUp } from "@/components/ui/count-up";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

const ACTIVE_STATUSES = new Set(["launching", "sending", "paused"]);
const POLL_INTERVAL_MS = 2500;

function statusVariant(status: string) {
    if (status === "sent") return "success" as const;
    if (status === "failed" || status === "bounced" || status === "complained") {
        return "destructive" as const;
    }
    if (status === "sending" || status === "queued" || status === "held") {
        return "secondary" as const;
    }
    return "outline" as const;
}

interface Props {
    campaign: EmailCampaignResponse;
    onCampaignChange: (campaign: EmailCampaignResponse) => void;
}

export function CampaignSendProgress({ campaign, onCampaignChange }: Props) {
    const { getAccessToken } = useAuth();
    const [messages, setMessages] = useState<CampaignMessagesResponse | null>(null);
    const [busy, setBusy] = useState<"pause" | "resume" | "stop" | "restart" | null>(
        null,
    );
    const [confirmAction, setConfirmAction] = useState<"stop" | "restart" | null>(
        null,
    );
    const lastFetch = useRef(0);

    const isActive = ACTIVE_STATUSES.has(campaign.status);

    const refresh = useCallback(async () => {
        const token = await getAccessToken();
        const [campaignRes, messagesRes] = await Promise.all([
            getEmailCampaignApiV1EmailCampaignsCampaignUuidGet({
                headers: { Authorization: `Bearer ${token}` },
                path: { campaign_uuid: campaign.uuid },
            }),
            listCampaignMessagesApiV1EmailCampaignsCampaignUuidMessagesGet({
                headers: { Authorization: `Bearer ${token}` },
                path: { campaign_uuid: campaign.uuid },
                query: { page: 1, limit: 25 },
            }),
        ]);
        if (campaignRes.data) onCampaignChange(campaignRes.data);
        if (messagesRes.data) setMessages(messagesRes.data);
        lastFetch.current = Date.now();
    }, [campaign.uuid, getAccessToken, onCampaignChange]);

    useEffect(() => {
        void refresh();
    }, [refresh]);

    useEffect(() => {
        if (!isActive) return;
        const timer = window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
        return () => window.clearInterval(timer);
    }, [isActive, refresh]);

    const progress = campaign.progress ?? {};
    const total = progress.total ?? 0;
    const sent = progress.sent ?? 0;
    const failed = progress.failed ?? 0;
    const cancelled = progress.cancelled ?? 0;
    const queued = progress.queued ?? 0;
    const sending = progress.sending ?? 0;
    const bounced = progress.bounced ?? 0;
    const complained = progress.complained ?? 0;
    const settled = sent + failed + cancelled + bounced + complained;
    const percent = total > 0 ? Math.min(100, Math.round((settled / total) * 100)) : 0;

    const act = async (action: "pause" | "resume" | "stop" | "restart") => {
        setBusy(action);
        try {
            const token = await getAccessToken();
            const options = {
                headers: { Authorization: `Bearer ${token}` },
                path: { campaign_uuid: campaign.uuid },
            };
            const response =
                action === "pause"
                    ? await pauseEmailCampaignApiV1EmailCampaignsCampaignUuidPausePost(options)
                    : action === "resume"
                      ? await resumeEmailCampaignApiV1EmailCampaignsCampaignUuidResumePost(options)
                      : action === "restart"
                        ? await restartEmailCampaignApiV1EmailCampaignsCampaignUuidRestartPost(
                              options,
                          )
                        : await stopEmailCampaignApiV1EmailCampaignsCampaignUuidStopPost(options);
            if (response.error) {
                toast.error(detailFromError(response.error, `Could not ${action} the campaign`));
                return;
            }
            if (response.data) onCampaignChange(response.data);
            toast.success(
                action === "pause"
                    ? "Campaign paused — queued emails wait for resume"
                    : action === "resume"
                      ? "Campaign resumed"
                      : action === "restart"
                        ? "Campaign restarted — undelivered emails are queued again"
                        : "Campaign stopped",
            );
            await refresh();
        } catch (cause) {
            toast.error(
                cause instanceof Error ? cause.message : `Could not ${action} the campaign`,
            );
        } finally {
            setBusy(null);
        }
    };

    return (
        <div className="space-y-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-lg font-semibold">Sending progress</h2>
                        <Badge variant={campaign.status === "completed" ? "success" : campaign.status === "failed" ? "destructive" : "secondary"}>
                            {campaign.status}
                        </Badge>
                        {isActive && (
                            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                live
                            </span>
                        )}
                    </div>
                    <p className="mt-1 text-sm text-muted-foreground">
                        {campaign.status === "cancelled" ||
                        campaign.status === "failed"
                            ? "This campaign is stopped. Restart it to queue every recipient that has not received the email yet."
                            : "Updates every few seconds. Paused campaigns keep their queued recipients and continue when resumed."}
                    </p>
                </div>
                <div className="flex flex-wrap gap-2">
                    {campaign.status === "sending" && (
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={() => void act("pause")}
                            disabled={busy !== null}
                        >
                            {busy === "pause" ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <Pause className="h-4 w-4" />
                            )}
                            Pause
                        </Button>
                    )}
                    {campaign.status === "paused" && (
                        <Button
                            size="sm"
                            onClick={() => void act("resume")}
                            disabled={busy !== null}
                        >
                            {busy === "resume" ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <Play className="h-4 w-4" />
                            )}
                            Resume
                        </Button>
                    )}
                    {(campaign.status === "cancelled" ||
                        campaign.status === "failed") && (
                        <Button
                            size="sm"
                            onClick={() => setConfirmAction("restart")}
                            disabled={busy !== null}
                        >
                            {busy === "restart" ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <RotateCcw className="h-4 w-4" />
                            )}
                            Restart campaign
                        </Button>
                    )}
                    {isActive && (
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setConfirmAction("stop")}
                            disabled={busy !== null}
                            className="text-destructive hover:text-destructive"
                        >
                            {busy === "stop" ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <Square className="h-4 w-4" />
                            )}
                            Cancel campaign
                        </Button>
                    )}
                </div>
            </div>

            {campaign.failure_reason && (
                <div
                    role="alert"
                    className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive"
                >
                    {campaign.failure_reason}
                </div>
            )}

            <div className="space-y-2 rounded-xl border bg-card p-4">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="text-sm font-medium">
                        <CountUp value={settled} /> of <CountUp value={total} /> processed
                    </span>
                    <span className="text-xs text-muted-foreground tabular-nums">
                        {percent}%
                    </span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-muted">
                    <div
                        className="h-full rounded-full bg-cta transition-[width] duration-500 motion-reduce:transition-none"
                        style={{ width: `${percent}%` }}
                    />
                </div>
                <div className="grid grid-cols-2 gap-2 pt-1 sm:grid-cols-3 lg:grid-cols-6">
                    <Counter label="Sent" value={sent} tone="text-emerald-600 dark:text-emerald-400" />
                    <Counter label="Queued" value={queued} />
                    <Counter label="Sending" value={sending} />
                    <Counter label="Failed" value={failed} tone="text-destructive" />
                    <Counter label="Bounced" value={bounced} />
                    <Counter label="Cancelled" value={cancelled} />
                </div>
            </div>

            <div className="space-y-2">
                <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold">Recent recipients</h3>
                    <span className="text-xs text-muted-foreground">
                        {messages?.total ?? 0} total · addresses masked
                    </span>
                </div>
                {!messages || messages.items.length === 0 ? (
                    <p className="rounded-lg border bg-muted/30 p-4 text-center text-sm text-muted-foreground">
                        No recipient rows yet. Rows appear as soon as the campaign is
                        launched.
                    </p>
                ) : (
                    <div className="overflow-x-auto rounded-lg border">
                        <table className="w-full text-sm">
                            <thead className="border-b bg-muted/40">
                                <tr>
                                    <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                                        Address
                                    </th>
                                    <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                                        Status
                                    </th>
                                    <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                                        Detail
                                    </th>
                                </tr>
                            </thead>
                            <tbody>
                                {messages.items.map((item, index) => (
                                    <tr
                                        key={`${item.email}-${index}`}
                                        className="border-b last:border-0"
                                    >
                                        <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">
                                            {item.email}
                                        </td>
                                        <td className="px-3 py-2">
                                            <Badge variant={statusVariant(item.status)}>
                                                {item.status}
                                            </Badge>
                                        </td>
                                        <td className="max-w-[320px] truncate px-3 py-2 text-xs text-muted-foreground">
                                            {item.error_message ||
                                                (item.sent_at
                                                    ? `sent ${new Date(item.sent_at).toLocaleTimeString()}`
                                                    : "—")}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {campaign.status === "completed" && (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Users className="h-4 w-4" />
                    This campaign is complete. Delivered addresses stay recorded for
                    audit.
                </p>
            )}

            <AlertDialog
                open={confirmAction !== null}
                onOpenChange={(open) => {
                    if (!open && busy === null) setConfirmAction(null);
                }}
            >
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>
                            {confirmAction === "restart"
                                ? "Restart campaign?"
                                : "Cancel campaign?"}
                        </AlertDialogTitle>
                        <AlertDialogDescription>
                            {confirmAction === "restart"
                                ? "Recipients that were never delivered are queued again. Addresses that already received an email are not mailed twice."
                                : "Emails already sent stay sent; everything still queued is cancelled. You can restart the campaign later."}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={busy !== null}>
                            Keep campaign
                        </AlertDialogCancel>
                        <Button
                            variant={confirmAction === "stop" ? "destructive" : "default"}
                            disabled={busy !== null}
                            onClick={() => {
                                const action = confirmAction;
                                setConfirmAction(null);
                                if (action) void act(action);
                            }}
                        >
                            {busy !== null && (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            )}
                            {confirmAction === "restart"
                                ? "Restart campaign"
                                : "Cancel campaign"}
                        </Button>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}

function Counter({
    label,
    value,
    tone,
}: {
    label: string;
    value: number;
    tone?: string;
}) {
    return (
        <div className="rounded-lg border bg-muted/20 px-2.5 py-2">
            <p className="text-xs text-muted-foreground">{label}</p>
            <CountUp value={value} className={`block text-base font-semibold ${tone ?? ""}`} />
        </div>
    );
}
