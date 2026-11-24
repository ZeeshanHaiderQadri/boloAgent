import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AgentPage from "./page";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  getConversation: vi.fn(),
  send: vi.fn(),
  remove: vi.fn(),
  decide: vi.fn(),
  regenerate: vi.fn(),
  uploadUrl: vi.fn(),
  processDocument: vi.fn(),
  getDocument: vi.fn(),
  activity: vi.fn(),
  voiceStatus: vi.fn(),
  turnCredentials: vi.fn(),
  getAvatar: vi.fn(),
  updateAvatar: vi.fn(),
  avatarVoices: vi.fn(),
  auth: { user: { id: 1 } as { id: number } | null, loading: false },
}));

vi.mock("@/client/sdk.gen", () => ({
  listConversationsApiV1CopilotConversationsGet: mocks.list,
  createConversationApiV1CopilotConversationsPost: mocks.create,
  getConversationApiV1CopilotConversationsConversationIdGet: mocks.getConversation,
  sendMessageApiV1CopilotConversationsConversationIdMessagesPost: mocks.send,
  deleteConversationApiV1CopilotConversationsConversationIdDelete: mocks.remove,
  decideApprovalApiV1CopilotConversationsConversationIdApprovalsPost: mocks.decide,
  regenerateReplyApiV1CopilotConversationsConversationIdRegeneratePost:
    mocks.regenerate,
  getUploadUrlApiV1KnowledgeBaseUploadUrlPost: mocks.uploadUrl,
  processDocumentApiV1KnowledgeBaseProcessDocumentPost: mocks.processDocument,
  getDocumentApiV1KnowledgeBaseDocumentsDocumentUuidGet: mocks.getDocument,
  getActivityApiV1CopilotConversationsConversationIdActivityGet: mocks.activity,
  voiceStatusApiV1CopilotVoiceGet: mocks.voiceStatus,
  getTurnCredentialsApiV1TurnCredentialsGet: mocks.turnCredentials,
  getUserAvatarApiV1UserAvatarGet: mocks.getAvatar,
  updateUserAvatarApiV1UserAvatarPut: mocks.updateAvatar,
  getUserAvatarVoicesApiV1UserAvatarVoicesGet: mocks.avatarVoices,
}));

// The 3D kits need WebGL and a live GLB; the page tests only care about the
// avatar's chrome, so the canvas is stubbed out.
vi.mock("@/components/agent/avatar/AgentAvatar", () => ({
  AgentAvatar: () => <div data-testid="avatar-canvas" />,
  avatarName: (character: string) =>
    character === "male" ? "Hacker" : "Bibi",
  avatarPreview: () => "/avatars/preview.png",
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    ...mocks.auth,
    getAccessToken: async () => "token",
    redirectToLogin: vi.fn(),
  }),
}));

const conversation = {
  id: 7,
  title: "Q3 pipeline",
  created_at: "2026-11-20T10:00:00Z",
  updated_at: "2026-11-20T10:05:00Z",
};

const snapshot = {
  conversation_id: 7,
  title: "Q3 pipeline",
  status: "idle" as const,
  messages: [
    { role: "user" as const, content: "How is the pipeline?" },
    { role: "assistant" as const, content: "Here is your pipeline summary." },
  ],
  pending_approvals: [],
};

const avatarProfile = {
  character: "female" as const,
  setup_completed: true,
  enabled: false,
  appearance: { glasses: true, hood: false, hud: false },
  action: "idle" as const,
  pet_state: "expanded" as const,
};

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  mocks.auth.user = { id: 1 };
  mocks.auth.loading = false;
  mocks.list.mockResolvedValue({ data: [conversation] });
  mocks.getConversation.mockResolvedValue({ data: snapshot });
  mocks.activity.mockResolvedValue({ data: { events: [] } });
  mocks.voiceStatus.mockResolvedValue({ data: { ready: true } });
  mocks.getAvatar.mockResolvedValue({ data: { ...avatarProfile } });
  mocks.updateAvatar.mockImplementation(async ({ body }) => ({
    data: { ...avatarProfile, ...body },
  }));
  mocks.avatarVoices.mockResolvedValue({
    data: { configured: false, voices: [] },
  });
});

describe("Bolo Agent page", () => {
  it("opens the latest conversation and shows the transcript", async () => {
    render(<AgentPage />);

    expect(await screen.findByText("Here is your pipeline summary.")).toBeTruthy();
    expect(screen.getByText("How is the pipeline?")).toBeTruthy();
    expect(screen.getByText("Ready")).toBeTruthy();
    expect(mocks.getConversation).toHaveBeenCalledWith(
      expect.objectContaining({ path: { conversation_id: 7 } }),
    );
  });

  it("turns a pending write into an approval card and posts the decision", async () => {
    mocks.getConversation.mockResolvedValue({
      data: {
        ...snapshot,
        status: "waiting_for_approval",
        pending_approvals: [
          {
            id: "approval-1",
            kind: "tool_approval",
            calls: [
              {
                tool: "launch_email_campaign",
                risk: "write",
                arguments: { campaign_uuid: "spring-2026" },
              },
            ],
          },
        ],
      },
    });
    mocks.decide.mockResolvedValue({
      data: {
        ...snapshot,
        messages: [
          ...snapshot.messages,
          { role: "assistant", content: "Campaign launched to 42 recipients." },
        ],
      },
    });

    render(<AgentPage />);

    expect(await screen.findByText("Approval needed")).toBeTruthy();
    expect(screen.getByText("Launch email campaign")).toBeTruthy();
    expect(screen.getByText(/spring-2026/)).toBeTruthy();
    expect(screen.getByText("Awaiting approval")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /approve/i }));

    await waitFor(() =>
      expect(mocks.decide).toHaveBeenCalledWith(
        expect.objectContaining({
          path: { conversation_id: 7 },
          body: expect.objectContaining({
            approval_id: "approval-1",
            decision: "approve",
          }),
        }),
      ),
    );
    expect(
      await screen.findByText("Campaign launched to 42 recipients."),
    ).toBeTruthy();
  });

  it("starts a conversation on the first message when none exist", async () => {
    mocks.list.mockResolvedValue({ data: [] });
    mocks.create.mockResolvedValue({ data: conversation });
    mocks.send.mockResolvedValue({
      data: {
        ...snapshot,
        messages: [
          { role: "user", content: "Find roofers in Dallas" },
          { role: "assistant", content: "I can start that lead search." },
        ],
      },
    });

    render(<AgentPage />);
    expect(await screen.findByText("What should we work on?")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Message Bolo Agent"), {
      target: { value: "Find roofers in Dallas" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(mocks.create).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(mocks.send).toHaveBeenCalledWith(
        expect.objectContaining({
          path: { conversation_id: 7 },
          body: expect.objectContaining({
            message: "Find roofers in Dallas",
            client_request_id: expect.any(String),
          }),
        }),
      ),
    );
    expect(
      await screen.findByText("I can start that lead search."),
    ).toBeTruthy();
  });

  it("surfaces API refusals instead of failing silently", async () => {
    mocks.list.mockResolvedValue({ data: [] });
    mocks.create.mockResolvedValue({ data: conversation });
    mocks.send.mockResolvedValue({
      error: { detail: "Connect your own model before chatting." },
    });

    render(<AgentPage />);
    await screen.findByText("What should we work on?");

    fireEvent.change(screen.getByLabelText("Message Bolo Agent"), {
      target: { value: "Hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    expect(
      await screen.findByText("Connect your own model before chatting."),
    ).toBeTruthy();
  });

  it("enables the call button once the workspace has voice services", async () => {
    render(<AgentPage />);

    const button = await screen.findByRole("button", { name: "Start voice call" });
    await waitFor(() =>
      expect((button as HTMLButtonElement).disabled).toBe(false),
    );
  });

  it("explains why calling is unavailable and disables the button", async () => {
    mocks.voiceStatus.mockResolvedValue({
      data: {
        ready: false,
        message: "Connect your own LLM, STT and TTS to enable voice.",
      },
    });

    render(<AgentPage />);

    expect(
      await screen.findByText("Connect your own LLM, STT and TTS to enable voice."),
    ).toBeTruthy();
    expect(
      (screen.getByRole("button", {
        name: "Start voice call",
      }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("deletes a conversation after confirmation", async () => {
    mocks.remove.mockResolvedValue({});
    render(<AgentPage />);
    await screen.findByText("Here is your pipeline summary.");

    fireEvent.click(
      screen.getByRole("button", { name: "Delete conversation Q3 pipeline" }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Delete conversation" }),
    );

    await waitFor(() =>
      expect(mocks.remove).toHaveBeenCalledWith(
        expect.objectContaining({ path: { conversation_id: 7 } }),
      ),
    );
    expect(await screen.findByText("No conversations yet. Send the first message to start one.")).toBeTruthy();
  });

  it("attaches a file and sends its document id with the next message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true }),
    );
    mocks.uploadUrl.mockResolvedValue({
      data: {
        upload_url: "https://storage.test/upload",
        document_uuid: "doc-1",
        s3_key: "org/files/notes.txt",
      },
    });
    mocks.processDocument.mockResolvedValue({ data: { status: "queued" } });
    mocks.getDocument.mockResolvedValue({
      data: { processing_status: "completed" },
    });
    mocks.send.mockResolvedValue({ data: snapshot });

    render(<AgentPage />);
    await screen.findByText("Here is your pipeline summary.");

    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(input, {
      target: {
        files: [new File(["hello"], "notes.txt", { type: "text/plain" })],
      },
    });

    expect(await screen.findByText("notes.txt")).toBeTruthy();
    await waitFor(() =>
      expect(mocks.processDocument).toHaveBeenCalledWith(
        expect.objectContaining({
          body: expect.objectContaining({
            document_uuid: "doc-1",
            retrieval_mode: "full_document",
          }),
        }),
      ),
    );

    fireEvent.change(screen.getByLabelText("Message Bolo Agent"), {
      target: { value: "Summarize the attached file" },
    });
    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "Send message" }) as HTMLButtonElement)
          .disabled,
      ).toBe(false),
    );
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() =>
      expect(mocks.send).toHaveBeenCalledWith(
        expect.objectContaining({
          body: expect.objectContaining({
            message: "Summarize the attached file",
            attachments: ["doc-1"],
          }),
        }),
      ),
    );
    vi.unstubAllGlobals();
  });

  it("regenerates the latest assistant reply in place", async () => {
    mocks.regenerate.mockResolvedValue({
      data: {
        ...snapshot,
        messages: [
          snapshot.messages[0],
          { role: "assistant", content: "A fresh pipeline summary." },
        ],
      },
    });
    render(<AgentPage />);
    await screen.findByText("Here is your pipeline summary.");

    fireEvent.click(screen.getByRole("button", { name: "Regenerate reply" }));

    await waitFor(() =>
      expect(mocks.regenerate).toHaveBeenCalledWith(
        expect.objectContaining({
          path: { conversation_id: 7 },
          body: expect.objectContaining({
            client_request_id: expect.any(String),
          }),
        }),
      ),
    );
    expect(await screen.findByText("A fresh pipeline summary.")).toBeTruthy();
    expect(screen.queryByText("Here is your pipeline summary.")).toBeNull();
  });

  it("records a thumbs rating on an assistant reply", async () => {
    render(<AgentPage />);
    await screen.findByText("Here is your pipeline summary.");

    const down = screen.getByRole("button", { name: "Bad reply" });
    fireEvent.click(down);
    expect(down.getAttribute("aria-pressed")).toBe("true");

    fireEvent.click(down);
    expect(down.getAttribute("aria-pressed")).toBe("false");
  });

  it("copies a reply to the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    render(<AgentPage />);
    await screen.findByText("Here is your pipeline summary.");

    const copyButtons = screen.getAllByRole("button", { name: "Copy message" });
    fireEvent.click(copyButtons[copyButtons.length - 1]);

    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith("Here is your pipeline summary."),
    );
    expect(await screen.findByRole("button", { name: "Copied" })).toBeTruthy();
  });

  it("mirrors live task activity in the workspace pane", async () => {
    mocks.activity.mockResolvedValue({
      data: {
        events: [
          {
            id: 1,
            conversation_id: 7,
            turn_id: "turn-1",
            kind: "turn_started",
            created_at: "2026-11-20T10:05:00Z",
          },
          {
            id: 2,
            conversation_id: 7,
            turn_id: "turn-1",
            kind: "tool_finished",
            tool: "business_overview",
            status: "executed",
            duration_ms: 240,
            result: {
              leads: { total: 42, with_email: 30, new: 5 },
              email_campaigns: { sent: 120 },
            },
            created_at: "2026-11-20T10:05:01Z",
          },
          {
            id: 3,
            conversation_id: 7,
            turn_id: "turn-1",
            kind: "turn_finished",
            status: "idle",
            created_at: "2026-11-20T10:05:02Z",
          },
        ],
      },
    });

    render(<AgentPage />);
    await screen.findByText("Here is your pipeline summary.");

    expect(await screen.findByText("Live workspace")).toBeTruthy();
    expect(screen.getByText("All caught up")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Overview" })).toBeTruthy();

    const frame = screen.getByTitle("Workspace page: Overview");
    expect(frame.getAttribute("src")).toBe("/overview?embed=1");

    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(await screen.findByText("Emails sent")).toBeTruthy();
    expect(
      screen.getByRole("tab", { name: "Overview" }).getAttribute("aria-selected"),
    ).toBe("false");
    expect(mocks.activity).toHaveBeenCalledWith(
      expect.objectContaining({
        path: { conversation_id: 7 },
        query: { after_id: 0, limit: 500 },
      }),
    );
  });

  it("embeds the page a tool touched and links out to the full route", async () => {
    mocks.activity.mockResolvedValue({
      data: {
        events: [
          {
            id: 1,
            conversation_id: 7,
            turn_id: "turn-1",
            kind: "tool_finished",
            tool: "list_email_campaigns",
            status: "executed",
            result: { total: 3, campaigns: [] },
            created_at: "2026-11-20T10:05:01Z",
          },
        ],
      },
    });

    render(<AgentPage />);
    await screen.findByText("Here is your pipeline summary.");

    const frame = await screen.findByTitle("Workspace page: Email campaigns");
    expect(frame.getAttribute("src")).toBe("/email/campaigns?embed=1");
    expect(
      screen
        .getByRole("link", { name: "Open Email campaigns in a new tab" })
        .getAttribute("href"),
    ).toBe("/email/campaigns");
  });

  it("collapses the conversation list and brings it back", async () => {
    render(<AgentPage />);
    await screen.findByText("Here is your pipeline summary.");

    fireEvent.click(screen.getByRole("button", { name: "Hide conversations" }));
    expect(screen.queryByText("Conversations")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Show conversations" }));
    expect(await screen.findByText("Conversations")).toBeTruthy();
  });

  it("folds the conversation list away while a turn is running", async () => {
    mocks.activity.mockResolvedValue({
      data: {
        events: [
          {
            id: 1,
            conversation_id: 7,
            turn_id: "turn-1",
            kind: "turn_started",
            created_at: "2026-11-20T10:05:00Z",
          },
        ],
      },
    });

    render(<AgentPage />);
    await screen.findByText("Here is your pipeline summary.");

    await waitFor(() => expect(screen.queryByText("Conversations")).toBeNull());
    expect(
      screen.getByRole("button", { name: "Show conversations" }),
    ).toBeTruthy();
  });

  it("stops an in-flight turn when the user interrupts it", async () => {
    mocks.send.mockImplementation(
      ({ signal }: { signal?: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        }),
    );
    render(<AgentPage />);
    await screen.findByText("Here is your pipeline summary.");

    fireEvent.change(screen.getByLabelText("Message Bolo Agent"), {
      target: { value: "Think for a long time" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByText("Thinking…")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Stop response" }));

    expect(await screen.findByText("Response stopped.")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Send message" }),
    ).toBeTruthy();
  });

  it("asks first-time users to pick an avatar and saves the choice", async () => {
    mocks.getAvatar.mockResolvedValue({
      data: {
        ...avatarProfile,
        setup_completed: false,
        appearance: { glasses: true, hood: true, hud: true },
      },
    });
    mocks.avatarVoices.mockImplementation(
      async ({ query }: { query: { character: string } }) =>
        query.character === "male"
          ? {
              data: {
                configured: true,
                provider: "cartesia",
                voices: [{ voice_id: "male-voice", name: "Ranger" }],
              },
            }
          : {
              data: {
                configured: true,
                provider: "cartesia",
                voices: [{ voice_id: "female-voice", name: "Pipa" }],
              },
            },
    );

    render(<AgentPage />);

    expect(await screen.findByText("Choose your agent's look")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^Hacker/ }));

    await waitFor(() =>
      expect(
        (
          screen.getByRole("combobox", {
            name: "Avatar voice",
          }) as HTMLSelectElement
        ).value,
      ).toBe("male-voice"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Use Hacker" }));

    await waitFor(() =>
      expect(mocks.updateAvatar).toHaveBeenCalledWith(
        expect.objectContaining({
          body: expect.objectContaining({
            character: "male",
            setup_completed: true,
            enabled: true,
            pet_state: "expanded",
            appearance: { glasses: true, hood: true, hud: true },
            action: "idle",
            voice: { provider: "cartesia", voice_id: "male-voice" },
          }),
        }),
      ),
    );
    await waitFor(() =>
      expect(screen.queryByText("Choose your agent's look")).toBeNull(),
    );
  });

  it("remembers a skipped avatar setup without showing the pet", async () => {
    mocks.getAvatar.mockResolvedValue({
      data: { ...avatarProfile, setup_completed: false },
    });

    render(<AgentPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Not now" }));

    await waitFor(() =>
      expect(mocks.updateAvatar).toHaveBeenCalledWith(
        expect.objectContaining({
          body: { setup_completed: true, enabled: false },
        }),
      ),
    );
    expect(screen.queryByText("Choose your agent's look")).toBeNull();
  });
});
