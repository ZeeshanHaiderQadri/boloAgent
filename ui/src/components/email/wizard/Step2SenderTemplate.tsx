"use client";

import { AlertTriangle, Eye, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import {
    getEmailTemplateApiV1EmailTemplatesTemplateUuidGet,
} from "@/client";
import type { EmailCampaignResponse, EmailSenderResponse, EmailTemplateSummary } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

interface Props {
    campaign: EmailCampaignResponse;
    senders: EmailSenderResponse[];
    templates: EmailTemplateSummary[];
    senderUuid: string;
    templateUuid: string;
    onSenderChange: (uuid: string) => void;
    onTemplateChange: (uuid: string) => void;
    onBack: () => void;
    onNext: () => void;
    fieldErrors: Record<string, string>;
}

export function Step2SenderTemplate({
    campaign,
    senders,
    templates,
    senderUuid,
    templateUuid,
    onSenderChange,
    onTemplateChange,
    onBack,
    onNext,
    fieldErrors,
}: Props) {
    const { getAccessToken } = useAuth();
    const selectedSender = senders.find((s) => s.uuid === senderUuid) ?? null;
    const selectedTemplate = templates.find((t) => t.uuid === templateUuid) ?? null;

    // Template HTML preview state
    const [previewHtml, setPreviewHtml] = useState<string | null>(null);
    const [previewSubject, setPreviewSubject] = useState<string>("");
    const [previewLoading, setPreviewLoading] = useState(false);
    const [previewError, setPreviewError] = useState<string | null>(null);

    // Fetch template HTML when a template is selected
    useEffect(() => {
        if (!templateUuid) {
            setPreviewHtml(null);
            setPreviewSubject("");
            setPreviewError(null);
            return;
        }
        let cancelled = false;
        const loadPreview = async () => {
            setPreviewLoading(true);
            setPreviewError(null);
            try {
                const token = await getAccessToken();
                const res = await getEmailTemplateApiV1EmailTemplatesTemplateUuidGet({
                    headers: { Authorization: `Bearer ${token}` },
                    path: { template_uuid: templateUuid },
                });
                if (res.error) {
                    throw new Error(detailFromError(res.error, "Failed to load template"));
                }
                if (!cancelled && res.data) {
                    setPreviewHtml(res.data.html_body ?? "");
                    setPreviewSubject(res.data.subject ?? "");
                }
            } catch (cause) {
                if (!cancelled) {
                    setPreviewError(cause instanceof Error ? cause.message : "Failed to load template");
                }
            } finally {
                if (!cancelled) setPreviewLoading(false);
            }
        };
        void loadPreview();
        return () => { cancelled = true; };
    }, [templateUuid, getAccessToken]);

    // Determine which warnings to show
    const missingUnsubscribe =
        selectedTemplate && !selectedTemplate.has_unsubscribe_placeholder;
    const isSmtp = selectedSender?.provider === "smtp";
    const isUnverified =
        selectedSender && selectedSender.status !== "verified";

    return (
        <div className="space-y-5">
            <div>
                <h2 className="text-base font-semibold">Step 2 — Sender and Template</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                    Choose which sender and template to use for this campaign.
                </p>
            </div>

            {/* Sender picker */}
            <div className="grid gap-2">
                <Label htmlFor="wizard-sender">Sender</Label>
                <Select value={senderUuid} onValueChange={onSenderChange}>
                    <SelectTrigger
                        id="wizard-sender"
                        className={fieldErrors.sender ? "border-destructive" : ""}
                    >
                        <SelectValue placeholder="Select a sender…" />
                    </SelectTrigger>
                    <SelectContent>
                        {senders.map((s) => (
                            <SelectItem key={s.uuid} value={s.uuid}>
                                {s.name} — {s.from_email}
                                {s.status !== "verified" ? " (unverified)" : ""}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                {fieldErrors.sender && (
                    <p role="alert" className="text-xs text-destructive">
                        {fieldErrors.sender}
                    </p>
                )}
                {selectedSender && (
                    <p className="text-xs text-muted-foreground">
                        {campaign.sender?.uuid === selectedSender.uuid
                            ? (campaign.sender?.guidance ?? `${selectedSender.limits?.daily_cap ?? "?"} per day`)
                            : `${selectedSender.limits?.daily_cap ?? "?"} per day`}
                    </p>
                )}
            </div>

            {/* Template picker */}
            <div className="grid gap-2">
                <Label htmlFor="wizard-template">Template</Label>
                <Select value={templateUuid} onValueChange={onTemplateChange}>
                    <SelectTrigger
                        id="wizard-template"
                        className={fieldErrors.template ? "border-destructive" : ""}
                    >
                        <SelectValue placeholder="Select a template…" />
                    </SelectTrigger>
                    <SelectContent>
                        {templates.map((t) => (
                            <SelectItem key={t.uuid} value={t.uuid}>
                                {t.name}
                                {!t.has_unsubscribe_placeholder ? " (no unsubscribe)" : ""}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                {fieldErrors.template && (
                    <p role="alert" className="text-xs text-destructive">
                        {fieldErrors.template}
                    </p>
                )}
            </div>

            {/* Template HTML preview */}
            {selectedTemplate && (
                <div className="space-y-2 rounded-lg border bg-muted/20 p-3">
                    <div className="flex items-center gap-2">
                        <Eye className="h-3.5 w-3.5 text-muted-foreground" />
                        <p className="text-xs font-medium text-muted-foreground">
                            Email preview: {selectedTemplate.name}
                        </p>
                    </div>
                    <p className="text-xs font-medium text-muted-foreground">
                        Subject: {previewSubject || selectedTemplate.subject}
                    </p>
                    {previewLoading && (
                        <div className="flex items-center justify-center py-6">
                            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                        </div>
                    )}
                    {previewError && (
                        <p className="text-xs text-destructive">{previewError}</p>
                    )}
                    {previewHtml && !previewLoading && (
                        <div className="overflow-hidden rounded-md border bg-white">
                            <iframe
                                srcDoc={previewHtml}
                                title="Email template preview"
                                className="h-64 w-full border-0"
                                sandbox="allow-same-origin"
                            />
                        </div>
                    )}
                    <p className="text-xs text-muted-foreground">
                        Merge fields like{" "}
                        <code className="text-xs">{`{{business_name}}`}</code>{" "}
                        show as placeholder text. The final email fills them
                        from each recipient&rsquo;s lead record.
                    </p>
                </div>
            )}

            {/* Warnings */}
            {missingUnsubscribe && (
                <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-400">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <p>
                        This template has no unsubscribe link — launch will be blocked.
                        Add{" "}
                        <code className="text-xs">{`{{unsubscribe_link}}`}</code> to the
                        template before launching.
                    </p>
                </div>
            )}
            {isSmtp && (
                <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-400">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <p>
                        Plain SMTP does not report bounces or spam complaints. Monitor the
                        sending mailbox manually. Use a separate sending domain or mailbox
                        for cold outreach to protect your primary domain.
                    </p>
                </div>
            )}
            {isUnverified && (
                <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-400">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <p>
                        This sender is not verified. Verify it before launching to protect
                        your sending domain.
                    </p>
                </div>
            )}

            <div className="flex gap-3">
                <Button variant="outline" size="sm" onClick={onBack}>
                    Back
                </Button>
                <Button size="sm" onClick={onNext}>
                    Next
                </Button>
            </div>
        </div>
    );
}
