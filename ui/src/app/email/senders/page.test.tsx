import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
    listEmailSendersApiV1EmailSendersGet,
    testSendEmailApiV1EmailSendersSenderUuidTestSendPost,
    verifyEmailSenderApiV1EmailSendersSenderUuidVerifyPost,
} from "@/client";
import type { EmailSenderResponse } from "@/client/types.gen";

import EmailSendersPage from "./page";

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
    listEmailSendersApiV1EmailSendersGet: vi.fn(),
    createEmailSenderApiV1EmailSendersPost: vi.fn(),
    updateEmailSenderApiV1EmailSendersSenderUuidPatch: vi.fn(),
    deleteEmailSenderApiV1EmailSendersSenderUuidDelete: vi.fn(),
    verifyEmailSenderApiV1EmailSendersSenderUuidVerifyPost: vi.fn(),
    testSendEmailApiV1EmailSendersSenderUuidTestSendPost: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const sender: EmailSenderResponse = {
    uuid: "sender-1",
    name: "Support",
    provider: "smtp",
    from_name: "Bolo Support",
    from_email: "support@example.com",
    reply_to_email: null,
    settings: { host: "smtp.hostinger.com", port: 465, security: "ssl" },
    status: "verified",
    is_default: true,
    limits: { daily_cap: 100, rate_per_second: 1 },
    daily_cap_override: null,
    rate_per_second_override: null,
    credentials_configured: true,
    credential_hint: { username: "support@example.com" },
    last_verified_at: "2026-11-18T10:00:00Z",
    created_at: "2026-11-18T09:00:00Z",
    updated_at: null,
};

beforeEach(() => {
    vi.clearAllMocks();
    auth.loading = false;
    vi.mocked(listEmailSendersApiV1EmailSendersGet).mockResolvedValue({
        data: [sender],
    } as never);
    vi.mocked(verifyEmailSenderApiV1EmailSendersSenderUuidVerifyPost).mockResolvedValue({
        data: {
            status: "verified",
            last_verified_at: "2026-11-18T10:00:00Z",
            sandbox_mode: false,
            send_quota: null,
            message: "Sender verified.",
        },
    } as never);
    vi.mocked(testSendEmailApiV1EmailSendersSenderUuidTestSendPost).mockResolvedValue({
        data: { provider_message_id: "id-1", message: "accepted" },
    } as never);
});

describe("email senders page", () => {
    it("shows providers, limits and the SMTP bounce warning", async () => {
        render(<EmailSendersPage />);
        expect(await screen.findByRole("heading", { name: "Support" })).toBeDefined();
        expect(screen.getByText("SMTP")).toBeDefined();
        expect(screen.getByText("Verified")).toBeDefined();
        expect(screen.getByText(/100\/day/)).toBeDefined();
        expect(screen.getByText(/do not report bounces/i)).toBeDefined();
        expect(listEmailSendersApiV1EmailSendersGet).toHaveBeenCalledWith({
            headers: { Authorization: "Bearer token" },
        });
    });

    it("marks the SES provider as untested", async () => {
        vi.mocked(listEmailSendersApiV1EmailSendersGet).mockResolvedValue({
            data: [
                sender,
                {
                    ...sender,
                    uuid: "sender-ses",
                    name: "SES mailbox",
                    provider: "ses",
                    is_default: false,
                },
            ],
        } as never);

        render(<EmailSendersPage />);

        expect(await screen.findByText("Amazon SES (untested)")).toBeDefined();
    });

    it("verifies a sender and refreshes the list", async () => {
        render(<EmailSendersPage />);
        await screen.findByRole("heading", { name: "Support" });
        fireEvent.click(screen.getByRole("button", { name: /Verify/ }));
        await waitFor(() => {
            expect(verifyEmailSenderApiV1EmailSendersSenderUuidVerifyPost).toHaveBeenCalledWith({
                headers: { Authorization: "Bearer token" },
                path: { sender_uuid: "sender-1" },
            });
        });
        await waitFor(() => {
            expect(listEmailSendersApiV1EmailSendersGet).toHaveBeenCalledTimes(2);
        });
    });

    it("sends a test message to the entered address", async () => {
        render(<EmailSendersPage />);
        await screen.findByRole("heading", { name: "Support" });
        fireEvent.click(screen.getByRole("button", { name: /Send test/ }));
        const dialog = screen.getByRole("dialog");
        fireEvent.change(within(dialog).getByLabelText("Send to *"), {
            target: { value: "reviewer@example.net" },
        });
        fireEvent.click(within(dialog).getByRole("button", { name: "Send test" }));
        await waitFor(() => {
            expect(testSendEmailApiV1EmailSendersSenderUuidTestSendPost).toHaveBeenCalledWith({
                headers: { Authorization: "Bearer token" },
                path: { sender_uuid: "sender-1" },
                body: { to_email: "reviewer@example.net" },
            });
        });
    });

    it("fills the Hostinger preset without asking for a host", async () => {
        render(<EmailSendersPage />);
        fireEvent.click(await screen.findByRole("button", { name: "Add sender" }));
        const dialog = screen.getByRole("dialog");
        fireEvent.click(within(dialog).getByRole("button", { name: "Use Hostinger preset" }));
        expect((within(dialog).getByLabelText("Host *") as HTMLInputElement).value).toBe(
            "smtp.hostinger.com"
        );
        expect((within(dialog).getByLabelText("Port *") as HTMLInputElement).value).toBe("465");
        expect(within(dialog).getByText(/does not report bounces/i)).toBeDefined();
    });
});
