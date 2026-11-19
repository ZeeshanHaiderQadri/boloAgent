"use client";

import { AlertTriangle, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
    addLeadsToEmailCampaignApiV1EmailCampaignsCampaignUuidAddLeadsPost,
    createEmailCampaignApiV1EmailCampaignsPost,
    listEmailCampaignsApiV1EmailCampaignsGet,
    updateEmailCampaignApiV1EmailCampaignsCampaignUuidPatch,
} from "@/client";
import type {
    AudienceRuleRequest,
    CampaignFilters,
    EmailCampaignResponse,
} from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

export interface FilterState {
    q?: string;
    status?: string;
    tag?: string;
    category?: string;
    country?: string;
    state?: string;
    city?: string;
    has_phone?: boolean;
    has_email?: boolean;
}

interface Props {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    audienceType: "leads" | "project" | "tag" | "filter";
    leadIds?: number[];
    projectId?: number;
    tag?: string;
    filterState?: FilterState;
    pageVisibleCount?: number;
}

const DRAFT_STATUSES = ["draft", "ready"];

export function AddToCampaignDialog({
    open,
    onOpenChange,
    audienceType,
    leadIds,
    projectId,
    tag,
    filterState,
    pageVisibleCount,
}: Props) {
    const { getAccessToken } = useAuth();
    const router = useRouter();

    const [tab, setTab] = useState<"new" | "existing">("new");
    const [drafts, setDrafts] = useState<EmailCampaignResponse[]>([]);
    const [loadingDrafts, setLoadingDrafts] = useState(false);
    const [draftsError, setDraftsError] = useState<string | null>(null);

    const [newName, setNewName] = useState("");
    const [creating, setCreating] = useState(false);
    const [createError, setCreateError] = useState<string | null>(null);

    const [selectedDraftUuid, setSelectedDraftUuid] = useState("");
    const [updating, setUpdating] = useState(false);
    const [updateError, setUpdateError] = useState<string | null>(null);

    // For leads audienceType: allow switching to filter-based audience
    const [useAllFilter, setUseAllFilter] = useState(false);

    useEffect(() => {
        if (!open) return;
        setTab("new");
        setNewName("");
        setCreateError(null);
        setUpdateError(null);
        setDraftsError(null);
        setSelectedDraftUuid("");
        setUseAllFilter(false);

        let cancelled = false;
        const loadDrafts = async () => {
            setLoadingDrafts(true);
            try {
                const token = await getAccessToken();
                const response = await listEmailCampaignsApiV1EmailCampaignsGet({
                    headers: { Authorization: `Bearer ${token}` },
                });
                if (response.error) {
                    throw new Error(detailFromError(response.error, "Failed to load campaigns"));
                }
                if (!cancelled) {
                    const all = response.data ?? [];
                    setDrafts(all.filter((c) => DRAFT_STATUSES.includes(c.status)));
                }
            } catch (cause) {
                if (!cancelled) {
                    setDraftsError(cause instanceof Error ? cause.message : "Failed to load campaigns");
                }
            } finally {
                if (!cancelled) setLoadingDrafts(false);
            }
        };
        void loadDrafts();
        return () => { cancelled = true; };
    }, [open, getAccessToken]);

    function buildAudienceRule(): AudienceRuleRequest {
        // `has_email: false` (No email filter) is a real filter, so truthiness
        // alone would drop it and flip the audience to has_email: true.
        const hasActiveFilter =
            filterState != null &&
            Object.values(filterState).some(
                (value) => value !== undefined && value !== null && value !== "",
            );
        if (audienceType === "leads") {
            if (useAllFilter) {
                // Use filterState if available; fall back to has_email=true so the
                // API always gets at least one filter field.
                const filters: CampaignFilters =
                    hasActiveFilter && filterState
                        ? {
                              q: filterState.q ?? undefined,
                              status: (filterState.status as CampaignFilters["status"]) ?? undefined,
                              tag: filterState.tag ?? undefined,
                              category: filterState.category ?? undefined,
                              country: filterState.country ?? undefined,
                              state: filterState.state ?? undefined,
                              city: filterState.city ?? undefined,
                              has_phone: filterState.has_phone ?? undefined,
                              has_email: filterState.has_email ?? undefined,
                          }
                        : { has_email: true };
                return { audience_type: "filter", filters, all_matching: true };
            }
            // If no leads are selected, fall back to all-email filter rather
            // than sending an empty lead_ids array that the API rejects.
            if (!leadIds || leadIds.length === 0) {
                return { audience_type: "filter", filters: { has_email: true }, all_matching: true };
            }
            return { audience_type: "leads", lead_ids: leadIds };
        }
        if (audienceType === "project" && projectId != null) {
            return { audience_type: "project", project_id: projectId };
        }
        if (audienceType === "tag" && tag) {
            return { audience_type: "tag", tag };
        }
        // filter or any fallback — always include at least has_email so the API
        // never sees an empty filter object.
        const filters: CampaignFilters =
            hasActiveFilter && filterState
                ? {
                      q: filterState.q ?? undefined,
                      status: (filterState.status as CampaignFilters["status"]) ?? undefined,
                      tag: filterState.tag ?? undefined,
                      category: filterState.category ?? undefined,
                      country: filterState.country ?? undefined,
                      state: filterState.state ?? undefined,
                      city: filterState.city ?? undefined,
                      has_phone: filterState.has_phone ?? undefined,
                      has_email: filterState.has_email ?? undefined,
                  }
                : { has_email: true };
        return { audience_type: "filter", filters, all_matching: true };
    }

    const handleCreate = async () => {
        if (!newName.trim() || creating) return;
        setCreating(true);
        setCreateError(null);
        try {
            const token = await getAccessToken();
            const rule = buildAudienceRule();
            const response = await createEmailCampaignApiV1EmailCampaignsPost({
                headers: { Authorization: `Bearer ${token}` },
                body: { name: newName.trim(), audience: rule },
            });
            if (response.error) {
                throw new Error(detailFromError(response.error, "Failed to create campaign"));
            }
            const created = response.data;
            if (!created) throw new Error("Failed to create campaign");
            toast.success(`Campaign "${created.name}" created`);
            onOpenChange(false);
            router.push(`/email/campaigns/${created.uuid}`);
        } catch (cause) {
            setCreateError(cause instanceof Error ? cause.message : "Failed to create campaign");
        } finally {
            setCreating(false);
        }
    };

    const handleUpdate = async () => {
        if (!selectedDraftUuid || updating) return;
        setUpdating(true);
        setUpdateError(null);
        try {
            const token = await getAccessToken();
            const appendSelectedLeads =
                audienceType === "leads" && !useAllFilter && (leadIds?.length ?? 0) > 0;
            if (appendSelectedLeads) {
                // Append the checked rows; the draft's existing audience is kept.
                const response =
                    await addLeadsToEmailCampaignApiV1EmailCampaignsCampaignUuidAddLeadsPost({
                        headers: { Authorization: `Bearer ${token}` },
                        path: { campaign_uuid: selectedDraftUuid },
                        body: { lead_ids: leadIds ?? [] },
                    });
                if (response.error) {
                    throw new Error(
                        detailFromError(response.error, "Failed to add leads to campaign"),
                    );
                }
                toast.success(
                    `Added ${leadIds?.length ?? 0} lead(s) to the campaign audience`,
                );
            } else {
                const rule = buildAudienceRule();
                const response = await updateEmailCampaignApiV1EmailCampaignsCampaignUuidPatch({
                    headers: { Authorization: `Bearer ${token}` },
                    path: { campaign_uuid: selectedDraftUuid },
                    body: { audience: rule },
                });
                if (response.error) {
                    throw new Error(
                        detailFromError(response.error, "Failed to update campaign"),
                    );
                }
                toast.success("Campaign audience updated");
            }
            onOpenChange(false);
            router.push(`/email/campaigns/${selectedDraftUuid}`);
        } catch (cause) {
            setUpdateError(cause instanceof Error ? cause.message : "Failed to update campaign");
        } finally {
            setUpdating(false);
        }
    };

    const busy = creating || updating;

    return (
        <Dialog open={open} onOpenChange={(v) => { if (!busy) onOpenChange(v); }}>
            <DialogContent className="sm:max-w-lg">
                <DialogHeader>
                    <DialogTitle>Add to email campaign — nothing sends</DialogTitle>
                    <DialogDescription>
                        Add this audience to a new or existing draft campaign. No email is
                        sent from this dialog.
                    </DialogDescription>
                </DialogHeader>

                {/* Page-visible warning for leads audienceType */}
                {audienceType === "leads" && pageVisibleCount !== undefined && (
                    <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                        <div className="space-y-2">
                            <p>
                                Checked rows apply to the visible page only ({pageVisibleCount}{" "}
                                lead(s)). To add everyone matching your current filter, use
                                &quot;All matching filter&quot; below.
                            </p>
                            <div className="flex items-center gap-2">
                                <Checkbox
                                    id="use-all-filter"
                                    checked={useAllFilter}
                                    onCheckedChange={(checked) =>
                                        setUseAllFilter(checked === true)
                                    }
                                />
                                <Label htmlFor="use-all-filter" className="cursor-pointer text-sm">
                                    Use all matching filter instead
                                </Label>
                            </div>
                        </div>
                    </div>
                )}

                {draftsError && (
                    <p role="alert" className="text-sm text-destructive">{draftsError}</p>
                )}

                <Tabs value={tab} onValueChange={(v) => setTab(v as "new" | "existing")}>
                    <TabsList className="w-full">
                        <TabsTrigger value="new" className="flex-1">New campaign</TabsTrigger>
                        <TabsTrigger value="existing" className="flex-1">
                            Existing draft
                            {drafts.length > 0 && (
                                <span className="ml-1.5 rounded-full bg-muted px-1.5 text-xs">
                                    {drafts.length}
                                </span>
                            )}
                        </TabsTrigger>
                    </TabsList>

                    <TabsContent value="new" className="mt-4 space-y-4">
                        {createError && (
                            <p role="alert" className="text-sm text-destructive">{createError}</p>
                        )}
                        <div className="grid gap-2">
                            <Label htmlFor="new-campaign-name">Campaign name *</Label>
                            <Input
                                id="new-campaign-name"
                                value={newName}
                                onChange={(e) => setNewName(e.target.value)}
                                placeholder="e.g. November re-engagement"
                                onKeyDown={(e) => {
                                    if (e.key === "Enter") void handleCreate();
                                }}
                                autoFocus
                            />
                        </div>
                        <DialogFooter>
                            <Button
                                variant="outline"
                                onClick={() => onOpenChange(false)}
                                disabled={busy}
                            >
                                Cancel
                            </Button>
                            <Button
                                onClick={() => void handleCreate()}
                                disabled={!newName.trim() || busy}
                            >
                                {creating && <Loader2 className="h-4 w-4 animate-spin" />}
                                {creating ? "Creating..." : "Create & open"}
                            </Button>
                        </DialogFooter>
                    </TabsContent>

                    <TabsContent value="existing" className="mt-4 space-y-4">
                        {updateError && (
                            <p role="alert" className="text-sm text-destructive">{updateError}</p>
                        )}
                        {loadingDrafts ? (
                            <p className="text-sm text-muted-foreground">Loading campaigns…</p>
                        ) : drafts.length === 0 ? (
                            <p className="text-sm text-muted-foreground">
                                No draft or ready campaigns found. Create a new one instead.
                            </p>
                        ) : (
                            <div className="grid gap-2">
                                <Label htmlFor="existing-campaign-select">Select campaign</Label>
                                <Select
                                    value={selectedDraftUuid}
                                    onValueChange={setSelectedDraftUuid}
                                >
                                    <SelectTrigger id="existing-campaign-select">
                                        <SelectValue placeholder="Choose a campaign…" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {drafts.map((c) => (
                                            <SelectItem key={c.uuid} value={c.uuid}>
                                                {c.name}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                        )}
                        <DialogFooter>
                            <Button
                                variant="outline"
                                onClick={() => onOpenChange(false)}
                                disabled={busy}
                            >
                                Cancel
                            </Button>
                            <Button
                                onClick={() => void handleUpdate()}
                                disabled={!selectedDraftUuid || busy || drafts.length === 0}
                            >
                                {updating && <Loader2 className="h-4 w-4 animate-spin" />}
                                {updating ? "Updating..." : "Add & open"}
                            </Button>
                        </DialogFooter>
                    </TabsContent>
                </Tabs>
            </DialogContent>
        </Dialog>
    );
}
