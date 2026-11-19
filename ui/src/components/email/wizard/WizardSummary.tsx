"use client";

import type { EmailCampaignResponse } from "@/client/types.gen";

interface Props {
    campaign: EmailCampaignResponse;
}

export function WizardSummary({ campaign }: Props) {
    const eligibleCount = campaign.counts.eligible_count;
    const senderName = campaign.sender?.name ?? "Not set";
    const templateName = campaign.template?.name ?? "Not set";
    const guidance = campaign.sender?.guidance ?? "—";

    return (
        <aside className="sticky top-4 rounded-xl border bg-card p-4 text-sm space-y-3">
            <h3 className="font-semibold text-sm">Campaign summary</h3>
            <dl className="space-y-2">
                <div>
                    <dt className="text-xs text-muted-foreground">Eligible recipients</dt>
                    <dd className="font-semibold tabular-nums">
                        {eligibleCount > 0
                            ? eligibleCount.toLocaleString()
                            : <span className="text-muted-foreground italic">Run preview</span>}
                    </dd>
                </div>
                <div>
                    <dt className="text-xs text-muted-foreground">Sender</dt>
                    <dd className={senderName === "Not set" ? "text-muted-foreground" : ""}>{senderName}</dd>
                </div>
                <div>
                    <dt className="text-xs text-muted-foreground">Template</dt>
                    <dd className={templateName === "Not set" ? "text-muted-foreground" : ""}>{templateName}</dd>
                </div>
                <div>
                    <dt className="text-xs text-muted-foreground">Estimate</dt>
                    <dd className="text-xs text-muted-foreground">{guidance}</dd>
                </div>
            </dl>
        </aside>
    );
}
