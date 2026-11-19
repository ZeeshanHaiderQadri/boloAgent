"use client";

import {
    AlertCircle,
    AlertTriangle,
    CheckCircle2,
    ChevronLeft,
    Loader2,
    Lock,
    RefreshCw,
    XCircle,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
    getEmailCampaignApiV1EmailCampaignsCampaignUuidGet,
    listEmailSendersApiV1EmailSendersGet,
    listEmailTemplatesApiV1EmailTemplatesGet,
    previewCampaignAudienceApiV1EmailCampaignsPreviewPost,
    updateEmailCampaignApiV1EmailCampaignsCampaignUuidPatch,
} from "@/client";
import type {
    AudienceRuleRequest,
    CampaignPreviewResponse,
    EmailCampaignResponse,
    EmailSenderResponse,
    EmailTemplateSummary,
} from "@/client/types.gen";
import { PageLayout } from "@/components/layout/PageLayout";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { EMAIL_CONSENT_WARNING } from "@/lib/emailConsent";

const DRAFT_STATUSES = ["draft", "ready"];

function statusBadge(status: string) {
    const variants: Record<string, string> = {
        draft: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
        ready: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
        launching: "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300",
        sending: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900 dark:text-emerald-300",
        completed: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
        cancelled: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
        failed: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
    };
    return (
        <span
            className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${variants[status] ?? "bg-muted text-muted-foreground"}`}
        >
            {status.charAt(0).toUpperCase() + status.slice(1)}
        </span>
    );
}

function audienceSummary(campaign: EmailCampaignResponse): string {
    const a = campaign.audience as Record<string, unknown>;
    switch (campaign.audience_type) {
        case "leads": {
            const ids = (a.lead_ids as number[] | undefined) ?? [];
            return `${ids.length} lead${ids.length === 1 ? "" : "s"} selected`;
        }
        case "project":
            return `Lead project #${String(a.project_id ?? "?")}`;
        case "tag":
            return `Tag: ${String(a.tag ?? "?")}`;
        case "filter":
            return "All leads matching filter";
        default:
            return campaign.audience_type;
    }
}

const BUCKET_LABELS: [keyof CampaignPreviewResponse["counts"], string][] = [
    ["recipient_count", "Total matched"],
    ["excluded_no_email_count", "No email"],
    ["excluded_invalid_email_count", "Invalid email"],
    ["excluded_suppressed_count", "Suppressed"],
    ["excluded_opted_out_count", "Opted out"],
    ["excluded_unknown_count", "Unknown consent (excluded)"],
    ["duplicates_collapsed_count", "Duplicates collapsed"],
    ["eligible_count", "Eligible"],
];

export default function EmailCampaignDetailPage() {
    const { uuid: campaignUuid } = useParams<{ uuid: string }>();
    const { user, loading: authLoading, getAccessToken, redirectToLogin } = useAuth();

    const [campaign, setCampaign] = useState<EmailCampaignResponse | null>(null);
    const [senders, setSenders] = useState<EmailSenderResponse[]>([]);
    const [templates, setTemplates] = useState<EmailTemplateSummary[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [reload, setReload] = useState(0);

    // Form state
    const [name, setName] = useState("");
    const [senderUuid, setSenderUuid] = useState("");
    const [templateUuid, setTemplateUuid] = useState("");
    const [consentPolicy, setConsentPolicy] = useState<"opted_in_only" | "include_unknown">(
        "opted_in_only",
    );
    const [overrideReason, setOverrideReason] = useState("");
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);

    // Preview state
    const [preview, setPreview] = useState<CampaignPreviewResponse | null>(null);
    const [previewing, setPreviewing] = useState(false);
    const [previewError, setPreviewError] = useState<string | null>(null);
    const [reasonRequired, setReasonRequired] = useState(false);

    // Launch dialog (always disabled in this release)
    const [launchOpen, setLaunchOpen] = useState(false);

    useEffect(() => {
        if (!authLoading && !user) redirectToLogin();
    }, [authLoading, user, redirectToLogin]);

    useEffect(() => {
        if (authLoading || !user) return;
        let cancelled = false;
        const load = async () => {
            setLoading(true);
            setError(null);
            try {
                const token = await getAccessToken();
                const [campRes, sendersRes, templatesRes] = await Promise.all([
                    getEmailCampaignApiV1EmailCampaignsCampaignUuidGet({
                        headers: { Authorization: `Bearer ${token}` },
                        path: { campaign_uuid: campaignUuid },
                    }),
                    listEmailSendersApiV1EmailSendersGet({
                        headers: { Authorization: `Bearer ${token}` },
                    }),
                    listEmailTemplatesApiV1EmailTemplatesGet({
                        headers: { Authorization: `Bearer ${token}` },
                    }),
                ]);
                if (campRes.error) {
                    throw new Error(detailFromError(campRes.error, "Failed to load campaign"));
                }
                if (!cancelled && campRes.data) {
                    const c = campRes.data;
                    setCampaign(c);
                    setName(c.name);
                    setSenderUuid(c.sender?.uuid ?? "");
                    setTemplateUuid(c.template?.uuid ?? "");
                    setConsentPolicy(
                        c.consent_policy === "include_unknown" ? "include_unknown" : "opted_in_only",
                    );
                    setOverrideReason(c.consent_override_reason ?? "");
                }
                if (!cancelled && !sendersRes.error) setSenders(sendersRes.data ?? []);
                if (!cancelled && !templatesRes.error) setTemplates(templatesRes.data ?? []);
            } catch (cause) {
                if (!cancelled)
                    setError(cause instanceof Error ? cause.message : "Failed to load campaign");
            } finally {
                if (!cancelled) setLoading(false);
            }
        };
        void load();
        return () => {
            cancelled = true;
        };
    }, [authLoading, user, getAccessToken, campaignUuid, reload]);

    const save = async () => {
        if (!campaign || saving || !name.trim()) return;
        // Validate consent override before hitting the API
        if (consentPolicy === "include_unknown" && !overrideReason.trim()) {
            setSaveError(
                "A written reason is required when including unknown-consent leads.",
            );
            return;
        }
        setSaving(true);
        setSaveError(null);
        try {
            const token = await getAccessToken();
            const response = await updateEmailCampaignApiV1EmailCampaignsCampaignUuidPatch({
                headers: { Authorization: `Bearer ${token}` },
                path: { campaign_uuid: campaignUuid },
                body: {
                    name: name.trim(),
                    sender_uuid: senderUuid || null,
                    template_uuid: templateUuid || null,
                    consent_policy: consentPolicy,
                    consent_override_reason:
                        consentPolicy === "include_unknown"
                            ? overrideReason.trim() || null
                            : null,
                },
            });
            if (response.error) {
                throw new Error(detailFromError(response.error, "Failed to save campaign"));
            }
            if (response.data) {
                const updated = response.data;
                setCampaign(updated);
                setName(updated.name);
                setSenderUuid(updated.sender?.uuid ?? "");
                setTemplateUuid(updated.template?.uuid ?? "");
                setConsentPolicy(
                    updated.consent_policy === "include_unknown"
                        ? "include_unknown"
                        : "opted_in_only",
                );
                setOverrideReason(updated.consent_override_reason ?? "");
            }
            toast.success("Campaign saved");
        } catch (cause) {
            setSaveError(
                cause instanceof Error ? cause.message : "Failed to save campaign",
            );
        } finally {
            setSaving(false);
        }
    };

    const runPreview = async () => {
        if (!campaign || previewing) return;
        // Validate before calling the API
        if (consentPolicy === "include_unknown" && !overrideReason.trim()) {
            setReasonRequired(true);
            setPreviewError(
                "A written reason is required to preview with the include-unknown policy.",
            );
            return;
        }
        setReasonRequired(false);
        setPreviewing(true);
        setPreviewError(null);
        setPreview(null);
        try {
            const token = await getAccessToken();
            // campaign.audience_type is a separate field on the response;
            // campaign.audience only holds the detail payload (lead_ids, project_id,
            // etc.). Merge them here so the preview receives a valid AudienceRuleRequest.
            const storedDetail = (campaign.audience ?? {}) as Record<string, unknown>;
            const audience: AudienceRuleRequest = {
                audience_type: campaign.audience_type as AudienceRuleRequest["audience_type"],
                lead_ids: storedDetail.lead_ids as number[] | undefined,
                project_id: storedDetail.project_id as number | undefined,
                tag: storedDetail.tag as string | undefined,
                filters: storedDetail.filters as AudienceRuleRequest["filters"] | undefined,
                all_matching: storedDetail.all_matching as boolean | undefined,
            };
            const response = await previewCampaignAudienceApiV1EmailCampaignsPreviewPost({
                headers: { Authorization: `Bearer ${token}` },
                body: {
                    audience,
                    consent_policy: consentPolicy,
                    consent_override_reason:
                        consentPolicy === "include_unknown"
                            ? overrideReason.trim() || null
                            : null,
                },
            });
            if (response.error) {
                const detail = detailFromError(response.error, "Preview failed");
                if (detail.includes("rate limit") || detail.includes("429")) {
                    throw new Error("Too many previews — wait a moment and try again.");
                }
                throw new Error(detail);
            }
            setPreview(response.data!);
        } catch (cause) {
            setPreviewError(cause instanceof Error ? cause.message : "Preview failed");
        } finally {
            setPreviewing(false);
        }
    };

    if (loading || authLoading || !user) {
        return (
            <PageLayout title="Email campaign" description="Loading...">
                <div className="space-y-4" role="status" aria-label="Loading campaign">
                    {[1, 2, 3].map((item) => (
                        <Skeleton key={item} className="h-24 w-full rounded-lg" />
                    ))}
                </div>
            </PageLayout>
        );
    }

    if (error) {
        return (
            <PageLayout title="Email campaign" description="">
                <div
                    role="alert"
                    className="rounded-lg border border-destructive/20 bg-destructive/10 p-4 text-sm text-destructive"
                >
                    <p>{error}</p>
                    <Button
                        variant="outline"
                        size="sm"
                        className="mt-3"
                        onClick={() => setReload((v) => v + 1)}
                    >
                        Retry
                    </Button>
                </div>
            </PageLayout>
        );
    }

    if (!campaign) return null;

    const isDraft = DRAFT_STATUSES.includes(campaign.status);
    const selectedSender = senders.find((s) => s.uuid === senderUuid) ?? null;
    const selectedTemplate = templates.find((t) => t.uuid === templateUuid) ?? null;
    const senderGuidance =
        selectedSender
            ? (campaign.sender?.uuid === selectedSender.uuid
                ? campaign.sender?.guidance
                : null) ?? `${selectedSender.limits?.daily_cap ?? "?"} per day`
            : null;

    return (
        <>
            <PageLayout
                title={campaign.name}
                description="Configure who receives this campaign and how it is sent."
            >
                {/* Top bar */}
                <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                        {statusBadge(campaign.status)}
                        <Link
                            href="/email/campaigns"
                            className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
                        >
                            <ChevronLeft className="h-3.5 w-3.5" />
                            All campaigns
                        </Link>
                    </div>
                    {isDraft && (
                        <div className="flex items-center gap-2">
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={() => void runPreview()}
                                disabled={previewing}
                            >
                                {previewing ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                ) : (
                                    <RefreshCw className="h-4 w-4" />
                                )}
                                Preview audience
                            </Button>
                            <Button
                                size="sm"
                                onClick={() => void save()}
                                disabled={saving || !name.trim()}
                            >
                                {saving && <Loader2 className="h-4 w-4 animate-spin" />}
                                {saving ? "Saving…" : "Save"}
                            </Button>
                            <Button
                                size="sm"
                                variant="secondary"
                                disabled
                                onClick={() => setLaunchOpen(true)}
                                title="Email sending is currently disabled"
                            >
                                <Lock className="h-3.5 w-3.5" />
                                Launch
                            </Button>
                        </div>
                    )}
                </div>

                {/* Sending-disabled notice */}
                <div className="mb-5 flex items-start gap-2 rounded-lg border bg-muted/40 p-3 text-sm text-muted-foreground">
                    <Lock className="mt-0.5 h-4 w-4 shrink-0" />
                    <p>
                        Email sending is currently disabled (
                        <code className="text-xs">EMAIL_CAMPAIGN_SENDING_ENABLED=false</code>).
                        Configure your campaign and run a preview — the Launch button will be
                        unlocked when sending is enabled.
                    </p>
                </div>

                {!isDraft && (
                    <div className="mb-5 flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                        <p>
                            This campaign can no longer be edited — status is{" "}
                            <strong>{campaign.status}</strong>.
                        </p>
                    </div>
                )}

                {/* Two-column grid */}
                <div className="grid gap-6 lg:grid-cols-2">
                    {/* ── Left column: settings ───────────────────────────────── */}
                    <div className="space-y-5 rounded-xl border bg-card p-5">
                        <h2 className="text-base font-semibold">Campaign settings</h2>

                        {saveError && (
                            <div
                                role="alert"
                                className="rounded-lg border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive"
                            >
                                {saveError}
                            </div>
                        )}

                        {/* Name */}
                        <div className="grid gap-2">
                            <Label htmlFor="campaign-name">Name</Label>
                            <Input
                                id="campaign-name"
                                value={name}
                                onChange={(e) => setName(e.target.value)}
                                disabled={!isDraft}
                                placeholder="e.g. Spring outreach"
                            />
                        </div>

                        {/* Sender */}
                        <div className="grid gap-2">
                            <Label htmlFor="sender-select">Sender</Label>
                            <Select
                                value={senderUuid}
                                onValueChange={setSenderUuid}
                                disabled={!isDraft}
                            >
                                <SelectTrigger id="sender-select">
                                    <SelectValue placeholder="Select a sender…" />
                                </SelectTrigger>
                                <SelectContent>
                                    {senders.map((sender) => (
                                        <SelectItem key={sender.uuid} value={sender.uuid}>
                                            {sender.name} — {sender.from_email}
                                            {sender.status !== "verified" ? " (unverified)" : ""}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            {senderGuidance && (
                                <p className="text-xs text-muted-foreground">{senderGuidance}</p>
                            )}
                            {selectedSender && selectedSender.status !== "verified" && (
                                <p className="text-xs text-destructive">
                                    This sender is not verified. Verify it before launching.
                                </p>
                            )}
                        </div>

                        {/* Template */}
                        <div className="grid gap-2">
                            <Label htmlFor="template-select">Template</Label>
                            <Select
                                value={templateUuid}
                                onValueChange={setTemplateUuid}
                                disabled={!isDraft}
                            >
                                <SelectTrigger id="template-select">
                                    <SelectValue placeholder="Select a template…" />
                                </SelectTrigger>
                                <SelectContent>
                                    {templates.map((template) => (
                                        <SelectItem key={template.uuid} value={template.uuid}>
                                            {template.name}
                                            {!template.has_unsubscribe_placeholder
                                                ? " (no unsubscribe)"
                                                : ""}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            {selectedTemplate &&
                                !selectedTemplate.has_unsubscribe_placeholder && (
                                    <p className="text-xs text-destructive">
                                        Missing{" "}
                                        <code className="text-xs">{"{{unsubscribe_link}}"}</code>{" "}
                                        — launch will be blocked.
                                    </p>
                                )}
                        </div>

                        {/* Consent policy */}
                        <div className="grid gap-2">
                            <Label htmlFor="consent-policy">Consent policy</Label>
                            <Select
                                value={consentPolicy}
                                onValueChange={(v) => {
                                    setConsentPolicy(
                                        v as "opted_in_only" | "include_unknown",
                                    );
                                    setReasonRequired(false);
                                    setPreviewError(null);
                                }}
                                disabled={!isDraft}
                            >
                                <SelectTrigger id="consent-policy">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="opted_in_only">
                                        Opted-in only (recommended)
                                    </SelectItem>
                                    <SelectItem value="include_unknown">
                                        Include unknown consent
                                    </SelectItem>
                                </SelectContent>
                            </Select>
                        </div>

                        {/* Include-unknown override */}
                        {consentPolicy === "include_unknown" && (
                            <div className="space-y-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3">
                                <div className="flex items-start gap-2 text-xs text-amber-700 dark:text-amber-400">
                                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                                    <p>{EMAIL_CONSENT_WARNING}</p>
                                </div>
                                <div className="grid gap-1.5">
                                    <Label
                                        htmlFor="override-reason"
                                        className={reasonRequired ? "text-destructive" : ""}
                                    >
                                        Written reason (required)
                                    </Label>
                                    <Textarea
                                        id="override-reason"
                                        value={overrideReason}
                                        onChange={(e) => {
                                            setOverrideReason(e.target.value);
                                            if (e.target.value.trim()) {
                                                setReasonRequired(false);
                                                setPreviewError(null);
                                            }
                                        }}
                                        disabled={!isDraft}
                                        placeholder="e.g. These are existing customers from before the consent system was introduced on 2026-01-01."
                                        rows={3}
                                        className={
                                            reasonRequired
                                                ? "border-destructive focus-visible:ring-destructive"
                                                : ""
                                        }
                                    />
                                    {reasonRequired && (
                                        <p
                                            role="alert"
                                            className="text-xs text-destructive"
                                        >
                                            Enter a reason before previewing or saving.
                                        </p>
                                    )}
                                </div>
                            </div>
                        )}
                    </div>

                    {/* ── Right column: audience + preview ───────────────────── */}
                    <div className="space-y-5">
                        {/* Audience summary */}
                        <div className="rounded-xl border bg-card p-5">
                            <h2 className="mb-3 text-base font-semibold">Audience</h2>
                            <div className="flex flex-wrap items-center gap-2 text-sm">
                                <Badge variant="outline">
                                    {campaign.audience_type}
                                </Badge>
                                <span className="text-muted-foreground">
                                    {audienceSummary(campaign)}
                                </span>
                            </div>
                            <p className="mt-2 text-xs text-muted-foreground">
                                The audience is configured here as a starting point. Use the
                                &ldquo;Add to email campaign&rdquo; action from the Leads CRM or a
                                Lead project to change it to a specific selection.
                            </p>
                        </div>

                        {/* Preview error */}
                        {previewError && (
                            <div
                                role="alert"
                                className="flex items-start gap-2 rounded-xl border border-destructive/20 bg-destructive/10 p-4 text-sm text-destructive"
                            >
                                <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
                                <p>{previewError}</p>
                            </div>
                        )}

                        {/* Preview results */}
                        {previewing && (
                            <div className="flex items-center justify-center rounded-xl border bg-card p-8">
                                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                            </div>
                        )}

                        {preview && !previewing && (
                            <div className="rounded-xl border bg-card p-5">
                                <h2 className="mb-4 text-base font-semibold">
                                    Audience preview
                                </h2>
                                <div className="grid grid-cols-2 gap-3">
                                    {BUCKET_LABELS.map(([key, label]) => (
                                        <div
                                            key={key}
                                            className={`rounded-lg border p-3 ${key === "eligible_count" ? "border-emerald-500/30 bg-emerald-500/5" : "bg-muted/30"}`}
                                        >
                                            <p className="text-xs text-muted-foreground">
                                                {label}
                                            </p>
                                            <p className="mt-1 text-lg font-semibold tabular-nums">
                                                {preview.counts[key].toLocaleString()}
                                            </p>
                                        </div>
                                    ))}
                                </div>

                                {preview.masked_sample.length > 0 && (
                                    <div className="mt-4">
                                        <p className="mb-2 text-xs font-medium text-muted-foreground">
                                            Masked sample — never full addresses
                                        </p>
                                        <ul className="space-y-1">
                                            {preview.masked_sample.map((addr, i) => (
                                                <li
                                                    key={i}
                                                    className="font-mono text-xs text-muted-foreground"
                                                >
                                                    {addr}
                                                </li>
                                            ))}
                                        </ul>
                                    </div>
                                )}

                                {preview.warnings.length > 0 && (
                                    <div className="mt-3 space-y-1">
                                        {preview.warnings.map((w, i) => (
                                            <div
                                                key={i}
                                                className="flex items-start gap-2 text-xs text-amber-700 dark:text-amber-400"
                                            >
                                                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                                                <span>{w}</span>
                                            </div>
                                        ))}
                                    </div>
                                )}

                                {preview.counts.eligible_count === 0 && (
                                    <p className="mt-3 text-sm text-destructive">
                                        No eligible recipients — launch would be blocked.
                                    </p>
                                )}

                                {preview.counts.eligible_count > 0 && (
                                    <div className="mt-3 flex items-center gap-1.5 text-sm text-emerald-600 dark:text-emerald-400">
                                        <CheckCircle2 className="h-4 w-4" />
                                        {preview.counts.eligible_count.toLocaleString()} eligible
                                        recipient{preview.counts.eligible_count === 1 ? "" : "s"}
                                    </div>
                                )}
                            </div>
                        )}

                        {/* Stored counts (always shown) */}
                        <div className="rounded-xl border bg-card p-5">
                            <h2 className="mb-4 text-base font-semibold">
                                Stored counts
                                <span className="ml-2 text-xs font-normal text-muted-foreground">
                                    — last saved
                                </span>
                            </h2>
                            <div className="grid grid-cols-2 gap-3">
                                {BUCKET_LABELS.map(([key, label]) => (
                                    <div
                                        key={key}
                                        className={`rounded-lg border p-3 ${key === "eligible_count" ? "border-emerald-500/30 bg-emerald-500/5" : "bg-muted/30"}`}
                                    >
                                        <p className="text-xs text-muted-foreground">{label}</p>
                                        <p className="mt-1 text-lg font-semibold tabular-nums">
                                            {campaign.counts[key].toLocaleString()}
                                        </p>
                                    </div>
                                ))}
                            </div>
                        </div>

                        {/* Failure reason */}
                        {campaign.failure_reason && (
                            <div className="flex items-start gap-2 rounded-xl border border-destructive/20 bg-destructive/10 p-4 text-sm text-destructive">
                                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                                <div>
                                    <p className="font-medium">Campaign failed</p>
                                    <p className="mt-1">{campaign.failure_reason}</p>
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            </PageLayout>

            {/* Launch confirmation — permanently disabled in this release */}
            <AlertDialog open={launchOpen} onOpenChange={setLaunchOpen}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Launch is disabled</AlertDialogTitle>
                        <AlertDialogDescription>
                            <code>EMAIL_CAMPAIGN_SENDING_ENABLED</code> is off. No messages
                            will be sent. Configure your campaign and check back when sending
                            is enabled.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Close</AlertDialogCancel>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </>
    );
}
