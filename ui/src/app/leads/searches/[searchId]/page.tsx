"use client";

import {
    ArrowLeft,
    ListPlus,
    Loader2,
    Megaphone,
    Pencil,
    PhoneCall,
    Plus,
    RefreshCw,
    Search,
    Square,
    Timer,
    XCircle,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    cancelLeadSearchApiV1LeadsSearchesSearchIdCancelPost,
    getLeadSearchApiV1LeadsSearchesSearchIdGet,
    getLeadStatsApiV1LeadsStatsGet,
    listLeadsApiV1LeadsGet,
    retryLeadSearchApiV1LeadsSearchesSearchIdRetryPost,
    stopLeadSearchApiV1LeadsSearchesSearchIdStopPost,
} from "@/client/sdk.gen";
import type {
    LeadListResponse,
    LeadSearchResponse,
    LeadStatsResponse,
} from "@/client/types.gen";
import { PageShell } from "@/components/layout/PageShell";
import { DispatchLeadsDialog } from "@/components/leads/DispatchLeadsDialog";
import { ExportLeadsButton } from "@/components/leads/ExportLeadsButton";
import { ExtendLeadSearchDialog } from "@/components/leads/ExtendLeadSearchDialog";
import { LeadSearchEditDialog } from "@/components/leads/LeadSearchEditDialog";
import { LeadTable } from "@/components/leads/LeadTable";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
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
    isLeadSearchActive,
    LEAD_SORT_LABELS,
    LEAD_STATUS_LABELS,
    LEAD_STATUSES,
    leadSearchStatusLabel,
    type LeadStatus,
} from "@/lib/leadStatus";

const PAGE_SIZE = 50;

function statusVariant(status: string) {
    if (status === "completed") return "success" as const;
    if (status === "failed") return "destructive" as const;
    if (status === "cancelled" || status === "stopped") return "outline" as const;
    return "secondary" as const;
}

export default function LeadSearchPage() {
    const { user, redirectToLogin, loading } = useAuth();
    const params = useParams<{ searchId: string }>();
    const searchId = Number(params.searchId);
    const hasFetched = useRef(false);

    const [search, setSearch] = useState<LeadSearchResponse | null>(null);
    const [stats, setStats] = useState<LeadStatsResponse | null>(null);
    const [data, setData] = useState<LeadListResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [query, setQuery] = useState("");
    const [appliedQuery, setAppliedQuery] = useState("");
    const [status, setStatus] = useState("all");
    const [hasPhone, setHasPhone] = useState(true);
    const [sort, setSort] = useState<"created_at" | "rating" | "title" | "status">("created_at");
    const [page, setPage] = useState(1);

    const [selectedIds, setSelectedIds] = useState<number[]>([]);
    const [dispatchOpen, setDispatchOpen] = useState(false);
    const [editOpen, setEditOpen] = useState(false);
    const [extendOpen, setExtendOpen] = useState(false);
    const [actionBusy, setActionBusy] = useState(false);

    useEffect(() => {
        if (!loading && !user) redirectToLogin();
    }, [loading, user, redirectToLogin]);

    const fetchSearch = useCallback(async () => {
        const response = await getLeadSearchApiV1LeadsSearchesSearchIdGet({
            path: { search_id: searchId },
        });
        if (response.data) setSearch(response.data);
        return response;
    }, [searchId]);

    const fetchLeads = useCallback(async () => {
        if (loading || !user || !Number.isInteger(searchId)) return;
        const [leadsResponse, statsResponse] = await Promise.all([
            listLeadsApiV1LeadsGet({
                query: {
                    search_id: searchId,
                    q: appliedQuery || null,
                    status: status === "all" ? null : (status as LeadStatus),
                    has_phone: hasPhone ? true : null,
                    sort,
                    page,
                    limit: PAGE_SIZE,
                },
            }),
            getLeadStatsApiV1LeadsStatsGet({ query: { search_id: searchId } }),
        ]);
        if (leadsResponse.error) {
            setError(detailFromError(leadsResponse.error, "Could not load this search"));
            return;
        }
        setData(leadsResponse.data ?? null);
        setStats(statsResponse.data ?? null);
        setSelectedIds([]);
    }, [loading, user, searchId, appliedQuery, status, hasPhone, sort, page]);

    const loadAll = useCallback(
        async (showSkeleton: boolean) => {
            if (!Number.isInteger(searchId) || loading || !user) return;
            if (showSkeleton) setIsLoading(true);
            setError(null);
            try {
                const response = await fetchSearch();
                if (response.error) {
                    setError(detailFromError(response.error, "Search not found"));
                    return;
                }
                await fetchLeads();
            } catch (loadError) {
                console.error("Failed to load lead search:", loadError);
                setError("Could not load this search");
            } finally {
                if (showSkeleton) setIsLoading(false);
            }
        },
        [fetchLeads, fetchSearch, loading, searchId, user],
    );

    useEffect(() => {
        if (loading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void loadAll(true);
    }, [loading, user, loadAll]);

    useEffect(() => {
        void fetchLeads();
    }, [fetchLeads]);

    // Poll while the worker is queued/running so counts update without a refresh.
    useEffect(() => {
        if (!search || !isLeadSearchActive(search.status)) return;
        const timer = window.setInterval(() => {
            void loadAll(false);
        }, 5000);
        return () => window.clearInterval(timer);
    }, [search, loadAll]);

    const handleCancel = async () => {
        setActionBusy(true);
        try {
            const response = await cancelLeadSearchApiV1LeadsSearchesSearchIdCancelPost({
                path: { search_id: searchId },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not cancel the search"));
                return;
            }
            toast.success("Search cancelled");
            await loadAll(false);
        } finally {
            setActionBusy(false);
        }
    };

    const handleStop = async () => {
        setActionBusy(true);
        try {
            const response = await stopLeadSearchApiV1LeadsSearchesSearchIdStopPost({
                path: { search_id: searchId },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not stop the search"));
                return;
            }
            if (response.data) setSearch(response.data);
            toast.success("Stopping — leads found so far are being saved");
            await loadAll(false);
        } finally {
            setActionBusy(false);
        }
    };

    const handleRetry = async () => {
        setActionBusy(true);
        try {
            const response = await retryLeadSearchApiV1LeadsSearchesSearchIdRetryPost({
                path: { search_id: searchId },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not retry the search"));
                return;
            }
            setSearch(response.data ?? null);
            toast.success("Search queued again");
        } finally {
            setActionBusy(false);
        }
    };

    const leads = data?.leads ?? [];
    const active = isLeadSearchActive(search?.status);
    const stopping = search?.status === "stopping";
    const found = search?.results_count ?? 0;
    const target = search?.target_results ?? 0;
    const progress = target > 0 ? Math.min(100, Math.round((found / target) * 100)) : 0;
    const autoCallRequested = Boolean(search?.params?.auto_call);
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
    const exportFilters = {
        search_id: searchId,
        q: appliedQuery || null,
        status: status === "all" ? null : (status as LeadStatus),
        has_phone: hasPhone ? true : null,
        sort,
    };

    return (
        <PageShell>
            <div className="mb-6">
                <Button variant="ghost" size="sm" asChild className="mb-3 -ml-3">
                    <Link href="/leads">
                        <ArrowLeft className="h-4 w-4" />
                        Leads
                    </Link>
                </Button>
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="min-w-0 flex-1">
                        <div className="mb-2 flex flex-wrap items-center gap-2">
                            <h1 className="text-2xl font-bold">
                                {search?.name ?? "Lead search"}
                            </h1>
                            {search && (
                                <Badge variant={statusVariant(search.status)}>
                                    {leadSearchStatusLabel(search.status)}
                                </Badge>
                            )}
                            {active && <Loader2 className="h-4 w-4 animate-spin text-cta" />}
                        </div>
                        <p className="text-sm text-muted-foreground">
                            {search
                                ? `${search.category} in ${search.location} · ${search.provider === "demo" ? "demo data" : "Google Maps"}`
                                : "Loading search details…"}
                        </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        <ExportLeadsButton
                            filters={exportFilters}
                            disabled={!data || data.total_count === 0}
                        />
                        {active && (
                            <>
                                <Button
                                    onClick={() => void handleStop()}
                                    disabled={actionBusy || stopping}
                                    title="Stop now and keep the leads collected so far"
                                >
                                    {stopping ? (
                                        <Loader2 className="h-4 w-4 animate-spin" />
                                    ) : (
                                        <Square className="h-4 w-4" />
                                    )}
                                    Stop &amp; keep leads
                                </Button>
                                <Button
                                    variant="outline"
                                    onClick={() => void handleCancel()}
                                    disabled={actionBusy || stopping}
                                    title="Abandon this search"
                                >
                                    <XCircle className="h-4 w-4" />
                                    Cancel
                                </Button>
                            </>
                        )}
                        {search && (search.status === "failed" || search.status === "cancelled" || search.status === "stopped") && (
                            <Button variant="outline" onClick={() => void handleRetry()} disabled={actionBusy}>
                                <RefreshCw className="h-4 w-4" />
                                Retry
                            </Button>
                        )}
                        {search && !active && (
                            <>
                                <Button
                                    variant="outline"
                                    onClick={() => setEditOpen(true)}
                                    title="Change the query, target, or filters and run the search again"
                                >
                                    <Pencil className="h-4 w-4" />
                                    Edit
                                </Button>
                                <Button
                                    variant="outline"
                                    onClick={() => setExtendOpen(true)}
                                    title="Collect more leads with the same query"
                                >
                                    <ListPlus className="h-4 w-4" />
                                    Extend
                                </Button>
                            </>
                        )}
                        <Button asChild>
                            <Link href="/leads/new">
                                <Plus className="h-4 w-4" />
                                New search
                            </Link>
                        </Button>
                    </div>
                </div>
            </div>

            {error && (
                <p role="alert" className="mb-4 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
                    {error}
                </p>
            )}

            {search?.error_message && (
                <p role="alert" className="mb-4 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
                    <span className="font-medium">Search failed: </span>
                    {search.error_message}
                </p>
            )}

            {search?.auto_call_campaign_id && (
                <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4 text-sm">
                    <span className="flex items-center gap-2">
                        <PhoneCall className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
                        <span>
                            <span className="font-medium">Outbound calling started.</span> The
                            collected leads were queued in a campaign automatically.
                        </span>
                    </span>
                    <Button size="sm" variant="outline" asChild>
                        <Link href={`/campaigns/${search.auto_call_campaign_id}`}>
                            Open campaign
                        </Link>
                    </Button>
                </div>
            )}

            {search?.auto_call_error && (
                <div className="mb-4 rounded-xl border border-amber-500/40 bg-amber-500/10 p-4 text-sm">
                    <p className="font-medium">Automatic calling could not start</p>
                    <p className="mt-1 text-muted-foreground">{search.auto_call_error}</p>
                    <p className="mt-1 text-muted-foreground">
                        The leads are saved. Set up an outbound caller ID in Telephony, then
                        select the leads and choose “Send to campaign”.
                    </p>
                </div>
            )}

            {!active && target > 0 && found < target && (
                <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-muted/50 p-4 text-sm">
                    <span>
                        <span className="font-medium">
                            Only {found} of {target} lead(s) found.
                        </span>{" "}
                        Extend the same search, or edit the category/location and
                        run it again.
                    </span>
                    <div className="flex flex-wrap gap-2">
                        <Button size="sm" variant="outline" onClick={() => setExtendOpen(true)}>
                            <ListPlus className="h-3.5 w-3.5" />
                            Extend
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => setEditOpen(true)}>
                            <Pencil className="h-3.5 w-3.5" />
                            Edit &amp; retry
                        </Button>
                    </div>
                </div>
            )}

            {active && (
                <div className="mb-6 space-y-3 rounded-xl border border-cta/30 bg-cta/10 p-4 text-sm">
                    <div className="flex items-start gap-3">
                        <Timer className="mt-0.5 h-5 w-5 shrink-0 text-cta" />
                        <div className="min-w-0 flex-1">
                            <p className="font-medium">
                                {stopping ? "Stopping the search…" : "Collecting businesses…"}
                            </p>
                            <p className="text-muted-foreground">
                                {target > 0
                                    ? `${found} of ${target} requested lead(s) found. Results appear as they are saved — the search stops automatically at the target.`
                                    : "Results appear here as they are found; you can safely navigate away."}
                            </p>
                            {autoCallRequested && !search?.auto_call_campaign_id && (
                                <p className="mt-1 text-muted-foreground">
                                    An outbound campaign will start automatically when the search
                                    finishes.
                                </p>
                            )}
                        </div>
                        <span className="text-base font-semibold tabular-nums">
                            {found}
                            {target > 0 ? ` / ${target}` : ""}
                        </span>
                    </div>
                    {target > 0 && (
                        <div className="h-2 overflow-hidden rounded-full bg-cta/20">
                            <div
                                className="h-full rounded-full bg-cta transition-[width] duration-500"
                                style={{ width: `${progress}%` }}
                            />
                        </div>
                    )}
                </div>
            )}

            <div className="mb-6 grid gap-4 sm:grid-cols-3">
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium text-muted-foreground">
                            Leads found
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-2xl font-semibold tabular-nums">
                            {stats?.total ?? search?.results_count ?? 0}
                            {target > 0 && (
                                <span className="ml-1 text-sm font-normal text-muted-foreground">
                                    / {target}
                                </span>
                            )}
                        </p>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium text-muted-foreground">
                            Callable numbers
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-2xl font-semibold tabular-nums">{stats?.with_phone ?? 0}</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium text-muted-foreground">
                            New this search
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-2xl font-semibold tabular-nums">
                            {search?.new_leads_count ?? 0}
                        </p>
                    </CardContent>
                </Card>
            </div>

            <Card>
                <CardHeader className="flex flex-wrap items-center justify-between gap-4 space-y-0">
                    <CardTitle className="flex items-center gap-2">
                        <Search className="h-5 w-5 text-cta" />
                        Results
                    </CardTitle>
                    <form
                        className="flex flex-wrap items-end gap-3"
                        onSubmit={(event) => {
                            event.preventDefault();
                            setPage(1);
                            setAppliedQuery(query.trim());
                        }}
                    >
                        <div className="min-w-[200px] space-y-1.5">
                            <Label className="text-xs text-muted-foreground">Search results</Label>
                            <Input
                                value={query}
                                onChange={(event) => setQuery(event.target.value)}
                                placeholder="Business, phone, email…"
                            />
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
                                    setSort(value as typeof sort);
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
                            Apply
                        </Button>
                    </form>
                </CardHeader>
                <CardContent className="space-y-4">
                    {selectedIds.length > 0 && (
                        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-cta/40 bg-cta/10 p-3">
                            <span className="text-sm font-medium">{selectedIds.length} selected</span>
                            <Button size="sm" onClick={() => setDispatchOpen(true)}>
                                <Megaphone className="h-3.5 w-3.5" />
                                Send to campaign
                            </Button>
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
                            emptyMessage={
                                active
                                    ? "Leads appear here as soon as the scraper finds them."
                                    : "No leads match these filters."
                            }
                        />
                    )}

                    {data && data.total_pages > 1 && (
                        <div className="flex items-center justify-between text-sm text-muted-foreground">
                            <span>
                                Page {data.page} of {data.total_pages} · {data.total_count} lead(s)
                            </span>
                            <div className="flex gap-2">
                                <Button
                                    variant="outline"
                                    size="sm"
                                    disabled={data.page <= 1}
                                    onClick={() => setPage((current) => Math.max(1, current - 1))}
                                >
                                    Previous
                                </Button>
                                <Button
                                    variant="outline"
                                    size="sm"
                                    disabled={data.page >= data.total_pages}
                                    onClick={() => setPage((current) => current + 1)}
                                >
                                    Next
                                </Button>
                            </div>
                        </div>
                    )}
                </CardContent>
            </Card>

            <DispatchLeadsDialog
                leadIds={selectedIds}
                open={dispatchOpen}
                onOpenChange={setDispatchOpen}
                onDispatched={() => setSelectedIds([])}
            />

            <LeadSearchEditDialog
                open={editOpen}
                onOpenChange={setEditOpen}
                search={search}
                onSaved={(updated) => {
                    setSearch(updated);
                    setSelectedIds([]);
                    void loadAll(false);
                }}
            />

            <ExtendLeadSearchDialog
                open={extendOpen}
                onOpenChange={setExtendOpen}
                search={search}
                onExtended={(updated) => {
                    setSearch(updated);
                    setSelectedIds([]);
                    void loadAll(false);
                }}
            />
        </PageShell>
    );
}
