"use client";

import { useState } from "react";

import type { EmailCampaignResponse } from "@/client/types.gen";
import {
    AddToCampaignDialog,
    type FilterState,
} from "@/components/email/add-to-campaign-dialog";
import { Button } from "@/components/ui/button";

interface Props {
    campaign: EmailCampaignResponse;
    onNext: () => void;
}

function audienceSummary(campaign: EmailCampaignResponse): string {
    const a = campaign.audience as Record<string, unknown>;
    switch (campaign.audience_type) {
        case "leads": {
            const ids = (a.lead_ids as number[] | undefined) ?? [];
            return `${ids.length} lead${ids.length === 1 ? "" : "s"} selected`;
        }
        case "project":
            return `Lead project #${String(a.project_id ?? "?")}`;
        case "tag":
            return `Tag: ${String(a.tag ?? "?")}`;
        case "filter":
            return "All leads matching filter";
        default:
            return campaign.audience_type;
    }
}

const AUDIENCE_CARDS: Array<{
    type: string;
    label: string;
    description: string;
}> = [
    { type: "leads", label: "Selected leads", description: "A fixed list of explicitly chosen leads." },
    { type: "project", label: "Lead project", description: "All leads in a saved lead project." },
    { type: "tag", label: "Tag", description: "All leads with a specific tag." },
    { type: "filter", label: "All matching filter", description: "Everyone matching your current CRM filter." },
];

export function Step1Audience({ campaign, onNext }: Props) {
    const [dialogOpen, setDialogOpen] = useState(false);
    const audience = campaign.audience as Record<string, unknown>;
    const storedFilters =
        campaign.audience_type === "filter"
            ? (audience.filters as Record<string, unknown> | undefined)
            : undefined;
    const { all_matching: _allMatching, ...filterState } = storedFilters ?? {};

    return (
        <div className="space-y-5">
            <div>
                <h2 className="text-base font-semibold">Step 1 — Audience</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                    Choose who receives this campaign. Use the{" "}
                    <strong>Change audience</strong> button to pick a different audience
                    type.
                </p>
            </div>

            {/* Audience type cards */}
            <div className="grid gap-3 sm:grid-cols-2">
                {AUDIENCE_CARDS.map((card) => {
                    const active = campaign.audience_type === card.type;
                    return (
                        <div
                            key={card.type}
                            className={`rounded-lg border p-4 ${
                                active
                                    ? "border-primary bg-primary/5"
                                    : "border-border bg-card opacity-50"
                            }`}
                        >
                            <p className="font-medium text-sm">{card.label}</p>
                            <p className="mt-0.5 text-xs text-muted-foreground">
                                {card.description}
                            </p>
                            {active && (
                                <p className="mt-2 text-xs font-semibold text-primary">
                                    {audienceSummary(campaign)}
                                </p>
                            )}
                        </div>
                    );
                })}
            </div>

            <div className="flex gap-3">
                <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setDialogOpen(true)}
                >
                    Change audience
                </Button>
                <Button size="sm" onClick={onNext}>
                    Next
                </Button>
            </div>

            <AddToCampaignDialog
                open={dialogOpen}
                onOpenChange={setDialogOpen}
                audienceType={
                    (campaign.audience_type as "leads" | "project" | "tag" | "filter") || "filter"
                }
                leadIds={(audience.lead_ids as number[] | undefined) ?? []}
                projectId={(audience.project_id as number | undefined) ?? undefined}
                tag={(audience.tag as string | undefined) ?? undefined}
                filterState={storedFilters ? (filterState as FilterState) : undefined}
            />
        </div>
    );
}
