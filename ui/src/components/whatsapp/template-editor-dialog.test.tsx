import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
    createWhatsappTemplateApiV1WhatsappTemplatesPost,
    getWhatsappTemplateApiV1WhatsappTemplatesTemplateUuidGet,
} from "@/client";

import { WhatsAppTemplateDialog } from "./template-editor-dialog";

const { auth, toast } = vi.hoisted(() => ({
    auth: { getAccessToken: vi.fn(async () => "token") },
    toast: { success: vi.fn(), error: vi.fn() },
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => auth }));
vi.mock("sonner", () => ({ toast }));
vi.mock("@/client", () => ({
    createWhatsappTemplateApiV1WhatsappTemplatesPost: vi.fn(),
    updateWhatsappTemplateApiV1WhatsappTemplatesTemplateUuidPatch: vi.fn(),
    getWhatsappTemplateApiV1WhatsappTemplatesTemplateUuidGet: vi.fn(),
}));

beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(createWhatsappTemplateApiV1WhatsappTemplatesPost).mockResolvedValue({
        data: {
            uuid: "t1",
            name: "First contact",
            language: "en",
            category: "marketing",
            body: "Hi {{business_name}}",
            variables: ["business_name"],
            created_at: null,
            updated_at: null,
        },
    } as never);
});

describe("WhatsAppTemplateDialog", () => {
    it("previews sample values and creates the template", async () => {
        const onSaved = vi.fn();
        const onOpenChange = vi.fn();
        render(
            <WhatsAppTemplateDialog
                open
                templateUuid={null}
                onOpenChange={onOpenChange}
                onSaved={onSaved}
            />,
        );

        const dialog = screen.getByRole("dialog");
        fireEvent.change(within(dialog).getByLabelText("Name *"), {
            target: { value: "First contact" },
        });
        fireEvent.change(within(dialog).getByLabelText("Message *"), {
            target: { value: "Hi {{business_name}}, about {{category}} in {{city}}." },
        });

        expect(
            within(dialog).getByText("Hi Apex Labs, about Marketing agency in Austin."),
        ).toBeTruthy();

        fireEvent.click(within(dialog).getByRole("button", { name: "Create template" }));

        await waitFor(() => {
            expect(createWhatsappTemplateApiV1WhatsappTemplatesPost).toHaveBeenCalledWith({
                headers: { Authorization: "Bearer token" },
                body: {
                    name: "First contact",
                    body: "Hi {{business_name}}, about {{category}} in {{city}}.",
                    language: "en",
                    category: "marketing",
                },
            });
        });
        await waitFor(() => expect(onSaved).toHaveBeenCalled());
        await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
        expect(toast.success).toHaveBeenCalledWith("Template created");
    });

    it("loads an existing template for editing", async () => {
        vi.mocked(getWhatsappTemplateApiV1WhatsappTemplatesTemplateUuidGet).mockResolvedValue({
            data: {
                uuid: "t1",
                name: "Follow up",
                language: "en",
                category: "utility",
                body: "Hello {{city}}",
                variables: ["city"],
                created_at: null,
                updated_at: null,
            },
        } as never);

        render(
            <WhatsAppTemplateDialog
                open
                templateUuid="t1"
                onOpenChange={vi.fn()}
                onSaved={vi.fn()}
            />,
        );

        await waitFor(() =>
            expect(
                (screen.getByLabelText("Name *") as HTMLInputElement).value,
            ).toBe("Follow up"),
        );
        expect(
            (screen.getByLabelText("Message *") as HTMLTextAreaElement).value,
        ).toBe("Hello {{city}}");
    });

    it("caps the message at 1,000 characters", () => {
        render(
            <WhatsAppTemplateDialog
                open
                templateUuid={null}
                onOpenChange={vi.fn()}
                onSaved={vi.fn()}
            />,
        );

        const message = screen.getByLabelText("Message *") as HTMLTextAreaElement;
        expect(message.maxLength).toBe(1000);

        fireEvent.change(screen.getByLabelText("Name *"), {
            target: { value: "Long one" },
        });
        fireEvent.change(message, { target: { value: "x".repeat(1001) } });
        fireEvent.click(screen.getByRole("button", { name: "Create template" }));

        expect(
            screen.getByText(/Messages are limited to 1,000 characters/),
        ).toBeTruthy();
        expect(createWhatsappTemplateApiV1WhatsappTemplatesPost).not.toHaveBeenCalled();
    });

    it("flags placeholders the backend will reject", () => {
        render(
            <WhatsAppTemplateDialog
                open
                templateUuid={null}
                onOpenChange={vi.fn()}
                onSaved={vi.fn()}
            />,
        );

        fireEvent.change(screen.getByLabelText("Message *"), {
            target: { value: "Hello {{first_name}}" },
        });

        expect(screen.getByText(/Unknown placeholder\(s\)/)).toBeTruthy();
    });
});
