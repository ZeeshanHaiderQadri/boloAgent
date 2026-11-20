import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
    getWhatsappDisclaimerApiV1WhatsappDisclaimerGet,
    whatsappLeadLinksApiV1WhatsappLeadsLeadIdLinksGet,
} from "@/client";
import type { LeadResponse } from "@/client/types.gen";

import { WhatsAppLinkButton } from "./WhatsAppLinkButton";

const { auth } = vi.hoisted(() => ({
    auth: {
        getAccessToken: vi.fn(async () => "token"),
    },
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => auth }));
vi.mock("@/client", () => ({
    getWhatsappDisclaimerApiV1WhatsappDisclaimerGet: vi.fn(),
    acceptWhatsappDisclaimerApiV1WhatsappDisclaimerPost: vi.fn(),
    whatsappLeadLinksApiV1WhatsappLeadsLeadIdLinksGet: vi.fn(),
}));

function lead(overrides: Partial<LeadResponse> = {}): LeadResponse {
    return {
        id: 7,
        title: "Apex Labs",
        category: "Marketing agency",
        address: "1 Main St",
        city: "Austin",
        state: "TX",
        country: "US",
        postal_code: "78701",
        phone: "(512) 555-0142",
        phone_e164: "+15125550142",
        whatsapp_available: true,
        email: null,
        website: null,
        place_id: "p1",
        rating: null,
        review_count: null,
        latitude: null,
        longitude: null,
        open_hours: {},
        crm_status: "new",
        owner_user_id: null,
        tags: [],
        notes: null,
        source: "demo",
        last_contacted_at: null,
        next_follow_up_at: null,
        created_at: "2026-11-17T12:00:00Z",
        updated_at: "2026-11-17T12:00:00Z",
        ...overrides,
    };
}

beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getWhatsappDisclaimerApiV1WhatsappDisclaimerGet).mockResolvedValue({
        data: { version: "2026-11-20", text: "Notice", accepted: true, accepted_at: null },
    } as never);
});

afterEach(() => {
    vi.restoreAllMocks();
});

describe("WhatsAppLinkButton", () => {
    it("does not render when the backend cannot build a link", () => {
        render(<WhatsAppLinkButton lead={lead({ whatsapp_available: false })} />);

        expect(screen.queryByRole("button", { name: /whatsapp/i })).toBeNull();
        expect(whatsappLeadLinksApiV1WhatsappLeadsLeadIdLinksGet).not.toHaveBeenCalled();
    });

    it("opens the chooser and links out with the backend-built wa.me URL", async () => {
        const fetchSpy = vi.spyOn(globalThis, "fetch");
        vi.mocked(whatsappLeadLinksApiV1WhatsappLeadsLeadIdLinksGet).mockResolvedValue({
            data: {
                available: true,
                reason: null,
                phone_e164: "+15125550142",
                country: "US",
                links: [
                    {
                        template_uuid: "t1",
                        name: "First contact",
                        language: "en",
                        body: "Hi Apex Labs, about your Marketing agency in Austin.",
                        url: "https://wa.me/15125550142?text=Hi%20Apex%20Labs",
                    },
                ],
            },
        } as never);

        render(<WhatsAppLinkButton lead={lead()} />);
        fireEvent.click(screen.getByRole("button", { name: /open whatsapp message for apex/i }));

        const name = await screen.findByText("First contact");
        expect(screen.getByText("+1 (512) 555-0142")).toBeTruthy();
        expect(screen.getByText("(United States or Canada)")).toBeTruthy();
        const anchor = name.closest("a");
        expect(anchor?.getAttribute("href")).toBe(
            "https://wa.me/15125550142?text=Hi%20Apex%20Labs",
        );
        expect(anchor?.getAttribute("target")).toBe("_blank");
        expect(anchor?.getAttribute("rel")).toBe("noopener noreferrer");
        expect(whatsappLeadLinksApiV1WhatsappLeadsLeadIdLinksGet).toHaveBeenCalledWith({
            headers: { Authorization: "Bearer token" },
            path: { lead_id: 7 },
        });
        // Building a link is the only request; nothing is sent from Bolo.
        expect(fetchSpy).not.toHaveBeenCalled();
    });

    it("asks for the notice before building any link", async () => {
        vi.mocked(getWhatsappDisclaimerApiV1WhatsappDisclaimerGet).mockResolvedValue({
            data: { version: "2026-11-20", text: "Notice", accepted: false, accepted_at: null },
        } as never);

        render(<WhatsAppLinkButton lead={lead()} />);
        fireEvent.click(screen.getByRole("button", { name: /open whatsapp message for apex/i }));

        expect(
            await screen.findByRole("dialog", { name: "Before you message on WhatsApp" }),
        ).toBeTruthy();
        expect(whatsappLeadLinksApiV1WhatsappLeadsLeadIdLinksGet).not.toHaveBeenCalled();
    });

    it("explains an unknown country instead of guessing one", async () => {
        vi.mocked(whatsappLeadLinksApiV1WhatsappLeadsLeadIdLinksGet).mockResolvedValue({
            data: {
                available: false,
                reason: "country_unknown",
                phone_e164: null,
                links: [],
            },
        } as never);

        render(<WhatsAppLinkButton lead={lead()} />);
        fireEvent.click(screen.getByRole("button", { name: /open whatsapp message for apex/i }));

        expect(await screen.findByText(/country for this number is unknown/i)).toBeTruthy();
        await waitFor(() =>
            expect(screen.getByRole("link", { name: "WhatsApp numbers" })).toBeTruthy(),
        );
    });

    it("points to templates when the organization has none", async () => {
        vi.mocked(whatsappLeadLinksApiV1WhatsappLeadsLeadIdLinksGet).mockResolvedValue({
            data: {
                available: true,
                reason: null,
                phone_e164: "+15125550142",
                links: [],
            },
        } as never);

        render(<WhatsAppLinkButton lead={lead()} />);
        fireEvent.click(screen.getByRole("button", { name: /open whatsapp message for apex/i }));

        expect(await screen.findByText("No WhatsApp templates yet.")).toBeTruthy();
    });
});

describe("WhatsAppLinkButton consent hints", () => {
    beforeEach(() => {
        vi.mocked(whatsappLeadLinksApiV1WhatsappLeadsLeadIdLinksGet).mockResolvedValue({
            data: {
                available: true,
                reason: null,
                phone_e164: "+15125550142",
                links: [
                    {
                        template_uuid: "t1",
                        name: "First contact",
                        body: "Hi Apex Labs",
                        url: "https://wa.me/15125550142?text=Hi",
                    },
                ],
            },
        } as never);
    });

    async function openChooser(target: LeadResponse) {
        render(<WhatsAppLinkButton lead={target} />);
        fireEvent.click(
            screen.getByRole("button", {
                name: `Open WhatsApp message for ${target.title}`,
            })
        );
        await screen.findByText("First contact");
    }

    it("always says WhatsApp consent is not tracked and flags unknown email consent", async () => {
        await openChooser(lead());
        expect(screen.getByText(/Bolo does not track WhatsApp consent/)).toBeTruthy();
        expect(screen.getByText(/No email consent recorded/)).toBeTruthy();
    });

    it("warns when the lead opted out of marketing email", async () => {
        await openChooser(lead({ email_consent: "opted_out" }));
        expect(screen.getByText(/Email consent: opted out/)).toBeTruthy();
    });

    it("shows email opt-in without treating it as WhatsApp consent", async () => {
        await openChooser(lead({ email_consent: "opted_in" }));
        const note = screen.getByText(/Email consent: opted in/);
        expect(note.textContent).toContain("does not prove");
    });
});
