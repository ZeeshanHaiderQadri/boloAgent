import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
    acceptWhatsappDisclaimerApiV1WhatsappDisclaimerPost,
    getWhatsappDisclaimerApiV1WhatsappDisclaimerGet,
} from "@/client";

import { WhatsAppDisclaimerDialog } from "./disclaimer-dialog";

const { auth } = vi.hoisted(() => ({
    auth: { getAccessToken: vi.fn(async () => "token") },
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => auth }));
vi.mock("@/client", () => ({
    getWhatsappDisclaimerApiV1WhatsappDisclaimerGet: vi.fn(),
    acceptWhatsappDisclaimerApiV1WhatsappDisclaimerPost: vi.fn(),
}));

const TEXT =
    "WhatsApp can ban or restrict a number that sends spam or messages people did not ask for.";

beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getWhatsappDisclaimerApiV1WhatsappDisclaimerGet).mockResolvedValue({
        data: { version: "2026-11-20", text: TEXT, accepted: false, accepted_at: null },
    } as never);
    vi.mocked(acceptWhatsappDisclaimerApiV1WhatsappDisclaimerPost).mockResolvedValue({
        data: {
            version: "2026-11-20",
            text: TEXT,
            accepted: true,
            accepted_at: "2026-11-20T10:00:00Z",
        },
    } as never);
});

describe("WhatsAppDisclaimerDialog", () => {
    it("shows the server notice and requires the checkbox before accepting", async () => {
        const onAccepted = vi.fn();
        const onOpenChange = vi.fn();
        render(
            <WhatsAppDisclaimerDialog
                open
                onOpenChange={onOpenChange}
                onAccepted={onAccepted}
            />
        );

        const dialog = screen.getByRole("dialog");
        await waitFor(() => expect(within(dialog).getByText(TEXT)).toBeTruthy());

        const submit = within(dialog).getByRole("button", { name: "I understand" });
        expect((submit as HTMLButtonElement).disabled).toBe(true);
        expect(acceptWhatsappDisclaimerApiV1WhatsappDisclaimerPost).not.toHaveBeenCalled();

        fireEvent.click(
            within(dialog).getByRole("checkbox", {
                name: "Acknowledge the WhatsApp notice",
            })
        );
        fireEvent.click(submit);

        await waitFor(() => {
            expect(acceptWhatsappDisclaimerApiV1WhatsappDisclaimerPost).toHaveBeenCalledWith({
                headers: { Authorization: "Bearer token" },
            });
        });
        await waitFor(() =>
            expect(onAccepted).toHaveBeenCalledWith(
                expect.objectContaining({ accepted: true })
            )
        );
        await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    });

    it("keeps acceptance disabled when the notice cannot be loaded", async () => {
        vi.mocked(getWhatsappDisclaimerApiV1WhatsappDisclaimerGet).mockResolvedValue({
            error: { detail: "Not found" },
        } as never);
        render(<WhatsAppDisclaimerDialog open onOpenChange={vi.fn()} />);

        const dialog = screen.getByRole("dialog");
        await waitFor(() => expect(within(dialog).getByRole("alert")).toBeTruthy());
        expect(within(dialog).getByText("Not found")).toBeTruthy();
        expect(
            (
                within(dialog).getByRole("button", {
                    name: "I understand",
                }) as HTMLButtonElement
            ).disabled
        ).toBe(true);
        expect(within(dialog).getByRole("button", { name: "Retry" })).toBeTruthy();
    });
});
