export const LEAD_STATUSES = [
    "new",
    "contacted",
    "interested",
    "not_interested",
    "won",
    "lost",
    "do_not_call",
] as const;

export type LeadStatus = (typeof LEAD_STATUSES)[number];

export const LEAD_STATUS_LABELS: Record<string, string> = {
    new: "New",
    contacted: "Contacted",
    interested: "Interested",
    not_interested: "Not interested",
    won: "Won",
    lost: "Lost",
    do_not_call: "Do not call",
};

export const LEAD_STATUS_CLASSES: Record<string, string> = {
    new: "border-transparent bg-sky-500/15 text-sky-700 dark:text-sky-300",
    contacted: "border-transparent bg-indigo-500/15 text-indigo-700 dark:text-indigo-300",
    interested: "border-transparent bg-amber-500/20 text-amber-800 dark:text-amber-300",
    not_interested: "border-transparent bg-slate-500/15 text-slate-700 dark:text-slate-300",
    won: "border-transparent bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
    lost: "border-transparent bg-rose-500/15 text-rose-700 dark:text-rose-300",
    do_not_call: "border-transparent bg-red-600 text-white",
};

export function leadStatusLabel(status: string | null | undefined): string {
    if (!status) return "Unknown";
    return LEAD_STATUS_LABELS[status] ?? status.replace(/_/g, " ");
}

export function leadStatusClass(status: string | null | undefined): string {
    if (!status) return LEAD_STATUS_CLASSES.new;
    return LEAD_STATUS_CLASSES[status] ?? LEAD_STATUS_CLASSES.new;
}

export const LEAD_SEARCH_STATUS_LABELS: Record<string, string> = {
    queued: "Queued",
    running: "Searching",
    stopping: "Stopping…",
    stopped: "Stopped (partial)",
    completed: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
};

export function leadSearchStatusLabel(status: string | null | undefined): string {
    if (!status) return "Unknown";
    return LEAD_SEARCH_STATUS_LABELS[status] ?? status;
}

export function isLeadSearchActive(status: string | null | undefined): boolean {
    return status === "queued" || status === "running" || status === "stopping";
}

export const LEAD_SORT_LABELS: Record<string, string> = {
    created_at: "Newest first",
    rating: "Highest rated",
    title: "Business name",
    status: "CRM status",
};

export function leadSortLabel(sort: string | null | undefined): string {
    return LEAD_SORT_LABELS[sort ?? "created_at"] ?? "Newest first";
}
