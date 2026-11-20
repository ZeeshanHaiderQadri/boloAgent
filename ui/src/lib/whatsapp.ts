/**
 * Preview helpers for WhatsApp click-to-chat templates.
 *
 * Mirrors `api/services/whatsapp/templates.py`: placeholders are filled with
 * sample values, a missing value becomes an empty string, and runs of spaces
 * are collapsed per line. The preview is illustrative only — the link the
 * operator opens is always built by the backend.
 */

export const WHATSAPP_VARIABLES = [
    { name: "business_name", label: "Business name", sample: "Apex Labs" },
    { name: "category", label: "Category", sample: "Marketing agency" },
    { name: "city", label: "City", sample: "Austin" },
] as const;

const PLACEHOLDER_RE = /\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g;

const SAMPLE_VALUES: Record<string, string> = Object.fromEntries(
    WHATSAPP_VARIABLES.map((variable) => [variable.name, variable.sample]),
);

export function extractTemplateVariables(body: string): string[] {
    const seen: string[] = [];
    for (const match of body.matchAll(PLACEHOLDER_RE)) {
        const name = match[1];
        if (!seen.includes(name)) seen.push(name);
    }
    return seen;
}

export function renderTemplatePreview(
    body: string,
    values: Record<string, string> = SAMPLE_VALUES,
): string {
    return body
        .replace(PLACEHOLDER_RE, (_, name: string) => values[name] ?? "")
        .split("\n")
        .map((line) => line.replace(/[ \t]{2,}/g, " ").trim())
        .join("\n")
        .trim();
}

/**
 * The icon is only shown when the number carries its own country: an
 * international (``+``) number or one the import pipeline already normalized
 * to E.164. Raw local numbers stay hidden rather than guessing a country.
 */
export function hasInternationalPhone(lead: {
    phone?: string | null;
    phone_e164?: string | null;
}): boolean {
    return (lead.phone_e164 ?? "").trim().startsWith("+") ||
        (lead.phone ?? "").trim().startsWith("+");
}
