"use client";

import { Loader2, Megaphone, Pencil, PhoneCall } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
    getCampaignsApiV1CampaignGet,
    getLeadsMetaApiV1LeadsMetaGet,
    getWorkflowsSummaryApiV1WorkflowSummaryGet,
    listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet,
    updateLeadSearchApiV1LeadsSearchesSearchIdPatch,
} from "@/client/sdk.gen";
import type {
    CampaignResponse,
    LeadMetaResponse,
    LeadSearchResponse,
    TelephonyConfigurationListItem,
    WorkflowSummaryResponse,
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
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { COUNTRIES } from "@/lib/countries";
import {
    LEAD_LANGUAGES,
    LEAD_MAX_TARGET_RESULTS,
    type LeadWebsiteFilter,
} from "@/lib/leadSearchOptions";

interface LeadSearchEditDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    search: LeadSearchResponse | null;
    onSaved?: (search: LeadSearchResponse) => void;
}

function numberValue(value: unknown, fallback: number): number {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
}

export function LeadSearchEditDialog({
    open,
    onOpenChange,
    search,
    onSaved,
}: LeadSearchEditDialogProps) {
    const { user } = useAuth();
    const [meta, setMeta] = useState<LeadMetaResponse | null>(null);
    const [workflows, setWorkflows] = useState<WorkflowSummaryResponse[]>([]);
    const [telephonyConfigs, setTelephonyConfigs] = useState<
        TelephonyConfigurationListItem[]
    >([]);
    const [campaigns, setCampaigns] = useState<CampaignResponse[]>([]);
    const [loadingOptions, setLoadingOptions] = useState(false);

    const [name, setName] = useState("");
    const [category, setCategory] = useState("");
    const [location, setLocation] = useState("");
    const [countryCode, setCountryCode] = useState("US");
    const [language, setLanguage] = useState("en");
    const [provider, setProvider] = useState("demo");
    const [targetResults, setTargetResults] = useState("25");
    const [websiteFilter, setWebsiteFilter] = useState<LeadWebsiteFilter>("any");
    const [emailExtraction, setEmailExtraction] = useState(false);
    const [depth, setDepth] = useState("3");
    const [radius, setRadius] = useState("10");
    const [zoom, setZoom] = useState("12");
    const [maxTime, setMaxTime] = useState("600");
    const [autoCall, setAutoCall] = useState(false);
    const [workflowId, setWorkflowId] = useState("");
    const [telephonyId, setTelephonyId] = useState("auto");
    const [addToCampaign, setAddToCampaign] = useState(false);
    const [campaignId, setCampaignId] = useState("");
    const [keepExisting, setKeepExisting] = useState(true);

    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Load the saved search into the form every time the dialog opens.
    useEffect(() => {
        if (!open || !search) return;
        const params = search.params ?? {};
        setName(search.name ?? "");
        setCategory(search.category ?? "");
        setLocation(search.location ?? "");
        setCountryCode(search.country_code ?? "US");
        setLanguage(search.language ?? "en");
        setProvider(search.provider ?? "demo");
        setTargetResults(String(search.target_results ?? 25));
        setWebsiteFilter(
            (params.website_filter as LeadWebsiteFilter | undefined) ?? "any",
        );
        setEmailExtraction(Boolean(params.email));
        setDepth(String(numberValue(params.depth, 3)));
        setRadius(String(numberValue(params.radius_km, 10)));
        setZoom(String(numberValue(params.zoom, 12)));
        setMaxTime(String(numberValue(params.max_time_seconds, 600)));
        setAutoCall(Boolean(params.auto_call));
        setWorkflowId(
            params.workflow_id ? String(params.workflow_id) : "",
        );
        setTelephonyId(
            params.telephony_configuration_id
                ? String(params.telephony_configuration_id)
                : "auto",
        );
        const savedCampaign = Number(params.add_to_campaign_id);
        setAddToCampaign(Boolean(savedCampaign));
        setCampaignId(savedCampaign ? String(savedCampaign) : "");
        setKeepExisting(true);
        setError(null);
    }, [open, search]);

    useEffect(() => {
        if (!open || !user) return;
        let cancelled = false;
        setLoadingOptions(true);
        Promise.all([
            getLeadsMetaApiV1LeadsMetaGet({}),
            getWorkflowsSummaryApiV1WorkflowSummaryGet({
                query: { status: "active,archived" },
            }),
            listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet({}),
            getCampaignsApiV1CampaignGet({}),
        ])
            .then(
                ([
                    metaResponse,
                    workflowResponse,
                    telephonyResponse,
                    campaignResponse,
                ]) => {
                    if (cancelled) return;
                    if (metaResponse.data) setMeta(metaResponse.data);
                    setWorkflows(workflowResponse.data ?? []);
                    setTelephonyConfigs(
                        telephonyResponse.data?.configurations ?? [],
                    );
                    setCampaigns(
                        (campaignResponse.data?.campaigns ?? []).filter(
                            (campaign) =>
                                campaign.source_type === "csv" &&
                                !["created", "syncing"].includes(campaign.state),
                        ),
                    );
                },
            )
            .catch(() => {
                if (!cancelled) {
                    toast.error("Could not load voice agents and phone numbers");
                }
            })
            .finally(() => {
                if (!cancelled) setLoadingOptions(false);
            });
        return () => {
            cancelled = true;
        };
    }, [open, user]);

    const handleSubmit = async () => {
        if (!search) return;
        const target = Number(targetResults);
        if (
            !Number.isInteger(target) ||
            target < 1 ||
            target > LEAD_MAX_TARGET_RESULTS
        ) {
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
        if (addToCampaign && !campaignId) {
            setError("Choose the campaign that should receive these leads.");
            return;
        }
        if (keepExisting && target <= search.results_count) {
            setError(
                `This search already has ${search.results_count} leads. Raise the target above that, or start the search fresh.`,
            );
            return;
        }
        setSubmitting(true);
        setError(null);
        try {
            const response = await updateLeadSearchApiV1LeadsSearchesSearchIdPatch({
                path: { search_id: search.id },
                body: {
                    name: name.trim() || undefined,
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
                    keep_existing_results: keepExisting,
                    add_to_campaign_id: addToCampaign
                        ? Number(campaignId)
                        : null,
                },
            });
            if (response.error) {
                setError(detailFromError(response.error, "Could not update the search"));
                return;
            }
            if (!response.data) {
                setError("The search was updated without a response. Refresh the page.");
                return;
            }
            const campaign = campaigns.find(
                (item) => String(item.id) === campaignId,
            );
            toast.success(
                addToCampaign && campaign
                    ? `Search updated and queued · leads will be added to “${campaign.name}”`
                    : "Search updated and queued again",
            );
            onSaved?.(response.data);
            onOpenChange(false);
        } catch (submitError) {
            console.error("Lead search update failed:", submitError);
            setError("Could not update the search");
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <Pencil className="h-5 w-5 text-cta" />
                        Edit this search
                    </DialogTitle>
                    <DialogDescription>
                        Fix the query, target, or filters and run it again. The
                        scraper searches from the beginning; existing leads stay
                        attached unless you start fresh.
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-4">
                    {error && (
                        <p
                            role="alert"
                            className="rounded-md bg-destructive/10 p-3 text-sm text-destructive"
                        >
                            {error}
                        </p>
                    )}

                    <div className="grid gap-4 sm:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="edit-lead-category">Category</Label>
                            <Input
                                id="edit-lead-category"
                                value={category}
                                maxLength={200}
                                onChange={(event) => setCategory(event.target.value)}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="edit-lead-location">Location</Label>
                            <Input
                                id="edit-lead-location"
                                value={location}
                                maxLength={200}
                                onChange={(event) => setLocation(event.target.value)}
                            />
                        </div>
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="edit-lead-name">Search name</Label>
                        <Input
                            id="edit-lead-name"
                            value={name}
                            maxLength={255}
                            placeholder={`${category || "Category"} in ${location || "Location"}`}
                            onChange={(event) => setName(event.target.value)}
                        />
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
                                    {Array.from(
                                        new Set([
                                            ...(meta?.providers ?? []),
                                            search?.provider ?? "demo",
                                        ]),
                                    ).map((name) => (
                                        <SelectItem key={name} value={name}>
                                            {name === "google_maps"
                                                ? "Google Maps"
                                                : name === "demo"
                                                  ? "Demo data"
                                                  : name}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                    </div>

                    <div className="grid gap-4 sm:grid-cols-3">
                        <div className="space-y-2">
                            <Label htmlFor="edit-lead-target">How many leads?</Label>
                            <Input
                                id="edit-lead-target"
                                type="number"
                                min={1}
                                max={LEAD_MAX_TARGET_RESULTS}
                                value={targetResults}
                                onChange={(event) => setTargetResults(event.target.value)}
                            />
                            <p className="text-xs text-muted-foreground">
                                {search && search.results_count > 0
                                    ? `${search.results_count} collected so far; the target must be higher to find more.`
                                    : "The search stops automatically at this number."}
                            </p>
                        </div>
                        <div className="space-y-2">
                            <Label>Business website</Label>
                            <Select
                                value={websiteFilter}
                                onValueChange={(value) =>
                                    setWebsiteFilter(value as LeadWebsiteFilter)
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
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="edit-lead-depth">Depth (pages)</Label>
                            <Input
                                id="edit-lead-depth"
                                type="number"
                                min={1}
                                max={20}
                                value={depth}
                                onChange={(event) => setDepth(event.target.value)}
                            />
                        </div>
                    </div>

                    <div className="grid gap-4 sm:grid-cols-3">
                        <div className="space-y-2">
                            <Label htmlFor="edit-lead-radius">Radius (km)</Label>
                            <Input
                                id="edit-lead-radius"
                                type="number"
                                min={1}
                                max={100}
                                value={radius}
                                onChange={(event) => setRadius(event.target.value)}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="edit-lead-zoom">Map zoom (3–21)</Label>
                            <Input
                                id="edit-lead-zoom"
                                type="number"
                                min={3}
                                max={21}
                                value={zoom}
                                onChange={(event) => setZoom(event.target.value)}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="edit-lead-max-time">Max runtime (s)</Label>
                            <Input
                                id="edit-lead-max-time"
                                type="number"
                                min={60}
                                max={900}
                                step={30}
                                value={maxTime}
                                onChange={(event) => setMaxTime(event.target.value)}
                            />
                        </div>
                    </div>

                    <label className="flex items-start gap-2 text-sm">
                        <Checkbox
                            checked={emailExtraction}
                            onCheckedChange={(checked) =>
                                setEmailExtraction(checked === true)
                            }
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
                                    When the search finishes, every lead with a phone
                                    number is queued in an outbound campaign.
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
                                                    loadingOptions
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
                            </div>
                        )}
                    </div>

                    <div className="space-y-3 rounded-lg border p-3">
                        <label className="flex items-start gap-2 text-sm">
                            <Checkbox
                                checked={addToCampaign}
                                onCheckedChange={(checked) =>
                                    setAddToCampaign(checked === true)
                                }
                            />
                            <span>
                                <span className="flex items-center gap-1.5 font-medium">
                                    <Megaphone className="h-4 w-4 text-cta" />
                                    Add these leads to an existing campaign
                                </span>
                                <span className="block text-xs text-muted-foreground">
                                    Every lead this search collects is appended to
                                    the chosen outbound campaign. Leads already in
                                    that campaign are skipped.
                                </span>
                            </span>
                        </label>
                        {addToCampaign && (
                            <div className="space-y-2 pt-1">
                                <Label>Campaign</Label>
                                <Select
                                    value={campaignId}
                                    onValueChange={setCampaignId}
                                >
                                    <SelectTrigger aria-label="Campaign to receive these leads">
                                        <SelectValue
                                            placeholder={
                                                loadingOptions
                                                    ? "Loading…"
                                                    : campaigns.length
                                                      ? "Choose a campaign"
                                                      : "No eligible campaigns"
                                            }
                                        />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {campaigns.map((campaign) => (
                                            <SelectItem
                                                key={campaign.id}
                                                value={String(campaign.id)}
                                            >
                                                {campaign.name} · {campaign.state}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                {campaigns.length === 0 && (
                                    <p className="text-xs text-muted-foreground">
                                        Create or start an outbound campaign
                                        first. Campaigns still syncing cannot
                                        receive new leads.
                                    </p>
                                )}
                            </div>
                        )}
                    </div>

                    <label className="flex items-start gap-2 rounded-lg border p-3 text-sm">
                        <Checkbox
                            checked={keepExisting}
                            onCheckedChange={(checked) =>
                                setKeepExisting(checked === true)
                            }
                        />
                        <span>
                            Keep the {search?.results_count ?? 0} lead(s) already collected
                            <span className="block text-xs text-muted-foreground">
                                Uncheck to unlink them from this search when the query
                                changes. They stay in the CRM.
                            </span>
                        </span>
                    </label>
                </div>

                <DialogFooter>
                    <Button
                        variant="outline"
                        onClick={() => onOpenChange(false)}
                        disabled={submitting}
                    >
                        Cancel
                    </Button>
                    <Button onClick={() => void handleSubmit()} disabled={submitting}>
                        {submitting ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                            <Pencil className="h-4 w-4" />
                        )}
                        Save &amp; run again
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
