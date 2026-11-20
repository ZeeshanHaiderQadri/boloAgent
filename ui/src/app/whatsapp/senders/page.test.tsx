import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { listWhatsappSendersApiV1WhatsappSendersGet } from "@/client";
import type { WhatsAppSenderResponse } from "@/client/types.gen";

import WhatsAppSendersPage from "./page";

const { auth } = vi.hoisted(() => ({
    auth: {
        user: { id: "user" },
        loading: false,
        getAccessToken: vi.fn(async () => "token"),
        redirectToLogin: vi.fn(),
    },
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => auth }));
vi.mock("@/client", () => ({
    listWhatsappSendersApiV1WhatsappSendersGet: vi.fn(),
    createWhatsappSenderApiV1WhatsappSendersPost: vi.fn(),
    updateWhatsappSenderApiV1WhatsappSendersSenderUuidPatch: vi.fn(),
    deleteWhatsappSenderApiV1WhatsappSendersSenderUuidDelete: vi.fn(),
}));

const sender: WhatsAppSenderResponse = {
    uuid: "s1",
    label: "Main line",
    phone_e164: "+15125550100",
    country: "US",
    is_default: true,
    created_at: "2026-11-19T10:00:00Z",
    updated_at: null,
};

beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listWhatsappSendersApiV1WhatsappSendersGet).mockResolvedValue({
        data: [sender],
    } as never);
});

describe("WhatsApp senders page", () => {
    it("lists saved numbers and states both disclaimers", async () => {
        render(<WhatsAppSendersPage />);

        expect(await screen.findByRole("heading", { name: "Main line" })).toBeTruthy();
        expect(screen.getByText("+15125550100")).toBeTruthy();
        expect(screen.getByText("Default")).toBeTruthy();

        expect(screen.getByText(/WhatsApp can ban or restrict a number/i)).toBeTruthy();
        expect(screen.getByText(/Bolo is not responsible for any action WhatsApp takes/i)).toBeTruthy();
        expect(
            screen.getByText(/opens whichever WhatsApp account you are signed into/i),
        ).toBeTruthy();
        expect(screen.getByText(/informational only/i)).toBeTruthy();
        expect(listWhatsappSendersApiV1WhatsappSendersGet).toHaveBeenCalledWith({
            headers: { Authorization: "Bearer token" },
        });
    });
});
