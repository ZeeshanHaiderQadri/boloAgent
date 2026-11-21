"use client";

import { ListPlus, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { extendLeadSearchApiV1LeadsSearchesSearchIdExtendPost } from "@/client/sdk.gen";
import type { LeadSearchResponse } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromError } from "@/lib/apiError";
import { LEAD_MAX_TARGET_RESULTS } from "@/lib/leadSearchOptions";

const PRESETS = [25, 50, 100, 200, 500];

interface ExtendLeadSearchDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    search: LeadSearchResponse | null;
    onExtended?: (search: LeadSearchResponse) => void;
}

export function ExtendLeadSearchDialog({
    open,
    onOpenChange,
    search,
    onExtended,
}: ExtendLeadSearchDialogProps) {
    const [additional, setAdditional] = useState("50");
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (open) {
            setAdditional("50");
            setError(null);
        }
    }, [open]);

    const collected = search?.results_count ?? 0;
    const base = Math.max(search?.target_results ?? 0, collected);
    const extra = Number(additional);
    const newTarget = Number.isFinite(extra) && extra > 0 ? base + extra : base;
    const tooLarge = newTarget > LEAD_MAX_TARGET_RESULTS;

    const handleSubmit = async () => {
        if (!search) return;
        if (!Number.isInteger(extra) || extra < 1) {
            setError("Enter how many more leads to collect.");
            return;
        }
        if (newTarget > LEAD_MAX_TARGET_RESULTS) {
            setError(
                `This would reach ${newTarget} leads, above the limit of ${LEAD_MAX_TARGET_RESULTS}.`,
            );
            return;
        }

        setSubmitting(true);
        setError(null);
        try {
            const response =
                await extendLeadSearchApiV1LeadsSearchesSearchIdExtendPost({
                    path: { search_id: search.id },
                    body: { additional_results: extra },
                });
            if (response.error) {
                setError(detailFromError(response.error, "Could not extend the search"));
                return;
            }
            if (!response.data) {
                setError("The search was extended without a response. Refresh the page.");
                return;
            }
            toast.success(
                `Collecting up to ${newTarget} leads with the same query`,
            );
            onExtended?.(response.data);
            onOpenChange(false);
        } catch (submitError) {
            console.error("Lead search extension failed:", submitError);
            setError("Could not extend the search");
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <ListPlus className="h-5 w-5 text-cta" />
                        Extend this search
                    </DialogTitle>
                    <DialogDescription>
                        Keep “{search?.category ?? "this query"}” in{" "}
                        {search?.location ?? "the same location"} and collect more
                        businesses. Existing leads are kept.
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-4">
                    {error && (
                        <p
                            role="alert"
                            className="rounded-md bg-destructive/10 p-3 text-sm text-destructive"
                        >
                            {error}
                        </p>
                    )}

                    <div className="rounded-lg border bg-muted/40 p-3 text-sm">
                        <div className="flex items-center justify-between">
                            <span className="text-muted-foreground">Collected so far</span>
                            <span className="font-medium tabular-nums">{collected}</span>
                        </div>
                        <div className="mt-1 flex items-center justify-between">
                            <span className="text-muted-foreground">Current target</span>
                            <span className="font-medium tabular-nums">
                                {search?.target_results ?? "—"}
                            </span>
                        </div>
                    </div>

                    <div className="space-y-2">
                        <Label>How many more leads?</Label>
                        <div className="flex flex-wrap gap-2">
                            {PRESETS.map((preset) => (
                                <Button
                                    key={preset}
                                    type="button"
                                    size="sm"
                                    variant={additional === String(preset) ? "default" : "outline"}
                                    onClick={() => setAdditional(String(preset))}
                                >
                                    +{preset}
                                </Button>
                            ))}
                        </div>
                        <Input
                            type="number"
                            min={1}
                            max={LEAD_MAX_TARGET_RESULTS}
                            value={additional}
                            onChange={(event) => setAdditional(event.target.value)}
                        />
                        <p className="text-xs text-muted-foreground">
                            New target: {newTarget} lead(s). The search keeps going
                            with new cities or neighbourhoods inside{" "}
                            {search?.location ?? "the location"}; businesses already
                            collected are kept.
                        </p>
                        {tooLarge && (
                            <p className="text-xs text-destructive">
                                The maximum for one search is {LEAD_MAX_TARGET_RESULTS}.
                            </p>
                        )}
                    </div>
                </div>

                <DialogFooter>
                    <Button
                        variant="outline"
                        onClick={() => onOpenChange(false)}
                        disabled={submitting}
                    >
                        Cancel
                    </Button>
                    <Button
                        onClick={() => void handleSubmit()}
                        disabled={submitting || tooLarge}
                    >
                        {submitting ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                            <ListPlus className="h-4 w-4" />
                        )}
                        Extend search
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
