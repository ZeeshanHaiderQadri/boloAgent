"use client";

import {
    AlertCircle,
    AlertTriangle,
    ChevronLeft,
    Loader2,
    RefreshCw,
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
import { PageLayout, PageSection } from "@/components/layout/PageLayout";
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
    switch (status) {
        case "draft":
            return <Badge variant="secondary">Draft</Badge>;
        case "ready":
            return <Badge variant="outline">Ready</Badge>;
        case "sending":
            return <Badge>Sending</Badge>;
        case "sent":
            return <Badge>Sent</Badge>;
        case "cancelled":
            return <Badge variant="destructive">Cancelled</Badge>;
        case "failed":
            return <Badge variant="destructive">Failed</Badge>;
        default:
            return <Badge variant="secondary">{status}</Badge>;
    }
}

export default function EmailCampaignDetailPage() {
    const { uuid } = useParams<{ uuid: string }>();
    const { user, loading: authLoading, getAccessToken, redirectToLogin } = useAuth();

    const [campaign, setCampaign] = useState<EmailCampaignResponse | null>(null);
    const [senders, setSenders] = useState<EmailSenderResponse[]>([]);
    const [templates, setTemplates] = useState<EmailTemplateSummary[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [reload, setReload] = useState(0);

    // Edit fields
    const [name, setName] = useState("");
    const [senderUuid, setSenderUuid] = useState<string>("");
    const [templateUuid, setTemplateUuid] = useState<string>("");
    const [consentPolicy, setConsentPolicy] = useState<"opted_in_only" | "include_unknown">("opted_in_only");
    const [overrideReason, setOverrideReason] = useState("");
    const [saving, setSaving] = useState(false);
    const [saveError, setSaveError] = useState<string | null>(null);

    // Preview
    const [preview, setPreview] = useState<CampaignPreviewResponse | null>(null);
    const [previewing, setPreviewing] = useState(false);
    const [previewError, setPreviewError] = useState<string | null>(null);

    // Launch (disabled — nothing sends)
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
                const [campaignRes, sendersRes, templatesRes] = await Promise.all([
                    getEmailCampaignApiV1EmailCampaignsCampaignUuidGet({
                        headers: { Authorization: `Bearer ${token}` },
                        path: { campaign_uuid: uuid },
                    }),
                    listEmailSendersApiV1EmailSendersGet({
                        headers: { Authorization: `Bearer ${token}` },
                    }),
                    listEmailTemplatesApiV1EmailTemplatesGet({
                        headers: { Authorization: `Bearer ${token}` },
                    }),
                ]);
                if (campaignRes.error) {
                    throw new Error(detailFromError(campaignRes.error, "Failed to load campaign"));
                }
                if (sendersRes.error) {
                    throw new Error(detailFromError(sendersRes.error, "Failed to load senders"));
                }
                if (templatesRes.error) {
                    throw new Error(detailFromError(templatesRes.error, "Failed to load templates"));
                }
                if (!cancelled) {
                    const c = campaignRes.data!;
                    setCampaign(c);
                    setSenders(sendersRes.data ?? []);
                    setTemplates(templatesRes.data ?? []);
                    setName(c.name);
                    setSenderUuid(c.sender?.uuid ?? "");
                    setTemplateUuid(c.template?.uuid ?? "");
                    setConsentPolicy(
                        (c.consent_policy as "opted_in_only" | "include_unknown") ?? "opted_in_only"
                    );
                    setOverrideReason(c.consent_override_reason ?? "");
                }
            } catch (cause) {
                if (!cancelled) {
                    setError(cause instanceof Error ? cause.message : "Failed to load campaign");
                }
            } finally {
                if (!cancelled) setLoading(false);
            }
        };
        void load();
        return () => { cancelled = true; };
    }, [authLoading, user, getAccessToken, uuid, reload]);

    const isDraft = campaign ? DRAFT_STATUSES.includes(campaign.status) : false;

    const save = async () => {
        if (!campaign || saving) return;
        setSaving(true);
        setSaveError(null);
        try {
            const token = await getAccessToken();
            const body: Parameters<typeof updateEmailCampaignApiV1EmailCampaignsCampaignUuidPatch>[0]["body"] = {
                name: name.trim() || null,
                sender_uuid: senderUuid || null,
                template_uuid: templateUuid || null,
                consent_policy: consentPolicy,
                consent_override_reason: consentPolicy === "include_unknown" ? (overrideReason.trim() || null) : null,
            };
            const response = await updateEmailCampaignApiV1EmailCampaignsCampaignUuidPatch({
                headers: { Authorization: `Bearer ${token}` },
                path: { campaign_uuid: uuid },
                body,
            });
            if (response.error) {
                throw new Error(detailFromError(response.error, "Failed to save campaign"));
            }
            const updated = response.data!;
            setCampaign(updated);
            setName(updated.name);
            setSenderUuid(updated.sender?.uuid ?? "");
            setTemplateUuid(updated.template?.uuid ?? "");
            setConsentPolicy(
                (updated.consent_policy as "opted_in_only" | "include_unknown") ?? "opted_in_only"
            );
            setOverrideReason(updated.consent_override_reason ?? "");
            toast.success("Campaign saved");
        } catch (cause) {
            setSaveError(cause instanceof Error ? cause.message : "Failed to save campaign");
        } finally {
            setSaving(false);
        }
    };

    const runPreview = async () => {
        if (!campaign || previewing) return;
        setPreviewing(true);
        setPreviewError(null);
        try {
            const token = await getAccessToken();
            const audience = campaign.audience as AudienceRuleRequest;
            const response = await previewCampaignAudienceApiV1EmailCampaignsPreviewPost({
                headers: { Authorization: `Bearer ${token}` },
                body: {
                    audience,
                    consent_policy: consentPolicy,
                    consent_override_reason:
                        consentPolicy === "include_unknown" ? (overrideReason.trim() || null) : null,
                },
            });
            if (response.error) {
                throw new Error(detailFromError(response.error, "Preview failed"));
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

    const selectedSender = senders.find((s) => s.uuid === senderUuid) ?? null;
    const selectedTemplate = templates.find((t) => t.uuid === templateUuid) ?? null;

    return (
        <>
            <PageLayout
                title={campaign.name}
                description="Configure who receives this campaign and how it is sent."
            >
                <div className="mb-4 flex items-center gap-3">
                    {statusBadge(campaign.status)}
                    <Link
                        href="/email/campaigns"
                        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
                    >
                        <ChevronLeft className="h-3.5 w-3.5" />
                        All campaigns
                    </Link>
                </div>

                {!isDraft && (
                    <div className="mb-4 flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-sm">
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                        <p>
                            This campaign can no longer be edited — it has status{" "}
                            <strong>{campaign.status}</strong>.
                        </p>
                    </div>
                )}

                {/* Launch disabled banner */}
                <div className="mb-4 flex items-start gap-2 rounded-lg border bg-muted/40 p-4 text-sm text-muted-foreground">
                    <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                    <p>
                        Email sending is currently disabled. The Launch button is intentionally
                        locked. Configure your campaign, run a preview, and it will be ready
                        when sending is enabled.
                    </p>
                </div>

                <PageSection
                    title="Campaign settings"
                    description="Configure who receives this campaign and how it is sent."
                    actions={
                        isDraft ? (
                            <div className="flex items-center gap-2">
                                <Button
                                    variant="outline"
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
                                    onClick={() => void save()}
                                    disabled={saving || !name.trim()}
                                >
                                    {saving && <Loader2 className="h-4 w-4 animate-spin" />}
                                    {saving ? "Saving..." : "Save"}
                                </Button>
                                <Button
                                    variant="secondary"
                                    disabled
                                    title="Email sending is currently disabled"
                                >
                                    Launch
                                </Button>
                            </div>
                        ) : null
                    }
                >
                    {saveError && (
                        <div
                            role="alert"
                            className="mb-4 rounded-lg border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive"
                        >
                            {saveError}
                        </div>
                    )}

                    <div className="space-y-5">
                        <div className="grid gap-2">
                            <Label htmlFor="campaign-name">Name *</Label>
                            <Input
                                id="campaign-name"
                                value={name}
                                onChange={(e) => setName(e.target.value)}
                                disabled={!isDraft}
                                placeholder="Campaign name"
                            />
                        </div>

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
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            {selectedSender && campaign.sender?.uuid === selectedSender.uuid && campaign.sender.guidance && (
                                <p className="text-xs text-muted-foreground">
                                    {campaign.sender.guidance}
                                </p>
                            )}
                        </div>

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
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            {selectedTemplate && !selectedTemplate.has_unsubscribe_placeholder && (
                                <p className="text-xs text-destructive">
                                    This template has no unsubscribe placeholder. A campaign cannot
                                    launch without one.
                                </p>
                            )}
                        </div>

                        <div className="grid gap-2">
                            <Label htmlFor="consent-policy">Consent policy</Label>
                            <Select
                                value={consentPolicy}
                                onValueChange={(v) =>
                                    setConsentPolicy(v as "opted_in_only" | "include_unknown")
                                }
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

                        {consentPolicy === "include_unknown" && (
                            <div className="space-y-3">
                                <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
                                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                                    <p>{EMAIL_CONSENT_WARNING}</p>
                                </div>
                                <div className="grid gap-2">
                                    <Label htmlFor="override-reason">
                                        Written reason for including unknown consent *
                                    </Label>
                                    <Textarea
                                        id="override-reason"
                                        value={overrideReason}
                                        onChange={(e) => setOverrideReason(e.target.value)}
                                        disabled={!isDraft}
                                        placeholder="e.g. These are existing customers from before the consent system was introduced on 2026-01-01."
                                        rows={3}
                                    />
                                </div>
                            </div>
                        )}
                    </div>
                </PageSection>

                {/* Preview results */}
                {previewError && (
                    <div
                        role="alert"
                        className="mt-4 rounded-lg border border-destructive/20 bg-destructive/10 p-4 text-sm text-destructive"
                    >
                        {previewError}
                    </div>
                )}

                {preview && (
                    <PageSection
                        title="Audience preview"
                        description="Counts are estimates based on the current audience rule and consent policy."
                    >
                        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                            <Stat label="Recipients" value={preview.counts.recipient_count} />
                            <Stat label="Eligible" value={preview.counts.eligible_count} />
                            <Stat label="No email" value={preview.counts.excluded_no_email_count} />
                            <Stat label="Invalid email" value={preview.counts.excluded_invalid_email_count} />
                            <Stat label="Suppressed" value={preview.counts.excluded_suppressed_count} />
                            <Stat label="Opted out" value={preview.counts.excluded_opted_out_count} />
                            <Stat label="Unknown consent" value={preview.counts.excluded_unknown_count} />
                            <Stat label="Duplicates collapsed" value={preview.counts.duplicates_collapsed_count} />
                            <Stat label="Unknown leads" value={preview.unknown_lead_count} />
                        </div>

                        {preview.warnings.length > 0 && (
                            <div className="mt-4 space-y-1">
                                {preview.warnings.map((w, i) => (
                                    <div
                                        key={i}
                                        className="flex items-start gap-2 text-sm text-amber-700 dark:text-amber-400"
                                    >
                                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                                        <span>{w}</span>
                                    </div>
                                ))}
                            </div>
                        )}

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
                    </PageSection>
                )}

                {/* Campaign counts (from stored response) */}
                {campaign.counts && (
                    <PageSection
                        title="Stored counts"
                        description="Last saved audience counts for this campaign."
                    >
                        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                            <Stat label="Recipients" value={campaign.counts.recipient_count} />
                            <Stat label="Eligible" value={campaign.counts.eligible_count} />
                            <Stat label="No email" value={campaign.counts.excluded_no_email_count} />
                            <Stat label="Invalid email" value={campaign.counts.excluded_invalid_email_count} />
                            <Stat label="Suppressed" value={campaign.counts.excluded_suppressed_count} />
                            <Stat label="Opted out" value={campaign.counts.excluded_opted_out_count} />
                        </div>
                    </PageSection>
                )}

                {/* Failure reason */}
                {campaign.failure_reason && (
                    <div className="flex items-start gap-2 rounded-lg border border-destructive/20 bg-destructive/10 p-4 text-sm text-destructive">
                        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                        <div>
                            <p className="font-medium">Campaign failed</p>
                            <p className="mt-1">{campaign.failure_reason}</p>
                        </div>
                    </div>
                )}
            </PageLayout>

            {/* Launch confirmation — permanently disabled */}
            <AlertDialog open={launchOpen} onOpenChange={setLaunchOpen}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Launch disabled</AlertDialogTitle>
                        <AlertDialogDescription>
                            Email sending is currently disabled. No messages will be sent.
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

function Stat({ label, value }: { label: string; value: number }) {
    return (
        <div className="rounded-lg border bg-muted/30 p-3">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className="mt-1 text-lg font-semibold">{value.toLocaleString()}</p>
        </div>
    );
}
