"use client";

import { Loader2, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";

import {
    acceptWhatsappDisclaimerApiV1WhatsappDisclaimerPost,
    getWhatsappDisclaimerApiV1WhatsappDisclaimerGet,
} from "@/client";
import type { WhatsAppDisclaimerResponse } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

/**
 * One-time notice before any WhatsApp message link can be built. The server
 * stores the acceptance per user and organization; the lead-links endpoint
 * returns 403 until it exists, so this dialog is the only way through.
 */
export function WhatsAppDisclaimerDialog({
    open,
    onOpenChange,
    onAccepted,
}: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onAccepted?: (status: WhatsAppDisclaimerResponse) => void;
}) {
    const { getAccessToken } = useAuth();
    const [status, setStatus] = useState<WhatsAppDisclaimerResponse | null>(null);
    const [checked, setChecked] = useState(false);
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [reload, setReload] = useState(0);

    useEffect(() => {
        if (!open) return;
        let cancelled = false;
        setChecked(false);
        setError(null);
        setLoading(true);
        const load = async () => {
            try {
                const token = await getAccessToken();
                const response = await getWhatsappDisclaimerApiV1WhatsappDisclaimerGet({
                    headers: { Authorization: `Bearer ${token}` },
                });
                if (response.error) {
                    throw new Error(
                        detailFromError(response.error, "Failed to load the WhatsApp notice")
                    );
                }
                if (!cancelled) setStatus(response.data ?? null);
            } catch (cause) {
                if (!cancelled) {
                    setError(
                        cause instanceof Error
                            ? cause.message
                            : "Failed to load the WhatsApp notice"
                    );
                }
            } finally {
                if (!cancelled) setLoading(false);
            }
        };
        void load();
        return () => {
            cancelled = true;
        };
    }, [open, getAccessToken, reload]);

    const accept = async () => {
        if (!checked || saving) return;
        setSaving(true);
        setError(null);
        try {
            const token = await getAccessToken();
            const response = await acceptWhatsappDisclaimerApiV1WhatsappDisclaimerPost({
                headers: { Authorization: `Bearer ${token}` },
            });
            if (response.error) {
                throw new Error(
                    detailFromError(response.error, "Failed to save your acceptance")
                );
            }
            if (!response.data) throw new Error("Failed to save your acceptance");
            setStatus(response.data);
            onAccepted?.(response.data);
            onOpenChange(false);
        } catch (cause) {
            setError(
                cause instanceof Error ? cause.message : "Failed to save your acceptance"
            );
        } finally {
            setSaving(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={(next) => (!saving ? onOpenChange(next) : null)}>
            {/* The chooser opens this from inside a clickable lead row; keep
                clicks in the portal from bubbling to the row's navigation. */}
            <DialogContent
                className="sm:max-w-lg"
                onClick={(event) => event.stopPropagation()}
            >
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <ShieldAlert className="h-5 w-5 text-amber-600" aria-hidden />
                        Before you message on WhatsApp
                    </DialogTitle>
                    <DialogDescription>
                        One-time notice. You only see this once per account.
                    </DialogDescription>
                </DialogHeader>

                {loading ? (
                    <div
                        className="flex items-center gap-2 py-6 text-sm text-muted-foreground"
                        role="status"
                    >
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Loading notice...
                    </div>
                ) : (
                    <div className="space-y-4 text-sm">
                        <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-4">
                            {status?.text ??
                                "WhatsApp can ban or restrict a number that sends spam or messages people did not ask for."}
                        </p>
                        <label className="flex cursor-pointer items-start gap-3">
                            <Checkbox
                                checked={checked}
                                onCheckedChange={(value) => setChecked(value === true)}
                                aria-label="Acknowledge the WhatsApp notice"
                                className="mt-0.5"
                            />
                            <span>
                                I understand this risk, and I will only message people who
                                agreed to hear from me.
                            </span>
                        </label>
                    </div>
                )}

                {error && (
                    <div role="alert" className="space-y-2 text-sm text-destructive">
                        <p>{error}</p>
                        {!status && (
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={() => setReload((value) => value + 1)}
                            >
                                Retry
                            </Button>
                        )}
                    </div>
                )}

                <DialogFooter>
                    <Button
                        variant="outline"
                        onClick={() => onOpenChange(false)}
                        disabled={saving}
                    >
                        Cancel
                    </Button>
                    <Button
                        onClick={() => void accept()}
                        disabled={!checked || saving || loading || !status}
                    >
                        {saving && <Loader2 className="h-4 w-4 animate-spin" />}
                        {saving ? "Saving..." : "I understand"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

/**
 * Opens the notice as soon as a WhatsApp page is shown to a user who has not
 * accepted the current wording. Failures stay silent: the page banner still
 * explains the risk and the chooser enforces acceptance through its 403.
 */
export function useWhatsAppDisclaimerPrompt() {
    const { user, loading: authLoading, getAccessToken } = useAuth();
    const [open, setOpen] = useState(false);

    useEffect(() => {
        if (authLoading || !user) return;
        let cancelled = false;
        const check = async () => {
            try {
                const token = await getAccessToken();
                const response = await getWhatsappDisclaimerApiV1WhatsappDisclaimerGet({
                    headers: { Authorization: `Bearer ${token}` },
                });
                if (!cancelled && response.data && !response.data.accepted) setOpen(true);
            } catch {
                // See the doc comment above.
            }
        };
        void check();
        return () => {
            cancelled = true;
        };
    }, [authLoading, user, getAccessToken]);

    return { open, setOpen };
}
