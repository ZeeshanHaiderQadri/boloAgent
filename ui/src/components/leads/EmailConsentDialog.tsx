"use client";

import { Loader2, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
    bulkUpdateLeadEmailConsentApiV1LeadsEmailConsentBulkPost,
    updateLeadEmailConsentApiV1LeadsLeadIdEmailConsentPatch,
} from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { detailFromError } from "@/lib/apiError";
import {
    EMAIL_CONSENT_LABELS,
    EMAIL_CONSENT_WARNING,
    EMAIL_CONSENTS,
    type EmailConsent,
} from "@/lib/emailConsent";

interface EmailConsentDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    leadIds: number[];
    /** Current consent for a single lead; omit for bulk edits. */
    current?: string | null;
    onSaved: () => void | Promise<void>;
}

function bulkConsentMessage(changed: number, matched: number): string {
    if (changed === matched) {
        return `Email consent updated for ${changed} lead(s)`;
    }
    return `Email consent updated for ${changed} of ${matched} lead(s); the reason was saved for all`;
}

export function EmailConsentDialog({
    open,
    onOpenChange,
    leadIds,
    current,
    onSaved,
}: EmailConsentDialogProps) {
    const [consent, setConsent] = useState<EmailConsent>("unknown");
    const [reason, setReason] = useState("");
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (!open) return;
        const initial = EMAIL_CONSENTS.includes(current as EmailConsent)
            ? (current as EmailConsent)
            : "unknown";
        setConsent(initial);
        setReason("");
        setBusy(false);
    }, [open, current]);

    const isBulk = leadIds.length > 1;
    const needsReason = consent === "opted_in";

    const submit = async () => {
        if (leadIds.length === 0) return;
        if (needsReason && !reason.trim()) {
            toast.error("Write down the proof of consent before opting someone in");
            return;
        }
        setBusy(true);
        try {
            const body = {
                email_consent: consent,
                reason: reason.trim() || null,
            };
            const response = isBulk
                ? await bulkUpdateLeadEmailConsentApiV1LeadsEmailConsentBulkPost({
                      body: { ...body, lead_ids: leadIds },
                  })
                : await updateLeadEmailConsentApiV1LeadsLeadIdEmailConsentPatch({
                      path: { lead_id: leadIds[0] },
                      body,
                  });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not save consent"));
                return;
            }
            toast.success(
                isBulk
                    ? bulkConsentMessage(
                          response.data && "changed" in response.data
                              ? response.data.changed
                              : leadIds.length,
                          response.data && "matched" in response.data
                              ? response.data.matched
                              : leadIds.length,
                      )
                    : "Email consent saved",
            );
            onOpenChange(false);
            await onSaved();
        } catch (error) {
            console.error("Email consent update failed:", error);
            toast.error("Could not save consent");
        } finally {
            setBusy(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Email consent</DialogTitle>
                    <DialogDescription>
                        {isBulk
                            ? `Record the same consent decision for ${leadIds.length} leads.`
                            : "Record whether this lead agreed to receive marketing email."}
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-4">
                    <div className="flex gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
                        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                        <p>{EMAIL_CONSENT_WARNING}</p>
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="email-consent">Decision</Label>
                        <Select
                            value={consent}
                            onValueChange={(value) => setConsent(value as EmailConsent)}
                        >
                            <SelectTrigger id="email-consent">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                {EMAIL_CONSENTS.map((item) => (
                                    <SelectItem key={item} value={item}>
                                        {EMAIL_CONSENT_LABELS[item]}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <p className="text-xs text-muted-foreground">
                            {consent === "unknown"
                                ? "Clears any recorded decision. The lead will not receive marketing email."
                                : consent === "opted_in"
                                  ? "Only choose this when you have proof of permission (signup form, written request, or recorded conversation)."
                                  : "Stops all marketing email to this lead."}
                        </p>
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="email-consent-reason">
                            Why {needsReason ? "(required)" : "(optional)"}
                        </Label>
                        <Textarea
                            id="email-consent-reason"
                            value={reason}
                            onChange={(event) => setReason(event.target.value)}
                            placeholder={
                                needsReason
                                    ? "Example: Signed up at the trade show booth on 12 Nov; confirmation email filed in the shared inbox."
                                    : "Optional context for the activity timeline."
                            }
                            rows={3}
                            maxLength={2000}
                        />
                        <p className="text-xs text-muted-foreground">
                            Your name and the time are recorded with this decision in the
                            activity timeline.
                        </p>
                    </div>
                </div>

                <DialogFooter>
                    <Button
                        variant="outline"
                        onClick={() => onOpenChange(false)}
                        disabled={busy}
                    >
                        Cancel
                    </Button>
                    <Button onClick={() => void submit()} disabled={busy}>
                        {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                        Save consent
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
