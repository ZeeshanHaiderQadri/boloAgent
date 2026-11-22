"use client";

import {
    ArrowRight,
    Building2,
    FolderOpen,
    Loader2,
    Phone,
    Plus,
    Search,
    Trash2,
    Trophy,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    deleteLeadSearchApiV1LeadsSearchesSearchIdDelete,
    getLeadStatsApiV1LeadsStatsGet,
    listLeadsApiV1LeadsGet,
    listLeadSearchesApiV1LeadsSearchesGet,
} from "@/client/sdk.gen";
import type {
    LeadSearchListResponse,
    LeadStatsResponse,
} from "@/client/types.gen";
import type { LeadResponse } from "@/client/types.gen";
import { PageLayout, PageSection } from "@/components/layout/PageLayout";
import { ImportLeadsButton } from "@/components/leads/ImportLeadsButton";
import { LeadTable } from "@/components/leads/LeadTable";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { useOrganizationTimezone } from "@/hooks/useOrganizationTimezone";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { formatDateTime } from "@/lib/dateTime";
import { isLeadSearchActive, leadSearchStatusLabel } from "@/lib/leadStatus";

function searchStatusVariant(status: string) {
    if (status === "completed") return "success" as const;
    if (status === "failed") return "destructive" as const;
    if (status === "cancelled" || status === "stopped") return "outline" as const;
    return "secondary" as const;
}

export default function LeadsPage() {
    const { user, redirectToLogin, loading } = useAuth();
    const timezone = useOrganizationTimezone();
    const hasFetched = useRef(false);

    const [stats, setStats] = useState<LeadStatsResponse | null>(null);
    const [searches, setSearches] = useState<LeadSearchListResponse | null>(null);
    const [recentLeads, setRecentLeads] = useState<LeadResponse[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [loadError, setLoadError] = useState<string | null>(null);

    useEffect(() => {
        if (!loading && !user) redirectToLogin();
    }, [loading, user, redirectToLogin]);

    const fetchOverview = useCallback(async () => {
        setIsLoading(true);
        setLoadError(null);
        try {
            const [statsResponse, searchesResponse, leadsResponse] = await Promise.all([
                getLeadStatsApiV1LeadsStatsGet({}),
                listLeadSearchesApiV1LeadsSearchesGet({ query: { limit: 6 } }),
                listLeadsApiV1LeadsGet({ query: { limit: 5, sort: "created_at" } }),
            ]);

            if (statsResponse.error) {
                setLoadError(detailFromError(statsResponse.error, "Could not load lead statistics"));
                return;
            }
            setStats(statsResponse.data ?? null);
            setSearches(searchesResponse.data ?? null);
            setRecentLeads(leadsResponse.data?.leads ?? []);
        } catch (error) {
            console.error("Failed to load leads overview:", error);
            setLoadError("Could not load the leads workspace");
        } finally {
            setIsLoading(false);
        }
    }, []);

    const handleDeleteSearch = async (searchId: number, searchName: string) => {
        if (
            !window.confirm(
                `Delete the search “${searchName}”? Leads it found stay in your CRM.`,
            )
        ) {
            return;
        }
        const response = await deleteLeadSearchApiV1LeadsSearchesSearchIdDelete({
            path: { search_id: searchId },
        });
        if (response.error) {
            toast.error(detailFromError(response.error, "Could not delete the search"));
            return;
        }
        toast.success("Search deleted — leads kept in CRM");
        void fetchOverview();
    };

    useEffect(() => {
        if (loading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void fetchOverview();
    }, [loading, user, fetchOverview]);

    const hasAnyData = (stats?.total ?? 0) > 0 || (searches?.total_count ?? 0) > 0;
    const activeSearch = searches?.searches.find((search) => isLeadSearchActive(search.status));

    return (
        <PageLayout
            title="Leads"
            description="Find local businesses, keep them in a lightweight CRM, and send their phone numbers straight to an outbound voice agent."
            actions={
                <>
                    <Button variant="outline" asChild>
                        <Link href="/leads/all">
                            <Building2 className="h-4 w-4" />
                            CRM
                        </Link>
                    </Button>
                    <Button variant="outline" asChild>
                        <Link href="/leads/projects">
                            <FolderOpen className="h-4 w-4" />
                            Projects
                        </Link>
                    </Button>
                    <ImportLeadsButton onImported={() => void fetchOverview()} />
                    <Button asChild>
                        <Link href="/leads/new">
                            <Plus className="h-4 w-4" />
                            New search
                        </Link>
                    </Button>
                </>
            }
        >
            {loadError && (
                <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
                    {loadError}
                </div>
            )}

            {isLoading && !stats ? (
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                    {[...Array(4)].map((_, index) => (
                        <div key={index} className="h-28 animate-pulse rounded-xl bg-muted" />
                    ))}
                </div>
            ) : (
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                    <StatCard
                        icon={Building2}
                        label="Leads in CRM"
                        value={stats?.total ?? 0}
                        hint={`${stats?.searches ?? 0} searches run`}
                    />
                    <StatCard
                        icon={Phone}
                        label="Callable numbers"
                        value={stats?.with_phone ?? 0}
                        hint="Valid E.164 phone numbers"
                        accent
                    />
                    <StatCard
                        icon={Search}
                        label="New to contact"
                        value={stats?.new ?? 0}
                        hint="No outreach yet"
                    />
                    <StatCard
                        icon={Trophy}
                        label="Won"
                        value={stats?.won ?? 0}
                        hint={`${stats?.interested ?? 0} interested`}
                    />
                </div>
            )}

            {activeSearch && (
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-cta/30 bg-cta/10 px-4 py-3 text-sm">
                    <span className="flex items-center gap-2">
                        <Loader2 className="h-4 w-4 animate-spin text-cta" />
                        <span>
                            <span className="font-medium">{activeSearch.name}</span> is{" "}
                            {leadSearchStatusLabel(activeSearch.status).toLowerCase()} — results land
                            here automatically.
                        </span>
                    </span>
                    <Button size="sm" variant="outline" asChild>
                        <Link href={`/leads/searches/${activeSearch.id}`}>Watch progress</Link>
                    </Button>
                </div>
            )}

            {!isLoading && !hasAnyData ? (
                <Card>
                    <CardContent className="flex flex-col items-center gap-4 py-14 text-center">
                        <div className="rounded-full bg-cta/15 p-4">
                            <Search className="h-8 w-8 text-cta" />
                        </div>
                        <div className="space-y-1">
                            <h2 className="text-xl font-semibold">Build your first calling list</h2>
                            <p className="mx-auto max-w-xl text-sm text-muted-foreground">
                                Describe a category and a place — “Dentists in Austin, Texas” or
                                “Software companies in California” — and Bolo collects business names,
                                phone numbers, emails, and locations you can call with a voice agent.
                            </p>
                        </div>
                        <Button asChild>
                            <Link href="/leads/new">
                                <Plus className="h-4 w-4" />
                                Start a search
                            </Link>
                        </Button>
                    </CardContent>
                </Card>
            ) : (
                <>
                    <PageSection
                        title="Recent searches"
                        description="Each search enriches your shared lead database instead of duplicating it."
                        actions={
                            <Button variant="ghost" size="sm" asChild>
                                <Link href="/leads/new">
                                    <Plus className="h-4 w-4" />
                                    New
                                </Link>
                            </Button>
                        }
                    >
                        {isLoading ? (
                            <div className="h-40 animate-pulse rounded-lg bg-muted" />
                        ) : searches && searches.searches.length > 0 ? (
                            <div className="overflow-x-auto">
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Search</TableHead>
                                            <TableHead>Provider</TableHead>
                                            <TableHead>Status</TableHead>
                                            <TableHead>Results</TableHead>
                                            <TableHead>Created</TableHead>
                                            <TableHead className="text-right">Actions</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {searches.searches.map((search) => (
                                            <TableRow key={search.id}>
                                                <TableCell className="max-w-[320px]">
                                                    <div className="flex flex-col">
                                                        <Link
                                                            href={`/leads/searches/${search.id}`}
                                                            className="truncate font-medium hover:text-cta hover:underline"
                                                        >
                                                            {search.name}
                                                        </Link>
                                                        <span className="truncate text-xs text-muted-foreground">
                                                            {search.category} in {search.location}
                                                        </span>
                                                    </div>
                                                </TableCell>
                                                <TableCell className="text-sm text-muted-foreground">
                                                    {search.provider === "demo" ? "Demo data" : "Google Maps"}
                                                </TableCell>
                                                <TableCell>
                                                    <div className="flex items-center gap-2">
                                                        <Badge variant={searchStatusVariant(search.status)}>
                                                            {leadSearchStatusLabel(search.status)}
                                                        </Badge>
                                                        {isLeadSearchActive(search.status) && (
                                                            <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                                                        )}
                                                    </div>
                                                </TableCell>
                                                <TableCell className="text-sm">
                                                    {search.results_count}
                                                    {search.new_leads_count > 0 && (
                                                        <span className="ml-1 text-xs text-emerald-600 dark:text-emerald-400">
                                                            +{search.new_leads_count} new
                                                        </span>
                                                    )}
                                                </TableCell>
                                                <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                                                    {formatDateTime(search.created_at, timezone)}
                                                </TableCell>
                                                <TableCell className="text-right">
                                                    <div className="flex items-center justify-end gap-2">
                                                        <Button size="sm" variant="outline" asChild>
                                                            <Link href={`/leads/searches/${search.id}`}>
                                                                Open
                                                                <ArrowRight className="h-3.5 w-3.5" />
                                                            </Link>
                                                        </Button>
                                                        {!isLeadSearchActive(search.status) && (
                                                            <Button
                                                                size="icon"
                                                                variant="ghost"
                                                                aria-label={`Delete ${search.name}`}
                                                                title="Delete this search; its leads stay in the CRM"
                                                                onClick={() =>
                                                                    void handleDeleteSearch(
                                                                        search.id,
                                                                        search.name,
                                                                    )
                                                                }
                                                            >
                                                                <Trash2 className="h-4 w-4" />
                                                            </Button>
                                                        )}
                                                    </div>
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </div>
                        ) : (
                            <p className="py-6 text-center text-sm text-muted-foreground">
                                No searches yet. Start one to populate your CRM.
                            </p>
                        )}
                    </PageSection>

                    <PageSection
                        title="Latest leads"
                        description={`${stats?.new ?? 0} lead(s) have not been contacted yet.`}
                        actions={
                            <Button variant="ghost" size="sm" asChild>
                                <Link href="/leads/all">
                                    Open CRM
                                    <ArrowRight className="h-4 w-4" />
                                </Link>
                            </Button>
                        }
                    >
                        {isLoading ? (
                            <div className="h-40 animate-pulse rounded-lg bg-muted" />
                        ) : (
                            <LeadTable
                                leads={recentLeads}
                                emptyMessage="Leads will appear here after your first search finishes."
                            />
                        )}
                    </PageSection>
                </>
            )}
        </PageLayout>
    );
}

function StatCard({
    icon: Icon,
    label,
    value,
    hint,
    accent = false,
}: {
    icon: typeof Building2;
    label: string;
    value: number;
    hint: string;
    accent?: boolean;
}) {
    return (
        <Card className={accent ? "border-cta/40" : undefined}>
            <CardContent className="flex items-start justify-between gap-4 pt-6">
                <div>
                    <p className="text-sm text-muted-foreground">{label}</p>
                    <p className="mt-1 text-3xl font-semibold tabular-nums">{value}</p>
                    <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
                </div>
                <div className={accent ? "rounded-lg bg-cta/15 p-2" : "rounded-lg bg-muted p-2"}>
                    <Icon className={accent ? "h-5 w-5 text-cta" : "h-5 w-5 text-muted-foreground"} />
                </div>
            </CardContent>
        </Card>
    );
}
