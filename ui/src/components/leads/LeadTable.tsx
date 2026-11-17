"use client";

import { Globe, Mail, MapPin, Pencil, Phone, Star } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import type { LeadResponse } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { useOrganizationTimezone } from "@/hooks/useOrganizationTimezone";
import { formatDate } from "@/lib/dateTime";

import { LeadStatusBadge } from "./LeadStatusBadge";

interface LeadTableProps {
    leads: LeadResponse[];
    selectable?: boolean;
    selectedIds?: number[];
    onToggle?: (leadId: number) => void;
    onToggleAll?: () => void;
    /** Row-level edit action (opens an inline edit dialog). */
    onEdit?: (lead: LeadResponse) => void;
    /**
     * When provided, rows become draggable and carry the returned lead IDs in
     * ``application/x-bolo-lead-ids`` so project cards can accept the drop.
     */
    dragPayload?: (lead: LeadResponse) => number[] | null;
    emptyMessage?: string;
    compact?: boolean;
}

function websiteHref(website: string) {
    return /^https?:\/\//i.test(website) ? website : `https://${website}`;
}

function websiteLabel(website: string) {
    return website
        .replace(/^https?:\/\//i, "")
        .replace(/^www\./i, "")
        .replace(/\/+$/, "");
}

export function LeadTable({
    leads,
    selectable = false,
    selectedIds = [],
    onToggle,
    onToggleAll,
    onEdit,
    dragPayload,
    emptyMessage = "No leads yet.",
    compact = false,
}: LeadTableProps) {
    const router = useRouter();
    const timezone = useOrganizationTimezone();
    const allSelected =
        leads.length > 0 && leads.every((lead) => selectedIds.includes(lead.id));

    if (leads.length === 0) {
        return (
            <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
                {emptyMessage}
            </div>
        );
    }

    return (
        <div className="overflow-x-auto">
            <Table>
                <TableHeader>
                    <TableRow>
                        {selectable && (
                            <TableHead className="w-10">
                                <Checkbox
                                    aria-label="Select all leads"
                                    checked={allSelected}
                                    onCheckedChange={() => onToggleAll?.()}
                                />
                            </TableHead>
                        )}
                        <TableHead>Business</TableHead>
                        <TableHead>Category</TableHead>
                        <TableHead>Contact</TableHead>
                        {!compact && <TableHead>Email</TableHead>}
                        {!compact && <TableHead>Website</TableHead>}
                        {!compact && <TableHead>Location</TableHead>}
                        <TableHead>Rating</TableHead>
                        <TableHead>Status</TableHead>
                        {!compact && <TableHead>Added</TableHead>}
                        {onEdit && <TableHead className="text-right">Actions</TableHead>}
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {leads.map((lead) => {
                        const selected = selectedIds.includes(lead.id);
                        return (
                            <TableRow
                                key={lead.id}
                                data-state={selected ? "selected" : undefined}
                                className="cursor-pointer hover:bg-muted/50"
                                draggable={Boolean(dragPayload)}
                                onDragStart={(event) => {
                                    const ids = dragPayload?.(lead);
                                    if (!ids || ids.length === 0) {
                                        event.preventDefault();
                                        return;
                                    }
                                    event.dataTransfer.setData(
                                        "application/x-bolo-lead-ids",
                                        JSON.stringify(ids),
                                    );
                                    event.dataTransfer.effectAllowed = "move";
                                }}
                                onClick={() => router.push(`/leads/${lead.id}`)}
                            >
                                {selectable && (
                                    <TableCell
                                        onClick={(event) => event.stopPropagation()}
                                    >
                                        <Checkbox
                                            aria-label={`Select ${lead.title}`}
                                            checked={selected}
                                            onCheckedChange={() => onToggle?.(lead.id)}
                                        />
                                    </TableCell>
                                )}
                                <TableCell className="max-w-[280px]">
                                    <div className="flex flex-col gap-0.5">
                                        <Link
                                            href={`/leads/${lead.id}`}
                                            onClick={(event) => event.stopPropagation()}
                                            className="truncate font-medium hover:text-cta hover:underline"
                                        >
                                            {lead.title}
                                        </Link>
                                        {lead.tags && lead.tags.length > 0 && (
                                            <div className="mt-1 flex flex-wrap gap-1">
                                                {lead.tags.slice(0, 3).map((tag) => (
                                                    <span
                                                        key={tag}
                                                        className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground"
                                                    >
                                                        {tag}
                                                    </span>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                </TableCell>
                                <TableCell className="max-w-[180px]">
                                    <span className="line-clamp-2 text-sm text-muted-foreground">
                                        {lead.category || "—"}
                                    </span>
                                </TableCell>
                                <TableCell>
                                    <div className="flex flex-col gap-1 text-sm">
                                        {lead.phone ? (
                                            <a
                                                href={`tel:${lead.phone_e164 || lead.phone}`}
                                                onClick={(event) => event.stopPropagation()}
                                                className="flex items-center gap-1.5 hover:text-cta hover:underline"
                                            >
                                                <Phone className="h-3.5 w-3.5 shrink-0" />
                                                <span className="truncate">{lead.phone}</span>
                                            </a>
                                        ) : (
                                            <span className="flex items-center gap-1.5 text-muted-foreground">
                                                <Phone className="h-3.5 w-3.5 shrink-0" />
                                                No phone
                                            </span>
                                        )}
                                    </div>
                                </TableCell>
                                {!compact && (
                                    <TableCell className="max-w-[220px]">
                                        {lead.email ? (
                                            <a
                                                href={`mailto:${lead.email}`}
                                                onClick={(event) => event.stopPropagation()}
                                                className="flex items-center gap-1.5 text-sm hover:text-cta hover:underline"
                                            >
                                                <Mail className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                                                <span className="truncate">{lead.email}</span>
                                            </a>
                                        ) : (
                                            <span className="text-sm text-muted-foreground">—</span>
                                        )}
                                    </TableCell>
                                )}
                                {!compact && (
                                    <TableCell className="max-w-[200px]">
                                        {lead.website ? (
                                            <a
                                                href={websiteHref(lead.website)}
                                                target="_blank"
                                                rel="noreferrer"
                                                onClick={(event) => event.stopPropagation()}
                                                className="flex items-center gap-1.5 text-sm hover:text-cta hover:underline"
                                                title={lead.website}
                                            >
                                                <Globe className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                                                <span className="truncate">
                                                    {websiteLabel(lead.website)}
                                                </span>
                                            </a>
                                        ) : (
                                            <span className="text-sm text-muted-foreground">—</span>
                                        )}
                                    </TableCell>
                                )}
                                {!compact && (
                                    <TableCell className="max-w-[220px]">
                                        <div className="flex items-start gap-1.5 text-sm text-muted-foreground">
                                            <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                                            <span className="line-clamp-2">
                                                {[lead.city, lead.state, lead.country]
                                                    .filter(Boolean)
                                                    .join(", ") ||
                                                    lead.address ||
                                                    "—"}
                                            </span>
                                        </div>
                                    </TableCell>
                                )}
                                <TableCell>
                                    {lead.rating != null ? (
                                        <div className="flex items-center gap-1 text-sm">
                                            <Star className="h-3.5 w-3.5 fill-amber-400 text-amber-400" />
                                            <span>{lead.rating.toFixed(1)}</span>
                                            {lead.review_count != null && (
                                                <span className="text-xs text-muted-foreground">
                                                    ({lead.review_count})
                                                </span>
                                            )}
                                        </div>
                                    ) : (
                                        <span className="text-sm text-muted-foreground">—</span>
                                    )}
                                </TableCell>
                                <TableCell>
                                    <LeadStatusBadge status={lead.crm_status} />
                                </TableCell>
                                {!compact && (
                                    <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                                        {formatDate(lead.created_at, timezone)}
                                    </TableCell>
                                )}
                                {onEdit && (
                                    <TableCell
                                        className="text-right"
                                        onClick={(event) => event.stopPropagation()}
                                    >
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="icon"
                                            aria-label={`Edit ${lead.title}`}
                                            title="Edit contact details"
                                            onClick={() => onEdit(lead)}
                                        >
                                            <Pencil className="h-4 w-4" />
                                        </Button>
                                    </TableCell>
                                )}
                            </TableRow>
                        );
                    })}
                </TableBody>
            </Table>
        </div>
    );
}
