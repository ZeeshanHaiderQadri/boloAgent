"use client";

import { AlertTriangle, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { EMAIL_CONSENT_WARNING } from "@/lib/emailConsent";

interface Props {
    consentPolicy: "opted_in_only" | "include_unknown";
    overrideReason: string;
    onConsentChange: (policy: "opted_in_only" | "include_unknown") => void;
    onReasonChange: (reason: string) => void;
    onBack: () => void;
    onNext: () => void;
    fieldErrors: Record<string, string>;
    /** True while the real audience-preview request is in flight. */
    previewing?: boolean;
    previewError?: string | null;
}

export function Step3Consent({
    consentPolicy,
    overrideReason,
    onConsentChange,
    onReasonChange,
    onBack,
    onNext,
    fieldErrors,
    previewing = false,
    previewError,
}: Props) {
    return (
        <div className="space-y-5">
            <div>
                <h2 className="text-base font-semibold">Step 3 — Consent policy</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                    Choose which leads are eligible to receive this campaign based on
                    their marketing-email consent.
                </p>
            </div>

            {/* Radio cards */}
            <div className="grid gap-3 sm:grid-cols-2">
                <button
                    type="button"
                    onClick={() => onConsentChange("opted_in_only")}
                    className={`rounded-lg border p-4 text-left transition-colors ${
                        consentPolicy === "opted_in_only"
                            ? "border-primary bg-primary/5"
                            : "border-border hover:border-muted-foreground"
                    }`}
                >
                    <p className="font-medium text-sm">Opted-in only</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                        Only send to leads who have explicitly consented. Recommended for
                        all marketing email.
                    </p>
                </button>
                <button
                    type="button"
                    onClick={() => onConsentChange("include_unknown")}
                    className={`rounded-lg border p-4 text-left transition-colors ${
                        consentPolicy === "include_unknown"
                            ? "border-amber-500 bg-amber-500/5"
                            : "border-border hover:border-muted-foreground"
                    }`}
                >
                    <p className="font-medium text-sm">Include unknown consent</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                        Also send to leads without a recorded consent decision. Requires a
                        written reason.
                    </p>
                </button>
            </div>

            {/* Include-unknown reason */}
            {consentPolicy === "include_unknown" && (
                <div className="space-y-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3">
                    <div className="flex items-start gap-2 text-xs text-amber-700 dark:text-amber-400">
                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                        <p>{EMAIL_CONSENT_WARNING}</p>
                    </div>
                    <div className="grid gap-1.5">
                        <Label
                            htmlFor="wizard-override-reason"
                            className={
                                fieldErrors.consent_override_reason ? "text-destructive" : ""
                            }
                        >
                            Written reason (required)
                        </Label>
                        <Textarea
                            id="wizard-override-reason"
                            value={overrideReason}
                            onChange={(e) => onReasonChange(e.target.value)}
                            placeholder="e.g. These are existing customers from before the consent system was introduced on 2026-01-01."
                            rows={3}
                            className={
                                fieldErrors.consent_override_reason
                                    ? "border-destructive focus-visible:ring-destructive"
                                    : ""
                            }
                        />
                        {fieldErrors.consent_override_reason && (
                            <p role="alert" className="text-xs text-destructive">
                                {fieldErrors.consent_override_reason}
                            </p>
                        )}
                    </div>
                </div>
            )}

            {previewing && (
                <div
                    role="status"
                    className="space-y-1.5 rounded-lg border bg-muted/20 p-3 text-sm"
                >
                    {["Resolving audience", "Checking suppression and consent"].map((label) => (
                        <p key={label} className="flex items-center gap-2 text-muted-foreground">
                            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                            {label}
                        </p>
                    ))}
                    <p className="text-xs text-muted-foreground">
                        Counts appear on the next step as soon as the preview responds.
                    </p>
                </div>
            )}

            {previewError && !previewing && (
                <div
                    role="alert"
                    className="rounded-lg border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive"
                >
                    <p className="font-medium">Could not resolve the audience</p>
                    <p className="mt-0.5">{previewError}</p>
                    <p className="mt-1 text-xs">
                        Your consent choice is saved. Press Next to retry the preview.
                    </p>
                </div>
            )}

            <div className="flex gap-3">
                <Button variant="outline" size="sm" onClick={onBack} disabled={previewing}>
                    Back
                </Button>
                <Button size="sm" onClick={onNext} disabled={previewing}>
                    {previewing && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                    {previewing ? "Resolving…" : "Next"}
                </Button>
            </div>
        </div>
    );
}
