"use client";

import {
    BookmarkPlus,
    Filter,
    FolderPlus,
    Loader2,
    Mail,
    Megaphone,
    Pencil,
    RefreshCw,
    Search,
    ShieldCheck,
    Trash2,
    Users,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import {
    assignLeadsToProjectApiV1LeadProjectsProjectIdLeadsPost,
    bulkUpdateLeadsApiV1LeadsBulkPost,
    createLeadProjectApiV1LeadProjectsPost,
    getLeadFacetsApiV1LeadsFacetsGet,
    getLeadStatsApiV1LeadsStatsGet,
    listLeadProjectsApiV1LeadProjectsGet,
    listLeadsApiV1LeadsGet,
} from "@/client/sdk.gen";
import type {
    LeadFacetsResponse,
    LeadListResponse,
    LeadProjectResponse,
    LeadStatsResponse,
} from "@/client/types.gen";
import { AddToCampaignDialog } from "@/components/email/add-to-campaign-dialog";
import { PageLayout, PageSection } from "@/components/layout/PageLayout";
import { DispatchLeadsDialog } from "@/components/leads/DispatchLeadsDialog";
import { EmailConsentDialog } from "@/components/leads/EmailConsentDialog";
import { ExportLeadsButton } from "@/components/leads/ExportLeadsButton";
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
import { COUNTRY_BY_CODE } from "@/lib/countries";
import {
    LEAD_SORT_LABELS,
    LEAD_STATUS_LABELS,
    LEAD_STATUSES,
    type LeadStatus,
} from "@/lib/leadStatus";

type LeadSort = "created_at" | "rating" | "title" | "status";
type WebsiteFilter = "any" | "has" | "no";

const PAGE_SIZE_OPTIONS = [25, 50, 100];
const FILTER_STORAGE_KEY = "bolo.leads.filters.v1";
const VIEW_STORAGE_KEY = "bolo.leads.views.v1";
const ANY = "all";

interface SavedLeadFilters {
    query?: string;
    status?: string;
    tag?: string;
    category?: string;
    country?: string;
    state?: string;
    city?: string;
    hasPhone?: boolean;
    website?: WebsiteFilter;
    /** Legacy boolean from before website became a three-way filter. */
    hasWebsite?: boolean;
    hasEmail?: boolean;
    sort?: LeadSort;
    pageSize?: number;
}

interface SavedLeadView {
    id: string;
    name: string;
    filters: SavedLeadFilters;
    createdAt: string;
}

function loadSavedViews(): SavedLeadView[] {
    if (typeof window === "undefined") return [];
    try {
        const parsed = JSON.parse(
            window.localStorage.getItem(VIEW_STORAGE_KEY) ?? "[]",
        );
        if (!Array.isArray(parsed)) return [];
        return parsed.filter(
            (view): view is SavedLeadView =>
                Boolean(view) &&
                typeof view.id === "string" &&
                typeof view.name === "string" &&
                Boolean(view.filters) &&
                typeof view.filters === "object",
        );
    } catch {
        return [];
    }
}

function persistSavedViews(views: SavedLeadView[]) {
    window.localStorage.setItem(VIEW_STORAGE_KEY, JSON.stringify(views));
}

function newViewId() {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
        return crypto.randomUUID();
    }
    return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function loadSavedFilters(): SavedLeadFilters {
    if (typeof window === "undefined") return {};
    try {
        const parsed = JSON.parse(
            window.localStorage.getItem(FILTER_STORAGE_KEY) ?? "{}",
        );
        if (!parsed || typeof parsed !== "object") return {};
        return parsed as SavedLeadFilters;
    } catch {
        return {};
    }
}

function initialStatus(value: string | undefined): string {
    if (value === ANY) return ANY;
    return value && LEAD_STATUSES.includes(value as LeadStatus) ? value : ANY;
}

function initialSort(value: string | undefined): LeadSort {
    return value && value in LEAD_SORT_LABELS ? (value as LeadSort) : "created_at";
}

function initialWebsite(saved: SavedLeadFilters): WebsiteFilter {
    if (saved.website === "has" || saved.website === "no") return saved.website;
    if (saved.website === "any") return "any";
    // The old checkbox only expressed "has website"; unchecked meant no filter.
    return saved.hasWebsite ? "has" : "any";
}

function pageChoices(totalPages: number): number[] {
    return Array.from({ length: Math.max(1, totalPages) }, (_, index) => index + 1);
}

export default function AllLeadsPage() {
    const { user, redirectToLogin, loading } = useAuth();
    const [savedFilters] = useState(loadSavedFilters);

    const [data, setData] = useState<LeadListResponse | null>(null);
    const [stats, setStats] = useState<LeadStatsResponse | null>(null);
    const [facets, setFacets] = useState<LeadFacetsResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [query, setQuery] = useState(savedFilters.query ?? "");
    const [appliedQuery, setAppliedQuery] = useState(savedFilters.query ?? "");
    const [status, setStatus] = useState(() => initialStatus(savedFilters.status));
    const [tag, setTag] = useState(savedFilters.tag ?? "");
    const [category, setCategory] = useState(savedFilters.category ?? "");
    const [country, setCountry] = useState(savedFilters.country ?? "");
    const [stateFilter, setStateFilter] = useState(savedFilters.state ?? "");
    const [city, setCity] = useState(savedFilters.city ?? "");
    const [hasPhone, setHasPhone] = useState(savedFilters.hasPhone ?? true);
    const [website, setWebsite] = useState<WebsiteFilter>(() =>
        initialWebsite(savedFilters),
    );
    const [hasEmail, setHasEmail] = useState(savedFilters.hasEmail ?? false);
    const [sort, setSort] = useState<LeadSort>(() => initialSort(savedFilters.sort));
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(() =>
        PAGE_SIZE_OPTIONS.includes(savedFilters.pageSize ?? 0)
            ? (savedFilters.pageSize as number)
            : 25,
    );

    const [selectedIds, setSelectedIds] = useState<number[]>([]);
    const [bulkStatus, setBulkStatus] = useState("");
    const [bulkBusy, setBulkBusy] = useState(false);
    const [dispatchOpen, setDispatchOpen] = useState(false);
    const [consentOpen, setConsentOpen] = useState(false);
    const [emailCampaignOpen, setEmailCampaignOpen] = useState(false);

    // Projects are saved lists of these same CRM rows (one project per lead).
    const [projects, setProjects] = useState<LeadProjectResponse[]>([]);
    const [projectDialogOpen, setProjectDialogOpen] = useState(false);
    const [projectName, setProjectName] = useState("");
    const [projectBusy, setProjectBusy] = useState(false);
    const [assignProjectId, setAssignProjectId] = useState("");
    const [assignBusy, setAssignBusy] = useState(false);

    // Saved views keep reusable slices of the CRM (e.g. "Austin coffee shops,
    // no website") so they can be applied again later or when picking leads
    // for a campaign.
    const [savedViews, setSavedViews] = useState<SavedLeadView[]>(loadSavedViews);
    const [activeViewId, setActiveViewId] = useState("");
    const [viewDialogOpen, setViewDialogOpen] = useState(false);
    const [viewName, setViewName] = useState("");
    const [editingViewId, setEditingViewId] = useState<string | null>(null);

    useEffect(() => {
        if (!loading && !user) redirectToLogin();
    }, [loading, user, redirectToLogin]);

    // Remember filter choices in this browser so returning operators land on
    // the same slice of the CRM.
    useEffect(() => {
        const payload: SavedLeadFilters = {
            query: appliedQuery,
            status,
            tag,
            category,
            country,
            state: stateFilter,
            city,
            hasPhone,
            website,
            hasEmail,
            sort,
            pageSize,
        };
        window.localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(payload));
    }, [
        appliedQuery,
        status,
        tag,
        category,
        country,
        stateFilter,
        city,
        hasPhone,
        website,
        hasEmail,
        sort,
        pageSize,
    ]);

    useEffect(() => {
        if (loading || !user) return;
        let cancelled = false;
        getLeadFacetsApiV1LeadsFacetsGet({
            query: {
                country: country || null,
                state: stateFilter || null,
            },
        })
            .then((response) => {
                if (!cancelled && response.data) setFacets(response.data);
            })
            .catch((facetError) => {
                console.error("Failed to load lead filter values:", facetError);
            });
        return () => {
            cancelled = true;
        };
    }, [loading, user, country, stateFilter]);

    const fetchProjects = useCallback(async () => {
        try {
            const response = await listLeadProjectsApiV1LeadProjectsGet({});
            if (response.data) setProjects(response.data.projects ?? []);
        } catch (projectError) {
            console.error("Failed to load lead projects:", projectError);
        }
    }, []);

    useEffect(() => {
        if (loading || !user) return;
        void fetchProjects();
    }, [loading, user, fetchProjects]);

    const fetchLeads = useCallback(async () => {
        if (loading || !user) return;
        setIsLoading(true);
        setError(null);
        try {
            const [leadsResponse, statsResponse] = await Promise.all([
                listLeadsApiV1LeadsGet({
                    query: {
                        q: appliedQuery || null,
                        status: status === "all" ? null : (status as LeadStatus),
                        tag: tag.trim() || null,
                        category: category || null,
                        has_phone: hasPhone ? true : null,
                        has_website:
                            website === "any" ? null : website === "has",
                        has_email: hasEmail ? true : null,
                        country: country || null,
                        state: stateFilter || null,
                        city: city || null,
                        sort,
                        page,
                        limit: pageSize,
                    },
                }),
                getLeadStatsApiV1LeadsStatsGet({}),
            ]);

            if (leadsResponse.error) {
                setError(detailFromError(leadsResponse.error, "Could not load leads"));
                return;
            }
            setData(leadsResponse.data ?? null);
            setStats(statsResponse.data ?? null);
            setSelectedIds([]);
        } catch (fetchError) {
            console.error("Failed to load leads:", fetchError);
            setError("Could not load leads");
        } finally {
            setIsLoading(false);
        }
    }, [
        loading,
        user,
        appliedQuery,
        status,
        tag,
        category,
        hasPhone,
        website,
        hasEmail,
        country,
        stateFilter,
        city,
        sort,
        page,
        pageSize,
    ]);

    useEffect(() => {
        void fetchLeads();
    }, [fetchLeads]);

    const leads = data?.leads ?? [];
    const activeView = savedViews.find((view) => view.id === activeViewId) ?? null;

    const snapshotFilters = useCallback(
        (): SavedLeadFilters => ({
            query: appliedQuery,
            status,
            tag,
            category,
            country,
            state: stateFilter,
            city,
            hasPhone,
            website,
            hasEmail,
            sort,
            pageSize,
        }),
        [
            appliedQuery,
            status,
            tag,
            category,
            country,
            stateFilter,
            city,
            hasPhone,
            website,
            hasEmail,
            sort,
            pageSize,
        ],
    );

    const applySavedView = (view: SavedLeadView) => {
        const filters = view.filters;
        setQuery(filters.query ?? "");
        setAppliedQuery(filters.query ?? "");
        setStatus(initialStatus(filters.status));
        setTag(filters.tag ?? "");
        setCategory(filters.category ?? "");
        setCountry(filters.country ?? "");
        setStateFilter(filters.state ?? "");
        setCity(filters.city ?? "");
        setHasPhone(filters.hasPhone ?? false);
        setWebsite(initialWebsite(filters));
        setHasEmail(filters.hasEmail ?? false);
        setSort(initialSort(filters.sort));
        if (filters.pageSize && PAGE_SIZE_OPTIONS.includes(filters.pageSize)) {
            setPageSize(filters.pageSize);
        }
        setPage(1);
        setActiveViewId(view.id);
    };

    const openCreateView = () => {
        setEditingViewId(null);
        setViewName("");
        setViewDialogOpen(true);
    };

    const openEditView = () => {
        if (!activeView) return;
        setEditingViewId(activeView.id);
        setViewName(activeView.name);
        setViewDialogOpen(true);
    };

    const saveView = () => {
        const name = viewName.trim();
        if (!name) return;
        const filters = snapshotFilters();
        let next: SavedLeadView[];
        if (editingViewId) {
            next = savedViews.map((view) =>
                view.id === editingViewId ? { ...view, name, filters } : view,
            );
            setActiveViewId(editingViewId);
            toast.success(`Updated “${name}”`);
        } else {
            const view: SavedLeadView = {
                id: newViewId(),
                name,
                filters,
                createdAt: new Date().toISOString(),
            };
            next = [...savedViews, view];
            setActiveViewId(view.id);
            toast.success(`Saved “${name}”`);
        }
        setSavedViews(next);
        persistSavedViews(next);
        setViewDialogOpen(false);
    };

    const deleteActiveView = () => {
        if (!activeView) return;
        if (!window.confirm(`Delete the saved view “${activeView.name}”?`)) return;
        const next = savedViews.filter((view) => view.id !== activeView.id);
        setSavedViews(next);
        persistSavedViews(next);
        setActiveViewId("");
        toast.success(`Deleted “${activeView.name}”`);
    };

    const toggleSelected = (leadId: number) => {
        setSelectedIds((current) =>
            current.includes(leadId)
                ? current.filter((id) => id !== leadId)
                : [...current, leadId],
        );
    };

    const toggleAll = () => {
        setSelectedIds((current) =>
            leads.every((lead) => current.includes(lead.id)) ? [] : leads.map((lead) => lead.id),
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
        } catch (bulkError) {
            console.error("Bulk update failed:", bulkError);
            toast.error("Bulk update failed");
        } finally {
            setBulkBusy(false);
        }
    };

    const clearFilters = () => {
        setQuery("");
        setAppliedQuery("");
        setStatus(ANY);
        setTag("");
        setCategory("");
        setCountry("");
        setStateFilter("");
        setCity("");
        setHasPhone(false);
        setWebsite("any");
        setHasEmail(false);
        setSort("created_at");
        setPage(1);
        setActiveViewId("");
    };

    const exportFilters = {
        q: appliedQuery || null,
        status: status === "all" ? null : (status as LeadStatus),
        tag: tag.trim() || null,
        category: category || null,
        has_phone: hasPhone ? true : null,
        has_website: website === "any" ? null : website === "has",
        has_email: hasEmail ? true : null,
        country: country || null,
        state: stateFilter || null,
        city: city || null,
        sort,
    };

    const currentProjectFilters = {
        q: appliedQuery || null,
        status: status === ANY ? null : (status as LeadStatus),
        tag: tag.trim() || null,
        category: category || null,
        has_phone: hasPhone ? true : null,
        has_website: website === "any" ? null : website === "has",
        has_email: hasEmail ? true : null,
        country: country || null,
        state: stateFilter || null,
        city: city || null,
    };

    const saveFiltersAsProject = async () => {
        const name = projectName.trim();
        if (!name) return;
        setProjectBusy(true);
        try {
            const response = await createLeadProjectApiV1LeadProjectsPost({
                body: { name, filters: currentProjectFilters },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not save the project"));
                return;
            }
            toast.success(
                `Saved “${name}” with ${response.data?.lead_count ?? 0} lead(s)`,
            );
            setProjectDialogOpen(false);
            setProjectName("");
            await fetchProjects();
        } catch (projectError) {
            console.error("Save filtered project failed:", projectError);
            toast.error("Could not save the project");
        } finally {
            setProjectBusy(false);
        }
    };

    const assignSelectedToProject = async () => {
        if (!assignProjectId || selectedIds.length === 0) return;
        setAssignBusy(true);
        try {
            const response = await assignLeadsToProjectApiV1LeadProjectsProjectIdLeadsPost({
                path: { project_id: Number(assignProjectId) },
                body: { lead_ids: selectedIds },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not add the leads"));
                return;
            }
            const project = projects.find(
                (item) => item.id === Number(assignProjectId),
            );
            toast.success(
                `Added ${response.data?.assigned ?? selectedIds.length} lead(s) to “${
                    project?.name ?? "project"
                }”`,
            );
            setAssignProjectId("");
            await fetchProjects();
        } catch (projectError) {
            console.error("Add leads to project failed:", projectError);
            toast.error("Could not add the leads");
        } finally {
            setAssignBusy(false);
        }
    };

    const countryOptions = facets?.countries ?? [];
    const stateOptions = facets?.states ?? [];
    const cityOptions = facets?.cities ?? [];
    const categoryOptions = facets?.categories ?? [];
    const totalPages = Math.max(1, data?.total_pages ?? 0);

    return (
        <PageLayout
            title="CRM"
            description="Every business Bolo has collected, with its contact details and call history."
            actions={
                <>
                    <ExportLeadsButton filters={exportFilters} disabled={!data || data.total_count === 0} />
                    <Button variant="outline" onClick={() => void fetchLeads()} disabled={isLoading}>
                        <RefreshCw className={isLoading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
                        Refresh
                    </Button>
                    <Button variant="outline" asChild>
                        <Link href="/leads/projects">
                            <FolderPlus className="h-4 w-4" />
                            Projects
                        </Link>
                    </Button>
                    <Button asChild>
                        <Link href="/leads/new">
                            <Search className="h-4 w-4" />
                            New search
                        </Link>
                    </Button>
                </>
            }        >
            {error && (
                <p role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
                    {error}
                </p>
            )}

            <PageSection
                title={
                    <span className="flex items-center gap-2">
                        <Users className="h-5 w-5 text-cta" />
                        CRM
                        {stats && (
                            <span className="text-sm font-normal text-muted-foreground">
                                {stats.total} total · {stats.with_phone} callable
                            </span>
                        )}
                    </span>
                }
                description="Filter, tag, and move businesses through your outreach pipeline. Filters and saved views are remembered in this browser."
                toolbar={
                    <div className="space-y-3">
                        <div className="flex flex-wrap items-end gap-3">
                            <div className="w-60 space-y-1.5">
                                <Label className="text-xs text-muted-foreground">
                                    Saved views
                                </Label>
                                <Select
                                    value={activeViewId || ANY}
                                    onValueChange={(value) => {
                                        if (value === ANY) {
                                            setActiveViewId("");
                                            return;
                                        }
                                        const view = savedViews.find(
                                            (item) => item.id === value,
                                        );
                                        if (view) applySavedView(view);
                                    }}
                                >
                                    <SelectTrigger>
                                        <SelectValue placeholder="Pick a saved view" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value={ANY}>No saved view</SelectItem>
                                        {savedViews.map((view) => (
                                            <SelectItem key={view.id} value={view.id}>
                                                {view.name}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <Button type="button" variant="outline" onClick={openCreateView}>
                                <BookmarkPlus className="h-4 w-4" />
                                Save current filters
                            </Button>
                            <Button
                                type="button"
                                variant="outline"
                                onClick={() => {
                                    setProjectName("");
                                    setProjectDialogOpen(true);
                                }}
                            >
                                <FolderPlus className="h-4 w-4" />
                                Save as project
                            </Button>
                            {activeView && (
                                <>
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon"
                                        onClick={openEditView}
                                        title={`Update “${activeView.name}”`}
                                        aria-label={`Update ${activeView.name}`}
                                    >
                                        <Pencil className="h-4 w-4" />
                                    </Button>
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon"
                                        onClick={deleteActiveView}
                                        title={`Delete “${activeView.name}”`}
                                        aria-label={`Delete ${activeView.name}`}
                                    >
                                        <Trash2 className="h-4 w-4" />
                                    </Button>
                                </>
                            )}
                        </div>
                        <form
                            className="flex flex-wrap items-end gap-3"
                            onSubmit={(event) => {
                                event.preventDefault();
                                setPage(1);
                                setAppliedQuery(query.trim());
                            }}
                        >
                        <div className="min-w-[220px] flex-1 space-y-1.5">
                            <Label htmlFor="lead-search" className="text-xs text-muted-foreground">
                                Search
                            </Label>
                            <div className="relative">
                                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                                <Input
                                    id="lead-search"
                                    value={query}
                                    onChange={(event) => setQuery(event.target.value)}
                                    placeholder="Business, phone, email, city…"
                                    className="pl-9"
                                />
                            </div>
                        </div>
                        <div className="w-56 space-y-1.5">
                            <Label className="text-xs text-muted-foreground">Category</Label>
                            <Select
                                value={category || ANY}
                                onValueChange={(value) => {
                                    setCategory(value === ANY ? "" : value);
                                    setPage(1);
                                }}
                            >
                                <SelectTrigger>
                                    <SelectValue placeholder="All major categories" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value={ANY}>
                                        All major categories
                                    </SelectItem>
                                    {categoryOptions.map((item) => (
                                        <SelectItem key={item} value={item}>
                                            {item}
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
                        <div className="w-44 space-y-1.5">
                            <Label className="text-xs text-muted-foreground">Country</Label>
                            <Select
                                value={country || ANY}
                                onValueChange={(value) => {
                                    setCountry(value === ANY ? "" : value);
                                    setStateFilter("");
                                    setCity("");
                                    setPage(1);
                                }}
                            >
                                <SelectTrigger>
                                    <SelectValue placeholder="Any country" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value={ANY}>Any country</SelectItem>
                                    {countryOptions.map((item) => (
                                        <SelectItem key={item} value={item}>
                                            {COUNTRY_BY_CODE.get(item)?.label ?? item}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="w-40 space-y-1.5">
                            <Label className="text-xs text-muted-foreground">State</Label>
                            <Select
                                value={stateFilter || ANY}
                                onValueChange={(value) => {
                                    setStateFilter(value === ANY ? "" : value);
                                    setCity("");
                                    setPage(1);
                                }}
                                disabled={!country || stateOptions.length === 0}
                            >
                                <SelectTrigger>
                                    <SelectValue
                                        placeholder={
                                            country ? "Any state" : "Choose a country"
                                        }
                                    />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value={ANY}>Any state</SelectItem>
                                    {stateOptions.map((item) => (
                                        <SelectItem key={item} value={item}>
                                            {item}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="w-40 space-y-1.5">
                            <Label className="text-xs text-muted-foreground">City</Label>
                            <Select
                                value={city || ANY}
                                onValueChange={(value) => {
                                    setCity(value === ANY ? "" : value);
                                    setPage(1);
                                }}
                                disabled={!stateFilter || cityOptions.length === 0}
                            >
                                <SelectTrigger>
                                    <SelectValue
                                        placeholder={
                                            stateFilter ? "Any city" : "Choose a state"
                                        }
                                    />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value={ANY}>Any city</SelectItem>
                                    {cityOptions.map((item) => (
                                        <SelectItem key={item} value={item}>
                                            {item}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
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
                                    <SelectItem value="all">Any status</SelectItem>
                                    {LEAD_STATUSES.map((item) => (
                                        <SelectItem key={item} value={item}>
                                            {LEAD_STATUS_LABELS[item]}
                                        </SelectItem>
                                    ))}
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
                        <div className="flex flex-wrap items-center gap-4 pb-2 text-sm">
                            <label className="flex items-center gap-2">
                                <Checkbox
                                    checked={hasPhone}
                                    onCheckedChange={(checked) => {
                                        setHasPhone(checked === true);
                                        setPage(1);
                                    }}
                                />
                                Has phone
                            </label>
                            <label className="flex items-center gap-2">
                                <Checkbox
                                    checked={hasEmail}
                                    onCheckedChange={(checked) => {
                                        setHasEmail(checked === true);
                                        setPage(1);
                                    }}
                                />
                                Has email
                            </label>
                        </div>
                        <Button type="submit" variant="secondary">
                            <Filter className="h-4 w-4" />
                            Apply
                        </Button>
                        <Button type="button" variant="ghost" onClick={clearFilters}>
                            Clear
                        </Button>
                        </form>
                    </div>
                }
            >
                {selectedIds.length > 0 && (
                    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-cta/40 bg-cta/10 p-3">
                        <span className="text-sm font-medium">{selectedIds.length} selected</span>
                        <Select value={bulkStatus} onValueChange={setBulkStatus}>
                            <SelectTrigger className="h-9 w-44">
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
                        <Button size="sm" onClick={() => setDispatchOpen(true)}>
                            <Megaphone className="h-3.5 w-3.5" />
                            Send to campaign
                        </Button>
                        <Button
                            size="sm"
                            variant="outline"
                            disabled={bulkBusy}
                            onClick={() => setEmailCampaignOpen(true)}
                        >
                            <Mail className="h-3.5 w-3.5" />
                            Add to email campaign
                        </Button>
                        {projects.length > 0 && (
                            <>
                                <Select
                                    value={assignProjectId}
                                    onValueChange={setAssignProjectId}
                                >
                                    <SelectTrigger className="h-9 w-48">
                                        <SelectValue placeholder="Add to project…" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {projects.map((project) => (
                                            <SelectItem
                                                key={project.id}
                                                value={String(project.id)}
                                            >
                                                {project.name}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                <Button
                                    size="sm"
                                    variant="secondary"
                                    disabled={!assignProjectId || assignBusy}
                                    onClick={() => void assignSelectedToProject()}
                                >
                                    {assignBusy && (
                                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                    )}
                                    Add to project
                                </Button>
                            </>
                        )}
                        <Button size="sm" variant="ghost" onClick={() => setSelectedIds([])}>
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
                        emptyMessage="No leads match these filters."
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
                                onClick={() => setPage((current) => Math.max(1, current - 1))}
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
                                    setPage((current) => Math.min(totalPages, current + 1))
                                }
                            >
                                Next
                            </Button>
                        </div>
                    </div>
                )}
            </PageSection>

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
                onSaved={() => fetchLeads()}
            />

            <AddToCampaignDialog
                open={emailCampaignOpen}
                onOpenChange={setEmailCampaignOpen}
                audienceType="leads"
                leadIds={selectedIds}
                filterState={{
                    q: appliedQuery || undefined,
                    status: status !== "all" ? status : undefined,
                    tag: tag || undefined,
                    category: category || undefined,
                    country: country || undefined,
                    state: stateFilter || undefined,
                    city: city || undefined,
                    has_phone: hasPhone || undefined,
                    has_email: hasEmail || undefined,
                }}
                pageVisibleCount={selectedIds.length}
            />

            <Dialog open={viewDialogOpen} onOpenChange={setViewDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>
                            {editingViewId ? "Update saved view" : "Save current filters"}
                        </DialogTitle>
                        <DialogDescription>
                            {editingViewId
                                ? "Saving replaces this view's stored filters with the ones currently applied."
                                : "Name this filter set so you can reuse it later or when picking leads for a campaign."}
                        </DialogDescription>
                    </DialogHeader>
                    <form
                        className="space-y-2"
                        onSubmit={(event) => {
                            event.preventDefault();
                            saveView();
                        }}
                    >
                        <Label htmlFor="saved-view-name" className="text-xs text-muted-foreground">
                            View name
                        </Label>
                        <Input
                            id="saved-view-name"
                            value={viewName}
                            onChange={(event) => setViewName(event.target.value)}
                            placeholder="e.g. Austin coffee shops — no website"
                            autoFocus
                        />
                        <DialogFooter>
                            <Button
                                type="button"
                                variant="outline"
                                onClick={() => setViewDialogOpen(false)}
                            >
                                Cancel
                            </Button>
                            <Button type="submit" disabled={!viewName.trim()}>
                                {editingViewId ? "Update view" : "Save view"}
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>

            <Dialog open={projectDialogOpen} onOpenChange={setProjectDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Save filtered results as a project</DialogTitle>
                        <DialogDescription>
                            This files every lead matching the filters currently applied
                            ({data?.total_count ?? 0} lead(s), not just this page) into a
                            project you can export or send to a campaign. Leads already
                            filed under another project move here — they are never
                            duplicated.
                        </DialogDescription>
                    </DialogHeader>
                    <form
                        className="space-y-3"
                        onSubmit={(event) => {
                            event.preventDefault();
                            void saveFiltersAsProject();
                        }}
                    >
                        <div className="space-y-1.5">
                            <Label
                                htmlFor="crm-project-name"
                                className="text-xs text-muted-foreground"
                            >
                                Project name
                            </Label>
                            <Input
                                id="crm-project-name"
                                value={projectName}
                                onChange={(event) => setProjectName(event.target.value)}
                                placeholder="e.g. US coffee shops — no website"
                                autoFocus
                            />
                        </div>
                        <DialogFooter>
                            <Button
                                type="button"
                                variant="outline"
                                onClick={() => setProjectDialogOpen(false)}
                            >
                                Cancel
                            </Button>
                            <Button
                                type="submit"
                                disabled={projectBusy || !projectName.trim()}
                            >
                                {projectBusy && <Loader2 className="h-4 w-4 animate-spin" />}
                                Save project
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>
        </PageLayout>
    );
}
