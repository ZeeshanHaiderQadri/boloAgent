import { describe, expect, it } from "vitest";

import type { CopilotActivityEvent } from "@/client/types.gen";

import { deriveTaskView } from "./taskView";

function event(
  patch: Partial<CopilotActivityEvent> & Pick<CopilotActivityEvent, "kind">,
): CopilotActivityEvent {
  return {
    id: 1,
    conversation_id: 7,
    turn_id: "turn-1",
    created_at: "2026-11-20T10:05:00Z",
    ...patch,
  };
}

describe("deriveTaskView", () => {
  it("opens the overview for business_overview", () => {
    const view = deriveTaskView([
      event({ kind: "tool_finished", tool: "business_overview", status: "executed" }),
    ]);
    expect(view?.path).toBe("/overview");
  });

  it("follows a lead search to its live search page", () => {
    const view = deriveTaskView([
      event({
        kind: "tool_finished",
        tool: "start_lead_search",
        status: "executed",
        result: { search_id: 12, name: "Dentists in Austin" },
      }),
    ]);
    expect(view).toMatchObject({ path: "/leads/searches/12" });
  });

  it("opens the email campaign detail after a launch", () => {
    const view = deriveTaskView([
      event({
        kind: "tool_finished",
        tool: "launch_email_campaign",
        status: "executed",
        result: { campaign_uuid: "spring-2026" },
      }),
    ]);
    expect(view?.path).toBe("/email/campaigns/spring-2026");
  });

  it("opens the voice campaign detail after a pause", () => {
    const view = deriveTaskView([
      event({
        kind: "tool_finished",
        tool: "pause_voice_campaign",
        status: "executed",
        result: { campaign_id: 5 },
      }),
    ]);
    expect(view?.path).toBe("/campaigns/5");
  });

  it("switches to the page while a tool is still running", () => {
    const view = deriveTaskView([
      event({ id: 1, kind: "turn_started" }),
      event({ id: 2, kind: "tool_started", tool: "search_leads" }),
    ]);
    expect(view?.path).toBe("/leads/all");
  });

  it("opens the CRM with the agent's lead filters", () => {
    const view = deriveTaskView([
      event({
        kind: "tool_finished",
        tool: "search_leads",
        status: "executed",
        arguments: { query: "UK", country: "GB", has_phone: true },
        result: { total: 2, leads: [] },
      }),
    ]);
    expect(view?.path).toBe("/leads/all?query=UK&country=GB&has_phone=1");
  });

  it("passes lead filters while the search is still running", () => {
    const view = deriveTaskView([
      event({
        kind: "tool_started",
        tool: "search_leads",
        arguments: { crm_status: "new", city: "London" },
      }),
    ]);
    expect(view?.path).toBe("/leads/all?status=new&city=London");
  });

  it("ignores declined tools and unknown pages", () => {
    const view = deriveTaskView([
      event({ id: 1, kind: "tool_finished", tool: "list_email_campaigns", status: "executed" }),
      event({ id: 2, kind: "tool_finished", tool: "launch_email_campaign", status: "declined" }),
      event({ id: 3, kind: "tool_finished", tool: "web_search", status: "executed" }),
    ]);
    expect(view?.path).toBe("/email/campaigns");
    expect(deriveTaskView([event({ kind: "tool_finished", tool: "web_fetch" })])).toBeNull();
  });

  it.each([
    ["list_email_templates", "/email/templates"],
    ["list_email_senders", "/email/senders"],
    ["list_email_campaigns", "/email/campaigns"],
    ["preview_email_audience", "/email/campaigns"],
    ["search_leads", "/leads/all"],
    ["tag_leads", "/leads/all"],
    ["list_lead_searches", "/leads"],
    ["list_voice_campaigns", "/campaigns"],
    ["voice_campaign_status", "/campaigns"],
    ["list_whatsapp_accounts", "/whatsapp/senders"],
    ["list_whatsapp_senders", "/whatsapp/senders"],
    ["list_whatsapp_templates", "/whatsapp/templates"],
    ["list_whatsapp_click_templates", "/whatsapp/templates"],
    ["whatsapp_lead_links", "/whatsapp/templates"],
    ["preview_whatsapp_message", "/whatsapp/templates"],
    ["list_recent_calls", "/recordings"],
    ["list_workspace_files", "/files"],
    ["read_document", "/files"],
    ["search_knowledge_base", "/files"],
  ])("maps %s to %s", (tool, path) => {
    const view = deriveTaskView([
      event({ kind: "tool_finished", tool, status: "executed", result: {} }),
    ]);
    expect(view?.path).toBe(path);
  });
});
