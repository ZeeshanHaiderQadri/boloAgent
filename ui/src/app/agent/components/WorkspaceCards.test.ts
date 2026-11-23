import { describe, expect, it } from "vitest";

import type { CopilotActivityEvent } from "@/client/types.gen";

import { deriveArtifacts } from "./WorkspaceCards";

let nextId = 1;

function event(partial: Partial<CopilotActivityEvent>): CopilotActivityEvent {
  return {
    id: nextId++,
    conversation_id: 1,
    turn_id: "turn-1",
    kind: "tool_finished",
    created_at: "2026-11-20T10:00:00Z",
    ...partial,
  };
}

describe("deriveArtifacts", () => {
  it("turns a started search into a tracker with its name", () => {
    const artifacts = deriveArtifacts([
      event({
        tool: "start_lead_search",
        status: "executed",
        result: { search_id: 5, name: "dentists in Austin" },
      }),
    ]);

    expect(artifacts).toEqual([
      {
        kind: "lead_search",
        key: "search:5",
        searchId: 5,
        name: "dentists in Austin",
      },
    ]);
  });

  it("keeps the newest occurrence of a repeated search", () => {
    const artifacts = deriveArtifacts([
      event({ tool: "start_lead_search", result: { search_id: 5, name: "first" } }),
      event({ tool: "lead_search_status", result: { search_id: 5, name: "second" } }),
    ]);

    expect(artifacts).toHaveLength(1);
    expect(artifacts[0]).toMatchObject({ kind: "lead_search", name: "second" });
  });

  it("maps results to their dedicated cards", () => {
    const artifacts = deriveArtifacts([
      event({ tool: "search_leads", result: { leads: [], total: 0 } }),
      event({ tool: "business_overview", result: { leads: { total: 3 } } }),
    ]);

    expect(artifacts.map((artifact) => artifact.kind)).toEqual([
      "overview",
      "leads",
    ]);
  });

  it("falls back to a generic card and carries failures", () => {
    const artifacts = deriveArtifacts([
      event({
        tool: "launch_email_campaign",
        status: "error",
        error: "Sender is not verified.",
      }),
    ]);

    expect(artifacts).toEqual([
      {
        kind: "generic",
        key: "tool:launch_email_campaign",
        tool: "launch_email_campaign",
        result: undefined,
        error: "Sender is not verified.",
      },
    ]);
  });

  it("ignores non-tool events and caps the panel at four cards", () => {
    const artifacts = deriveArtifacts([
      event({ kind: "turn_started", tool: null }),
      event({ tool: "tag_leads", result: { tagged: 1 } }),
      event({ tool: "search_leads", result: { leads: [] } }),
      event({ tool: "business_overview", result: { leads: {} } }),
      event({ tool: "start_lead_search", result: { search_id: 9 } }),
      event({ tool: "read_document", result: { chars: 10 } }),
    ]);

    expect(artifacts).toHaveLength(4);
    expect(artifacts[0]).toMatchObject({ kind: "generic", tool: "read_document" });
  });
});
