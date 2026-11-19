import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { LeadResponse } from "@/client/types.gen";

import { LeadTable } from "./LeadTable";

vi.mock("next/navigation", () => ({
    useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/hooks/useOrganizationTimezone", () => ({
    useOrganizationTimezone: () => "UTC",
}));

function lead(overrides: Partial<LeadResponse> = {}): LeadResponse {
    return {
        id: 1,
        title: "Apex Software Labs",
        category: "Software company",
        address: "123 Market St",
        city: "San Francisco",
        state: "CA",
        country: "US",
        postal_code: "94105",
        phone: "(415) 555-0142",
        phone_e164: "+14155550142",
        email: "hello@apex.example.com",
        website: "https://apex.example.com",
        place_id: "p1",
        rating: 4.6,
        review_count: 128,
        latitude: 37.79,
        longitude: -122.4,
        open_hours: {},
        crm_status: "new",
        owner_user_id: null,
        tags: ["hot"],
        notes: null,
        source: "demo",
        last_contacted_at: null,
        next_follow_up_at: null,
        created_at: "2026-11-17T12:00:00Z",
        updated_at: "2026-11-17T12:00:00Z",
        ...overrides,
    };
}

describe("LeadTable", () => {
    it("renders business, contact, and CRM information", () => {
        render(<LeadTable leads={[lead()]} />);

        expect(screen.getAllByText("Apex Software Labs").length).toBeGreaterThan(0);
        expect(screen.getByText("Software company")).toBeTruthy();
        expect(screen.getByText("(415) 555-0142")).toBeTruthy();
        expect(screen.getByText("hello@apex.example.com")).toBeTruthy();
        expect(screen.getByText("apex.example.com")).toBeTruthy();
        expect(screen.getByText("New")).toBeTruthy();
        expect(screen.getByText("hot")).toBeTruthy();
        expect(screen.getByText("4.6")).toBeTruthy();
    });

    it("marks rows without a phone as non-callable", () => {
        render(<LeadTable leads={[lead({ phone: null, phone_e164: null })]} />);

        expect(screen.getByText("No phone")).toBeTruthy();
    });

    it("wires row and select-all checkboxes to the parent", () => {
        const onToggle = vi.fn();
        const onToggleAll = vi.fn();
        render(
            <LeadTable
                leads={[lead(), lead({ id: 2, title: "Cedar Works" })]}
                selectable
                selectedIds={[1]}
                onToggle={onToggle}
                onToggleAll={onToggleAll}
            />,
        );

        fireEvent.click(screen.getByRole("checkbox", { name: /select apex software labs/i }));
        expect(onToggle).toHaveBeenCalledWith(1);

        fireEvent.click(screen.getByRole("checkbox", { name: /select all leads/i }));
        expect(onToggleAll).toHaveBeenCalled();
    });

    it("links social icons to the stored business profiles", () => {
        render(
            <LeadTable
                leads={[
                    lead({
                        social_links: {
                            instagram: "https://www.instagram.com/apex/",
                            youtube: "https://www.youtube.com/@apexlabs",
                            website: "https://example.com/not-a-platform",
                        },
                    }),
                ]}
            />,
        );

        const instagram = screen.getByRole("link", { name: "Instagram profile" });
        expect(instagram.getAttribute("href")).toBe(
            "https://www.instagram.com/apex/",
        );
        expect(instagram.getAttribute("target")).toBe("_blank");
        expect(screen.getByRole("link", { name: "YouTube profile" })).toBeTruthy();
        expect(screen.queryByRole("link", { name: "X profile" })).toBeNull();
    });

    it("shows the empty state instead of an empty table", () => {
        render(<LeadTable leads={[]} emptyMessage="Nothing matches" />);

        expect(screen.getByText("Nothing matches")).toBeTruthy();
        expect(screen.queryByRole("table")).toBeNull();
    });
});
