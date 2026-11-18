"use client";

import {
    ArrowLeft,
    FolderOpen,
    FolderPlus,
    Loader2,
    Megaphone,
    Phone,
    Plus,
    RefreshCw,
    Search,
    ShieldCheck,
    Tags,
    Trash2,
    Users,
    X,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { type DragEvent,useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    assignLeadsToProjectApiV1LeadProjectsProjectIdLeadsPost,
    bulkUpdateLeadsApiV1LeadsBulkPost,
    deleteLeadApiV1LeadsLeadIdDelete,
    deleteLeadProjectApiV1LeadProjectsProjectIdDelete,
    getLeadProjectApiV1LeadProjectsProjectIdGet,
    getLeadStatsApiV1LeadsStatsGet,
    listLeadProjectsApiV1LeadProjectsGet,
    listLeadsApiV1LeadsGet,
    unassignLeadsFromProjectApiV1LeadProjectsProjectIdLeadsDelete,
} from "@/client/sdk.gen";
import type {
    LeadListResponse,
    LeadProjectResponse,
    LeadResponse,
    LeadStatsResponse,
} from "@/client/types.gen";
import { PageLayout, PageSection } from "@/components/layout/PageLayout";
import { DispatchLeadsDialog } from "@/components/leads/DispatchLeadsDialog";
import { EmailConsentDialog } from "@/components/leads/EmailConsentDialog";
import { ExportLeadsButton } from "@/components/leads/ExportLeadsButton";
import { LeadEditDialog } from "@/components/leads/LeadEditDialog";
import { LeadTable } from "@/components/leads/LeadTable";
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
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import {
    LEAD_SORT_LABELS,
    LEAD_STATUS_LABELS,
    LEAD_STATUSES,
    type LeadStatus,
} from "@/lib/leadStatus";

type LeadSort = "created_at" | "rating" | "title" | "status";
type WebsiteFilter = "any" | "has" | "no";

const LEAD_DRAG_TYPE = "application/x-bolo-lead-ids";
const PAGE_SIZE_OPTIONS = [25, 50, 100];
const ANY = "all";

function parseDraggedLeads(event: DragEvent<HTMLElement>): number[] {
    try {
        const raw = event.dataTransfer.getData(LEAD_DRAG_TYPE);
        if (!raw) return [];
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed)
            ? parsed.filter((id): id is number => Number.isInteger(id))
            : [];
    } catch {
        return [];
    }
}

function pageChoices(totalPages: number): number[] {
    return Array.from({ length: Math.max(1, totalPages) }, (_, index) => index + 1);
}

export default function LeadProjectPage() {
    const params = useParams<{ projectId: string }>();
    const projectId = Number(params.projectId);
    const router = useRouter();
    const { user, redirectToLogin, loading } = useAuth();
    const hasFetched = useRef(false);

    const [project, setProject] = useState<LeadProjectResponse | null>(null);
    const [projects, setProjects] = useState<LeadProjectResponse[]>([]);
    const [data, setData] = useState<LeadListResponse | null>(null);
    const [stats, setStats] = useState<LeadStatsResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [notFound, setNotFound] = useState(false);

    const [query, setQuery] = useState("");
    const [appliedQuery, setAppliedQuery] = useState("");
    const [status, setStatus] = useState<string>(ANY);
    const [tag, setTag] = useState("");
    const [website, setWebsite] = useState<WebsiteFilter>("any");
    const [hasPhone, setHasPhone] = useState(false);
    const [sort, setSort] = useState<LeadSort>("created_at");
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(25);

    const [selectedIds, setSelectedIds] = useState<number[]>([]);
    const [bulkStatus, setBulkStatus] = useState("");
    const [bulkBusy, setBulkBusy] = useState(false);
    const [tagsOpen, setTagsOpen] = useState(false);
    const [tagsDraft, setTagsDraft] = useState("");
    const [tagsBusy, setTagsBusy] = useState(false);
    const [moveTargetId, setMoveTargetId] = useState("");
    const [moveBusy, setMoveBusy] = useState(false);
    const [dropTarget, setDropTarget] = useState<number | null>(null);
    const [deleteProjectBusy, setDeleteProjectBusy] = useState(false);
    const [deleteOpen, setDeleteOpen] = useState(false);

    const [editingLead, setEditingLead] = useState<LeadResponse | null>(null);
    const [editOpen, setEditOpen] = useState(false);
    const [dispatchOpen, setDispatchOpen] = useState(false);
    const [consentOpen, setConsentOpen] = useState(false);

    useEffect(() => {
        if (!loading && !user) redirectToLogin();
    }, [loading, user, redirectToLogin]);

    const fetchProjects = useCallback(async () => {
        const response = await listLeadProjectsApiV1LeadProjectsGet({});
        if (response.data) setProjects(response.data.projects ?? []);
    }, []);

    const fetchLeads = useCallback(async () => {
        if (loading || !user || !Number.isFinite(projectId)) return;
        setIsLoading(true);
        setError(null);
        try {
            const [leadsResponse, statsResponse] = await Promise.all([
                listLeadsApiV1LeadsGet({
                    query: {
                        project_id: projectId,
                        q: appliedQuery || null,
                        status: status === ANY ? null : (status as LeadStatus),
                        tag: tag.trim() || null,
                        has_phone: hasPhone ? true : null,
                        has_website: website === "any" ? null : website === "has",
                        sort,
                        page,
                        limit: pageSize,
                    },
                }),
                getLeadStatsApiV1LeadsStatsGet({
                    query: { project_id: projectId },
                }),
            ]);
            if (leadsResponse.error) {
                setError(detailFromError(leadsResponse.error, "Could not load this project"));
                return;
            }
            setData(leadsResponse.data ?? null);
            setStats(statsResponse.data ?? null);
            setSelectedIds([]);
        } catch (fetchError) {
            console.error("Failed to load project leads:", fetchError);
            setError("Could not load this project");
        } finally {
            setIsLoading(false);
        }
    }, [
        loading,
        user,
        projectId,
        appliedQuery,
        status,
        tag,
        hasPhone,
        website,
        sort,
        page,
        pageSize,
    ]);

    const loadProject = useCallback(async () => {
        if (!Number.isFinite(projectId)) {
            setNotFound(true);
            setIsLoading(false);
            return;
        }
        try {
            const response = await getLeadProjectApiV1LeadProjectsProjectIdGet({
                path: { project_id: projectId },
            });
            if (response.error) {
                if (response.response?.status === 404) setNotFound(true);
                else setError(detailFromError(response.error, "Could not load the project"));
                return;
            }
            setProject(response.data ?? null);
        } catch (loadError) {
            console.error("Failed to load project:", loadError);
            setError("Could not load the project");
        }
    }, [projectId]);

    useEffect(() => {
        if (loading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void loadProject();
        void fetchProjects();
    }, [loading, user, loadProject, fetchProjects]);

    useEffect(() => {
        if (loading || !user) return;
        void fetchLeads();
    }, [loading, user, fetchLeads]);

    const refreshAll = async () => {
        await Promise.all([loadProject(), fetchProjects(), fetchLeads()]);
    };

    const leads = data?.leads ?? [];
    const totalPages = Math.max(1, data?.total_pages ?? 0);
    const otherProjects = projects.filter((item) => item.id !== projectId);

    const toggleSelected = (leadId: number) => {
        setSelectedIds((current) =>
            current.includes(leadId)
                ? current.filter((id) => id !== leadId)
                : [...current, leadId],
        );
    };

    const toggleAll = () => {
        setSelectedIds((current) =>
            leads.every((lead) => current.includes(lead.id))
                ? []
                : leads.map((lead) => lead.id),
        );
    };

    const applyBulkStatus = async () => {
        if (!bulkStatus || selectedIds.length === 0) return;
        setBulkBusy(true);
        try {
            const response = await bulkUpdateLeadsApiV1LeadsBulkPost({
                body: { lead_ids: selectedIds, crm_status: bulkStatus as LeadStatus },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Bulk update failed"));
                return;
            }
            toast.success(
                `Updated ${response.data?.updated ?? selectedIds.length} lead(s) to ${
                    LEAD_STATUS_LABELS[bulkStatus] ?? bulkStatus
                }`,
            );
            setBulkStatus("");
            await fetchLeads();
        } finally {
            setBulkBusy(false);
        }
    };

    const applyTags = async () => {
        const tags = tagsDraft
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean);
        if (tags.length === 0 || selectedIds.length === 0) return;
        setTagsBusy(true);
        try {
            const response = await bulkUpdateLeadsApiV1LeadsBulkPost({
                body: { lead_ids: selectedIds, add_tags: tags },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not add tags"));
                return;
            }
            toast.success(`Added ${tags.join(", ")} to ${selectedIds.length} lead(s)`);
            setTagsDraft("");
            setTagsOpen(false);
            await fetchLeads();
        } finally {
            setTagsBusy(false);
        }
    };

    const moveLeads = async (targetId: number, leadIds: number[]) => {
        if (!leadIds.length || targetId === projectId) return;
        const target = projects.find((item) => item.id === targetId);
        setMoveBusy(true);
        try {
            const response = await assignLeadsToProjectApiV1LeadProjectsProjectIdLeadsPost({
                path: { project_id: targetId },
                body: { lead_ids: leadIds },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not move the leads"));
                return;
            }
            toast.success(
                `Moved ${response.data?.assigned ?? leadIds.length} lead(s) to “${
                    target?.name ?? "project"
                }”`,
            );
            setMoveTargetId("");
            await refreshAll();
        } finally {
            setMoveBusy(false);
        }
    };

    const handleDrop = async (
        targetId: number,
        event: DragEvent<HTMLDivElement>,
    ) => {
        event.preventDefault();
        setDropTarget(null);
        const leadIds = parseDraggedLeads(event);
        if (leadIds.length === 0) return;
        await moveLeads(targetId, leadIds);
    };

    const removeSelected = async () => {
        if (selectedIds.length === 0) return;
        const confirmed = window.confirm(
            `Remove ${selectedIds.length} lead(s) from “${project?.name ?? "this project"}”? They stay in the CRM.`,
        );
        if (!confirmed) return;
        setMoveBusy(true);
        try {
            const response =
                await unassignLeadsFromProjectApiV1LeadProjectsProjectIdLeadsDelete({
                    path: { project_id: projectId },
                    body: { lead_ids: selectedIds },
                });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not remove the leads"));
                return;
            }
            toast.success(`Removed ${response.data?.removed ?? selectedIds.length} lead(s)`);
            await refreshAll();
        } finally {
            setMoveBusy(false);
        }
    };

    const deleteSelected = async () => {
        if (selectedIds.length === 0) return;
        const confirmed = window.confirm(
            `Delete ${selectedIds.length} lead(s) permanently from the CRM? Their notes and call history links are removed too.`,
        );
        if (!confirmed) return;
        setMoveBusy(true);
        try {
            const results = await Promise.all(
                selectedIds.map((leadId) =>
                    deleteLeadApiV1LeadsLeadIdDelete({ path: { lead_id: leadId } }),
                ),
            );
            const failed = results.filter((item) => item.error).length;
            if (failed > 0) {
                toast.error(`${failed} lead(s) could not be deleted`);
            } else {
                toast.success(`Deleted ${selectedIds.length} lead(s)`);
            }
            await refreshAll();
        } finally {
            setMoveBusy(false);
        }
    };

    const deleteProject = async () => {
        if (!project) return;
        setDeleteProjectBusy(true);
        try {
            const response = await deleteLeadProjectApiV1LeadProjectsProjectIdDelete({
                path: { project_id: projectId },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not delete the project"));
                return;
            }
            toast.success("Project deleted — leads remain in the CRM");
            setDeleteOpen(false);
            router.push("/leads/projects");
        } finally {
            setDeleteProjectBusy(false);
        }
    };

    const exportFilters = {
        project_id: projectId,
        q: appliedQuery || null,
        status: status === ANY ? null : (status as LeadStatus),
        tag: tag.trim() || null,
        has_phone: hasPhone ? true : null,
        has_website: website === "any" ? null : website === "has",
        sort,
    };

    const hasActiveFilters =
        Boolean(appliedQuery.trim()) ||
        status !== ANY ||
        Boolean(tag.trim()) ||
        website !== "any" ||
        hasPhone;

    if (notFound) {
        return (
            <PageLayout
                title="Project not found"
                description="It may have been deleted. Leads that were filed here are still in the CRM."
                actions={
                    <Button asChild>
                        <Link href="/leads/projects">
                            <ArrowLeft className="h-4 w-4" />
                            Back to projects
                        </Link>
                    </Button>
                }
            >
                <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">
                    This lead project no longer exists.
                </div>
            </PageLayout>
        );
    }

    return (
        <PageLayout
            title={project?.name ?? "Lead project"}
            description="A saved campaign list. Edit contact details, tag leads, move them between projects, export the sheet, or send the list to an outbound campaign."
            actions={
                <>
                    <Button variant="outline" asChild>
                        <Link href="/leads/projects">
                            <ArrowLeft className="h-4 w-4" />
                            Projects
                        </Link>
                    </Button>
                    <ExportLeadsButton
                        filters={exportFilters}
                        disabled={!data || data.total_count === 0}
                    />
                    <Button
                        variant="outline"
                        onClick={() => void refreshAll()}
                        disabled={isLoading}
                    >
                        <RefreshCw className={isLoading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
                        Refresh
                    </Button>
                    <Button
                        variant="outline"
                        className="text-destructive hover:text-destructive"
                        onClick={() => setDeleteOpen(true)}
                        disabled={deleteProjectBusy}
                    >
                        {deleteProjectBusy ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                            <Trash2 className="h-4 w-4" />
                        )}
                        Delete project
                    </Button>
                </>
            }
        >
            {error && (
                <p
                    role="alert"
                    className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive"
                >
                    {error}
                </p>
            )}

            <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_260px]">
                <PageSection
                    title={
                        <span className="flex items-center gap-2">
                            <Users className="h-5 w-5 text-cta" />
                            Leads in this project
                            {stats && (
                                <span className="text-sm font-normal text-muted-foreground">
                                    {stats.total} total · {stats.with_phone} callable
                                </span>
                            )}
                        </span>
                    }
                    description="Filter the list, tag a batch, or drag rows onto another project to move them. Leads are shared CRM records — nothing is duplicated."
                    toolbar={
                        <form
                            className="flex flex-wrap items-end gap-3"
                            onSubmit={(event) => {
                                event.preventDefault();
                                setPage(1);
                                setAppliedQuery(query.trim());
                            }}
                        >
                            <div className="min-w-[200px] flex-1 space-y-1.5">
                                <Label
                                    htmlFor="project-lead-search"
                                    className="text-xs text-muted-foreground"
                                >
                                    Search
                                </Label>
                                <div className="relative">
                                    <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                                    <Input
                                        id="project-lead-search"
                                        value={query}
                                        onChange={(event) => setQuery(event.target.value)}
                                        placeholder="Business, phone, email, city…"
                                        className="pl-9"
                                    />
                                </div>
                            </div>
                            <div className="w-40 space-y-1.5">
                                <Label className="text-xs text-muted-foreground">Status</Label>
                                <Select
                                    value={status}
                                    onValueChange={(value) => {
                                        setStatus(value);
                                        setPage(1);
                                    }}
                                >
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value={ANY}>Any status</SelectItem>
                                        {LEAD_STATUSES.map((item) => (
                                            <SelectItem key={item} value={item}>
                                                {LEAD_STATUS_LABELS[item]}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="w-36 space-y-1.5">
                                <Label className="text-xs text-muted-foreground">Tag</Label>
                                <Input
                                    value={tag}
                                    onChange={(event) => {
                                        setTag(event.target.value);
                                        setPage(1);
                                    }}
                                    placeholder="e.g. hot"
                                />
                            </div>
                            <div className="w-40 space-y-1.5">
                                <Label className="text-xs text-muted-foreground">Website</Label>
                                <Select
                                    value={website}
                                    onValueChange={(value) => {
                                        setWebsite(value as WebsiteFilter);
                                        setPage(1);
                                    }}
                                >
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="any">Any website</SelectItem>
                                        <SelectItem value="has">Has website</SelectItem>
                                        <SelectItem value="no">No website</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="w-40 space-y-1.5">
                                <Label className="text-xs text-muted-foreground">Sort</Label>
                                <Select
                                    value={sort}
                                    onValueChange={(value) => {
                                        setSort(value as LeadSort);
                                        setPage(1);
                                    }}
                                >
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {Object.entries(LEAD_SORT_LABELS).map(([value, label]) => (
                                            <SelectItem key={value} value={value}>
                                                {label}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <label className="flex items-center gap-2 pb-2 text-sm">
                                <Checkbox
                                    checked={hasPhone}
                                    onCheckedChange={(checked) => {
                                        setHasPhone(checked === true);
                                        setPage(1);
                                    }}
                                />
                                Has phone
                            </label>
                            <Button type="submit" variant="secondary">
                                <Search className="h-4 w-4" />
                                Apply
                            </Button>
                            <Button
                                type="button"
                                variant="ghost"
                                onClick={() => {
                                    setQuery("");
                                    setAppliedQuery("");
                                    setStatus(ANY);
                                    setTag("");
                                    setWebsite("any");
                                    setHasPhone(false);
                                    setSort("created_at");
                                    setPage(1);
                                }}
                            >
                                Clear
                            </Button>
                        </form>
                    }
                >
                    {selectedIds.length > 0 && (
                        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-cta/40 bg-cta/10 p-3">
                            <span className="text-sm font-medium">
                                {selectedIds.length} selected
                            </span>
                            <Select value={bulkStatus} onValueChange={setBulkStatus}>
                                <SelectTrigger className="h-9 w-40">
                                    <SelectValue placeholder="Set status…" />
                                </SelectTrigger>
                                <SelectContent>
                                    {LEAD_STATUSES.map((item) => (
                                        <SelectItem key={item} value={item}>
                                            {LEAD_STATUS_LABELS[item]}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            <Button
                                size="sm"
                                variant="secondary"
                                disabled={!bulkStatus || bulkBusy}
                                onClick={() => void applyBulkStatus()}
                            >
                                {bulkBusy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                                Apply status
                            </Button>
                            <Button
                                size="sm"
                                variant="outline"
                                disabled={bulkBusy}
                                onClick={() => setConsentOpen(true)}
                            >
                                <ShieldCheck className="h-3.5 w-3.5" />
                                Email consent
                            </Button>
                            <Button
                                size="sm"
                                variant="outline"
                                onClick={() => setTagsOpen(true)}
                            >
                                <Tags className="h-3.5 w-3.5" />
                                Add tags
                            </Button>
                            <Select value={moveTargetId} onValueChange={setMoveTargetId}>
                                <SelectTrigger className="h-9 w-48">
                                    <SelectValue placeholder="Move to project…" />
                                </SelectTrigger>
                                <SelectContent>
                                    {otherProjects.length === 0 ? (
                                        <SelectItem value="none" disabled>
                                            Create another project first
                                        </SelectItem>
                                    ) : (
                                        otherProjects.map((item) => (
                                            <SelectItem
                                                key={item.id}
                                                value={String(item.id)}
                                            >
                                                {item.name}
                                            </SelectItem>
                                        ))
                                    )}
                                </SelectContent>
                            </Select>
                            <Button
                                size="sm"
                                variant="secondary"
                                disabled={!moveTargetId || moveBusy || moveTargetId === "none"}
                                onClick={() =>
                                    void moveLeads(Number(moveTargetId), selectedIds)
                                }
                            >
                                Move
                            </Button>
                            <Button size="sm" onClick={() => setDispatchOpen(true)}>
                                <Megaphone className="h-3.5 w-3.5" />
                                Send to campaign
                            </Button>
                            <Button
                                size="sm"
                                variant="outline"
                                onClick={() => void removeSelected()}
                                disabled={moveBusy}
                            >
                                <X className="h-3.5 w-3.5" />
                                Remove from project
                            </Button>
                            <Button
                                size="sm"
                                variant="ghost"
                                className="text-destructive hover:text-destructive"
                                onClick={() => void deleteSelected()}
                                disabled={moveBusy}
                            >
                                <Trash2 className="h-3.5 w-3.5" />
                                Delete
                            </Button>
                            <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => setSelectedIds([])}
                            >
                                Clear
                            </Button>
                        </div>
                    )}

                    {isLoading ? (
                        <div className="h-64 animate-pulse rounded-lg bg-muted" />
                    ) : (
                        <LeadTable
                            leads={leads}
                            selectable
                            selectedIds={selectedIds}
                            onToggle={toggleSelected}
                            onToggleAll={toggleAll}
                            onEdit={(lead) => {
                                setEditingLead(lead);
                                setEditOpen(true);
                            }}
                            dragPayload={(lead) =>
                                selectedIds.includes(lead.id)
                                    ? selectedIds
                                    : [lead.id]
                            }
                            emptyMessage={
                                project
                                    ? hasActiveFilters
                                        ? "No leads match these filters. Clear them to see the rest of the project."
                                        : "No leads in this project yet. Add them from the CRM or drag rows here from another project."
                                    : "Loading project…"
                            }
                        />
                    )}

                    {data && data.total_count > 0 && (
                        <div className="flex flex-wrap items-center justify-between gap-3 pt-2 text-sm text-muted-foreground">
                            <span>
                                Page {data.page} of {totalPages} · {data.total_count} lead(s)
                            </span>
                            <div className="flex flex-wrap items-center gap-2">
                                <span className="text-xs">Rows</span>
                                <Select
                                    value={String(pageSize)}
                                    onValueChange={(value) => {
                                        setPageSize(Number(value));
                                        setPage(1);
                                    }}
                                >
                                    <SelectTrigger className="h-8 w-[76px]">
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {PAGE_SIZE_OPTIONS.map((size) => (
                                            <SelectItem key={size} value={String(size)}>
                                                {size}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    disabled={data.page <= 1}
                                    onClick={() =>
                                        setPage((current) => Math.max(1, current - 1))
                                    }
                                >
                                    Previous
                                </Button>
                                <span className="text-xs">Page</span>
                                <Select
                                    value={String(data.page)}
                                    onValueChange={(value) => setPage(Number(value))}
                                >
                                    <SelectTrigger
                                        className="h-8 w-[76px]"
                                        aria-label="Select page"
                                    >
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {pageChoices(totalPages).map((item) => (
                                            <SelectItem key={item} value={String(item)}>
                                                {item}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                <span className="text-xs">of {totalPages}</span>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    disabled={data.page >= totalPages}
                                    onClick={() =>
                                        setPage((current) =>
                                            Math.min(totalPages, current + 1),
                                        )
                                    }
                                >
                                    Next
                                </Button>
                            </div>
                        </div>
                    )}
                </PageSection>

                <aside className="space-y-4">
                    <div className="rounded-xl border bg-card p-4">
                        <h2 className="flex items-center gap-2 text-sm font-semibold">
                            <FolderOpen className="h-4 w-4 text-cta" />
                            Move leads
                        </h2>
                        <p className="mt-1 text-xs text-muted-foreground">
                            Drag table rows here to file them in another project, or use the
                            selection bar for a whole batch. A lead lives in one project.
                        </p>
                        <div className="mt-3 space-y-2">
                            {otherProjects.length === 0 ? (
                                <Link
                                    href="/leads/projects"
                                    className="flex items-center gap-2 rounded-lg border border-dashed p-3 text-xs text-muted-foreground hover:border-cta/40 hover:text-cta"
                                >
                                    <FolderPlus className="h-4 w-4" />
                                    Create another project to move leads
                                </Link>
                            ) : (
                                otherProjects.map((item) => (
                                    <div
                                        key={item.id}
                                        onDragOver={(event) => {
                                            event.preventDefault();
                                            event.dataTransfer.dropEffect = "move";
                                            setDropTarget(item.id);
                                        }}
                                        onDragLeave={(event) => {
                                            if (
                                                !event.currentTarget.contains(
                                                    event.relatedTarget as Node,
                                                )
                                            ) {
                                                setDropTarget((current) =>
                                                    current === item.id ? null : current,
                                                );
                                            }
                                        }}
                                        onDrop={(event) => void handleDrop(item.id, event)}
                                        className={[
                                            "flex items-center justify-between gap-2 rounded-lg border p-3 text-sm transition",
                                            dropTarget === item.id
                                                ? "border-cta bg-cta/10 ring-2 ring-cta/40"
                                                : "border-dashed hover:border-cta/40",
                                        ].join(" ")}
                                    >
                                        <div className="min-w-0">
                                            <Link
                                                href={`/leads/projects/${item.id}`}
                                                className="block truncate font-medium hover:text-cta hover:underline"
                                            >
                                                {item.name}
                                            </Link>
                                            <span className="text-xs text-muted-foreground">
                                                {item.lead_count ?? 0} lead(s) · drop here
                                            </span>
                                        </div>
                                        <Phone className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                                    </div>
                                ))
                            )}
                        </div>
                    </div>

                    <div className="rounded-xl border bg-card p-4 text-sm">
                        <h2 className="text-sm font-semibold">Need more leads?</h2>
                        <p className="mt-1 text-xs text-muted-foreground">
                            Grab new businesses from the CRM, then add them to this project
                            without copying rows.
                        </p>
                        <Button className="mt-3 w-full" variant="outline" asChild>
                            <Link href="/leads/all">
                                <Plus className="h-4 w-4" />
                                Open CRM
                            </Link>
                        </Button>
                    </div>
                </aside>
            </div>

            <LeadEditDialog
                lead={editingLead}
                open={editOpen}
                onOpenChange={setEditOpen}
                onSaved={() => {
                    setEditingLead(null);
                    void refreshAll();
                }}
            />

            <DispatchLeadsDialog
                leadIds={selectedIds}
                open={dispatchOpen}
                onOpenChange={setDispatchOpen}
                onDispatched={() => setSelectedIds([])}
            />

            <EmailConsentDialog
                leadIds={selectedIds}
                open={consentOpen}
                onOpenChange={setConsentOpen}
                onSaved={() => refreshAll()}
            />

            <Dialog open={tagsOpen} onOpenChange={setTagsOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Add tags</DialogTitle>
                    </DialogHeader>
                    <form
                        className="space-y-3"
                        onSubmit={(event) => {
                            event.preventDefault();
                            void applyTags();
                        }}
                    >
                        <div className="space-y-1.5">
                            <Label htmlFor="project-tags">Tags</Label>
                            <Input
                                id="project-tags"
                                value={tagsDraft}
                                onChange={(event) => setTagsDraft(event.target.value)}
                                placeholder="menu-ready, priority"
                                autoFocus
                            />
                            <p className="text-xs text-muted-foreground">
                                Comma separated. Existing tags are kept, so this is additive.
                            </p>
                        </div>
                        <DialogFooter>
                            <Button
                                type="button"
                                variant="outline"
                                onClick={() => setTagsOpen(false)}
                            >
                                Cancel
                            </Button>
                            <Button
                                type="submit"
                                disabled={tagsBusy || !tagsDraft.trim()}
                            >
                                {tagsBusy && <Loader2 className="h-4 w-4 animate-spin" />}
                                Add to {selectedIds.length} lead(s)
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>

            <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Delete this project?</DialogTitle>
                        <DialogDescription>
                            {project
                                ? `“${project.name}” will be removed. Its ${project.lead_count ?? 0} lead(s) stay in the CRM and simply become unfiled.`
                                : ""}
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button
                            type="button"
                            variant="outline"
                            onClick={() => setDeleteOpen(false)}
                            disabled={deleteProjectBusy}
                        >
                            Cancel
                        </Button>
                        <Button
                            type="button"
                            variant="destructive"
                            onClick={() => void deleteProject()}
                            disabled={deleteProjectBusy}
                        >
                            {deleteProjectBusy && <Loader2 className="h-4 w-4 animate-spin" />}
                            Delete project
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </PageLayout>
    );
}
