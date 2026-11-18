"use client";

import {
    ArrowLeft,
    Building2,
    CalendarClock,
    ExternalLink,
    Globe,
    Loader2,
    Mail,
    MapPin,
    MapPinned,
    Megaphone,
    Phone,
    PhoneOff,
    Plus,
    RefreshCw,
    ShieldCheck,
    Star,
    StickyNote,
    Trash2,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    addLeadNoteApiV1LeadsLeadIdActivitiesPost,
    deleteLeadApiV1LeadsLeadIdDelete,
    getLeadDetailApiV1LeadsLeadIdGet,
    updateLeadApiV1LeadsLeadIdPatch,
} from "@/client/sdk.gen";
import type { LeadDetailResponse, LeadUpdateRequest } from "@/client/types.gen";
import { PageShell } from "@/components/layout/PageShell";
import { DispatchLeadsDialog } from "@/components/leads/DispatchLeadsDialog";
import { EmailConsentBadge } from "@/components/leads/EmailConsentBadge";
import { EmailConsentDialog } from "@/components/leads/EmailConsentDialog";
import { LeadStatusBadge } from "@/components/leads/LeadStatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { useOrganizationTimezone } from "@/hooks/useOrganizationTimezone";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { formatDateTime } from "@/lib/dateTime";
import {
    LEAD_STATUS_LABELS,
    LEAD_STATUSES,
    type LeadStatus,
} from "@/lib/leadStatus";
import { leadMapsUrl } from "@/lib/maps";

function activityIcon(type: string) {
    switch (type) {
        case "campaign":
            return Megaphone;
        case "status_change":
            return RefreshCw;
        case "call":
            return Phone;
        case "created":
            return Plus;
        default:
            return StickyNote;
    }
}

export default function LeadDetailPage() {
    const { user, redirectToLogin, loading } = useAuth();
    const params = useParams<{ leadId: string }>();
    const router = useRouter();
    const leadId = Number(params.leadId);
    const timezone = useOrganizationTimezone();
    const hasFetched = useRef(false);

    const [detail, setDetail] = useState<LeadDetailResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [statusBusy, setStatusBusy] = useState(false);
    const [deleteBusy, setDeleteBusy] = useState(false);
    const [tagsDraft, setTagsDraft] = useState("");
    const [tagsBusy, setTagsBusy] = useState(false);
    const [notesDraft, setNotesDraft] = useState("");
    const [notesBusy, setNotesBusy] = useState(false);
    const [followUpDraft, setFollowUpDraft] = useState("");
    const [noteBody, setNoteBody] = useState("");
    const [noteBusy, setNoteBusy] = useState(false);
    const [dispatchOpen, setDispatchOpen] = useState(false);
    const [consentOpen, setConsentOpen] = useState(false);

    useEffect(() => {
        if (!loading && !user) redirectToLogin();
    }, [loading, user, redirectToLogin]);

    const load = useCallback(
        async (showSkeleton = false) => {
            if (loading || !user || !Number.isInteger(leadId)) return;
            if (showSkeleton) setIsLoading(true);
            try {
                const response = await getLeadDetailApiV1LeadsLeadIdGet({
                    path: { lead_id: leadId },
                });
                if (response.error) {
                    setError(detailFromError(response.error, "Lead not found"));
                    return;
                }
                if (response.data) {
                    setDetail(response.data);
                    setTagsDraft((response.data.lead.tags ?? []).join(", "));
                    setNotesDraft(response.data.lead.notes ?? "");
                    setFollowUpDraft(response.data.lead.next_follow_up_at?.slice(0, 10) ?? "");
                }
            } catch (loadError) {
                console.error("Failed to load lead:", loadError);
                setError("Could not load this lead");
            } finally {
                if (showSkeleton) setIsLoading(false);
            }
        },
        [loading, user, leadId],
    );

    useEffect(() => {
        if (loading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void load(true);
    }, [loading, user, load]);

    const lead = detail?.lead;

    const patchLead = async (body: LeadUpdateRequest) => {
        const response = await updateLeadApiV1LeadsLeadIdPatch({
            path: { lead_id: leadId },
            body,
        });
        if (response.error) {
            toast.error(detailFromError(response.error, "Update failed"));
            return false;
        }
        await load(false);
        return true;
    };

    const handleStatusChange = async (nextStatus: string) => {
        setStatusBusy(true);
        try {
            if (await patchLead({ crm_status: nextStatus as LeadStatus })) {
                toast.success(`Status set to ${LEAD_STATUS_LABELS[nextStatus] ?? nextStatus}`);
            }
        } finally {
            setStatusBusy(false);
        }
    };

    const handleDelete = async () => {
        if (!lead) return;
        const confirmed = window.confirm(
            `Delete “${lead.title}” from your leads? Its notes and call history link will be removed.`,
        );
        if (!confirmed) return;
        setDeleteBusy(true);
        try {
            const response = await deleteLeadApiV1LeadsLeadIdDelete({
                path: { lead_id: leadId },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not delete this lead"));
                return;
            }
            toast.success("Lead deleted");
            router.push("/leads/all");
        } finally {
            setDeleteBusy(false);
        }
    };

    const handleTagsSave = async () => {
        const tags = tagsDraft
            .split(",")
            .map((tag) => tag.trim())
            .filter(Boolean)
            .slice(0, 20);
        setTagsBusy(true);
        try {
            if (await patchLead({ tags })) toast.success("Tags saved");
        } finally {
            setTagsBusy(false);
        }
    };

    const handleNotesSave = async () => {
        setNotesBusy(true);
        try {
            if (await patchLead({ notes: notesDraft })) toast.success("Notes saved");
        } finally {
            setNotesBusy(false);
        }
    };

    const handleFollowUpSave = async () => {
        setStatusBusy(true);
        try {
            const value = followUpDraft
                ? new Date(`${followUpDraft}T09:00:00`).toISOString()
                : null;
            if (await patchLead({ next_follow_up_at: value })) {
                toast.success(value ? "Follow-up scheduled" : "Follow-up cleared");
            }
        } finally {
            setStatusBusy(false);
        }
    };

    const handleAddNote = async () => {
        if (!noteBody.trim()) return;
        setNoteBusy(true);
        try {
            const response = await addLeadNoteApiV1LeadsLeadIdActivitiesPost({
                path: { lead_id: leadId },
                body: { body: noteBody.trim() },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not add note"));
                return;
            }
            setNoteBody("");
            await load(false);
            toast.success("Note added");
        } finally {
            setNoteBusy(false);
        }
    };

    if (isLoading && !detail) {
        return (
            <PageShell>
                <div className="h-72 animate-pulse rounded-xl bg-muted" />
            </PageShell>
        );
    }

    if (error || !lead) {
        return (
            <PageShell>
                <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-6 text-sm text-destructive">
                    {error ?? "Lead not found"}
                </div>
                <Button variant="outline" asChild className="mt-4">
                    <Link href="/leads/all">
                        <ArrowLeft className="h-4 w-4" />
                        Back to leads
                    </Link>
                </Button>
            </PageShell>
        );
    }

    const dialable = Boolean(lead.phone_e164) && lead.crm_status !== "do_not_call";

    return (
        <PageShell>
            <div className="mb-6">
                <Button variant="ghost" size="sm" asChild className="mb-3 -ml-3">
                    <Link href="/leads/all">
                        <ArrowLeft className="h-4 w-4" />
                        CRM
                    </Link>
                </Button>
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="min-w-0 flex-1">
                        <div className="mb-2 flex flex-wrap items-center gap-2">
                            <h1 className="text-2xl font-bold">{lead.title}</h1>
                            <LeadStatusBadge status={lead.crm_status} />
                            {lead.crm_status === "do_not_call" && (
                                <Badge variant="destructive" className="gap-1">
                                    <PhoneOff className="h-3 w-3" />
                                    Excluded from campaigns
                                </Badge>
                            )}
                        </div>
                        <p className="text-sm text-muted-foreground">
                            {lead.category || "Business"}
                            {lead.source === "demo" ? " · demo record" : ""}
                        </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        <Button
                            onClick={() => setDispatchOpen(true)}
                            disabled={!dialable}
                            title={!dialable ? "This lead has no dialable number" : undefined}
                        >
                            <Megaphone className="h-4 w-4" />
                            Send to campaign
                        </Button>
                        {lead.crm_status !== "do_not_call" && (
                            <Button
                                variant="outline"
                                onClick={() => void handleStatusChange("do_not_call")}
                                disabled={statusBusy}
                            >
                                <PhoneOff className="h-4 w-4" />
                                Do not call
                            </Button>
                        )}
                        <Button
                            variant="ghost"
                            className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                            onClick={() => void handleDelete()}
                            disabled={deleteBusy}
                        >
                            {deleteBusy ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <Trash2 className="h-4 w-4" />
                            )}
                            Delete
                        </Button>
                    </div>
                </div>
            </div>

            <div className="grid gap-6 lg:grid-cols-3">
                <div className="space-y-6 lg:col-span-2">
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <Building2 className="h-5 w-5 text-cta" />
                                Business details
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="grid gap-4 sm:grid-cols-2">
                            <DetailRow icon={Phone} label="Phone">
                                {lead.phone ? (
                                    <a
                                        href={`tel:${lead.phone_e164 || lead.phone}`}
                                        className="hover:text-cta hover:underline"
                                    >
                                        {lead.phone}
                                        {lead.phone_e164 && lead.phone_e164 !== lead.phone && (
                                            <span className="block text-xs text-muted-foreground">
                                                {lead.phone_e164}
                                            </span>
                                        )}
                                    </a>
                                ) : (
                                    <span className="text-muted-foreground">Not available</span>
                                )}
                            </DetailRow>
                            <DetailRow icon={Mail} label="Email">
                                {lead.email ? (
                                    <a href={`mailto:${lead.email}`} className="hover:text-cta hover:underline">
                                        {lead.email}
                                    </a>
                                ) : (
                                    <span className="text-muted-foreground">Not available</span>
                                )}
                            </DetailRow>
                            <DetailRow icon={Globe} label="Website">
                                {lead.website ? (
                                    <a
                                        href={lead.website}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="inline-flex items-center gap-1 hover:text-cta hover:underline"
                                    >
                                        {lead.website.replace(/^https?:\/\//, "")}
                                        <ExternalLink className="h-3 w-3" />
                                    </a>
                                ) : (
                                    <span className="text-muted-foreground">Not available</span>
                                )}
                            </DetailRow>
                            <DetailRow icon={Star} label="Rating">
                                {lead.rating != null
                                    ? `${lead.rating.toFixed(1)} (${lead.review_count ?? 0} reviews)`
                                    : "—"}
                            </DetailRow>
                            <DetailRow icon={MapPin} label="Address">
                                <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                                    <span>
                                        {lead.address ||
                                            [lead.city, lead.state, lead.country]
                                                .filter(Boolean)
                                                .join(", ") ||
                                            "—"}
                                    </span>
                                    <a
                                        href={leadMapsUrl(lead)}
                                        target="_blank"
                                        rel="noreferrer"
                                        className="inline-flex items-center gap-1 font-medium text-cta hover:underline"
                                        title="Verify this business on Google Maps"
                                    >
                                        <MapPinned className="h-3.5 w-3.5" />
                                        Map
                                    </a>
                                </span>
                            </DetailRow>
                            <DetailRow icon={CalendarClock} label="Added">
                                {formatDateTime(lead.created_at, timezone)}
                            </DetailRow>
                        </CardContent>
                    </Card>

                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <StickyNote className="h-5 w-5 text-cta" />
                                Notes
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-3">
                            <Textarea
                                value={notesDraft}
                                onChange={(event) => setNotesDraft(event.target.value)}
                                placeholder="What did you learn about this business? Best time to call, budget, objections…"
                                rows={4}
                            />
                            <div className="flex justify-end">
                                <Button
                                    size="sm"
                                    variant="secondary"
                                    onClick={() => void handleNotesSave()}
                                    disabled={notesBusy}
                                >
                                    {notesBusy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                                    Save notes
                                </Button>
                            </div>

                            <Separator />

                            <div className="space-y-2">
                                <Label htmlFor="lead-activity-note">Add a timeline note</Label>
                                <div className="flex gap-2">
                                    <Input
                                        id="lead-activity-note"
                                        value={noteBody}
                                        onChange={(event) => setNoteBody(event.target.value)}
                                        placeholder="Called, asked for pricing…"
                                        onKeyDown={(event) => {
                                            if (event.key === "Enter") void handleAddNote();
                                        }}
                                    />
                                    <Button
                                        variant="outline"
                                        onClick={() => void handleAddNote()}
                                        disabled={noteBusy || !noteBody.trim()}
                                    >
                                        {noteBusy ? (
                                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                        ) : (
                                            <Plus className="h-3.5 w-3.5" />
                                        )}
                                        Add
                                    </Button>
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    <Card>
                        <CardHeader>
                            <CardTitle>Activity timeline</CardTitle>
                        </CardHeader>
                        <CardContent>
                            {detail.activities.length === 0 ? (
                                <p className="text-sm text-muted-foreground">
                                    No activity yet. Notes, status changes, and campaigns appear here.
                                </p>
                            ) : (
                                <ol className="relative space-y-5 border-l pl-5">
                                    {detail.activities.map((activity) => {
                                        const Icon = activityIcon(activity.activity_type);
                                        return (
                                            <li key={activity.id} className="relative">
                                                <span className="absolute -left-[30px] flex h-6 w-6 items-center justify-center rounded-full border bg-background">
                                                    <Icon className="h-3.5 w-3.5 text-muted-foreground" />
                                                </span>
                                                <div className="flex flex-wrap items-baseline justify-between gap-2">
                                                    <p className="text-sm font-medium">
                                                        {activity.title || activity.activity_type.replace(/_/g, " ")}
                                                    </p>
                                                    <time className="text-xs text-muted-foreground">
                                                        {formatDateTime(activity.created_at, timezone)}
                                                    </time>
                                                </div>
                                                {activity.body && (
                                                    <p className="mt-1 whitespace-pre-wrap text-sm text-muted-foreground">
                                                        {activity.body}
                                                    </p>
                                                )}
                                            </li>
                                        );
                                    })}
                                </ol>
                            )}
                        </CardContent>
                    </Card>
                </div>

                <div className="space-y-6">
                    <Card>
                        <CardHeader>
                            <CardTitle>CRM</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="space-y-2">
                                <Label>Pipeline status</Label>
                                <Select
                                    value={lead.crm_status}
                                    onValueChange={(value) => void handleStatusChange(value)}
                                    disabled={statusBusy}
                                >
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {LEAD_STATUSES.map((item) => (
                                            <SelectItem key={item} value={item}>
                                                {LEAD_STATUS_LABELS[item]}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>

                            <div className="space-y-2">
                                <Label>Email consent</Label>
                                <div className="flex items-center justify-between gap-2">
                                    <EmailConsentBadge consent={lead.email_consent} />
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        onClick={() => setConsentOpen(true)}
                                    >
                                        <ShieldCheck className="h-3.5 w-3.5" />
                                        Change
                                    </Button>
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    {lead.email_consent === "unknown"
                                        ? "No permission recorded. This lead will not receive marketing email."
                                        : `${
                                              lead.email_consent_source === "bulk"
                                                  ? "Recorded in bulk"
                                                  : lead.email_consent_source === "manual"
                                                    ? "Recorded manually"
                                                    : lead.email_consent_source
                                                      ? `Recorded via ${lead.email_consent_source}`
                                                      : "Recorded"
                                          }${
                                              lead.email_consent_at
                                                  ? ` on ${formatDateTime(lead.email_consent_at, timezone)}`
                                                  : ""
                                          }.`}
                                </p>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="lead-tags">Tags</Label>
                                <div className="flex gap-2">
                                    <Input
                                        id="lead-tags"
                                        value={tagsDraft}
                                        onChange={(event) => setTagsDraft(event.target.value)}
                                        placeholder="hot, q4, referral"
                                    />
                                    <Button
                                        variant="secondary"
                                        onClick={() => void handleTagsSave()}
                                        disabled={tagsBusy}
                                    >
                                        {tagsBusy ? (
                                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                        ) : (
                                            "Save"
                                        )}
                                    </Button>
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    Comma-separated, up to 20 tags.
                                </p>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="lead-follow-up">Next follow-up</Label>
                                <div className="flex gap-2">
                                    <Input
                                        id="lead-follow-up"
                                        type="date"
                                        value={followUpDraft}
                                        onChange={(event) => setFollowUpDraft(event.target.value)}
                                    />
                                    <Button
                                        variant="secondary"
                                        onClick={() => void handleFollowUpSave()}
                                        disabled={statusBusy}
                                    >
                                        Save
                                    </Button>
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    <Card>
                        <CardHeader>
                            <CardTitle>Outbound calls</CardTitle>
                        </CardHeader>
                        <CardContent>
                            {detail.calls.length === 0 ? (
                                <p className="text-sm text-muted-foreground">
                                    No calls yet. Send this lead to a campaign to start outreach.
                                </p>
                            ) : (
                                <ul className="space-y-3">
                                    {detail.calls.map((call) => (
                                        <li key={call.workflow_run_id} className="rounded-lg border p-3 text-sm">
                                            <div className="flex items-center justify-between gap-2">
                                                <Link
                                                    href={`/campaigns/${call.campaign_id}`}
                                                    className="truncate font-medium hover:text-cta hover:underline"
                                                >
                                                    {call.campaign_name}
                                                </Link>
                                                <Badge variant={call.state === "completed" ? "success" : "secondary"}>
                                                    {call.state}
                                                </Badge>
                                            </div>
                                            <p className="mt-1 text-xs text-muted-foreground">
                                                {call.created_at
                                                    ? formatDateTime(call.created_at, timezone)
                                                    : "No start time"}
                                                {call.retry_count ? ` · retry ${call.retry_count}` : ""}
                                            </p>
                                            {(call.mapped_call_disposition || call.call_status) && (
                                                <p className="mt-2 text-xs">
                                                    <span className="text-muted-foreground">
                                                        Outcome:{" "}
                                                    </span>
                                                    <span className="font-medium">
                                                        {call.mapped_call_disposition ||
                                                            call.call_status}
                                                    </span>
                                                </p>
                                            )}
                                            {(call.call_tags ?? []).length > 0 && (
                                                <div className="mt-1 flex flex-wrap gap-1">
                                                    {(call.call_tags ?? []).map((tag) => (
                                                        <Badge
                                                            key={tag}
                                                            variant="secondary"
                                                            className="text-[10px]"
                                                        >
                                                            {tag}
                                                        </Badge>
                                                    ))}
                                                </div>
                                            )}
                                            {call.summary && (
                                                <p className="mt-1 whitespace-pre-wrap text-xs text-muted-foreground">
                                                    {call.summary}
                                                </p>
                                            )}
                                            <div className="mt-2 flex gap-3 text-xs">
                                                {call.recording_url && (
                                                    <a
                                                        href={call.recording_url}
                                                        target="_blank"
                                                        rel="noreferrer"
                                                        className="inline-flex items-center gap-1 text-cta hover:underline"
                                                    >
                                                        Recording
                                                        <ExternalLink className="h-3 w-3" />
                                                    </a>
                                                )}
                                                {call.transcript_url && (
                                                    <a
                                                        href={call.transcript_url}
                                                        target="_blank"
                                                        rel="noreferrer"
                                                        className="inline-flex items-center gap-1 text-cta hover:underline"
                                                    >
                                                        Transcript
                                                        <ExternalLink className="h-3 w-3" />
                                                    </a>
                                                )}
                                                <Link
                                                    href={`/usage?workflow_run_id=${call.workflow_run_id}`}
                                                    className="text-muted-foreground hover:text-cta hover:underline"
                                                >
                                                    Details
                                                </Link>
                                            </div>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </CardContent>
                    </Card>

                    <Card>
                        <CardHeader>
                            <CardTitle>Appears in searches</CardTitle>
                        </CardHeader>
                        <CardContent>
                            {detail.searches.length === 0 ? (
                                <p className="text-sm text-muted-foreground">No linked searches.</p>
                            ) : (
                                <ul className="space-y-2 text-sm">
                                    {detail.searches.map((search) => (
                                        <li key={search.id}>
                                            <Link
                                                href={`/leads/searches/${search.id}`}
                                                className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2 hover:bg-muted/50"
                                            >
                                                <span className="truncate">{search.name}</span>
                                                <Badge variant="outline">{search.status}</Badge>
                                            </Link>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </CardContent>
                    </Card>
                </div>
            </div>

            <DispatchLeadsDialog
                leadIds={[lead.id]}
                open={dispatchOpen}
                onOpenChange={setDispatchOpen}
            />

            <EmailConsentDialog
                leadIds={[lead.id]}
                current={lead.email_consent}
                open={consentOpen}
                onOpenChange={setConsentOpen}
                onSaved={() => load(false)}
            />
        </PageShell>
    );
}

function DetailRow({
    icon: Icon,
    label,
    children,
}: {
    icon: typeof Phone;
    label: string;
    children: ReactNode;
}) {
    return (
        <div className="flex items-start gap-2">
            <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            <div className="min-w-0">
                <p className="text-xs text-muted-foreground">{label}</p>
                <div className="break-words text-sm">{children}</div>
            </div>
        </div>
    );
}
