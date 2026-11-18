"use client";

import {
    AlertCircle,
    AlertTriangle,
    FileCode2,
    Loader2,
    Monitor,
    RefreshCw,
    Save,
    Send,
    Smartphone,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
    createEmailTemplateApiV1EmailTemplatesPost,
    getEmailTemplateApiV1EmailTemplatesTemplateUuidGet,
    listEmailTemplateMergeFieldsApiV1EmailTemplatesMergeFieldsGet,
    previewEmailTemplateApiV1EmailTemplatesPreviewPost,
    testSendEmailTemplateApiV1EmailTemplatesTemplateUuidTestSendPost,
    updateEmailTemplateApiV1EmailTemplatesTemplateUuidPatch,
} from "@/client";
import type {
    EmailTemplateFormat,
    EmailTemplateResponse,
    PreviewEmailTemplateResponse,
} from "@/client/types.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

const STARTER_HTML = `<div style="font-family: Arial, Helvetica, sans-serif; color: #111827; max-width: 600px; margin: 0 auto;">
  <p>Hi {{business_name}},</p>
  <p>Write your message here.</p>
  <p style="font-size: 12px; color: #6b7280;">
    You are receiving this email because you are listed as a business contact.
    <a href="{{unsubscribe_link}}">Unsubscribe</a>
  </p>
</div>`;

// mj-head is deliberately absent: Bolo's compiler rejects it instead of
// silently dropping mj-title and mj-preview.
const STARTER_MJML = `<mjml>
  <mj-body background-color="#ffffff" width="600px">
    <mj-section padding="24px">
      <mj-column width="100%">
        <mj-text font-size="16px" color="#111827">Hi {{business_name}},</mj-text>
        <mj-text>Write your message here.</mj-text>
        <mj-button href="https://boloagent.com">Visit boloagent.com</mj-button>
        <mj-text font-size="12px" color="#6b7280">
          You are receiving this email because you are listed as a business contact.
          <a href="{{unsubscribe_link}}">Unsubscribe</a>
        </mj-text>
      </mj-column>
    </mj-section>
  </mj-body>
</mjml>`;

const UNSUBSCRIBE_PATTERN = /\{\{\s*unsubscribe_link\s*\}\}/;

type MergeField = {
    name: string;
    label: string;
    description: string;
    fallback: string;
    sample: string;
    required: boolean;
};

function toMergeFields(rows: Array<Record<string, unknown>>): MergeField[] {
    return rows
        .filter((row) => typeof row.name === "string" && row.name)
        .map((row) => ({
            name: row.name as string,
            label: typeof row.label === "string" ? row.label : (row.name as string),
            description: typeof row.description === "string" ? row.description : "",
            fallback: typeof row.fallback === "string" ? row.fallback : "",
            sample: typeof row.sample === "string" ? row.sample : "",
            required: row.required === true,
        }));
}

export function TemplateEditorDialog({
    open,
    templateUuid,
    onOpenChange,
    onSaved,
}: {
    open: boolean;
    templateUuid: string | null;
    onOpenChange: (open: boolean) => void;
    onSaved: (template: EmailTemplateResponse) => void;
}) {
    const { getAccessToken } = useAuth();
    const [currentUuid, setCurrentUuid] = useState<string | null>(templateUuid);
    const [mergeFields, setMergeFields] = useState<MergeField[]>([]);
    const [name, setName] = useState("");
    const [subject, setSubject] = useState("");
    const [format, setFormat] = useState<EmailTemplateFormat>("html");
    const [source, setSource] = useState(STARTER_HTML);
    const [loading, setLoading] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [preview, setPreview] = useState<PreviewEmailTemplateResponse | null>(null);
    const [previewing, setPreviewing] = useState(false);
    const [previewError, setPreviewError] = useState<string | null>(null);
    const [sending, setSending] = useState(false);
    const sourceRef = useRef<HTMLTextAreaElement | null>(null);
    const loadSeq = useRef(0);

    useEffect(() => {
        if (!open) return;
        setCurrentUuid(templateUuid);
        setError(null);
        setPreview(null);
        setPreviewError(null);
        setName("");
        setSubject("");
        setFormat("html");
        setSource(STARTER_HTML);
        setLoading(!!templateUuid);
        setLoadError(null);

        const seq = ++loadSeq.current;
        const load = async () => {
            try {
                const token = await getAccessToken();
                const fieldsResponse = await listEmailTemplateMergeFieldsApiV1EmailTemplatesMergeFieldsGet({
                    headers: { Authorization: `Bearer ${token}` },
                });
                if (fieldsResponse.error) {
                    throw new Error(
                        detailFromError(fieldsResponse.error, "Failed to load merge fields")
                    );
                }
                if (seq !== loadSeq.current) return;
                setMergeFields(toMergeFields(fieldsResponse.data ?? []));

                if (!templateUuid) return;
                const templateResponse = await getEmailTemplateApiV1EmailTemplatesTemplateUuidGet({
                    headers: { Authorization: `Bearer ${token}` },
                    path: { template_uuid: templateUuid },
                });
                if (templateResponse.error) {
                    throw new Error(
                        detailFromError(templateResponse.error, "Failed to load template")
                    );
                }
                const template = templateResponse.data;
                if (!template || seq !== loadSeq.current) return;
                setName(template.name);
                setSubject(template.subject);
                setFormat(template.source_format === "mjml" ? "mjml" : "html");
                setSource(template.source);
                setPreview({
                    subject: template.subject,
                    html: template.html_body,
                    text: template.text_body,
                    has_unsubscribe_placeholder: template.has_unsubscribe_placeholder,
                    warnings: template.warnings,
                });
            } catch (cause) {
                if (seq !== loadSeq.current) return;
                setLoadError(
                    cause instanceof Error ? cause.message : "Failed to load template"
                );
            } finally {
                if (seq === loadSeq.current) setLoading(false);
            }
        };
        void load();
    }, [open, templateUuid, getAccessToken]);

    const hasLocalUnsubscribe = useMemo(
        () => UNSUBSCRIBE_PATTERN.test(subject) || UNSUBSCRIBE_PATTERN.test(source),
        [subject, source]
    );
    const hasUnsubscribe = preview
        ? preview.has_unsubscribe_placeholder
        : hasLocalUnsubscribe;

    const markEdited = () => {
        setPreview(null);
        setPreviewError(null);
        setError(null);
    };

    const insertMergeField = (field: MergeField) => {
        const token = `{{${field.name}}}`;
        const element = sourceRef.current;
        if (!element) {
            setSource((value) => `${value}${token}`);
            markEdited();
            return;
        }
        const start = element.selectionStart ?? source.length;
        const end = element.selectionEnd ?? start;
        const next = `${source.slice(0, start)}${token}${source.slice(end)}`;
        setSource(next);
        markEdited();
        requestAnimationFrame(() => {
            element.focus();
            const caret = start + token.length;
            element.setSelectionRange(caret, caret);
        });
    };

    const runPreview = async () => {
        if (previewing) return;
        setPreviewing(true);
        setPreviewError(null);
        try {
            const token = await getAccessToken();
            const response = await previewEmailTemplateApiV1EmailTemplatesPreviewPost({
                headers: { Authorization: `Bearer ${token}` },
                body: {
                    subject,
                    source_format: format,
                    source,
                },
            });
            if (response.error) {
                throw new Error(
                    detailFromError(response.error, "Could not build the preview")
                );
            }
            setPreview(response.data ?? null);
        } catch (cause) {
            setPreviewError(
                cause instanceof Error ? cause.message : "Could not build the preview"
            );
        } finally {
            setPreviewing(false);
        }
    };

    const save = async () => {
        if (saving || !name.trim()) return;
        setSaving(true);
        setError(null);
        try {
            const token = await getAccessToken();
            const body = {
                name: name.trim(),
                subject,
                source_format: format,
                source,
            };
            const response = currentUuid
                ? await updateEmailTemplateApiV1EmailTemplatesTemplateUuidPatch({
                    headers: { Authorization: `Bearer ${token}` },
                    path: { template_uuid: currentUuid },
                    body,
                })
                : await createEmailTemplateApiV1EmailTemplatesPost({
                    headers: { Authorization: `Bearer ${token}` },
                    body,
                });
            if (response.error) {
                throw new Error(detailFromError(response.error, "Failed to save template"));
            }
            const template = response.data;
            if (!template) throw new Error("Failed to save template");
            setCurrentUuid(template.uuid);
            onSaved(template);
            toast.success(currentUuid ? "Template saved" : "Template created");
            setPreview({
                subject: template.subject,
                html: template.html_body,
                text: template.text_body,
                has_unsubscribe_placeholder: template.has_unsubscribe_placeholder,
                warnings: template.warnings,
            });
        } catch (cause) {
            setError(cause instanceof Error ? cause.message : "Failed to save template");
        } finally {
            setSaving(false);
        }
    };

    const sendTest = async () => {
        if (!currentUuid || sending) return;
        setSending(true);
        setError(null);
        try {
            const token = await getAccessToken();
            const response = await testSendEmailTemplateApiV1EmailTemplatesTemplateUuidTestSendPost({
                headers: { Authorization: `Bearer ${token}` },
                path: { template_uuid: currentUuid },
                body: {},
            });
            if (response.error) {
                throw new Error(detailFromError(response.error, "Test send failed"));
            }
            toast.success(
                response.data?.message ?? "Test message sent to your own address."
            );
        } catch (cause) {
            setError(cause instanceof Error ? cause.message : "Test send failed");
        } finally {
            setSending(false);
        }
    };

    return (
        <Dialog
            open={open}
            onOpenChange={(next) => {
                if (!next && (saving || sending)) return;
                onOpenChange(next);
            }}
        >
            <DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-5xl">
                <DialogHeader>
                    <DialogTitle>
                        {currentUuid ? "Edit email template" : "New email template"}
                    </DialogTitle>
                    <DialogDescription>
                        Write plain HTML or the supported MJML subset. Bolo compiles it,
                        strips active content, and fills merge fields with sample values
                        for previews. Campaigns use the recipient&apos;s real values.
                    </DialogDescription>
                </DialogHeader>

                {loadError && (
                    <div
                        role="alert"
                        className="rounded-md border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive"
                    >
                        {loadError}
                    </div>
                )}

                {error && (
                    <div
                        role="alert"
                        className="flex items-start gap-2 rounded-md border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive"
                    >
                        <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
                        <span className="whitespace-pre-wrap">{error}</span>
                    </div>
                )}

                {!hasUnsubscribe && !loading && (
                    <div
                        role="status"
                        className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-sm"
                    >
                        <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-600" />
                        <span>
                            This template has no <code>{"{{unsubscribe_link}}"}</code>{" "}
                            placeholder. A campaign cannot launch with it until you add a
                            visible unsubscribe link.
                        </span>
                    </div>
                )}

                {loading ? (
                    <div className="space-y-3" role="status" aria-label="Loading template">
                        <Skeleton className="h-9 w-2/3" />
                        <Skeleton className="h-9 w-full" />
                        <Skeleton className="h-40 w-full" />
                    </div>
                ) : (
                    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
                        <div className="space-y-4">
                            <div className="grid gap-2">
                                <Label htmlFor="template-name">Name *</Label>
                                <Input
                                    id="template-name"
                                    value={name}
                                    onChange={(event) => {
                                        setName(event.target.value);
                                        setError(null);
                                    }}
                                    placeholder="Welcome email"
                                    maxLength={255}
                                />
                            </div>

                            <div className="grid gap-2">
                                <Label htmlFor="template-subject">Subject line *</Label>
                                <Input
                                    id="template-subject"
                                    value={subject}
                                    onChange={(event) => {
                                        setSubject(event.target.value);
                                        markEdited();
                                    }}
                                    placeholder="Quick question about {{business_name}}"
                                />
                                <p className="text-xs text-muted-foreground">
                                    Merge fields are allowed in the subject. Keep it on one
                                    line and under 998 characters.
                                </p>
                            </div>

                            <div className="grid gap-2">
                                <span id="template-format-label" className="text-sm font-medium">
                                    Source format
                                </span>
                                <div
                                    role="group"
                                    aria-labelledby="template-format-label"
                                    className="flex flex-wrap gap-2"
                                >
                                    <Button
                                        type="button"
                                        size="sm"
                                        variant={format === "html" ? "default" : "outline"}
                                        aria-pressed={format === "html"}
                                        onClick={() => {
                                            setFormat("html");
                                            if (source === STARTER_MJML) {
                                                setSource(STARTER_HTML);
                                            }
                                            markEdited();
                                        }}
                                    >
                                        <FileCode2 className="h-4 w-4" />
                                        HTML
                                    </Button>
                                    <Button
                                        type="button"
                                        size="sm"
                                        variant={format === "mjml" ? "default" : "outline"}
                                        aria-pressed={format === "mjml"}
                                        onClick={() => {
                                            setFormat("mjml");
                                            if (source === STARTER_HTML) {
                                                setSource(STARTER_MJML);
                                            }
                                            markEdited();
                                        }}
                                    >
                                        <FileCode2 className="h-4 w-4" />
                                        MJML
                                    </Button>
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    Switching format does not convert the text. Paste source
                                    in the format you select.
                                </p>
                            </div>

                            <div className="grid gap-2">
                                <Label htmlFor="template-source">
                                    {format === "mjml" ? "MJML source *" : "HTML source *"}
                                </Label>
                                <Textarea
                                    id="template-source"
                                    ref={sourceRef}
                                    value={source}
                                    onChange={(event) => {
                                        setSource(event.target.value);
                                        markEdited();
                                    }}
                                    spellCheck={false}
                                    rows={16}
                                    className="min-h-[320px] font-mono text-xs"
                                />
                            </div>

                            <div className="space-y-2">
                                <p className="text-sm font-medium">Insert a merge field</p>
                                <div className="flex flex-wrap gap-2">
                                    {mergeFields.map((field) => (
                                        <Button
                                            key={field.name}
                                            type="button"
                                            size="sm"
                                            variant="secondary"
                                            title={
                                                field.fallback
                                                    ? `Missing value falls back to "${field.fallback}"`
                                                    : "Missing values are left blank"
                                            }
                                            onClick={() => insertMergeField(field)}
                                        >
                                            {field.required ? (
                                                <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
                                            ) : null}
                                            {`{{${field.name}}}`}
                                        </Button>
                                    ))}
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    Missing values use a safe fallback; previews use fake
                                    sample data. Unknown merge fields are rejected when you
                                    save.
                                </p>
                            </div>
                        </div>

                        <div className="space-y-3">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <div>
                                    <p className="text-sm font-medium">Preview</p>
                                    <p className="text-xs text-muted-foreground">
                                        Sandboxed: scripts and forms cannot run.
                                    </p>
                                </div>
                                <Button
                                    type="button"
                                    size="sm"
                                    variant="outline"
                                    onClick={() => void runPreview()}
                                    disabled={previewing || !source.trim()}
                                >
                                    {previewing ? (
                                        <Loader2 className="h-4 w-4 animate-spin" />
                                    ) : (
                                        <RefreshCw className="h-4 w-4" />
                                    )}
                                    {previewing ? "Building..." : "Refresh"}
                                </Button>
                            </div>

                            {previewError && (
                                <div
                                    role="alert"
                                    className="rounded-md border border-destructive/20 bg-destructive/10 p-3 text-xs text-destructive"
                                >
                                    {previewError}
                                </div>
                            )}

                            {preview ? (
                                <div className="space-y-4">
                                    <div>
                                        <p className="mb-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                                            <Monitor className="h-3.5 w-3.5" />
                                            Desktop
                                        </p>
                                        <div className="mx-auto max-w-[640px] overflow-hidden rounded-md border bg-white">
                                            <iframe
                                                title="Desktop preview"
                                                sandbox=""
                                                referrerPolicy="no-referrer"
                                                srcDoc={preview.html}
                                                className="h-[360px] w-full"
                                            />
                                        </div>
                                    </div>
                                    <div>
                                        <p className="mb-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                                            <Smartphone className="h-3.5 w-3.5" />
                                            Mobile (360 px)
                                        </p>
                                        <div className="mx-auto w-[360px] max-w-full overflow-hidden rounded-md border bg-white">
                                            <iframe
                                                title="Mobile preview"
                                                sandbox=""
                                                referrerPolicy="no-referrer"
                                                srcDoc={preview.html}
                                                className="h-[360px] w-full"
                                            />
                                        </div>
                                    </div>
                                    <div className="space-y-1">
                                        <p className="text-xs font-medium">Rendered subject</p>
                                        <p className="break-words rounded-md border bg-muted/40 p-2 text-xs">
                                            {preview.subject}
                                        </p>
                                    </div>
                                    {preview.warnings.length > 0 && (
                                        <ul className="space-y-1">
                                            {preview.warnings.map((warning) => (
                                                <li
                                                    key={warning}
                                                    className="flex items-start gap-1.5 text-xs text-muted-foreground"
                                                >
                                                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-amber-600" />
                                                    <span>{warning}</span>
                                                </li>
                                            ))}
                                        </ul>
                                    )}
                                </div>
                            ) : (
                                <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
                                    Build a preview to see the desktop and mobile versions.
                                </div>
                            )}

                            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                                <Badge variant={hasUnsubscribe ? "secondary" : "outline"}>
                                    {hasUnsubscribe
                                        ? "Unsubscribe link present"
                                        : "Unsubscribe link missing"}
                                </Badge>
                            </div>
                        </div>
                    </div>
                )}

                <DialogFooter className="flex-wrap sm:justify-between">
                    <p className="text-xs text-muted-foreground sm:max-w-xs">
                        Send test emails go only to the address on your account. They use
                        fake sample merge values; no lead is contacted.
                    </p>
                    <div className="flex flex-wrap justify-end gap-2">
                        <Button
                            variant="outline"
                            onClick={() => onOpenChange(false)}
                            disabled={saving || sending}
                        >
                            Close
                        </Button>
                        <Button
                            variant="secondary"
                            onClick={() => void sendTest()}
                            disabled={!currentUuid || sending || saving || loading}
                            title={
                                currentUuid
                                    ? "Send the saved template to your own address"
                                    : "Save the template first"
                            }
                        >
                            {sending ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <Send className="h-4 w-4" />
                            )}
                            {sending ? "Sending..." : "Send test"}
                        </Button>
                        <Button
                            onClick={() => void save()}
                            disabled={saving || loading || !name.trim() || !!loadError}
                        >
                            {saving ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <Save className="h-4 w-4" />
                            )}
                            {saving ? "Saving..." : "Save"}
                        </Button>
                    </div>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
