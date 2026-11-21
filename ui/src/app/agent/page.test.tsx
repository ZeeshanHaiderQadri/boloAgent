import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AgentPage from "./page";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  getConversation: vi.fn(),
  send: vi.fn(),
  decide: vi.fn(),
  voiceStatus: vi.fn(),
  turnCredentials: vi.fn(),
  auth: { user: { id: 1 } as { id: number } | null, loading: false },
}));

vi.mock("@/client/sdk.gen", () => ({
  listConversationsApiV1CopilotConversationsGet: mocks.list,
  createConversationApiV1CopilotConversationsPost: mocks.create,
  getConversationApiV1CopilotConversationsConversationIdGet: mocks.getConversation,
  sendMessageApiV1CopilotConversationsConversationIdMessagesPost: mocks.send,
  decideApprovalApiV1CopilotConversationsConversationIdApprovalsPost: mocks.decide,
  voiceStatusApiV1CopilotVoiceGet: mocks.voiceStatus,
  getTurnCredentialsApiV1TurnCredentialsGet: mocks.turnCredentials,
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

beforeEach(() => {
  vi.clearAllMocks();
  mocks.auth.user = { id: 1 };
  mocks.auth.loading = false;
  mocks.list.mockResolvedValue({ data: [conversation] });
  mocks.getConversation.mockResolvedValue({ data: snapshot });
  mocks.voiceStatus.mockResolvedValue({ data: { ready: true } });
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
});
