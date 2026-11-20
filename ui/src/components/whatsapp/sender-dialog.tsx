"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
    createWhatsappSenderApiV1WhatsappSendersPost,
    updateWhatsappSenderApiV1WhatsappSendersSenderUuidPatch,
} from "@/client";
import type { WhatsAppSenderResponse } from "@/client/types.gen";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

export function WhatsAppSenderDialog({
    open,
    sender,
    onOpenChange,
    onSaved,
}: {
    open: boolean;
    sender?: WhatsAppSenderResponse | null;
    onOpenChange: (open: boolean) => void;
    onSaved: (sender: WhatsAppSenderResponse) => void;
}) {
    const { getAccessToken, user } = useAuth();
    const [label, setLabel] = useState("");
    const [phone, setPhone] = useState("");
    const [country, setCountry] = useState("");
    const [isDefault, setIsDefault] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    // Seed the country once per open so the effect below does not clobber typing.
    const seededFor = useRef<string | null>(null);

    useEffect(() => {
        if (!open) {
            seededFor.current = null;
            return;
        }
        const key = sender?.uuid ?? "new";
        if (seededFor.current === key) return;
        seededFor.current = key;
        setLabel(sender?.label ?? "");
        setPhone(sender?.phone_e164 ?? "");
        setCountry(sender?.country ?? "");
        setIsDefault(sender?.is_default ?? false);
        setError(null);
    }, [open, sender]);

    const save = async () => {
        if (saving) return;
        if (!label.trim()) {
            setError("Give this number a label.");
            return;
        }
        if (!phone.trim()) {
            setError("Enter the WhatsApp number.");
            return;
        }
        if (!/^[A-Za-z]{2}$/.test(country.trim())) {
            setError("Country must be a two-letter code such as US or GB.");
            return;
        }
        setSaving(true);
        setError(null);
        try {
            const token = await getAccessToken();
            const headers = { Authorization: `Bearer ${token}` };
            const payload = {
                label: label.trim(),
                phone: phone.trim(),
                country: country.trim().toUpperCase(),
                is_default: isDefault,
            };
            const response = sender
                ? await updateWhatsappSenderApiV1WhatsappSendersSenderUuidPatch({
                    headers,
                    path: { sender_uuid: sender.uuid },
                    body: payload,
                })
                : await createWhatsappSenderApiV1WhatsappSendersPost({
                    headers,
                    body: payload,
                });
            if (response.error) {
                throw new Error(detailFromError(response.error, "Failed to save number"));
            }
            if (!response.data) throw new Error("Failed to save number");
            toast.success(sender ? "Number updated" : "Number saved");
            onSaved(response.data);
            onOpenChange(false);
        } catch (cause) {
            setError(cause instanceof Error ? cause.message : "Failed to save number");
        } finally {
            setSaving(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={(next) => !saving && onOpenChange(next)}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle>
                        {sender ? "Edit WhatsApp number" : "Add WhatsApp number"}
                    </DialogTitle>
                    <DialogDescription>
                        Saved for reference only. Click-to-chat opens whichever WhatsApp
                        account you are signed into on your device.
                    </DialogDescription>
                </DialogHeader>

                <div className="grid gap-4 py-1">
                    <div className="grid gap-2">
                        <Label htmlFor="wa-sender-label">Label *</Label>
                        <Input
                            id="wa-sender-label"
                            value={label}
                            onChange={(event) => setLabel(event.target.value)}
                            placeholder="Main business line"
                            maxLength={255}
                        />
                    </div>
                    <div className="grid gap-2">
                        <Label htmlFor="wa-sender-phone">WhatsApp number *</Label>
                        <Input
                            id="wa-sender-phone"
                            value={phone}
                            onChange={(event) => setPhone(event.target.value)}
                            placeholder="+1 512 555 0100"
                            inputMode="tel"
                        />
                        <p className="text-xs text-muted-foreground">
                            With its country code, or in the local format for the country
                            below.
                        </p>
                    </div>
                    <div className="grid gap-2">
                        <Label htmlFor="wa-sender-country">Country *</Label>
                        <Input
                            id="wa-sender-country"
                            value={country}
                            onChange={(event) => setCountry(event.target.value)}
                            placeholder="US"
                            maxLength={2}
                            className="w-24 uppercase"
                        />
                        <p className="text-xs text-muted-foreground">
                            Two-letter code. This is the only country assumed for lead
                            numbers that have no country code; nothing else is guessed.
                        </p>
                    </div>
                    <label className="flex items-start gap-2 text-sm">
                        <Checkbox
                            checked={isDefault}
                            onCheckedChange={(checked) => setIsDefault(checked === true)}
                            aria-label="Use as default number"
                            className="mt-0.5"
                        />
                        <span>
                            Use as the default number
                            <span className="block text-xs text-muted-foreground">
                                Supplies the country hint for local-format lead numbers.
                            </span>
                        </span>
                    </label>
                    {!user && (
                        <p className="text-xs text-muted-foreground">
                            Sign in before saving a number.
                        </p>
                    )}
                </div>

                {error && (
                    <p role="alert" className="text-sm text-destructive">
                        {error}
                    </p>
                )}

                <DialogFooter>
                    <Button
                        variant="outline"
                        onClick={() => onOpenChange(false)}
                        disabled={saving}
                    >
                        Cancel
                    </Button>
                    <Button onClick={() => void save()} disabled={saving || !user}>
                        {saving && <Loader2 className="h-4 w-4 animate-spin" />}
                        {saving ? "Saving..." : sender ? "Save changes" : "Save number"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
