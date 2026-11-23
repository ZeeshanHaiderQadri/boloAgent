export const TOOL_LABELS: Record<string, string> = {
  read_document: "Read document",
  business_overview: "Business overview",
  search_leads: "Search leads",
  start_lead_search: "Start lead search",
  lead_search_status: "Lead search status",
  list_lead_searches: "List lead searches",
  list_voice_campaigns: "List voice campaigns",
  list_email_campaigns: "List email campaigns",
  list_recent_calls: "List recent calls",
  list_workspace_files: "List workspace files",
  search_knowledge_base: "Search knowledge base",
  web_search: "Web search",
  web_fetch: "Read web page",
  tag_leads: "Tag leads",
  create_email_campaign_draft: "Create email draft",
  preview_email_audience: "Preview audience",
  launch_email_campaign: "Launch email campaign",
  send_whatsapp_message: "Send WhatsApp message",
  start_voice_campaign: "Start voice campaign",
  pause_voice_campaign: "Pause voice campaign",
  resume_voice_campaign: "Resume voice campaign",
};

export function toolLabel(tool: string | null | undefined) {
  if (!tool) return "Tool";
  return TOOL_LABELS[tool] ?? tool.replaceAll("_", " ");
}
