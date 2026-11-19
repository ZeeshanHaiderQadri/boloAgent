"use client";

import {
    AlertTriangle,
    ChevronLeft,
    ChevronRight,
    Loader2,
    Plus,
    X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    listCampaignRecipientsApiV1EmailCampaignsCampaignUuidRecipientsGet,
    updateEmailCampaignApiV1EmailCampaignsCampaignUuidPatch,
} from "@/client";
import type {
    AudienceRuleRequest,
    CampaignFilters,
    CampaignRecipientsResponse,
    EmailCampaignResponse,
} from "@/client/types.gen";
import { AudiencePreviewPanel } from "@/components/email/wizard/AudiencePreviewPanel";
import {
    AlertDialog,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { emailConsentClass, emailConsentLabel } from "@/lib/emailConsent";

const BUCKET_OPTIONS = [
    { value: "all", label: "All buckets" },
    { value: "eligible", label: "Eligible" },
    { value: "manually_excluded", label: "Manually excluded" },
    { value: "opted_out", label: "Opted out" },
    { value: "unknown", label: "Unknown consent" },
    { value: "suppressed", label: "Suppressed" },
    { value: "invalid_email", label: "Invalid email" },
    { value: "no_email", label: "No email" },
];

interface Props {
    campaign: EmailCampaignResponse;
    onCampaignChange: (c: EmailCampaignResponse) => void;
    onBack: () => void;
    onNext: () => void;
    previewing?: boolean;
    previewError?: string | null;
    onRetryPreview?: () => void;
}

function audienceRuleForRequest(
    campaign: EmailCampaignResponse,
    excludedLeadIds: number[],
    manualEmails?: string[],
): AudienceRuleRequest {
    const aud = campaign.audience as Record<string, unknown>;
    const storedManual = (aud.manual_emails as string[] | undefined) ?? [];
    const rule: AudienceRuleRequest = {
        audience_type: campaign.audience_type as AudienceRuleRequest["audience_type"],
        lead_ids: (aud.lead_ids as number[] | undefined) ?? [],
        project_id: aud.project_id as number | undefined,
        tag: aud.tag as string | undefined,
        excluded_lead_ids: excludedLeadIds,
        manual_emails: manualEmails ?? storedManual,
        extra_lead_ids: (aud.extra_lead_ids as number[] | undefined) ?? [],
    };
    if (campaign.audience_type === "filter") {
        // Filter audiences store `all_matching` next to the filter fields, but
        // CampaignFilters forbids unknown keys, so strip it before sending.
        const filters = { ...((aud.filters as Record<string, unknown> | undefined) ?? {}) };
        delete filters.all_matching;
        rule.filters = filters as CampaignFilters;
        rule.all_matching = true;
    }
    return rule;
}

export function Step4Recipients({
    campaign,
    onCampaignChange,
    onBack,
    onNext,
    previewing = false,
    previewError,
    onRetryPreview,
}: Props) {
    const { getAccessToken } = useAuth();
    const [bucket, setBucket] = useState("all");
    const [page, setPage] = useState(1);
    const [data, setData] = useState<CampaignRecipientsResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [manualInput, setManualInput] = useState("");
    const [manualBusy, setManualBusy] = useState(false);
    // Selection spans pages; the bucket is kept so bulk include/exclude can
    // tell which selected leads are eligible versus already excluded.
    const [selected, setSelected] = useState<Map<number, string>>(new Map());
    const [bulkBusy, setBulkBusy] = useState(false);
    const [removeIds, setRemoveIds] = useState<number[] | null>(null);
    const [removing, setRemoving] = useState(false);
    const hasFetched = useRef(false);

    const manualEmails =
        ((campaign.audience as Record<string, unknown>).manual_emails as
            | string[]
            | undefined) ?? [];

    const limit = 20;

    const fetchPage = async (p: number, b: string) => {
        setLoading(true);
        setError(null);
        try {
            const token = await getAccessToken();
            const res =
                await listCampaignRecipientsApiV1EmailCampaignsCampaignUuidRecipientsGet({
                    headers: { Authorization: `Bearer ${token}` },
                    path: { campaign_uuid: campaign.uuid },
                    query: {
                        page: p,
                        limit,
                        bucket: b === "all" ? undefined : b,
                    },
                });
            if (res.error) {
                const detail = detailFromError(res.error, "Failed to load recipients");
                if ("status" in (res.error as object)) {
                    const status = (res.error as Record<string, unknown>).status;
                    if (status === 429) {
                        setError("Too many requests — wait a moment and try again.");
                        return;
                    }
                    if (status === 503) {
                        setError("Recipients list is temporarily unavailable. Try again shortly.");
                        return;
                    }
                }
                setError(detail);
                return;
            }
            setData(res.data!);
        } catch (cause) {
            setError(cause instanceof Error ? cause.message : "Failed to load recipients");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        if (hasFetched.current) return;
        hasFetched.current = true;
        void fetchPage(1, "all");
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const handleBucketChange = (b: string) => {
        setBucket(b);
        setPage(1);
        setSelected(new Map());
        hasFetched.current = false;
        void fetchPage(1, b);
    };

    const handlePageChange = (p: number) => {
        setPage(p);
        void fetchPage(p, bucket);
    };

    const pageLeadIds = (data?.items ?? []).flatMap((item) =>
        item.lead_id != null ? [item.lead_id] : [],
    );
    const allPageSelected =
        pageLeadIds.length > 0 && pageLeadIds.every((id) => selected.has(id));
    const somePageSelected = pageLeadIds.some((id) => selected.has(id));

    const toggleRow = (leadId: number, bucketName: string) => {
        setSelected((current) => {
            const next = new Map(current);
            if (next.has(leadId)) next.delete(leadId);
            else next.set(leadId, bucketName);
            return next;
        });
    };

    const toggleAllOnPage = () => {
        setSelected((current) => {
            const next = new Map(current);
            if (allPageSelected) {
                pageLeadIds.forEach((id) => next.delete(id));
            } else {
                (data?.items ?? []).forEach((item) => {
                    if (item.lead_id != null) next.set(item.lead_id, item.bucket);
                });
            }
            return next;
        });
    };

    /**
     * Add or remove explicit exclusions in one PATCH. Used by the inline
     * Include checkbox and by the bulk bar, so both paths persist the same
     * audience rule the API preview reads.
     */
    const applyExclusions = async (leadIds: number[], exclude: boolean) => {
        if (leadIds.length === 0) return false;
        setBulkBusy(true);
        setError(null);
        const aud = campaign.audience as Record<string, unknown>;
        const existing = (aud.excluded_lead_ids as number[] | undefined) ?? [];
        const next = new Set(existing);
        leadIds.forEach((id) => (exclude ? next.add(id) : next.delete(id)));
        try {
            const token = await getAccessToken();
            const res =
                await updateEmailCampaignApiV1EmailCampaignsCampaignUuidPatch({
                    headers: { Authorization: `Bearer ${token}` },
                    path: { campaign_uuid: campaign.uuid },
                    body: {
                        audience: audienceRuleForRequest(campaign, Array.from(next)),
                    },
                });
            if (res.error) {
                setError(detailFromError(res.error, "Failed to update exclusions"));
                return false;
            }
            if (res.data) {
                onCampaignChange(res.data);
                setSelected(new Map());
                await fetchPage(page, bucket);
            }
            return true;
        } catch (cause) {
            setError(
                cause instanceof Error ? cause.message : "Failed to update exclusions",
            );
            return false;
        } finally {
            setBulkBusy(false);
        }
    };

    const bulkApplyExclusions = (exclude: boolean) => {
        const ids = Array.from(selected.entries())
            .filter(([, bucketName]) =>
                exclude ? bucketName === "eligible" : bucketName === "manually_excluded",
            )
            .map(([id]) => id);
        void applyExclusions(ids, exclude);
    };

    const removeFromCampaign = async () => {
        if (!removeIds || removeIds.length === 0) return;
        setRemoving(true);
        const ok = await applyExclusions(removeIds, true);
        setRemoving(false);
        if (ok) {
            toast.success(
                `Removed ${removeIds.length} lead(s) from this campaign. They stay in your CRM.`,
            );
            setRemoveIds(null);
        }
    };

    const saveManualEmails = async (next: string[]) => {
        setManualBusy(true);
        setError(null);
        try {
            const aud = campaign.audience as Record<string, unknown>;
            const existing =
                (aud.excluded_lead_ids as number[] | undefined) ?? [];
            const token = await getAccessToken();
            const res =
                await updateEmailCampaignApiV1EmailCampaignsCampaignUuidPatch({
                    headers: { Authorization: `Bearer ${token}` },
                    path: { campaign_uuid: campaign.uuid },
                    body: {
                        audience: audienceRuleForRequest(campaign, existing, next),
                    },
                });
            if (res.error) {
                setError(detailFromError(res.error, "Failed to save manual addresses"));
                return false;
            }
            if (res.data) {
                onCampaignChange(res.data);
                setPage(1);
                hasFetched.current = false;
                await fetchPage(1, bucket);
            }
            return true;
        } catch (cause) {
            setError(
                cause instanceof Error
                    ? cause.message
                    : "Failed to save manual addresses",
            );
            return false;
        } finally {
            setManualBusy(false);
        }
    };

    const addManualEmails = async () => {
        const typed = manualInput
            .split(/[\s,;]+/)
            .map((value) => value.trim())
            .filter(Boolean);
        if (typed.length === 0) return;
        const merged = Array.from(new Set([...manualEmails, ...typed]));
        if (await saveManualEmails(merged)) {
            setManualInput("");
        }
    };

    const removeManualEmail = async (address: string) => {
        await saveManualEmails(manualEmails.filter((value) => value !== address));
    };

    const totalPages = data ? Math.ceil(data.total / limit) : 0;

    return (
        <div className="space-y-4">
            <div>
                <h2 className="text-base font-semibold">Step 4 — Recipients</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                    Review the resolved recipients list. This list is calculated live and
                    may change before launch. Uncheck eligible leads to exclude them, or
                    select rows to remove them from this campaign. Removing never deletes
                    the lead from your CRM.
                </p>
            </div>

            <AudiencePreviewPanel
                campaign={campaign}
                previewing={previewing}
                error={previewError}
                onRetry={onRetryPreview}
            />

            {/* Manual addresses: lets the operator send a test copy to their own
                inbox without creating a lead record. */}
            <div className="space-y-2 rounded-lg border bg-muted/20 p-3">
                <div>
                    <p className="text-sm font-medium">Add your own email addresses</p>
                    <p className="text-xs text-muted-foreground">
                        Added addresses receive this campaign in addition to the
                        audience above — handy for testing with your own inbox.
                        They skip the leads consent check but are still checked
                        against the suppression list.
                    </p>
                </div>
                <div className="flex flex-wrap gap-2">
                    <Input
                        value={manualInput}
                        onChange={(event) => setManualInput(event.target.value)}
                        placeholder="you@yourcompany.com, teammate@yourcompany.com"
                        className="h-9 flex-1 min-w-[240px]"
                        onKeyDown={(event) => {
                            if (event.key === "Enter") {
                                event.preventDefault();
                                void addManualEmails();
                            }
                        }}
                        disabled={manualBusy}
                    />
                    <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => void addManualEmails()}
                        disabled={manualBusy || !manualInput.trim()}
                    >
                        {manualBusy ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                            <Plus className="h-3.5 w-3.5" />
                        )}
                        Add
                    </Button>
                </div>
                {manualEmails.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                        {manualEmails.map((address) => (
                            <span
                                key={address}
                                className="inline-flex items-center gap-1 rounded-full border bg-background px-2 py-0.5 text-xs"
                            >
                                <span className="font-mono">{address}</span>
                                <button
                                    type="button"
                                    onClick={() => void removeManualEmail(address)}
                                    disabled={manualBusy}
                                    aria-label={`Remove ${address}`}
                                    className="rounded-full p-0.5 text-muted-foreground hover:text-destructive"
                                >
                                    <X className="h-3 w-3" />
                                </button>
                            </span>
                        ))}
                    </div>
                )}
            </div>

            {/* Bucket tabs */}
            <div className="flex flex-wrap gap-2">
                {BUCKET_OPTIONS.map((opt) => {
                    const count = opt.value === "all"
                        ? (data?.total ?? 0)
                        : (data?.totals_per_bucket?.[opt.value] ?? 0);
                    const isActive = bucket === opt.value;
                    return (
                        <button
                            key={opt.value}
                            type="button"
                            onClick={() => handleBucketChange(opt.value)}
                            className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
                                isActive
                                    ? "border-primary bg-primary/10 text-primary"
                                    : "border-border text-muted-foreground hover:border-muted-foreground"
                            }`}
                        >
                            {opt.label}
                            {count > 0 && (
                                <span className="rounded-full bg-muted px-1.5 text-xs tabular-nums">
                                    {count.toLocaleString()}
                                </span>
                            )}
                        </button>
                    );
                })}
            </div>

            {/* Why a campaign with a full list can still have zero eligible rows. */}
            {data &&
                (data.totals_per_bucket?.unknown ?? 0) > 0 &&
                (data.totals_per_bucket?.eligible ?? 0) === 0 &&
                campaign.consent_policy !== "include_unknown" && (
                    <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                        <p>
                            {data.totals_per_bucket.unknown} lead(s) have no recorded
                            consent. This campaign only emails opted-in leads, so none
                            are eligible. Go back to Step 3 to include unknown consent,
                            or record consent on the leads.
                        </p>
                    </div>
                )}

            {/* Bulk actions: act on the checked rows across pages. */}
            {selected.size > 0 && (
                <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/30 px-3 py-2 text-sm">
                    <span className="font-medium">
                        {selected.size} selected
                    </span>
                    <div className="ml-auto flex flex-wrap gap-2">
                        <Button
                            size="sm"
                            variant="outline"
                            disabled={
                                bulkBusy ||
                                !Array.from(selected.values()).includes("manually_excluded")
                            }
                            onClick={() => bulkApplyExclusions(false)}
                        >
                            Re-include
                        </Button>
                        <Button
                            size="sm"
                            variant="destructive"
                            disabled={bulkBusy || removing}
                            onClick={() => setRemoveIds(Array.from(selected.keys()))}
                        >
                            <X className="h-3.5 w-3.5" />
                            Remove from campaign
                        </Button>
                        <Button
                            size="sm"
                            variant="ghost"
                            disabled={bulkBusy || removing}
                            onClick={() => setSelected(new Map())}
                        >
                            Clear
                        </Button>
                    </div>
                </div>
            )}

            {loading && (
                <div
                    className="space-y-2"
                    role="status"
                    aria-label="Loading recipients"
                >
                    {[1, 2, 3, 4, 5].map((item) => (
                        <Skeleton key={item} className="h-10 w-full rounded-md" />
                    ))}
                </div>
            )}

            {error && (
                <div
                    role="alert"
                    className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-400"
                >
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <p>{error}</p>
                </div>
            )}

            {!loading && !error && data && (
                <>
                    {data.items.length === 0 ? (
                        <p className="py-6 text-center text-sm text-muted-foreground">
                            No recipients found in this bucket.
                        </p>
                    ) : (
                        <div className="overflow-x-auto rounded-lg border">
                            <table className="w-full text-sm">
                                <thead className="border-b bg-muted/40">
                                    <tr>
                                        <th className="w-10 px-3 py-2.5 text-left">
                                            <Checkbox
                                                checked={
                                                    allPageSelected
                                                        ? true
                                                        : somePageSelected
                                                          ? "indeterminate"
                                                          : false
                                                }
                                                onCheckedChange={toggleAllOnPage}
                                                aria-label="Select all leads on this page"
                                                disabled={pageLeadIds.length === 0}
                                            />
                                        </th>
                                        <th className="w-10 px-3 py-2.5 text-left">
                                            <span className="sr-only">Include</span>
                                        </th>
                                        <th className="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                                            Business
                                        </th>
                                        <th className="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                                            Email
                                        </th>
                                        <th className="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                                            Consent
                                        </th>
                                        <th className="px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                                            Status &amp; why
                                        </th>
                                        <th className="w-10 px-3 py-2.5">
                                            <span className="sr-only">Actions</span>
                                        </th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.items.map((item) => (
                                        <tr
                                            key={
                                                item.lead_id != null
                                                    ? `lead-${item.lead_id}`
                                                    : `manual-${item.email}`
                                            }
                                            className="border-b transition-colors last:border-0 hover:bg-muted/30"
                                        >
                                            <td className="px-3 py-2.5">
                                                {item.lead_id != null ? (
                                                    <Checkbox
                                                        checked={selected.has(item.lead_id)}
                                                        onCheckedChange={() =>
                                                            toggleRow(item.lead_id as number, item.bucket)
                                                        }
                                                        aria-label={`Select ${item.title}`}
                                                    />
                                                ) : null}
                                            </td>
                                            <td className="px-3 py-2.5">
                                                {item.lead_id != null &&
                                                item.bucket === "eligible" ? (
                                                    <Checkbox
                                                        checked={true}
                                                        disabled={bulkBusy}
                                                        onCheckedChange={() =>
                                                            void applyExclusions(
                                                                [item.lead_id as number],
                                                                true,
                                                            )
                                                        }
                                                        aria-label={`Exclude ${item.title}`}
                                                        className="data-[state=checked]:bg-emerald-600 data-[state=checked]:border-emerald-600"
                                                    />
                                                ) : item.lead_id != null &&
                                                  item.bucket === "manually_excluded" ? (
                                                    <Checkbox
                                                        checked={false}
                                                        disabled={bulkBusy}
                                                        onCheckedChange={() =>
                                                            void applyExclusions(
                                                                [item.lead_id as number],
                                                                false,
                                                            )
                                                        }
                                                        aria-label={`Re-include ${item.title}`}
                                                    />
                                                ) : null}
                                            </td>
                                            <td className="px-3 py-2.5">
                                                <span className="font-medium">
                                                    {item.title || "Manual address"}
                                                </span>
                                            </td>
                                            <td className="px-3 py-2.5">
                                                <span className="font-mono text-xs text-muted-foreground">
                                                    {item.email || "—"}
                                                </span>
                                            </td>
                                            <td className="px-3 py-2.5">
                                                <span
                                                    className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${emailConsentClass(item.email_consent)}`}
                                                >
                                                    {emailConsentLabel(item.email_consent)}
                                                </span>
                                            </td>
                                            <td className="max-w-[280px] px-3 py-2.5">
                                                <span
                                                    className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                                                        item.bucket === "eligible"
                                                            ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900 dark:text-emerald-300"
                                                            : item.bucket === "manually_excluded"
                                                              ? "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300"
                                                              : "bg-muted text-muted-foreground"
                                                    }`}
                                                >
                                                    {item.bucket === "manually_excluded"
                                                        ? "excluded"
                                                        : item.bucket}
                                                </span>
                                                <p className="mt-1 text-xs leading-snug text-muted-foreground">
                                                    {item.reason}
                                                </p>
                                            </td>
                                            <td className="px-3 py-2.5 text-right">
                                                {item.lead_id != null && (
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className="h-7 w-7 text-muted-foreground hover:text-destructive"
                                                        aria-label={`Remove ${item.title || item.email} from this campaign`}
                                                        disabled={removing || bulkBusy}
                                                        onClick={() =>
                                                            setRemoveIds([item.lead_id as number])
                                                        }
                                                    >
                                                        <X className="h-3.5 w-3.5" />
                                                    </Button>
                                                )}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}

                    {/* Pagination */}
                    {totalPages > 1 && (
                        <div className="flex items-center justify-between text-sm">
                            <span className="text-muted-foreground">
                                {data.total.toLocaleString()} result{data.total === 1 ? "" : "s"}
                            </span>
                            <div className="flex items-center gap-2">
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => handlePageChange(page - 1)}
                                    disabled={page <= 1}
                                >
                                    <ChevronLeft className="h-4 w-4" />
                                </Button>
                                <span className="tabular-nums">
                                    {page} / {totalPages}
                                </span>
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => handlePageChange(page + 1)}
                                    disabled={page >= totalPages}
                                >
                                    <ChevronRight className="h-4 w-4" />
                                </Button>
                            </div>
                        </div>
                    )}
                </>
            )}

            <div className="flex gap-3">
                <Button variant="outline" size="sm" onClick={onBack}>
                    Back
                </Button>
                <Button size="sm" onClick={onNext}>
                    Next
                </Button>
            </div>

            <AlertDialog
                open={removeIds !== null}
                onOpenChange={(open) => {
                    if (!open && !removing) setRemoveIds(null);
                }}
            >
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Remove lead(s) from this campaign?</AlertDialogTitle>
                        <AlertDialogDescription>
                            Remove {removeIds?.length ?? 0} lead(s) from this campaign&apos;s
                            audience. The leads stay in your CRM, so you can use them in
                            another campaign later.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={removing}>Cancel</AlertDialogCancel>
                        <Button
                            variant="destructive"
                            disabled={removing}
                            onClick={() => void removeFromCampaign()}
                        >
                            {removing && <Loader2 className="h-4 w-4 animate-spin" />}
                            {removing ? "Removing..." : "Remove"}
                        </Button>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
