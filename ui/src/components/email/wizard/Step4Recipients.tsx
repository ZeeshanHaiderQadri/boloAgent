"use client";

import { AlertTriangle, ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
    listCampaignRecipientsApiV1EmailCampaignsCampaignUuidRecipientsGet,
    updateEmailCampaignApiV1EmailCampaignsCampaignUuidPatch,
} from "@/client";
import type { CampaignRecipientsResponse, EmailCampaignResponse } from "@/client/types.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
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
}

export function Step4Recipients({ campaign, onCampaignChange, onBack, onNext }: Props) {
    const { getAccessToken } = useAuth();
    const [bucket, setBucket] = useState("all");
    const [page, setPage] = useState(1);
    const [data, setData] = useState<CampaignRecipientsResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const hasFetched = useRef(false);

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
        void fetchPage(1, "");
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const handleBucketChange = (b: string) => {
        setBucket(b);
        setPage(1);
        hasFetched.current = false;
        void fetchPage(1, b);
    };

    const handlePageChange = (p: number) => {
        setPage(p);
        void fetchPage(p, bucket);
    };

    const handleExclude = async (leadId: number) => {
        // Toggle: add to excluded_lead_ids if not there, remove if already there
        const aud = campaign.audience as Record<string, unknown>;
        const existing = (aud.excluded_lead_ids as number[] | undefined) ?? [];
        const alreadyExcluded = existing.includes(leadId);
        const newExcluded = alreadyExcluded
            ? existing.filter((id) => id !== leadId)
            : [...existing, leadId];
        try {
            const token = await getAccessToken();
            const res =
                await updateEmailCampaignApiV1EmailCampaignsCampaignUuidPatch({
                    headers: { Authorization: `Bearer ${token}` },
                    path: { campaign_uuid: campaign.uuid },
                    body: {
                        audience: {
                            audience_type: campaign.audience_type as "leads" | "project" | "tag" | "filter",
                            lead_ids: (aud.lead_ids as number[] | undefined) ?? [],
                            project_id: aud.project_id as number | undefined,
                            tag: aud.tag as string | undefined,
                            excluded_lead_ids: newExcluded,
                        },
                    },
                });
            if (!res.error && res.data) {
                onCampaignChange(res.data);
                // Refresh the table
                void fetchPage(page, bucket);
            }
        } catch {
            // ignore
        }
    };

    const totalPages = data ? Math.ceil(data.total / limit) : 0;

    return (
        <div className="space-y-4">
            <div>
                <h2 className="text-base font-semibold">Step 4 — Recipients</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                    Review the resolved recipients list. This list is calculated live and
                    may change before launch. Uncheck eligible leads to manually exclude
                    them.
                </p>
            </div>

            <div className="flex items-center gap-3">
                <Select value={bucket} onValueChange={handleBucketChange}>
                    <SelectTrigger className="w-48">
                        <SelectValue placeholder="All buckets" />
                    </SelectTrigger>
                    <SelectContent>
                        {BUCKET_OPTIONS.map((opt) => (
                            <SelectItem key={opt.value} value={opt.value}>
                                {opt.label}
                                {data?.totals_per_bucket &&
                                    opt.value &&
                                    data.totals_per_bucket[opt.value] !== undefined && (
                                        <span className="ml-2 text-xs text-muted-foreground">
                                            ({data.totals_per_bucket[opt.value]})
                                        </span>
                                    )}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                {loading && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
            </div>

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
                                        <th className="w-8 px-3 py-2 text-left" />
                                        <th className="px-3 py-2 text-left font-medium">Business</th>
                                        <th className="px-3 py-2 text-left font-medium">Masked email</th>
                                        <th className="px-3 py-2 text-left font-medium">Consent</th>
                                        <th className="px-3 py-2 text-left font-medium">Bucket</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.items.map((item) => (
                                        <tr key={item.lead_id} className="border-b last:border-0">
                                            <td className="px-3 py-2">
                                                {item.bucket === "eligible" && (
                                                    <Checkbox
                                                        checked={true}
                                                        onCheckedChange={() => void handleExclude(item.lead_id)}
                                                        aria-label={`Exclude ${item.title}`}
                                                    />
                                                )}
                                            </td>
                                            <td className="px-3 py-2 font-medium">{item.title}</td>
                                            <td className="px-3 py-2 font-mono text-xs text-muted-foreground">
                                                {item.masked_email || "—"}
                                            </td>
                                            <td className="px-3 py-2">
                                                <span
                                                    className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${emailConsentClass(item.email_consent)}`}
                                                >
                                                    {emailConsentLabel(item.email_consent)}
                                                </span>
                                            </td>
                                            <td className="px-3 py-2">
                                                <Badge variant="outline" className="text-xs">
                                                    {item.bucket}
                                                </Badge>
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
                                <span>
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
        </div>
    );
}
