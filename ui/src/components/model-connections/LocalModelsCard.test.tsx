import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { deleteLocalModel, downloadLocalModel, getLocalModelDownload, listLocalModels } from "@/client/sdk.gen";

import { LocalModelsCard } from "./LocalModelsCard";

vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: "user" }, loading: false }) }));
vi.mock("@/client/sdk.gen", () => ({
    listLocalModels: vi.fn(),
    downloadLocalModel: vi.fn(),
    getLocalModelDownload: vi.fn(),
    deleteLocalModel: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const response = {
    servers: ["ollama", "speaches"] as const,
    models: [
        {
            family: "ollama" as const,
            id: "lfm3:1.7b-q4_K_M",
            kind: "llm" as const,
            title: "LFM 3 · fast replies",
            description: "Small chat model for quick answers on a laptop.",
            installed: true,
            size_bytes: 1099638680,
            downloading: false,
            progress: null,
        },
        {
            family: "speaches" as const,
            id: "Systran/faster-whisper-large-v3",
            kind: "stt" as const,
            title: "Whisper large v3 · accurate listening",
            description: "More accurate than base, but heavy on a laptop CPU.",
            installed: false,
            downloading: false,
            progress: null,
        },
    ],
};

beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listLocalModels).mockResolvedValue({ data: response } as never);
    vi.mocked(downloadLocalModel).mockResolvedValue({ data: { job_id: "job-1", family: "speaches", model: "Systran/faster-whisper-large-v3" } } as never);
    vi.mocked(getLocalModelDownload).mockResolvedValue({ data: { status: "running", model: "Systran/faster-whisper-large-v3" } } as never);
    vi.mocked(deleteLocalModel).mockResolvedValue({ data: undefined } as never);
});

describe("local models card", () => {
    it("stays out of the way when this machine has no local model server", async () => {
        vi.mocked(listLocalModels).mockResolvedValue({ data: { servers: [], models: [] } } as never);
        render(<LocalModelsCard />);
        await waitFor(() => expect(vi.mocked(listLocalModels)).toHaveBeenCalled());
        expect(screen.queryByText("Models on this machine")).toBeNull();
    });

    it("lists installed models with their size and starts a download for the rest", async () => {
        render(<LocalModelsCard />);
        expect(await screen.findByText("Models on this machine")).toBeDefined();
        expect(screen.getByText("1.1 GB · Chat model on this machine")).toBeDefined();
        expect(screen.getByText("More accurate than base, but heavy on a laptop CPU.")).toBeDefined();
        fireEvent.click(screen.getByRole("button", { name: "Install" }));
        await waitFor(() => expect(vi.mocked(downloadLocalModel)).toHaveBeenCalledWith({ body: { family: "speaches", model: "Systran/faster-whisper-large-v3" } }));
        expect(vi.mocked(toast.success)).toHaveBeenCalledWith("Downloading Whisper large v3 · accurate listening");
    });

    it("asks before removing a model and reports the failure instead of pretending", async () => {
        vi.mocked(deleteLocalModel).mockResolvedValue({ error: { detail: "The local model server is not responding." } } as never);
        render(<LocalModelsCard />);
        fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
        const dialog = screen.getByRole("dialog", { name: "Remove LFM 3 · fast replies?" });
        fireEvent.click(within(dialog).getByRole("button", { name: "Remove model" }));
        await waitFor(() => expect(vi.mocked(deleteLocalModel)).toHaveBeenCalledWith({ path: { family: "ollama", model: "lfm3:1.7b-q4_K_M" } }));
        await waitFor(() => expect(vi.mocked(toast.error)).toHaveBeenCalledWith("The local model server is not responding."));
    });
});
