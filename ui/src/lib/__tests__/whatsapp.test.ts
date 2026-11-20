import { describe, expect, it } from "vitest";

import {
    extractTemplateVariables,
    hasInternationalPhone,
    renderTemplatePreview,
} from "@/lib/whatsapp";

describe("WhatsApp preview helpers", () => {
    it("lists placeholders in order of first use, without duplicates", () => {
        expect(
            extractTemplateVariables("Hi {{business_name}}, {{ city }} — {{business_name}}"),
        ).toEqual(["business_name", "city"]);
    });

    it("fills sample values and collapses the gaps left by missing ones", () => {
        expect(
            renderTemplatePreview("Hi {{business_name}}, about {{category}} in {{city}}."),
        ).toBe("Hi Apex Labs, about Marketing agency in Austin.");
        expect(renderTemplatePreview("Hello  {{city}}")).toBe("Hello Austin");
    });

    it("keeps newlines in the preview", () => {
        expect(renderTemplatePreview("Hi {{business_name}},\n{{city}}")).toBe(
            "Hi Apex Labs,\nAustin",
        );
    });

    it("only treats numbers with their own country as international", () => {
        expect(hasInternationalPhone({ phone: "(512) 555-0142", phone_e164: "+15125550142" })).toBe(
            true,
        );
        expect(hasInternationalPhone({ phone: "+44 20 7946 0958", phone_e164: null })).toBe(true);
        // No hardcoded +1: a local number stays without an icon.
        expect(hasInternationalPhone({ phone: "(512) 555-0142", phone_e164: null })).toBe(false);
        expect(hasInternationalPhone({ phone: null, phone_e164: null })).toBe(false);
    });
});
