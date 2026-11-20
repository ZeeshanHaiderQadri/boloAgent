"use client";

import { AlertTriangle, CheckCircle2, ExternalLink, Loader2, MessageCircle } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import {
    getWhatsappDisclaimerApiV1WhatsappDisclaimerGet,
    whatsappLeadLinksApiV1WhatsappLeadsLeadIdLinksGet,
} from "@/client";
import type { LeadResponse, WhatsAppLeadLinksResponse } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { WhatsAppDisclaimerDialog } from "@/components/whatsapp/disclaimer-dialog";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { describePhoneNumber, hasInternationalPhone } from "@/lib/whatsapp";

/**
 * Opens a chooser of click-to-chat messages for one lead. Nothing is sent from
 * Bolo: each choice is a wa.me link that opens the operator's own WhatsApp
 * with the message prefilled.
 */
export function WhatsAppLinkButton({
    lead,
    className,
}: {
    lead: LeadResponse;
    className?: string;
}) {
    // Hidden rather than guessed when the number carries no country.
    if (!hasInternationalPhone(lead)) return null;
    return <WhatsAppChooser lead={lead} className={className} />;
}

function WhatsAppChooser({ lead, className }: { lead: LeadResponse; className?: string }) {
    const { getAccessToken } = useAuth();
    const [open, setOpen] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [data, setData] = useState<WhatsAppLeadLinksResponse | null>(null);
    const [disclaimerOpen, setDisclaimerOpen] = useState(false);

    const show = async () => {
        setOpen(true);
        setLoading(true);
        setError(null);
        setData(null);
        try {
            const token = await getAccessToken();
            const status = await getWhatsappDisclaimerApiV1WhatsappDisclaimerGet({
                headers: { Authorization: `Bearer ${token}` },
            });
            if (status.data && !status.data.accepted) {
                // The backend refuses to build links until this is accepted.
                setOpen(false);
                setDisclaimerOpen(true);
                return;
            }
            const response = await whatsappLeadLinksApiV1WhatsappLeadsLeadIdLinksGet({
                headers: { Authorization: `Bearer ${token}` },
                path: { lead_id: lead.id },
            });
            if (response.error) {
                throw new Error(
                    detailFromError(response.error, "Failed to build WhatsApp links")
                );
            }
            setData(response.data ?? null);
        } catch (cause) {
            setError(
                cause instanceof Error ? cause.message : "Failed to build WhatsApp links"
            );
        } finally {
            setLoading(false);
        }
    };

    const target = data?.phone_e164
        ? describePhoneNumber(data.phone_e164, data.country)
        : null;

    const reasonText =
        data?.reason === "no_phone"
            ? "This lead has no phone number."
            : data?.reason === "unrecognized_number"
              ? "This number does not match the lead's country, so Bolo will not guess. Correct the phone number or its country on the lead."
              : "The country for this number is unknown. Add a WhatsApp number with its country on the WhatsApp numbers page, then try again.";

    return (
        <>
            <button
                type="button"
                title="Open WhatsApp message"
                aria-label={`Open WhatsApp message for ${lead.title}`}
                onClick={(event) => {
                    event.stopPropagation();
                    void show();
                }}
                className={cn(
                    "focus-ring rounded-sm text-muted-foreground transition-colors duration-200 hover:text-cta hover:drop-shadow-[0_0_4px_rgba(45,212,191,0.55)]",
                    className,
                )}
            >
                <MessageCircle className="h-3.5 w-3.5" aria-hidden />
            </button>

            <Dialog open={open} onOpenChange={setOpen}>
                <DialogContent className="sm:max-w-lg" onClick={(event) => event.stopPropagation()}>
                    <DialogHeader>
                        <DialogTitle className="break-words">
                            WhatsApp: {lead.title}
                        </DialogTitle>
                        <DialogDescription>
                            Pick a message to open in WhatsApp with the lead&apos;s details
                            filled in. Bolo does not send it; you press send inside WhatsApp.
                        </DialogDescription>
                    </DialogHeader>

                    <ConsentNote lead={lead} />

                    {target && (
                        <div className="flex flex-wrap items-baseline gap-x-1.5 text-sm">
                            <span className="text-muted-foreground">This link opens</span>
                            <span className="font-medium">{target.formatted}</span>
                            {target.countryName && (
                                <span className="text-muted-foreground">
                                    ({target.countryName})
                                </span>
                            )}
                        </div>
                    )}

                    {loading ? (
                        <div
                            className="flex items-center gap-2 py-6 text-sm text-muted-foreground"
                            role="status"
                        >
                            <Loader2 className="h-4 w-4 animate-spin" />
                            Building links...
                        </div>
                    ) : error ? (
                        <p role="alert" className="text-sm text-destructive">
                            {error}
                        </p>
                    ) : data && !data.available ? (
                        <div className="space-y-2 text-sm text-muted-foreground">
                            <p>{reasonText}</p>
                            {data.reason === "country_unknown" && (
                                <Button variant="outline" size="sm" asChild>
                                    <Link href="/whatsapp/senders">WhatsApp numbers</Link>
                                </Button>
                            )}
                        </div>
                    ) : data && data.links.length === 0 ? (
                        <div className="space-y-2 text-sm text-muted-foreground">
                            <p>No WhatsApp templates yet.</p>
                            <Button variant="outline" size="sm" asChild>
                                <Link href="/whatsapp/templates">Create a template</Link>
                            </Button>
                        </div>
                    ) : (
                        <div className="max-h-[60vh] space-y-3 overflow-y-auto">
                            {data?.links.map((link) => (
                                <a
                                    key={link.template_uuid}
                                    href={link.url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="group block rounded-lg border p-3 transition-colors hover:border-cta/50 hover:bg-muted/50"
                                >
                                    <div className="flex items-center justify-between gap-2">
                                        <span className="text-sm font-medium">
                                            {link.name}
                                        </span>
                                        <span className="flex items-center gap-1 text-xs text-muted-foreground group-hover:text-cta">
                                            Open in WhatsApp
                                            <ExternalLink className="h-3 w-3" aria-hidden />
                                        </span>
                                    </div>
                                    <p className="mt-1 whitespace-pre-wrap break-words text-xs text-muted-foreground">
                                        {link.body}
                                    </p>
                                </a>
                            ))}
                            <p className="text-xs text-muted-foreground">
                                Opens in whichever WhatsApp account you are signed into.
                                No message is sent until you press send.
                            </p>
                        </div>
                    )}
                </DialogContent>
            </Dialog>

            <WhatsAppDisclaimerDialog
                open={disclaimerOpen}
                onOpenChange={setDisclaimerOpen}
                onAccepted={() => void show()}
            />
        </>
    );
}

/**
 * Always-visible reminder that email consent is not WhatsApp consent. The
 * email consent below is only a signal; Bolo does not record WhatsApp consent.
 */
function ConsentNote({ lead }: { lead: LeadResponse }) {
    return (
        <div role="note" className="space-y-1.5 rounded-lg border bg-muted/40 p-3 text-xs">
            <p className="text-foreground">
                Bolo does not track WhatsApp consent. Before you press send, confirm
                this business agreed to hear from you on WhatsApp.
            </p>
            {lead.email_consent === "opted_in" ? (
                <p className="flex items-start gap-1.5 text-muted-foreground">
                    <CheckCircle2
                        className="mt-0.5 h-3 w-3 shrink-0 text-emerald-500"
                        aria-hidden
                    />
                    Email consent: opted in. That covers email only; it does not prove
                    they agreed to WhatsApp messages.
                </p>
            ) : lead.email_consent === "opted_out" ? (
                <p className="flex items-start gap-1.5 text-amber-600">
                    <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
                    Email consent: opted out. Treat this as a strong signal not to message
                    them unless they agreed to WhatsApp separately.
                </p>
            ) : (
                <p className="flex items-start gap-1.5 text-amber-600">
                    <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
                    No email consent recorded. That says nothing about WhatsApp; only send
                    if they agreed to hear from you.
                </p>
            )}
        </div>
    );
}
