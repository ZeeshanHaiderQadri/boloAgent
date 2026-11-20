/**
 * Preview helpers for WhatsApp click-to-chat templates.
 *
 * Mirrors `api/services/whatsapp/templates.py`: placeholders are filled with
 * sample values, a missing value becomes an empty string, and runs of spaces
 * are collapsed per line. The preview is illustrative only — the link the
 * operator opens is always built by the backend.
 */

import { defaultCountries, getActiveFormattingMask, parseCountry } from "react-international-phone";

/** Mirrors MAX_TEMPLATE_BODY_CHARS in api/services/whatsapp/templates.py. */
export const WHATSAPP_TEMPLATE_BODY_MAX = 1000;

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

function fillMask(mask: string, digits: string): string {
    let filled = "";
    let index = 0;
    for (const symbol of mask) {
        if (index >= digits.length) break;
        filled += symbol === "." ? digits[index++] : symbol;
    }
    return digits.length > index ? filled + digits.slice(index) : filled;
}

/**
 * Human-readable form of the number the chooser will open, e.g.
 * ``+1 (512) 852-2300`` for ``+15128522300`` with country ``US``. Falls back to
 * the plain E.164 string when the country is unknown or does not match the
 * number, so it never invents a grouping.
 */
export function describePhoneNumber(
    phoneE164: string,
    countryIso?: string | null,
): { formatted: string; countryName: string | null } {
    const digits = phoneE164.replace(/\D/g, "");
    const entry = countryIso
        ? defaultCountries.find((item) => item[1] === countryIso.toLowerCase())
        : undefined;
    if (!digits || !entry) return { formatted: phoneE164, countryName: null };
    const country = parseCountry(entry);
    if (!digits.startsWith(country.dialCode)) {
        return { formatted: phoneE164, countryName: null };
    }
    const national = digits.slice(country.dialCode.length);
    const mask = getActiveFormattingMask({
        phone: digits,
        country,
        defaultMask: "............",
    });
    return {
        formatted: `+${country.dialCode} ${fillMask(mask, national).trim()}`,
        // A +1 number does not reveal whether it is US or Canadian.
        countryName: country.dialCode === "1" ? "United States or Canada" : country.name,
    };
}
