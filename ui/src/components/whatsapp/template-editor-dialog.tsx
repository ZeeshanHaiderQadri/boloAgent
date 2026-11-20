"use client";

import { Loader2, Plus, Send } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
    createWhatsappTemplateApiV1WhatsappTemplatesPost,
    getWhatsappTemplateApiV1WhatsappTemplatesTemplateUuidGet,
    updateWhatsappTemplateApiV1WhatsappTemplatesTemplateUuidPatch,
} from "@/client";
import type { WhatsAppTemplateResponse } from "@/client/types.gen";
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
import { cn } from "@/lib/utils";
import {
    extractTemplateVariables,
    renderTemplatePreview,
    WHATSAPP_TEMPLATE_BODY_MAX,
    WHATSAPP_VARIABLES,
} from "@/lib/whatsapp";

const STARTER_BODY =
    "Hi {{business_name}}, I had a question about your {{category}} services in {{city}}.";

const CATEGORIES = [
    { value: "marketing", label: "Marketing" },
    { value: "utility", label: "Utility" },
    { value: "authentication", label: "Authentication" },
] as const;

const bodyTooLongMessage = `Messages are limited to ${WHATSAPP_TEMPLATE_BODY_MAX.toLocaleString()} characters.`;

export function WhatsAppTemplateDialog({
    open,
    templateUuid,
    onOpenChange,
    onSaved,
}: {
    open: boolean;
    templateUuid: string | null;
    onOpenChange: (open: boolean) => void;
    onSaved: (template: WhatsAppTemplateResponse) => void;
}) {
    const { getAccessToken } = useAuth();
    const [name, setName] = useState("");
    const [language, setLanguage] = useState("en");
    const [category, setCategory] = useState<string>("marketing");
    const [body, setBody] = useState(STARTER_BODY);
    const [loading, setLoading] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [reload, setReload] = useState(0);
    const bodyRef = useRef<HTMLTextAreaElement | null>(null);

    useEffect(() => {
        if (!open) return;
        setName("");
        setLanguage("en");
        setCategory("marketing");
        setBody(STARTER_BODY);
        setError(null);
        setLoadError(null);
        setLoading(!!templateUuid);

        if (!templateUuid) return;
        let cancelled = false;
        const load = async () => {
            try {
                const token = await getAccessToken();
                const response =
                    await getWhatsappTemplateApiV1WhatsappTemplatesTemplateUuidGet({
                        headers: { Authorization: `Bearer ${token}` },
                        path: { template_uuid: templateUuid },
                    });
                if (response.error) {
                    throw new Error(
                        detailFromError(response.error, "Failed to load template")
                    );
                }
                if (cancelled || !response.data) return;
                setName(response.data.name);
                setLanguage(response.data.language);
                setCategory(response.data.category);
                setBody(response.data.body);
            } catch (cause) {
                if (!cancelled) {
                    setLoadError(
                        cause instanceof Error ? cause.message : "Failed to load template"
                    );
                }
            } finally {
                if (!cancelled) setLoading(false);
            }
        };
        void load();
        return () => {
            cancelled = true;
        };
    }, [open, templateUuid, getAccessToken, reload]);

    const variables = useMemo(() => extractTemplateVariables(body), [body]);
    const unknownVariables = variables.filter(
        (name) => !WHATSAPP_VARIABLES.some((variable) => variable.name === name),
    );
    const preview = useMemo(() => renderTemplatePreview(body), [body]);

    const insertVariable = (name: string) => {
        const token = `{{${name}}}`;
        if (body.length + token.length > WHATSAPP_TEMPLATE_BODY_MAX) {
            setError(bodyTooLongMessage);
            return;
        }
        const element = bodyRef.current;
        if (!element) {
            setBody((current) => `${current}${token}`);
            return;
        }
        const start = element.selectionStart ?? body.length;
        const end = element.selectionEnd ?? body.length;
        setBody(`${body.slice(0, start)}${token}${body.slice(end)}`);
        requestAnimationFrame(() => {
            element.focus();
            element.setSelectionRange(start + token.length, start + token.length);
        });
    };

    const save = async () => {
        if (saving) return;
        if (!name.trim()) {
            setError("Template name cannot be empty.");
            return;
        }
        if (!body.trim()) {
            setError("Message body cannot be empty.");
            return;
        }
        if (body.trim().length > WHATSAPP_TEMPLATE_BODY_MAX) {
            setError(bodyTooLongMessage);
            return;
        }
        setSaving(true);
        setError(null);
        try {
            const token = await getAccessToken();
            const headers = { Authorization: `Bearer ${token}` };
            const payload = {
                name: name.trim(),
                body,
                language: language.trim(),
                category,
            };
            const response = templateUuid
                ? await updateWhatsappTemplateApiV1WhatsappTemplatesTemplateUuidPatch({
                    headers,
                    path: { template_uuid: templateUuid },
                    body: payload,
                })
                : await createWhatsappTemplateApiV1WhatsappTemplatesPost({
                    headers,
                    body: payload,
                });
            if (response.error) {
                throw new Error(detailFromError(response.error, "Failed to save template"));
            }
            if (!response.data) throw new Error("Failed to save template");
            toast.success(templateUuid ? "Template updated" : "Template created");
            onSaved(response.data);
            onOpenChange(false);
        } catch (cause) {
            setError(cause instanceof Error ? cause.message : "Failed to save template");
        } finally {
            setSaving(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={(next) => !saving && onOpenChange(next)}>
            <DialogContent className="sm:max-w-3xl">
                <DialogHeader>
                    <DialogTitle>
                        {templateUuid ? "Edit WhatsApp template" : "New WhatsApp template"}
                    </DialogTitle>
                    <DialogDescription>
                        The message is typed into your own WhatsApp through a click-to-chat
                        link. Bolo does not send it and nothing needs Meta approval.
                    </DialogDescription>
                </DialogHeader>

                {loading ? (
                    <div className="space-y-3" role="status" aria-label="Loading template">
                        <Skeleton className="h-9 w-full" />
                        <Skeleton className="h-9 w-full" />
                        <Skeleton className="h-48 w-full" />
                    </div>
                ) : loadError ? (
                    <div
                        role="alert"
                        className="rounded-lg border border-destructive/20 bg-destructive/10 p-4 text-sm text-destructive"
                    >
                        <p>{loadError}</p>
                        <Button
                            variant="outline"
                            size="sm"
                            className="mt-3"
                            onClick={() => setReload((value) => value + 1)}
                        >
                            Retry
                        </Button>
                    </div>
                ) : (
                    <div className="grid gap-5 md:grid-cols-[3fr_2fr]">
                        <div className="space-y-4">
                            <div className="grid gap-2">
                                <Label htmlFor="wa-template-name">Name *</Label>
                                <Input
                                    id="wa-template-name"
                                    value={name}
                                    onChange={(event) => setName(event.target.value)}
                                    placeholder="First contact"
                                    maxLength={255}
                                />
                            </div>
                            <div className="grid grid-cols-2 gap-3">
                                <div className="grid gap-2">
                                    <Label htmlFor="wa-template-category">Category</Label>
                                    <Select value={category} onValueChange={setCategory}>
                                        <SelectTrigger id="wa-template-category" className="w-full">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {CATEGORIES.map((item) => (
                                                <SelectItem key={item.value} value={item.value}>
                                                    {item.label}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="grid gap-2">
                                    <Label htmlFor="wa-template-language">Language</Label>
                                    <Input
                                        id="wa-template-language"
                                        value={language}
                                        onChange={(event) => setLanguage(event.target.value)}
                                        placeholder="en"
                                        maxLength={16}
                                    />
                                </div>
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor="wa-template-body">Message *</Label>
                                <Textarea
                                    id="wa-template-body"
                                    ref={bodyRef}
                                    value={body}
                                    onChange={(event) => setBody(event.target.value)}
                                    rows={9}
                                    maxLength={WHATSAPP_TEMPLATE_BODY_MAX}
                                />
                                <div className="flex flex-wrap items-center justify-between gap-1.5">
                                    <div className="flex flex-wrap items-center gap-1.5">
                                        <span className="text-xs text-muted-foreground">
                                            Insert:
                                        </span>
                                        {WHATSAPP_VARIABLES.map((variable) => (
                                            <Button
                                                key={variable.name}
                                                type="button"
                                                variant="outline"
                                                size="sm"
                                                className="h-7 gap-1 px-2 text-xs"
                                                onClick={() => insertVariable(variable.name)}
                                            >
                                                <Plus className="h-3 w-3" />
                                                {variable.label}
                                            </Button>
                                        ))}
                                    </div>
                                    <span
                                        className={cn(
                                            "text-xs tabular-nums",
                                            body.length > WHATSAPP_TEMPLATE_BODY_MAX
                                                ? "text-destructive"
                                                : "text-muted-foreground",
                                        )}
                                    >
                                        {body.length.toLocaleString()} /{" "}
                                        {WHATSAPP_TEMPLATE_BODY_MAX.toLocaleString()}
                                    </span>
                                </div>
                                {unknownVariables.length > 0 && (
                                    <p role="alert" className="text-xs text-destructive">
                                        Unknown placeholder(s):{" "}
                                        {unknownVariables.map((name) => `{{${name}}}`).join(", ")}.
                                        Use only the three placeholders above.
                                    </p>
                                )}
                            </div>
                        </div>

                        <div className="space-y-2">
                            <p className="text-sm font-medium">Preview</p>
                            <div className="rounded-xl border bg-[#e5ddd5] p-3 dark:bg-muted/60">
                                <div className="mb-2 flex items-center gap-2 rounded-lg bg-[#075e54] px-3 py-2 text-xs font-medium text-white">
                                    <Send className="h-3.5 w-3.5" aria-hidden />
                                    WhatsApp message
                                </div>
                                <div className="whitespace-pre-wrap break-words rounded-lg rounded-tl-none bg-white px-3 py-2 text-sm text-gray-900 shadow-sm dark:bg-card dark:text-foreground">
                                    {preview || "Your message preview appears here."}
                                </div>
                            </div>
                            <p className="text-xs text-muted-foreground">
                                Preview uses sample data (Apex Labs, Marketing agency, Austin).
                                The real message fills in each lead&apos;s details.
                            </p>
                            {variables.length > 0 && (
                                <div className="flex flex-wrap gap-1">
                                    {variables.map((name) => (
                                        <Badge
                                            key={name}
                                            variant={
                                                unknownVariables.includes(name)
                                                    ? "destructive"
                                                    : "secondary"
                                            }
                                        >
                                            {`{{${name}}}`}
                                        </Badge>
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>
                )}

                {error && (
                    <p role="alert" className="text-sm text-destructive">
                        {error}
                    </p>
                )}

                <DialogFooter>
                    <Button
                        variant="outline"
                        onClick={() => onOpenChange(false)}
                        disabled={saving}
                    >
                        Cancel
                    </Button>
                    <Button
                        onClick={() => void save()}
                        disabled={saving || loading || !!loadError}
                    >
                        {saving && <Loader2 className="h-4 w-4 animate-spin" />}
                        {saving ? "Saving..." : templateUuid ? "Save changes" : "Create template"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
