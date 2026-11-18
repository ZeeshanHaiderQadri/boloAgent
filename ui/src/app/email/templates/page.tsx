"use client";

import {
    AlertTriangle,
    Copy,
    FileText,
    Loader2,
    Pencil,
    Plus,
    Search,
    Trash2,
} from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
    deleteEmailTemplateApiV1EmailTemplatesTemplateUuidDelete,
    duplicateEmailTemplateApiV1EmailTemplatesTemplateUuidDuplicatePost,
    listEmailTemplatesApiV1EmailTemplatesGet,
} from "@/client";
import type { EmailTemplateResponse, EmailTemplateSummary } from "@/client/types.gen";
import { TemplateEditorDialog } from "@/components/email/template-editor-dialog";
import {
    LIST_ROW_ACTIONS_CLASS,
    LIST_ROW_CLASS,
    LIST_ROW_TARGET_CLASS,
} from "@/components/layout/ListRow";
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
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

function formatBadge(format: string) {
    return (
        <Badge variant="secondary">{format.toLowerCase() === "mjml" ? "MJML" : "HTML"}</Badge>
    );
}

function updatedLabel(template: EmailTemplateSummary) {
    const value = template.updated_at ?? template.created_at;
    if (!value) return "Never edited";
    return `Updated ${new Date(value).toLocaleDateString()}`;
}

export default function EmailTemplatesPage() {
    const { user, loading: authLoading, getAccessToken, redirectToLogin } = useAuth();
    const [templates, setTemplates] = useState<EmailTemplateSummary[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [reload, setReload] = useState(0);
    const [search, setSearch] = useState("");
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editUuid, setEditUuid] = useState<string | null>(null);
    const [deleteTarget, setDeleteTarget] = useState<EmailTemplateSummary | null>(null);
    const [deleting, setDeleting] = useState(false);
    const [deleteError, setDeleteError] = useState<string | null>(null);
    const [duplicating, setDuplicating] = useState<string | null>(null);

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
                const response = await listEmailTemplatesApiV1EmailTemplatesGet({
                    headers: { Authorization: `Bearer ${token}` },
                });
                if (response.error) {
                    throw new Error(
                        detailFromError(response.error, "Failed to load templates")
                    );
                }
                if (!cancelled) setTemplates(response.data ?? []);
            } catch (cause) {
                if (!cancelled) {
                    setError(
                        cause instanceof Error ? cause.message : "Failed to load templates"
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
    }, [authLoading, user, getAccessToken, reload]);

    const openCreate = () => {
        setEditUuid(null);
        setDialogOpen(true);
    };

    const openEdit = (template: EmailTemplateSummary) => {
        setEditUuid(template.uuid);
        setDialogOpen(true);
    };

    const applySaved = (template: EmailTemplateResponse) => {
        setTemplates((current) => {
            const exists = current.some((item) => item.uuid === template.uuid);
            return exists
                ? current.map((item) => (item.uuid === template.uuid ? template : item))
                : [template, ...current];
        });
    };

    const duplicate = async (template: EmailTemplateSummary) => {
        setDuplicating(template.uuid);
        try {
            const token = await getAccessToken();
            const response =
                await duplicateEmailTemplateApiV1EmailTemplatesTemplateUuidDuplicatePost({
                    headers: { Authorization: `Bearer ${token}` },
                    path: { template_uuid: template.uuid },
                });
            if (response.error) {
                throw new Error(detailFromError(response.error, "Failed to duplicate template"));
            }
            const copy = response.data;
            if (!copy) throw new Error("Failed to duplicate template");
            setTemplates((current) => [copy, ...current]);
            toast.success(`Created "${copy.name}"`);
        } catch (cause) {
            toast.error(
                cause instanceof Error ? cause.message : "Failed to duplicate template"
            );
        } finally {
            setDuplicating(null);
        }
    };

    const deleteTemplate = async () => {
        if (!deleteTarget || deleting) return;
        setDeleting(true);
        setDeleteError(null);
        try {
            const token = await getAccessToken();
            const response =
                await deleteEmailTemplateApiV1EmailTemplatesTemplateUuidDelete({
                    headers: { Authorization: `Bearer ${token}` },
                    path: { template_uuid: deleteTarget.uuid },
                });
            if (response.error) {
                throw new Error(detailFromError(response.error, "Failed to delete template"));
            }
            setTemplates((current) =>
                current.filter((item) => item.uuid !== deleteTarget.uuid)
            );
            setDeleteTarget(null);
            toast.success("Template deleted");
        } catch (cause) {
            setDeleteError(
                cause instanceof Error ? cause.message : "Failed to delete template"
            );
        } finally {
            setDeleting(false);
        }
    };

    const query = search.trim().toLowerCase();
    const filtered = templates.filter((template) =>
        [template.name, template.subject].some((value) =>
            value?.toLowerCase().includes(query)
        )
    );
    const missingUnsubscribe = templates.filter(
        (template) => !template.has_unsubscribe_placeholder
    ).length;

    return (
        <>
            <PageLayout
                title="Email templates"
                description="Write and test email copy before a campaign uses it. Previews use fake sample data."
            >
                <PageSection
                    title="Templates"
                    description="Each template stores its source, compiled HTML, and a plain-text fallback. HTML is sanitized on save."
                    actions={
                        <Button
                            onClick={openCreate}
                            disabled={authLoading || !user || loading || !!error}
                        >
                            <Plus className="h-4 w-4" />
                            New template
                        </Button>
                    }
                    toolbar={
                        <div className="relative">
                            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                            <Input
                                aria-label="Search templates"
                                placeholder="Search templates..."
                                value={search}
                                onChange={(event) => setSearch(event.target.value)}
                                className="pl-10"
                            />
                        </div>
                    }
                >
                    {authLoading || !user || loading ? (
                        <div className="space-y-4" role="status" aria-label="Loading templates">
                            {[1, 2, 3].map((item) => (
                                <Skeleton key={item} className="h-24 w-full rounded-lg" />
                            ))}
                        </div>
                    ) : error ? (
                        <div
                            role="alert"
                            className="rounded-lg border border-destructive/20 bg-destructive/10 p-4 text-sm text-destructive"
                        >
                            <p>{error}</p>
                            <Button
                                variant="outline"
                                size="sm"
                                className="mt-3"
                                onClick={() => setReload((value) => value + 1)}
                            >
                                Retry
                            </Button>
                        </div>
                    ) : filtered.length === 0 ? (
                        <div className="py-12 text-center">
                            <FileText className="mx-auto mb-4 h-12 w-12 text-muted-foreground" />
                            <p className="mb-4 text-muted-foreground">
                                {query
                                    ? "No templates match your search"
                                    : "No email templates yet"}
                            </p>
                            {query ? (
                                <Button variant="outline" onClick={() => setSearch("")}>
                                    Clear search
                                </Button>
                            ) : (
                                <Button onClick={openCreate}>Create your first template</Button>
                            )}
                        </div>
                    ) : (
                        <div className="space-y-4">
                            {filtered.map((template) => (
                                <div key={template.uuid} className={LIST_ROW_CLASS}>
                                    <div className="min-w-0 space-y-2">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <h3 className="break-words font-medium">
                                                <button
                                                    type="button"
                                                    className={LIST_ROW_TARGET_CLASS}
                                                    onClick={() => openEdit(template)}
                                                >
                                                    {template.name}
                                                </button>
                                            </h3>
                                            {formatBadge(template.source_format)}
                                            {!template.has_unsubscribe_placeholder && (
                                                <Badge variant="destructive">
                                                    No unsubscribe link
                                                </Badge>
                                            )}
                                        </div>
                                        <p className="break-words text-sm text-muted-foreground">
                                            {template.subject || "(No subject)"}
                                        </p>
                                        <p className="text-xs text-muted-foreground">
                                            {updatedLabel(template)}
                                        </p>
                                    </div>
                                    <div
                                        className={`${LIST_ROW_ACTIONS_CLASS} shrink-0 self-end sm:self-auto`}
                                    >
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            aria-label={`Edit ${template.name}`}
                                            onClick={() => openEdit(template)}
                                        >
                                            <Pencil className="h-4 w-4" />
                                            Edit
                                        </Button>
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            aria-label={`Duplicate ${template.name}`}
                                            disabled={duplicating === template.uuid}
                                            onClick={() => void duplicate(template)}
                                        >
                                            {duplicating === template.uuid ? (
                                                <Loader2 className="h-4 w-4 animate-spin" />
                                            ) : (
                                                <Copy className="h-4 w-4" />
                                            )}
                                            Duplicate
                                        </Button>
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            aria-label={`Delete ${template.name}`}
                                            className="text-destructive hover:text-destructive"
                                            onClick={() => {
                                                setDeleteError(null);
                                                setDeleteTarget(template);
                                            }}
                                        >
                                            <Trash2 className="h-4 w-4" />
                                        </Button>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </PageSection>

                {!loading && missingUnsubscribe > 0 && (
                    <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-sm">
                        <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-600" />
                        <p>
                            {missingUnsubscribe === 1
                                ? "One template has no unsubscribe placeholder."
                                : `${missingUnsubscribe} templates have no unsubscribe placeholder.`}{" "}
                            A campaign cannot launch with a template that lacks{" "}
                            <code>{"{{unsubscribe_link}}"}</code>.
                        </p>
                    </div>
                )}
            </PageLayout>

            <TemplateEditorDialog
                open={dialogOpen}
                templateUuid={editUuid}
                onOpenChange={(open) => {
                    setDialogOpen(open);
                    if (!open) setEditUuid(null);
                }}
                onSaved={applySaved}
            />

            <AlertDialog
                open={!!deleteTarget}
                onOpenChange={(open) => {
                    if (!open && !deleting) setDeleteTarget(null);
                }}
            >
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Delete template?</AlertDialogTitle>
                        <AlertDialogDescription>
                            Delete &quot;{deleteTarget?.name}&quot;? Messages already sent
                            keep their record; they just no longer point at this template.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    {deleteError && (
                        <p role="alert" className="text-sm text-destructive">
                            {deleteError}
                        </p>
                    )}
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
                        <Button
                            variant="destructive"
                            disabled={deleting}
                            onClick={() => void deleteTemplate()}
                        >
                            {deleting && <Loader2 className="h-4 w-4 animate-spin" />}
                            {deleting ? "Deleting..." : "Delete"}
                        </Button>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </>
    );
}
