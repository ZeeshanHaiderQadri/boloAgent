"use client";

import {
    Loader2,
    Mail,
    Megaphone,
    Plus,
    Trash2,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
    createEmailCampaignApiV1EmailCampaignsPost,
    deleteEmailCampaignApiV1EmailCampaignsCampaignUuidDelete,
    listEmailCampaignsApiV1EmailCampaignsGet,
} from "@/client";
import type { EmailCampaignResponse } from "@/client/types.gen";
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
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

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

function dateLabel(campaign: EmailCampaignResponse): string {
    const value = campaign.launched_at ?? campaign.updated_at ?? campaign.created_at;
    if (!value) return "Never updated";
    const prefix = campaign.launched_at ? "Launched" : "Updated";
    return `${prefix} ${new Date(value).toLocaleDateString()}`;
}

export default function EmailCampaignsPage() {
    const { user, loading: authLoading, getAccessToken, redirectToLogin } = useAuth();
    const router = useRouter();
    const [campaigns, setCampaigns] = useState<EmailCampaignResponse[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [reload, setReload] = useState(0);
    const [createOpen, setCreateOpen] = useState(false);
    const [createName, setCreateName] = useState("");
    const [creating, setCreating] = useState(false);
    const [createError, setCreateError] = useState<string | null>(null);
    const [deleteTarget, setDeleteTarget] = useState<EmailCampaignResponse | null>(null);
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
                const response = await listEmailCampaignsApiV1EmailCampaignsGet({
                    headers: { Authorization: `Bearer ${token}` },
                });
                if (response.error) {
                    throw new Error(detailFromError(response.error, "Failed to load campaigns"));
                }
                if (!cancelled) setCampaigns(response.data ?? []);
            } catch (cause) {
                if (!cancelled) {
                    setError(cause instanceof Error ? cause.message : "Failed to load campaigns");
                }
            } finally {
                if (!cancelled) setLoading(false);
            }
        };
        void load();
        return () => { cancelled = true; };
    }, [authLoading, user, getAccessToken, reload]);

    const openCreate = () => {
        setCreateName("");
        setCreateError(null);
        setCreateOpen(true);
    };

    const createCampaign = async () => {
        if (!createName.trim() || creating) return;
        setCreating(true);
        setCreateError(null);
        try {
            const token = await getAccessToken();
            const response = await createEmailCampaignApiV1EmailCampaignsPost({
                headers: { Authorization: `Bearer ${token}` },
                body: {
                    name: createName.trim(),
                    audience: { audience_type: "filter", all_matching: true },
                },
            });
            if (response.error) {
                throw new Error(detailFromError(response.error, "Failed to create campaign"));
            }
            const created = response.data;
            if (!created) throw new Error("Failed to create campaign");
            setCreateOpen(false);
            toast.success(`Campaign "${created.name}" created`);
            router.push(`/email/campaigns/${created.uuid}`);
        } catch (cause) {
            setCreateError(cause instanceof Error ? cause.message : "Failed to create campaign");
        } finally {
            setCreating(false);
        }
    };

    const deleteCampaign = async () => {
        if (!deleteTarget || deleting) return;
        setDeleting(true);
        setDeleteError(null);
        try {
            const token = await getAccessToken();
            const response = await deleteEmailCampaignApiV1EmailCampaignsCampaignUuidDelete({
                headers: { Authorization: `Bearer ${token}` },
                path: { campaign_uuid: deleteTarget.uuid },
            });
            if (response.error) {
                throw new Error(detailFromError(response.error, "Failed to delete campaign"));
            }
            setCampaigns((current) => current.filter((c) => c.uuid !== deleteTarget.uuid));
            setDeleteTarget(null);
            toast.success("Campaign deleted");
        } catch (cause) {
            setDeleteError(cause instanceof Error ? cause.message : "Failed to delete campaign");
        } finally {
            setDeleting(false);
        }
    };

    return (
        <>
            <PageLayout
                title="Email campaigns"
                description="Build and track email campaigns. Nothing sends until you launch — and launch is currently disabled."
            >
                <PageSection
                    title="Campaigns"
                    description="Each campaign targets an audience with a template and sender. Configure everything before launching."
                    actions={
                        <Button
                            onClick={openCreate}
                            disabled={authLoading || !user || loading || !!error}
                        >
                            <Plus className="h-4 w-4" />
                            New campaign
                        </Button>
                    }
                >
                    {authLoading || !user || loading ? (
                        <div className="space-y-4" role="status" aria-label="Loading campaigns">
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
                                onClick={() => setReload((v) => v + 1)}
                            >
                                Retry
                            </Button>
                        </div>
                    ) : campaigns.length === 0 ? (
                        <div className="py-12 text-center">
                            <Megaphone className="mx-auto mb-4 h-12 w-12 text-muted-foreground" />
                            <p className="mb-4 text-muted-foreground">No email campaigns yet</p>
                            <Button onClick={openCreate}>Create your first campaign</Button>
                        </div>
                    ) : (
                        <div className="space-y-4">
                            {campaigns.map((campaign) => (
                                <div key={campaign.uuid} className={LIST_ROW_CLASS}>
                                    <div className="min-w-0 space-y-2">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <h3 className="break-words font-medium">
                                                <button
                                                    type="button"
                                                    className={LIST_ROW_TARGET_CLASS}
                                                    onClick={() =>
                                                        router.push(`/email/campaigns/${campaign.uuid}`)
                                                    }
                                                >
                                                    {campaign.name}
                                                </button>
                                            </h3>
                                            {statusBadge(campaign.status)}
                                        </div>
                                        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                                            <span>{campaign.audience_type} audience</span>
                                            {campaign.sender && (
                                                <span>
                                                    <Mail className="mr-0.5 inline h-3 w-3" />
                                                    {campaign.sender.from_email}
                                                </span>
                                            )}
                                            {campaign.template && (
                                                <span>{campaign.template.name}</span>
                                            )}
                                        </div>
                                        <p className="text-xs text-muted-foreground">
                                            {dateLabel(campaign)}
                                        </p>
                                    </div>
                                    <div className={`${LIST_ROW_ACTIONS_CLASS} shrink-0 self-end sm:self-auto`}>
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            onClick={() =>
                                                router.push(`/email/campaigns/${campaign.uuid}`)
                                            }
                                        >
                                            Open
                                        </Button>
                                        {DRAFT_STATUSES.includes(campaign.status) && (
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                aria-label={`Delete ${campaign.name}`}
                                                className="text-destructive hover:text-destructive"
                                                onClick={() => {
                                                    setDeleteError(null);
                                                    setDeleteTarget(campaign);
                                                }}
                                            >
                                                <Trash2 className="h-4 w-4" />
                                            </Button>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </PageSection>
            </PageLayout>

            <Dialog
                open={createOpen}
                onOpenChange={(open) => {
                    if (!creating) setCreateOpen(open);
                }}
            >
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>New email campaign</DialogTitle>
                        <DialogDescription>
                            Give your campaign a name. You can configure audience, sender, and
                            template on the detail page.
                        </DialogDescription>
                    </DialogHeader>
                    {createError && (
                        <p role="alert" className="text-sm text-destructive">{createError}</p>
                    )}
                    <div className="grid gap-2 py-2">
                        <Label htmlFor="campaign-name">Name *</Label>
                        <Input
                            id="campaign-name"
                            value={createName}
                            onChange={(e) => setCreateName(e.target.value)}
                            placeholder="e.g. November re-engagement"
                            onKeyDown={(e) => {
                                if (e.key === "Enter") void createCampaign();
                            }}
                            autoFocus
                        />
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setCreateOpen(false)} disabled={creating}>
                            Cancel
                        </Button>
                        <Button
                            onClick={() => void createCampaign()}
                            disabled={!createName.trim() || creating}
                        >
                            {creating && <Loader2 className="h-4 w-4 animate-spin" />}
                            {creating ? "Creating..." : "Create"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <AlertDialog
                open={!!deleteTarget}
                onOpenChange={(open) => {
                    if (!open && !deleting) setDeleteTarget(null);
                }}
            >
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Delete campaign?</AlertDialogTitle>
                        <AlertDialogDescription>
                            Delete &quot;{deleteTarget?.name}&quot;? This action cannot be undone.
                            Only draft and ready campaigns can be deleted.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    {deleteError && (
                        <p role="alert" className="text-sm text-destructive">{deleteError}</p>
                    )}
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
                        <Button
                            variant="destructive"
                            disabled={deleting}
                            onClick={() => void deleteCampaign()}
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
