"use client";

import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import {
    getEmailCampaignApiV1EmailCampaignsCampaignUuidGet,
    listEmailSendersApiV1EmailSendersGet,
    listEmailTemplatesApiV1EmailTemplatesGet,
    previewCampaignAudienceApiV1EmailCampaignsPreviewPost,
    updateEmailCampaignApiV1EmailCampaignsCampaignUuidPatch,
} from "@/client";
import type {
    EmailCampaignResponse,
    EmailSenderResponse,
    EmailTemplateSummary,
} from "@/client/types.gen";
import { CampaignStepper } from "@/components/email/wizard/CampaignStepper";
import { Step1Audience } from "@/components/email/wizard/Step1Audience";
import { Step2SenderTemplate } from "@/components/email/wizard/Step2SenderTemplate";
import { Step3Consent } from "@/components/email/wizard/Step3Consent";
import { Step4Recipients } from "@/components/email/wizard/Step4Recipients";
import { Step5Review } from "@/components/email/wizard/Step5Review";
import { WizardSummary } from "@/components/email/wizard/WizardSummary";
import { PageLayout } from "@/components/layout/PageLayout";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

const STEPS = [
    { label: "Audience" },
    { label: "Sender & Template" },
    { label: "Consent" },
    { label: "Recipients" },
    { label: "Review" },
];

function resumeStep(campaign: EmailCampaignResponse): number {
    // If consent was explicitly set, start at recipients (step 3)
    // If sender and template are set, start at consent (step 2)
    // If sender is set, start at step 1 (template)
    // Otherwise start at step 0 (audience)
    if (campaign.consent_policy === "include_unknown" && campaign.consent_override_reason) {
        return 3;
    }
    if (campaign.sender && campaign.template) {
        return 2;
    }
    if (campaign.sender || campaign.template) {
        return 1;
    }
    return 0;
}

export default function EmailCampaignDetailPage() {
    const { uuid: campaignUuid } = useParams<{ uuid: string }>();
    const { user, loading: authLoading, getAccessToken, redirectToLogin } = useAuth();

    const [campaign, setCampaign] = useState<EmailCampaignResponse | null>(null);
    const [senders, setSenders] = useState<EmailSenderResponse[]>([]);
    const [templates, setTemplates] = useState<EmailTemplateSummary[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Wizard step
    const [currentStep, setCurrentStep] = useState(0);

    // Step 2 form state
    const [senderUuid, setSenderUuid] = useState("");
    const [templateUuid, setTemplateUuid] = useState("");

    // Step 3 form state
    const [consentPolicy, setConsentPolicy] = useState<"opted_in_only" | "include_unknown">(
        "opted_in_only",
    );
    const [overrideReason, setOverrideReason] = useState("");

    // Field errors
    const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

    const hasFetched = useRef(false);

    useEffect(() => {
        if (!authLoading && !user) redirectToLogin();
    }, [authLoading, user, redirectToLogin]);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        const load = async () => {
            setLoading(true);
            setError(null);
            try {
                const token = await getAccessToken();
                const [campRes, sendersRes, templatesRes] = await Promise.all([
                    getEmailCampaignApiV1EmailCampaignsCampaignUuidGet({
                        headers: { Authorization: `Bearer ${token}` },
                        path: { campaign_uuid: campaignUuid },
                    }),
                    listEmailSendersApiV1EmailSendersGet({
                        headers: { Authorization: `Bearer ${token}` },
                    }),
                    listEmailTemplatesApiV1EmailTemplatesGet({
                        headers: { Authorization: `Bearer ${token}` },
                    }),
                ]);
                if (campRes.error) {
                    throw new Error(detailFromError(campRes.error, "Failed to load campaign"));
                }
                if (campRes.data) {
                    const c = campRes.data;
                    setCampaign(c);
                    setSenderUuid(c.sender?.uuid ?? "");
                    setTemplateUuid(c.template?.uuid ?? "");
                    setConsentPolicy(
                        c.consent_policy === "include_unknown" ? "include_unknown" : "opted_in_only",
                    );
                    setOverrideReason(c.consent_override_reason ?? "");
                    setCurrentStep(resumeStep(c));
                }
                if (!sendersRes.error) setSenders(sendersRes.data ?? []);
                if (!templatesRes.error) setTemplates(templatesRes.data ?? []);
            } catch (cause) {
                setError(cause instanceof Error ? cause.message : "Failed to load campaign");
            } finally {
                setLoading(false);
            }
        };
        void load();
    }, [authLoading, user, getAccessToken, campaignUuid]);

    const autoSave = async (fields: Parameters<typeof updateEmailCampaignApiV1EmailCampaignsCampaignUuidPatch>[0]["body"]) => {
        const token = await getAccessToken();
        const response = await updateEmailCampaignApiV1EmailCampaignsCampaignUuidPatch({
            headers: { Authorization: `Bearer ${token}` },
            path: { campaign_uuid: campaignUuid },
            body: fields,
        });
        if (!response.error && response.data) setCampaign(response.data);
        return response;
    };

    const autoPreview = async () => {
        if (!campaign) return;
        const token = await getAccessToken();
        const audience = campaign.audience as Record<string, unknown>;
        const response = await previewCampaignAudienceApiV1EmailCampaignsPreviewPost({
            headers: { Authorization: `Bearer ${token}` },
            body: {
                audience: {
                    audience_type: campaign.audience_type as "leads" | "project" | "tag" | "filter",
                    lead_ids: audience.lead_ids as number[] | undefined,
                    project_id: audience.project_id as number | undefined,
                    tag: audience.tag as string | undefined,
                    excluded_lead_ids: audience.excluded_lead_ids as number[] | undefined,
                },
                consent_policy: consentPolicy as "opted_in_only" | "include_unknown",
                consent_override_reason:
                    consentPolicy === "include_unknown" ? overrideReason.trim() || null : null,
            },
        });
        if (response.data) {
            // Store the preview counts on the campaign so step 5 shows real data
            const updated = {
                ...campaign,
                counts: response.data.counts,
            };
            setCampaign(updated);
        }
    };

    const handleStep1Next = () => {
        setCurrentStep(1);
    };

    const handleStep2Next = async () => {
        // Auto-save sender + template
        await autoSave({ sender_uuid: senderUuid || null, template_uuid: templateUuid || null });
        setCurrentStep(2);
    };

    const handleStep3Next = async () => {
        // Validate consent override reason
        if (consentPolicy === "include_unknown" && !overrideReason.trim()) {
            setFieldErrors({
                consent_override_reason:
                    "Add a written reason to include unknown-consent leads.",
            });
            return;
        }
        setFieldErrors({});
        await autoSave({
            consent_policy: consentPolicy,
            consent_override_reason:
                consentPolicy === "include_unknown" ? overrideReason.trim() || null : null,
        });
        // Auto-run preview to populate counts for step 5
        await autoPreview();
        setCurrentStep(3);
    };

    const handleStep4Next = () => {
        setCurrentStep(4);
    };

    if (loading || authLoading || !user) {
        return (
            <PageLayout title="Email campaign" description="Loading...">
                <div className="space-y-4" role="status" aria-label="Loading campaign">
                    {[1, 2, 3].map((item) => (
                        <Skeleton key={item} className="h-24 w-full rounded-lg" />
                    ))}
                </div>
            </PageLayout>
        );
    }

    if (error) {
        return (
            <PageLayout title="Email campaign" description="">
                <div
                    role="alert"
                    className="rounded-lg border border-destructive/20 bg-destructive/10 p-4 text-sm text-destructive"
                >
                    <p>{error}</p>
                    <Button
                        variant="outline"
                        size="sm"
                        className="mt-3"
                        onClick={() => {
                            hasFetched.current = false;
                            setError(null);
                            setLoading(true);
                        }}
                    >
                        Retry
                    </Button>
                </div>
            </PageLayout>
        );
    }

    if (!campaign) return null;

    return (
        <PageLayout
            title={campaign.name}
            description="Configure this email campaign step by step."
        >
            {/* Back link */}
            <div className="mb-4">
                <Link
                    href="/email/campaigns"
                    className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
                >
                    <ChevronLeft className="h-3.5 w-3.5" />
                    All campaigns
                </Link>
            </div>

            {/* Stepper */}
            <CampaignStepper steps={STEPS} currentStep={currentStep} />

            {/* Two-column layout: wizard content + sticky summary */}
            <div className="grid gap-6 lg:grid-cols-[1fr_280px]">
                <div className="min-w-0 rounded-xl border bg-card p-5">
                    {currentStep === 0 && (
                        <Step1Audience
                            campaign={campaign}
                            onNext={handleStep1Next}
                        />
                    )}
                    {currentStep === 1 && (
                        <Step2SenderTemplate
                            campaign={campaign}
                            senders={senders}
                            templates={templates}
                            senderUuid={senderUuid}
                            templateUuid={templateUuid}
                            onSenderChange={setSenderUuid}
                            onTemplateChange={setTemplateUuid}
                            onBack={() => setCurrentStep(0)}
                            onNext={() => void handleStep2Next()}
                            fieldErrors={fieldErrors}
                        />
                    )}
                    {currentStep === 2 && (
                        <Step3Consent
                            consentPolicy={consentPolicy}
                            overrideReason={overrideReason}
                            onConsentChange={(p) => {
                                setConsentPolicy(p);
                                setFieldErrors({});
                            }}
                            onReasonChange={(r) => {
                                setOverrideReason(r);
                                if (r.trim()) setFieldErrors({});
                            }}
                            onBack={() => setCurrentStep(1)}
                            onNext={() => void handleStep3Next()}
                            fieldErrors={fieldErrors}
                        />
                    )}
                    {currentStep === 3 && (
                        <Step4Recipients
                            campaign={campaign}
                            onCampaignChange={setCampaign}
                            onBack={() => setCurrentStep(2)}
                            onNext={handleStep4Next}
                        />
                    )}
                    {currentStep === 4 && (
                        <Step5Review
                            campaign={campaign}
                            onBack={() => setCurrentStep(3)}
                        />
                    )}
                </div>

                {/* Sticky summary */}
                <WizardSummary campaign={campaign} />
            </div>
        </PageLayout>
    );
}
