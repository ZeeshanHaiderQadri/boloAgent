import { describe, expect, it } from "vitest";

import {
    isLeadSearchActive,
    leadSearchStatusLabel,
    leadSortLabel,
    leadStatusClass,
    leadStatusLabel,
} from "@/lib/leadStatus";

describe("lead status helpers", () => {
    it("labels every CRM status and humanizes unknown values", () => {
        expect(leadStatusLabel("new")).toBe("New");
        expect(leadStatusLabel("do_not_call")).toBe("Do not call");
        expect(leadStatusLabel("waiting_on_legal")).toBe("waiting on legal");
        expect(leadStatusLabel(null)).toBe("Unknown");
    });

    it("gives do-not-call a filled destructive treatment", () => {
        expect(leadStatusClass("do_not_call")).toContain("bg-red-600");
        // Unknown statuses fall back instead of rendering an unstyled badge.
        expect(leadStatusClass("bogus")).toBe(leadStatusClass("new"));
    });

    it("treats only queued and running searches as active", () => {
        expect(isLeadSearchActive("queued")).toBe(true);
        expect(isLeadSearchActive("running")).toBe(true);
        expect(isLeadSearchActive("completed")).toBe(false);
        expect(isLeadSearchActive("cancelled")).toBe(false);
        expect(isLeadSearchActive(undefined)).toBe(false);
    });

    it("labels search statuses and sort options", () => {
        expect(leadSearchStatusLabel("running")).toBe("Searching");
        expect(leadSearchStatusLabel("mystery")).toBe("mystery");
        expect(leadSortLabel("rating")).toBe("Highest rated");
        expect(leadSortLabel(null)).toBe("Newest first");
    });
});
