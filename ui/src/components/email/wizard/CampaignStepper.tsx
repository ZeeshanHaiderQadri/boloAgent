"use client";

import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

export interface Step {
    label: string;
}

interface Props {
    steps: Step[];
    currentStep: number;
}

/**
 * Wizard progress. Display-only by design: steps are not clickable, so the
 * only interaction is the Back/Next buttons inside each step. The step the
 * user is on is exposed with aria-current, and step changes are announced
 * through the visually hidden live region.
 */
export function CampaignStepper({ steps, currentStep }: Props) {
    const activeLabel = steps[currentStep]?.label ?? "";

    return (
        <nav aria-label="Campaign wizard steps" className="mb-6">
            <ol className="flex items-start gap-1">
                {steps.map((step, i) => {
                    const completed = i < currentStep;
                    const active = i === currentStep;
                    return (
                        <li
                            key={step.label}
                            aria-current={active ? "step" : undefined}
                            className="flex flex-1 items-start"
                        >
                            <div className="flex w-16 shrink-0 flex-col items-center gap-1.5 sm:w-24">
                                <span
                                    className={cn(
                                        "flex h-8 w-8 items-center justify-center rounded-full border-2 text-sm font-semibold transition-colors duration-300 motion-reduce:transition-none",
                                        completed &&
                                            "border-primary bg-primary text-primary-foreground",
                                        active &&
                                            "border-primary bg-background text-primary ring-4 ring-primary/15",
                                        !completed &&
                                            !active &&
                                            "border-border bg-muted/40 text-muted-foreground",
                                    )}
                                >
                                    {completed ? (
                                        <Check className="success-enter h-4 w-4" aria-hidden />
                                    ) : (
                                        <span aria-hidden>{i + 1}</span>
                                    )}
                                    <span className="sr-only">
                                        {completed
                                            ? "completed"
                                            : active
                                              ? "current step"
                                              : "upcoming"}
                                    </span>
                                </span>
                                <span
                                    className={cn(
                                        "hidden w-full truncate text-center text-xs transition-colors duration-300 motion-reduce:transition-none sm:block",
                                        active && "font-semibold text-foreground",
                                        completed && "text-foreground/80",
                                        !completed && !active && "text-muted-foreground",
                                    )}
                                    title={step.label}
                                >
                                    {step.label}
                                </span>
                            </div>
                            {i < steps.length - 1 && (
                                <div
                                    aria-hidden
                                    className="mt-4 h-0.5 flex-1 overflow-hidden rounded-full bg-muted"
                                >
                                    <div
                                        className={cn(
                                            "h-full origin-left rounded-full bg-primary transition-transform duration-700 ease-out motion-reduce:transition-none",
                                            completed ? "scale-x-100" : "scale-x-0",
                                        )}
                                    />
                                </div>
                            )}
                        </li>
                    );
                })}
            </ol>
            <p aria-live="polite" className="sr-only">
                Step {currentStep + 1} of {steps.length}
                {activeLabel ? `: ${activeLabel}` : ""}
            </p>
        </nav>
    );
}
