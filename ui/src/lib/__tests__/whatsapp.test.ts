import { describe, expect, it } from "vitest";

import {
    describePhoneNumber,
    extractTemplateVariables,
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
});

describe("describePhoneNumber", () => {
    it("formats a number with the country the backend resolved", () => {
        expect(describePhoneNumber("+15128522300", "US")).toEqual({
            formatted: "+1 (512) 852-2300",
            countryName: "United States",
        });
        expect(describePhoneNumber("+442079460018", "GB")).toEqual({
            formatted: "+44 2079 460018",
            countryName: "United Kingdom",
        });
    });

    it("falls back to plain E.164 when the country is unknown", () => {
        expect(describePhoneNumber("+15128522300", null)).toEqual({
            formatted: "+15128522300",
            countryName: null,
        });
    });
});
