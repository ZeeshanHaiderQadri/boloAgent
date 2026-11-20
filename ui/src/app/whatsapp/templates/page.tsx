"use client";

import { Loader2, MessageCircle, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
    deleteWhatsappTemplateApiV1WhatsappTemplatesTemplateUuidDelete,
    listWhatsappTemplatesApiV1WhatsappTemplatesGet,
} from "@/client";
import type { WhatsAppTemplateResponse, WhatsAppTemplateSummary } from "@/client/types.gen";
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
import { ListRowSkeleton } from "@/components/ui/skeleton";
import { WhatsAppTemplateDialog } from "@/components/whatsapp/template-editor-dialog";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

const CATEGORY_LABELS: Record<string, string> = {
    marketing: "Marketing",
    utility: "Utility",
    authentication: "Authentication",
};

export default function WhatsAppTemplatesPage() {
    const { user, loading: authLoading, getAccessToken, redirectToLogin } = useAuth();
    const [templates, setTemplates] = useState<WhatsAppTemplateSummary[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [reload, setReload] = useState(0);
    const [search, setSearch] = useState("");
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editUuid, setEditUuid] = useState<string | null>(null);
    const [deleteTarget, setDeleteTarget] = useState<WhatsAppTemplateSummary | null>(null);
    const [deleting, setDeleting] = useState(false);
    const [deleteError, setDeleteError] = useState<string | null>(null);

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
                const response = await listWhatsappTemplatesApiV1WhatsappTemplatesGet({
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

    const openEdit = (template: WhatsAppTemplateSummary) => {
        setEditUuid(template.uuid);
        setDialogOpen(true);
    };

    const applySaved = (template: WhatsAppTemplateResponse) => {
        setTemplates((current) => {
            const exists = current.some((item) => item.uuid === template.uuid);
            return exists
                ? current.map((item) => (item.uuid === template.uuid ? template : item))
                : [template, ...current];
        });
    };

    const deleteTemplate = async () => {
        if (!deleteTarget || deleting) return;
        setDeleting(true);
        setDeleteError(null);
        try {
            const token = await getAccessToken();
            const response =
                await deleteWhatsappTemplateApiV1WhatsappTemplatesTemplateUuidDelete({
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
        [template.name, template.body].some((value) =>
            value?.toLowerCase().includes(query)
        )
    );

    return (
        <>
            <PageLayout
                title="WhatsApp templates"
                description="Prepare click-to-chat messages for leads. Bolo never sends WhatsApp messages: templates only fill in a wa.me link you open yourself."
            >
                <PageSection
                    title="Templates"
                    description="Plain text with optional placeholders for the lead's business name, category and city. No Meta approval is needed."
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
                                <ListRowSkeleton key={item} />
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
                        <div className="list-enter rounded-xl border border-dashed py-14 text-center">
                            <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
                                <MessageCircle className="h-6 w-6 text-primary" aria-hidden />
                            </div>
                            <h3 className="text-base font-semibold text-foreground">
                                {query
                                    ? "No templates match your search"
                                    : "No WhatsApp templates yet"}
                            </h3>
                            <p className="mx-auto mt-1 max-w-sm text-sm text-muted-foreground">
                                {query
                                    ? "Try a different name or message text."
                                    : "Write a short message you can open in WhatsApp with each lead's details filled in."}
                            </p>
                            {query ? (
                                <Button
                                    variant="outline"
                                    className="mt-5"
                                    onClick={() => setSearch("")}
                                >
                                    Clear search
                                </Button>
                            ) : (
                                <Button className="mt-5" onClick={openCreate}>
                                    Create your first template
                                </Button>
                            )}
                        </div>
                    ) : (
                        <div className="space-y-4">
                            {filtered.map((template, index) => (
                                <div
                                    key={template.uuid}
                                    style={{ animationDelay: `${Math.min(index, 6) * 60}ms` }}
                                    className={`list-enter ${LIST_ROW_CLASS}`}
                                >
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
                                            <Badge variant="secondary">
                                                {CATEGORY_LABELS[template.category] ??
                                                    template.category}
                                            </Badge>
                                            <Badge variant="outline">{template.language}</Badge>
                                        </div>
                                        <p className="line-clamp-2 break-words text-sm text-muted-foreground">
                                            {template.body}
                                        </p>
                                        <p className="text-xs text-muted-foreground">
                                            {template.updated_at
                                                ? `Updated ${new Date(
                                                    template.updated_at
                                                ).toLocaleDateString()}`
                                                : "Never edited"}
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
            </PageLayout>

            <WhatsAppTemplateDialog
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
                            Delete &quot;{deleteTarget?.name}&quot;? Links already opened in
                            WhatsApp keep working; this template just stops appearing in the
                            chooser.
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
