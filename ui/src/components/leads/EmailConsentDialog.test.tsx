import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
    bulkUpdateLeadEmailConsentApiV1LeadsEmailConsentBulkPost,
    updateLeadEmailConsentApiV1LeadsLeadIdEmailConsentPatch,
} from "@/client/sdk.gen";

import { EmailConsentDialog } from "./EmailConsentDialog";

vi.mock("@/client/sdk.gen", () => ({
    bulkUpdateLeadEmailConsentApiV1LeadsEmailConsentBulkPost: vi.fn(),
    updateLeadEmailConsentApiV1LeadsLeadIdEmailConsentPatch: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function renderDialog(overrides: Partial<Parameters<typeof EmailConsentDialog>[0]> = {}) {
    const onOpenChange = vi.fn();
    const onSaved = vi.fn();
    render(
        <EmailConsentDialog
            open
            leadIds={[42]}
            onOpenChange={onOpenChange}
            onSaved={onSaved}
            {...overrides}
        />,
    );
    return { onOpenChange, onSaved };
}

function choose(label: string) {
    fireEvent.keyDown(screen.getByLabelText("Decision"), { key: "ArrowDown" });
    fireEvent.click(screen.getByRole("option", { name: label }));
}

beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    Element.prototype.scrollIntoView = vi.fn();
    Element.prototype.hasPointerCapture = vi.fn(() => false);
    Element.prototype.releasePointerCapture = vi.fn();
    vi.mocked(updateLeadEmailConsentApiV1LeadsLeadIdEmailConsentPatch).mockResolvedValue({
        data: {},
    } as never);
    vi.mocked(bulkUpdateLeadEmailConsentApiV1LeadsEmailConsentBulkPost).mockResolvedValue({
        data: { matched: 2, changed: 1 },
    } as never);
});

describe("EmailConsentDialog", () => {
    it("shows the warning and refuses an opt-in without a written reason", async () => {
        renderDialog();

        expect(screen.getByText(/Only record consent you can prove/)).toBeTruthy();
        choose("Opted in");
        fireEvent.click(screen.getByRole("button", { name: "Save consent" }));

        expect(toast.error).toHaveBeenCalledWith(
            "Write down the proof of consent before opting someone in",
        );
        expect(
            updateLeadEmailConsentApiV1LeadsLeadIdEmailConsentPatch,
        ).not.toHaveBeenCalled();
    });

    it("records an opt-in with its reason for a single lead", async () => {
        const { onOpenChange, onSaved } = renderDialog();

        choose("Opted in");
        fireEvent.change(screen.getByLabelText("Why (required)"), {
            target: { value: "Signed up at the trade show booth." },
        });
        fireEvent.click(screen.getByRole("button", { name: "Save consent" }));

        await waitFor(() =>
            expect(
                updateLeadEmailConsentApiV1LeadsLeadIdEmailConsentPatch,
            ).toHaveBeenCalledWith({
                path: { lead_id: 42 },
                body: {
                    email_consent: "opted_in",
                    reason: "Signed up at the trade show booth.",
                },
            }),
        );
        expect(onOpenChange).toHaveBeenCalledWith(false);
        expect(onSaved).toHaveBeenCalled();
    });

    it("can opt out without a reason and bulk-updates the selection", async () => {
        renderDialog({ leadIds: [1, 2, 3] });

        choose("Opted out");
        fireEvent.click(screen.getByRole("button", { name: "Save consent" }));

        await waitFor(() =>
            expect(
                bulkUpdateLeadEmailConsentApiV1LeadsEmailConsentBulkPost,
            ).toHaveBeenCalledWith({
                body: {
                    lead_ids: [1, 2, 3],
                    email_consent: "opted_out",
                    reason: null,
                },
            }),
        );
        expect(
            updateLeadEmailConsentApiV1LeadsLeadIdEmailConsentPatch,
        ).not.toHaveBeenCalled();
        expect(toast.success).toHaveBeenCalledWith(
            "Email consent updated for 1 of 2 lead(s); the reason was saved for all",
        );
    });
});
