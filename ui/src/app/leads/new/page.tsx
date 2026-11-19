"use client";

import {
    ArrowLeft,
    Info,
    Loader2,
    MapPin,
    PhoneCall,
    Search,
    Sparkles,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
    createLeadSearchApiV1LeadsSearchesPost,
    getLeadsMetaApiV1LeadsMetaGet,
    getWorkflowsSummaryApiV1WorkflowSummaryGet,
    listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet,
} from "@/client/sdk.gen";
import type {
    LeadMetaResponse,
    TelephonyConfigurationListItem,
    WorkflowSummaryResponse,
} from "@/client/types.gen";
import { PageShell } from "@/components/layout/PageShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
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
import { COUNTRIES } from "@/lib/countries";
import {
    LEAD_LANGUAGES,
    LEAD_MAX_TARGET_RESULTS,
    type LeadWebsiteFilter,
} from "@/lib/leadSearchOptions";

const PROVIDER_COPY: Record<string, { label: string; description: string }> = {
    google_maps: {
        label: "Google Maps",
        description: "Real businesses through the scraper service. Takes 3–10 minutes.",
    },
    demo: {
        label: "Demo data",
        description: "Deterministic sample businesses. Instant, no scraper needed.",
    },
};

type WebsiteFilter = LeadWebsiteFilter;

export default function NewLeadSearchPage() {
    const { user, redirectToLogin, loading } = useAuth();
    const router = useRouter();

    const [meta, setMeta] = useState<LeadMetaResponse | null>(null);
    const [category, setCategory] = useState("");
    const [location, setLocation] = useState("");
    const [countryCode, setCountryCode] = useState("US");
    const [language, setLanguage] = useState("en");
    const [provider, setProvider] = useState("");
    const [emailExtraction, setEmailExtraction] = useState(false);
    const [targetResults, setTargetResults] = useState("25");
    const [websiteFilter, setWebsiteFilter] = useState<WebsiteFilter>("any");
    const [autoCall, setAutoCall] = useState(false);
    const [workflowId, setWorkflowId] = useState("");
    const [telephonyId, setTelephonyId] = useState("auto");
    const [workflows, setWorkflows] = useState<WorkflowSummaryResponse[]>([]);
    const [telephonyConfigs, setTelephonyConfigs] = useState<TelephonyConfigurationListItem[]>([]);
    const [optionsLoading, setOptionsLoading] = useState(false);
    const [depth, setDepth] = useState("3");
    const [radius, setRadius] = useState("10");
    const [zoom, setZoom] = useState("12");
    const [maxTime, setMaxTime] = useState("600");
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!loading && !user) redirectToLogin();
    }, [loading, user, redirectToLogin]);

    useEffect(() => {
        if (loading || !user) return;
        let cancelled = false;
        getLeadsMetaApiV1LeadsMetaGet({})
            .then((response) => {
                if (cancelled) return;
                if (response.data) {
                    setMeta(response.data);
                    setProvider(response.data.default_provider);
                }
            })
            .catch(() => {
                if (!cancelled) toast.error("Could not load lead search options");
            });
        return () => {
            cancelled = true;
        };
    }, [loading, user]);

    useEffect(() => {
        if (loading || !user) return;
        let cancelled = false;
        setOptionsLoading(true);
        Promise.all([
            getWorkflowsSummaryApiV1WorkflowSummaryGet({
                query: { status: "active,archived" },
            }),
            listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet({}),
        ])
            .then(([workflowResponse, telephonyResponse]) => {
                if (cancelled) return;
                setWorkflows(workflowResponse.data ?? []);
                setTelephonyConfigs(
                    telephonyResponse.data?.configurations ?? [],
                );
            })
            .catch(() => {
                if (!cancelled) {
                    toast.error("Could not load voice agents and phone numbers");
                }
            })
            .finally(() => {
                if (!cancelled) setOptionsLoading(false);
            });
        return () => {
            cancelled = true;
        };
    }, [loading, user]);

    const handleSubmit = async () => {
        const target = Number(targetResults);
        if (!Number.isInteger(target) || target < 1 || target > LEAD_MAX_TARGET_RESULTS) {
            setError(`Enter how many leads to collect (1–${LEAD_MAX_TARGET_RESULTS}).`);
            return;
        }
        if (!category.trim() || !location.trim()) {
            setError("Describe what to look for and where.");
            return;
        }
        if (autoCall && !workflowId) {
            setError("Choose the voice agent that should call these leads.");
            return;
        }
        setSubmitting(true);
        setError(null);
        try {
            const response = await createLeadSearchApiV1LeadsSearchesPost({
                body: {
                    category: category.trim(),
                    location: location.trim(),
                    country_code: countryCode,
                    language,
                    provider,
                    depth: Number(depth),
                    radius_km: Number(radius),
                    zoom: Number(zoom),
                    email: emailExtraction,
                    max_time_seconds: Number(maxTime),
                    target_results: target,
                    website_filter: websiteFilter,
                    auto_call: autoCall,
                    workflow_id: autoCall ? Number(workflowId) : null,
                    telephony_configuration_id:
                        !autoCall || telephonyId === "auto"
                            ? null
                            : Number(telephonyId),
                },
            });

            if (response.error) {
                setError(detailFromError(response.error, "Could not start the search"));
                return;
            }
            if (response.data) {
                toast.success("Search queued");
                router.push(`/leads/searches/${response.data.id}`);
            }
        } catch (submitError) {
            console.error("Lead search failed to start:", submitError);
            setError("Could not start the search");
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <PageShell width="narrow">
            <div className="mb-8">
                <Button variant="ghost" size="sm" asChild className="mb-3 -ml-3">
                    <Link href="/leads">
                        <ArrowLeft className="h-4 w-4" />
                        Leads
                    </Link>
                </Button>
                <h1 className="mb-2 text-3xl font-bold">Find businesses to call</h1>
                <p className="text-muted-foreground">
                    Enter a category and a place. Bolo collects business names, phone numbers,
                    emails, websites, ratings, and addresses into your lead database.
                </p>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Search className="h-5 w-5 text-cta" />
                        What should we look for?
                    </CardTitle>
                    <CardDescription>
                        Example: “Dentists in Austin, Texas” or “Gyms in London, UK”.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-5">
                    {error && (
                        <p role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                            {error}
                        </p>
                    )}

                    <div className="space-y-2">
                        <Label htmlFor="lead-category">Category</Label>
                        <Input
                            id="lead-category"
                            placeholder="Software companies"
                            value={category}
                            maxLength={200}
                            onChange={(event) => setCategory(event.target.value)}
                        />
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="lead-location">Location</Label>
                        <div className="relative">
                            <MapPin className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                            <Input
                                id="lead-location"
                                placeholder="California, United States"
                                className="pl-9"
                                value={location}
                                maxLength={200}
                                onChange={(event) => setLocation(event.target.value)}
                            />
                        </div>
                    </div>

                    <div className="grid gap-4 sm:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="lead-target">How many leads?</Label>
                            <Input
                                id="lead-target"
                                type="number"
                                min={1}
                                max={LEAD_MAX_TARGET_RESULTS}
                                value={targetResults}
                                onChange={(event) => setTargetResults(event.target.value)}
                            />
                            <p className="text-xs text-muted-foreground">
                                Results start appearing immediately and the search
                                stops automatically at this number.
                            </p>
                        </div>
                        <div className="space-y-2">
                            <Label>Business website</Label>
                            <Select
                                value={websiteFilter}
                                onValueChange={(value) =>
                                    setWebsiteFilter(value as WebsiteFilter)
                                }
                            >
                                <SelectTrigger aria-label="Filter by business website">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="any">Any business</SelectItem>
                                    <SelectItem value="has_website">Has a website</SelectItem>
                                    <SelectItem value="no_website">No website</SelectItem>
                                </SelectContent>
                            </Select>
                            <p className="text-xs text-muted-foreground">
                                Only matching businesses are saved. “No website” is
                                handy for web-design and SEO outreach.
                            </p>
                        </div>
                    </div>

                    <div className="grid gap-4 sm:grid-cols-3">
                        <div className="space-y-2">
                            <Label>Country</Label>
                            <Select value={countryCode} onValueChange={setCountryCode}>
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {COUNTRIES.map((country) => (
                                        <SelectItem key={country.code} value={country.code}>
                                            {country.label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label>Language</Label>
                            <Select value={language} onValueChange={setLanguage}>
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {LEAD_LANGUAGES.map((item) => (
                                        <SelectItem key={item.code} value={item.code}>
                                            {item.label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label>Data source</Label>
                            <Select value={provider} onValueChange={setProvider}>
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {(meta?.providers ?? ["demo"]).map((name) => (
                                        <SelectItem key={name} value={name}>
                                            {PROVIDER_COPY[name]?.label ?? name}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                    </div>

                    <p className="text-xs text-muted-foreground">
                        {PROVIDER_COPY[provider]?.description ??
                            "The selected provider must be reachable from the API."}
                    </p>

                    <label className="flex items-start gap-2 rounded-lg border p-3 text-sm">
                        <Checkbox
                            checked={emailExtraction}
                            onCheckedChange={(checked) => setEmailExtraction(checked === true)}
                        />
                        <span>
                            Also find email addresses
                            <span className="block text-xs text-muted-foreground">
                                Visits each business website. Slower; Google Maps provider only.
                            </span>
                        </span>
                    </label>

                    <div className="space-y-3 rounded-lg border p-3">
                        <label className="flex items-start gap-2 text-sm">
                            <Checkbox
                                checked={autoCall}
                                onCheckedChange={(checked) => setAutoCall(checked === true)}
                            />
                            <span>
                                <span className="flex items-center gap-1.5 font-medium">
                                    <PhoneCall className="h-4 w-4 text-cta" />
                                    Call these leads automatically
                                </span>
                                <span className="block text-xs text-muted-foreground">
                                    As soon as the search finishes, every lead with a phone
                                    number is queued in a normal outbound campaign. Call
                                    outcomes and conversation notes appear on each lead.
                                </span>
                            </span>
                        </label>

                        {autoCall && (
                            <div className="grid gap-4 pt-1 sm:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>Voice agent</Label>
                                    <Select value={workflowId} onValueChange={setWorkflowId}>
                                        <SelectTrigger>
                                            <SelectValue
                                                placeholder={
                                                    optionsLoading
                                                        ? "Loading…"
                                                        : "Choose an agent"
                                                }
                                            />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {workflows.map((workflow) => (
                                                <SelectItem
                                                    key={workflow.id}
                                                    value={String(workflow.id)}
                                                >
                                                    {workflow.name}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-2">
                                    <Label>Caller ID / phone number</Label>
                                    <Select value={telephonyId} onValueChange={setTelephonyId}>
                                        <SelectTrigger>
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="auto">
                                                Choose automatically
                                            </SelectItem>
                                            {telephonyConfigs
                                                .filter((config) => !config.inactive)
                                                .map((config) => (
                                                    <SelectItem
                                                        key={config.id}
                                                        value={String(config.id)}
                                                    >
                                                        {config.name} · {config.provider}
                                                    </SelectItem>
                                                ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                {!optionsLoading && workflows.length === 0 && (
                                    <p className="text-xs text-destructive sm:col-span-2">
                                        No voice agents found. Create one first, or
                                        leave this off and send the leads to a
                                        campaign later.
                                    </p>
                                )}
                            </div>
                        )}
                    </div>

                    <Collapsible open={showAdvanced} onOpenChange={setShowAdvanced}>
                        <CollapsibleTrigger asChild>
                            <Button type="button" variant="ghost" size="sm" className="-ml-2">
                                <Sparkles className="h-4 w-4" />
                                Search tuning
                            </Button>
                        </CollapsibleTrigger>
                        <CollapsibleContent className="pt-3">
                            <div className="grid gap-4 sm:grid-cols-2">
                                <div className="space-y-2">
                                    <Label htmlFor="lead-depth">Depth (result pages)</Label>
                                    <Input
                                        id="lead-depth"
                                        type="number"
                                        min={1}
                                        max={20}
                                        value={depth}
                                        onChange={(event) => setDepth(event.target.value)}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="lead-radius">Radius (km)</Label>
                                    <Input
                                        id="lead-radius"
                                        type="number"
                                        min={1}
                                        max={100}
                                        value={radius}
                                        onChange={(event) => setRadius(event.target.value)}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="lead-zoom">Map zoom (3–21)</Label>
                                    <Input
                                        id="lead-zoom"
                                        type="number"
                                        min={3}
                                        max={21}
                                        value={zoom}
                                        onChange={(event) => setZoom(event.target.value)}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="lead-max-time">Max runtime (seconds)</Label>
                                    <Input
                                        id="lead-max-time"
                                        type="number"
                                        min={60}
                                        max={900}
                                        step={30}
                                        value={maxTime}
                                        onChange={(event) => setMaxTime(event.target.value)}
                                    />
                                </div>
                            </div>
                        </CollapsibleContent>
                    </Collapsible>

                    <div className="flex items-start gap-2 rounded-lg bg-muted/60 p-3 text-xs text-muted-foreground">
                        <Info className="mt-0.5 h-4 w-4 shrink-0" />
                        <span>
                            Searches run in the background and results stream into the
                            dashboard as they are found; you can leave this page. The
                            search stops by itself when it reaches your lead target or
                            the maximum runtime. Repeating a search refreshes business
                            details while keeping CRM status, tags, and notes. Make sure
                            your outreach follows local telemarketing rules (for example
                            TCPA or GDPR).
                        </span>
                    </div>

                    <div className="flex justify-end gap-2">
                        <Button variant="outline" asChild disabled={submitting}>
                            <Link href="/leads">Cancel</Link>
                        </Button>
                        <Button onClick={() => void handleSubmit()} disabled={submitting}>
                            {submitting ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <Search className="h-4 w-4" />
                            )}
                            Start search
                        </Button>
                    </div>
                </CardContent>
            </Card>
        </PageShell>
    );
}
