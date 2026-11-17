"use client";

import { ArrowRight, Loader2, Megaphone } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
    dispatchLeadsApiV1LeadsDispatchCampaignPost,
    getWorkflowsSummaryApiV1WorkflowSummaryGet,
    listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet,
} from "@/client/sdk.gen";
import type {
    LeadDispatchResponse,
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

interface DispatchLeadsDialogProps {
    leadIds: number[];
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onDispatched?: (result: LeadDispatchResponse) => void;
}

export function DispatchLeadsDialog({
    leadIds,
    open,
    onOpenChange,
    onDispatched,
}: DispatchLeadsDialogProps) {
    const { user } = useAuth();
    const [workflows, setWorkflows] = useState<WorkflowSummaryResponse[]>([]);
    const [telephonyConfigs, setTelephonyConfigs] = useState<TelephonyConfigurationListItem[]>([]);
    const [loadingOptions, setLoadingOptions] = useState(false);
    const [optionsError, setOptionsError] = useState<string | null>(null);

    const [name, setName] = useState("");
    const [workflowId, setWorkflowId] = useState("");
    const [telephonyId, setTelephonyId] = useState("auto");
    const [maxConcurrency, setMaxConcurrency] = useState("");
    const [rateLimit, setRateLimit] = useState("1");
    const [startImmediately, setStartImmediately] = useState(true);

    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<LeadDispatchResponse | null>(null);

    useEffect(() => {
        if (!open) return;
        setName((current) => current || `Leads outbound — ${new Date().toLocaleDateString()}`);
        setError(null);
        setResult(null);
    }, [open]);

    useEffect(() => {
        if (!open || !user) return;
        let cancelled = false;
        setLoadingOptions(true);
        setOptionsError(null);

        Promise.all([
            getWorkflowsSummaryApiV1WorkflowSummaryGet({ query: { status: "active,archived" } }),
            listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet({}),
        ])
            .then(([workflowResponse, telephonyResponse]) => {
                if (cancelled) return;
                if (workflowResponse.error) {
                    setOptionsError(detailFromError(workflowResponse.error, "Failed to load voice agents"));
                    return;
                }
                setWorkflows(workflowResponse.data ?? []);
                setTelephonyConfigs(telephonyResponse.data?.configurations ?? []);
            })
            .catch(() => {
                if (!cancelled) setOptionsError("Failed to load agents and phone numbers");
            })
            .finally(() => {
                if (!cancelled) setLoadingOptions(false);
            });

        return () => {
            cancelled = true;
        };
    }, [open, user]);

    const handleSubmit = async () => {
        if (!workflowId) {
            setError("Choose a voice agent for these leads.");
            return;
        }
        if (!name.trim()) {
            setError("Give the campaign a name.");
            return;
        }
        const concurrency = maxConcurrency.trim() ? Number(maxConcurrency) : null;
        if (concurrency != null && (!Number.isInteger(concurrency) || concurrency < 1)) {
            setError("Concurrency must be a whole number of at least 1.");
            return;
        }

        setSubmitting(true);
        setError(null);
        try {
            const response = await dispatchLeadsApiV1LeadsDispatchCampaignPost({
                body: {
                    lead_ids: leadIds,
                    name: name.trim(),
                    workflow_id: Number(workflowId),
                    telephony_configuration_id:
                        telephonyId === "auto" ? null : Number(telephonyId),
                    max_concurrency: concurrency,
                    rate_limit_per_second: Number(rateLimit),
                    start_immediately: startImmediately,
                },
            });

            if (response.error) {
                setError(detailFromError(response.error, "Could not create the campaign"));
                return;
            }
            if (!response.data) {
                setError("The campaign was created without a response. Check Campaigns.");
                return;
            }

            const skipped = response.data.skipped ?? [];
            const warnings = response.data.warnings ?? [];
            setResult({ ...response.data, skipped, warnings });
            toast.success(
                `${response.data.dispatched} lead(s) queued in “${response.data.campaign_name}”` +
                    (skipped.length ? ` · ${skipped.length} skipped` : ""),
            );
            for (const warning of warnings) {
                toast.warning(warning);
            }
            onDispatched?.(response.data);
        } catch (submitError) {
            console.error("Lead dispatch failed:", submitError);
            setError("Could not create the campaign");
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-lg">
                {result ? (
                    <>
                        <DialogHeader>
                            <DialogTitle className="flex items-center gap-2">
                                <Megaphone className="h-5 w-5 text-cta" />
                                Campaign created
                            </DialogTitle>
                            <DialogDescription>
                                {result.dispatched} lead(s) are queued in “{result.campaign_name}”.
                            </DialogDescription>
                        </DialogHeader>
                        <div className="space-y-3 text-sm">
                            <div className="rounded-lg border bg-muted/40 p-3">
                                <div className="flex items-center justify-between">
                                    <span className="text-muted-foreground">Campaign</span>
                                    <span className="font-medium">{result.campaign_name}</span>
                                </div>
                                <div className="mt-1 flex items-center justify-between">
                                    <span className="text-muted-foreground">Queued</span>
                                    <span className="font-medium">{result.dispatched}</span>
                                </div>
                                {(result.skipped ?? []).length > 0 && (
                                    <div className="mt-1 flex items-center justify-between">
                                        <span className="text-muted-foreground">Skipped</span>
                                        <span className="font-medium">{(result.skipped ?? []).length}</span>
                                    </div>
                                )}
                            </div>
                            {(result.skipped ?? []).length > 0 && (
                                <ul className="max-h-28 overflow-y-auto rounded-lg border p-2 text-xs text-muted-foreground">
                                    {(result.skipped ?? []).map((skip) => (
                                        <li key={skip.lead_id} className="flex justify-between py-0.5">
                                            <span>Lead #{skip.lead_id}</span>
                                            <span>{skip.reason.replace(/_/g, " ")}</span>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </div>
                        <DialogFooter>
                            <Button variant="outline" onClick={() => onOpenChange(false)}>
                                Close
                            </Button>
                            <Button asChild>
                                <a href={`/campaigns/${result.campaign_id}`}>
                                    Open campaign
                                    <ArrowRight className="h-4 w-4" />
                                </a>
                            </Button>
                        </DialogFooter>
                    </>
                ) : (
                    <>
                        <DialogHeader>
                            <DialogTitle className="flex items-center gap-2">
                                <Megaphone className="h-5 w-5 text-cta" />
                                Send {leadIds.length} lead(s) to an outbound campaign
                            </DialogTitle>
                            <DialogDescription>
                                A normal Bolo campaign is created from these phone numbers. You can
                                pause, retry, and monitor it under Campaigns.
                            </DialogDescription>
                        </DialogHeader>

                        <div className="space-y-4">
                            {optionsError && (
                                <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">
                                    {optionsError}
                                </p>
                            )}
                            {error && (
                                <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">
                                    {error}
                                </p>
                            )}

                            <div className="space-y-2">
                                <Label htmlFor="dispatch-name">Campaign name</Label>
                                <Input
                                    id="dispatch-name"
                                    value={name}
                                    maxLength={255}
                                    onChange={(event) => setName(event.target.value)}
                                />
                            </div>

                            <div className="grid gap-4 sm:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>Voice agent</Label>
                                    <Select value={workflowId} onValueChange={setWorkflowId}>
                                        <SelectTrigger>
                                            <SelectValue
                                                placeholder={loadingOptions ? "Loading…" : "Choose an agent"}
                                            />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {workflows.map((workflow) => (
                                                <SelectItem key={workflow.id} value={String(workflow.id)}>
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
                                            <SelectItem value="auto">Choose automatically</SelectItem>
                                            {telephonyConfigs
                                                .filter((config) => !config.inactive)
                                                .map((config) => (
                                                    <SelectItem key={config.id} value={String(config.id)}>
                                                        {config.name} · {config.provider}
                                                    </SelectItem>
                                                ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                            </div>

                            <div className="grid gap-4 sm:grid-cols-2">
                                <div className="space-y-2">
                                    <Label htmlFor="dispatch-concurrency">Max simultaneous calls</Label>
                                    <Input
                                        id="dispatch-concurrency"
                                        type="number"
                                        min={1}
                                        max={100}
                                        placeholder="Organization default"
                                        value={maxConcurrency}
                                        onChange={(event) => setMaxConcurrency(event.target.value)}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>Calls started per second</Label>
                                    <Select value={rateLimit} onValueChange={setRateLimit}>
                                        <SelectTrigger>
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {[1, 2, 3, 5, 10].map((rate) => (
                                                <SelectItem key={rate} value={String(rate)}>
                                                    {rate} / second
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                            </div>

                            <label className="flex items-start gap-2 text-sm">
                                <Checkbox
                                    checked={startImmediately}
                                    onCheckedChange={(checked) => setStartImmediately(checked === true)}
                                />
                                <span>
                                    Start calling immediately
                                    <span className="block text-xs text-muted-foreground">
                                        Uncheck to create the campaign in a paused state.
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
                            <Button onClick={() => void handleSubmit()} disabled={submitting || loadingOptions}>
                                {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
                                Create campaign
                            </Button>
                        </DialogFooter>
                    </>
                )}
            </DialogContent>
        </Dialog>
    );
}
