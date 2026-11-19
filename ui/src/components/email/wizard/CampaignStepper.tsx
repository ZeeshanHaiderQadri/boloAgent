"use client";

import { Check } from "lucide-react";

export interface Step {
    label: string;
}

interface Props {
    steps: Step[];
    currentStep: number;
}

export function CampaignStepper({ steps, currentStep }: Props) {
    return (
        <nav aria-label="Campaign wizard steps" className="mb-6">
            <ol className="flex items-center gap-0">
                {steps.map((step, i) => {
                    const completed = i < currentStep;
                    const active = i === currentStep;
                    return (
                        <li key={i} className="flex flex-1 items-center">
                            <div className="flex flex-col items-center gap-1 flex-1">
                                <div
                                    className={`flex h-8 w-8 items-center justify-center rounded-full border-2 text-sm font-semibold transition-colors ${
                                        completed
                                            ? "border-primary bg-primary text-primary-foreground"
                                            : active
                                              ? "border-primary bg-background text-primary"
                                              : "border-muted bg-muted/40 text-muted-foreground"
                                    }`}
                                    aria-current={active ? "step" : undefined}
                                >
                                    {completed ? (
                                        <Check className="h-4 w-4" />
                                    ) : (
                                        <span>{i + 1}</span>
                                    )}
                                </div>
                                <span
                                    className={`hidden text-xs md:block ${
                                        active
                                            ? "font-medium text-foreground"
                                            : "text-muted-foreground"
                                    }`}
                                >
                                    {step.label}
                                </span>
                            </div>
                            {i < steps.length - 1 && (
                                <div
                                    className={`h-0.5 flex-1 transition-colors ${
                                        completed ? "bg-primary" : "bg-muted"
                                    }`}
                                />
                            )}
                        </li>
                    );
                })}
            </ol>
        </nav>
    );
}
