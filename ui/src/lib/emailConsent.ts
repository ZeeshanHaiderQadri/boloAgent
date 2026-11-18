export const EMAIL_CONSENTS = ["unknown", "opted_in", "opted_out"] as const;

export type EmailConsent = (typeof EMAIL_CONSENTS)[number];

export const EMAIL_CONSENT_LABELS: Record<EmailConsent, string> = {
    unknown: "No consent recorded",
    opted_in: "Opted in",
    opted_out: "Opted out",
};

export const EMAIL_CONSENT_CLASSES: Record<EmailConsent, string> = {
    unknown: "border-transparent bg-slate-500/15 text-slate-700 dark:text-slate-300",
    opted_in:
        "border-transparent bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
    opted_out: "border-transparent bg-rose-500/15 text-rose-700 dark:text-rose-300",
};

// Keep this text in sync with EMAIL_CONSENT_WARNING in api/routes/leads.py. The
// backend stores it with every consent activity, so the audit trail shows the
// exact warning the operator saw.
export const EMAIL_CONSENT_WARNING =
    "Only record consent you can prove. Marketing email without permission can " +
    "breach anti-spam law and damage your sending domain.";

export function emailConsentLabel(consent: string | null | undefined): string {
    if (!consent) return EMAIL_CONSENT_LABELS.unknown;
    return EMAIL_CONSENT_LABELS[consent as EmailConsent] ?? consent;
}

export function emailConsentClass(consent: string | null | undefined): string {
    return (
        EMAIL_CONSENT_CLASSES[(consent ?? "unknown") as EmailConsent] ??
        EMAIL_CONSENT_CLASSES.unknown
    );
}

export function emailConsentSummary(
    consent: string | null | undefined,
    source: string | null | undefined,
    recordedAt: string | null | undefined,
    formatDate: (value: string) => string,
): string {
    if (!consent || consent === "unknown") {
        return "No permission recorded. This lead is not eligible for marketing email.";
    }
    const who =
        source === "bulk"
            ? "recorded in bulk"
            : source === "manual"
              ? "recorded manually"
              : source
                ? `recorded via ${source}`
                : "recorded";
    return recordedAt ? `${who} on ${formatDate(recordedAt)}` : who;
}
