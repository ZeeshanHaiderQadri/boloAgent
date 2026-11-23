import type { CopilotActivityEvent } from "@/client/types.gen";

export type TaskView = {
  path: string;
  title: string;
  tool: string;
};

/** Dashboard route that mirrors what each agent tool touches, so the live
 * workspace can open the real page instead of a summary card. */
const TOOL_PAGES: Record<string, { path: string; title: string }> = {
  business_overview: { path: "/overview", title: "Overview" },
  search_leads: { path: "/leads/all", title: "Lead CRM" },
  tag_leads: { path: "/leads/all", title: "Lead CRM" },
  list_lead_searches: { path: "/leads", title: "Leads" },
  start_lead_search: { path: "/leads/new", title: "Lead search" },
  lead_search_status: { path: "/leads/new", title: "Lead search" },
  list_voice_campaigns: { path: "/campaigns", title: "Campaigns" },
  voice_campaign_status: { path: "/campaigns", title: "Campaigns" },
  start_voice_campaign: { path: "/campaigns", title: "Campaigns" },
  pause_voice_campaign: { path: "/campaigns", title: "Campaigns" },
  resume_voice_campaign: { path: "/campaigns", title: "Campaigns" },
  list_email_campaigns: { path: "/email/campaigns", title: "Email campaigns" },
  preview_email_audience: { path: "/email/campaigns", title: "Email campaigns" },
  create_email_campaign_draft: { path: "/email/campaigns", title: "Email campaigns" },
  launch_email_campaign: { path: "/email/campaigns", title: "Email campaigns" },
  list_email_senders: { path: "/email/senders", title: "Email senders" },
  list_email_templates: { path: "/email/templates", title: "Email templates" },
  list_whatsapp_accounts: { path: "/whatsapp/senders", title: "WhatsApp numbers" },
  list_whatsapp_senders: { path: "/whatsapp/senders", title: "WhatsApp numbers" },
  list_whatsapp_templates: { path: "/whatsapp/templates", title: "WhatsApp templates" },
  list_whatsapp_click_templates: { path: "/whatsapp/templates", title: "WhatsApp templates" },
  preview_whatsapp_message: { path: "/whatsapp/templates", title: "WhatsApp templates" },
  send_whatsapp_message: { path: "/whatsapp/templates", title: "WhatsApp templates" },
  whatsapp_lead_links: { path: "/whatsapp/templates", title: "WhatsApp templates" },
  list_recent_calls: { path: "/recordings", title: "Recordings" },
  list_workspace_files: { path: "/files", title: "Knowledge base files" },
  read_document: { path: "/files", title: "Knowledge base files" },
  search_knowledge_base: { path: "/files", title: "Knowledge base files" },
};

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function detailPath(tool: string, result: Record<string, unknown> | null) {
  if (!result) return null;
  if (tool === "launch_email_campaign" || tool === "create_email_campaign_draft") {
    const uuid = str(result.campaign_uuid);
    if (uuid) return `/email/campaigns/${uuid}`;
  }
  if (
    tool === "start_voice_campaign" ||
    tool === "pause_voice_campaign" ||
    tool === "resume_voice_campaign"
  ) {
    const campaignId = num(result.campaign_id);
    if (campaignId !== null) return `/campaigns/${campaignId}`;
  }
  if (tool === "start_lead_search" || tool === "lead_search_status") {
    const searchId = num(result.search_id);
    if (searchId !== null) return `/leads/searches/${searchId}`;
  }
  return null;
}

/** Mirror the agent's lead filters into the CRM page URL so the embedded
 * page shows the same slice the assistant just searched, not the full list. */
function leadCrmPath(args: Record<string, unknown> | null) {
  if (!args) return "/leads/all";
  const params = new URLSearchParams();
  const text: [string, string][] = [
    ["query", "query"],
    ["status", "crm_status"],
    ["tag", "tag"],
    ["category", "category"],
    ["country", "country"],
    ["state", "state"],
    ["city", "city"],
    ["sort", "sort"],
  ];
  for (const [param, key] of text) {
    const value = str(args[key]);
    if (value) params.set(param, value);
  }
  for (const key of ["has_phone", "has_email"] as const) {
    const value = args[key];
    if (typeof value === "boolean") params.set(key, value ? "1" : "0");
  }
  const qs = params.toString();
  return qs ? `/leads/all?${qs}` : "/leads/all";
}

/** Newest executed tool wins; a tool that is still running already switches
 * the pane to its page so the user watches the work land. Declined or failed
 * tools leave the previous page in place. */
export function deriveTaskView(
  events: CopilotActivityEvent[],
): TaskView | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.kind !== "tool_started" && event.kind !== "tool_finished") continue;
    if (!event.tool) continue;
    if (event.kind === "tool_finished" && event.status !== "executed") continue;
    const page = TOOL_PAGES[event.tool];
    if (!page) continue;
    const result =
      event.kind === "tool_finished" ? asRecord(event.result) : null;
    const arguments_ = asRecord(event.arguments);
    return {
      path:
        detailPath(event.tool, result) ??
        (event.tool === "search_leads" ? leadCrmPath(arguments_) : page.path),
      title: page.title,
      tool: event.tool,
    };
  }
  return null;
}
