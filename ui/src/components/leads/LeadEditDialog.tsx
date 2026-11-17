"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { updateLeadApiV1LeadsLeadIdPatch } from "@/client/sdk.gen";
import type { LeadResponse } from "@/client/types.gen";
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

interface LeadEditDialogProps {
    lead: LeadResponse | null;
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onSaved?: (lead: LeadResponse) => void;
}

interface Draft {
    title: string;
    category: string;
    phone: string;
    email: string;
    website: string;
    address: string;
    city: string;
    state: string;
    country: string;
    postal_code: string;
}

function emptyDraft(lead: LeadResponse | null): Draft {
    return {
        title: lead?.title ?? "",
        category: lead?.category ?? "",
        phone: lead?.phone ?? "",
        email: lead?.email ?? "",
        website: lead?.website ?? "",
        address: lead?.address ?? "",
        city: lead?.city ?? "",
        state: lead?.state ?? "",
        country: lead?.country ?? "",
        postal_code: lead?.postal_code ?? "",
    };
}

export function LeadEditDialog({
    lead,
    open,
    onOpenChange,
    onSaved,
}: LeadEditDialogProps) {
    const [draft, setDraft] = useState<Draft>(() => emptyDraft(lead));
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        if (open) setDraft(emptyDraft(lead));
    }, [open, lead]);

    const update = (field: keyof Draft) => (value: string) =>
        setDraft((current) => ({ ...current, [field]: value }));

    const submit = async () => {
        if (!lead) return;
        if (!draft.title.trim()) {
            toast.error("Business name cannot be empty");
            return;
        }
        setSaving(true);
        try {
            const response = await updateLeadApiV1LeadsLeadIdPatch({
                path: { lead_id: lead.id },
                body: {
                    title: draft.title.trim(),
                    category: draft.category.trim(),
                    phone: draft.phone.trim(),
                    email: draft.email.trim(),
                    website: draft.website.trim(),
                    address: draft.address.trim(),
                    city: draft.city.trim(),
                    state: draft.state.trim(),
                    country: draft.country.trim(),
                    postal_code: draft.postal_code.trim(),
                },
            });
            if (response.error) {
                toast.error(detailFromError(response.error, "Could not save this lead"));
                return;
            }
            toast.success("Lead updated");
            if (response.data) onSaved?.(response.data);
            onOpenChange(false);
        } catch (error) {
            console.error("Lead update failed:", error);
            toast.error("Could not save this lead");
        } finally {
            setSaving(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
                <DialogHeader>
                    <DialogTitle>Edit lead</DialogTitle>
                    <DialogDescription>
                        Fix or enrich the contact details in this saved list. Clearing a
                        field removes it; changes apply to the shared CRM record.
                    </DialogDescription>
                </DialogHeader>
                <form
                    className="grid gap-4 sm:grid-cols-2"
                    onSubmit={(event) => {
                        event.preventDefault();
                        void submit();
                    }}
                >
                    <div className="space-y-1.5 sm:col-span-2">
                        <Label htmlFor="lead-edit-title">Business name</Label>
                        <Input
                            id="lead-edit-title"
                            value={draft.title}
                            onChange={(event) => update("title")(event.target.value)}
                            autoFocus
                        />
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor="lead-edit-category">Category</Label>
                        <Input
                            id="lead-edit-category"
                            value={draft.category}
                            onChange={(event) => update("category")(event.target.value)}
                        />
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor="lead-edit-phone">Phone</Label>
                        <Input
                            id="lead-edit-phone"
                            value={draft.phone}
                            onChange={(event) => update("phone")(event.target.value)}
                            placeholder="+1 512 555 0100"
                        />
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor="lead-edit-email">Email</Label>
                        <Input
                            id="lead-edit-email"
                            type="email"
                            value={draft.email}
                            onChange={(event) => update("email")(event.target.value)}
                        />
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor="lead-edit-website">Website</Label>
                        <Input
                            id="lead-edit-website"
                            value={draft.website}
                            onChange={(event) => update("website")(event.target.value)}
                            placeholder="example.com"
                        />
                    </div>
                    <div className="space-y-1.5 sm:col-span-2">
                        <Label htmlFor="lead-edit-address">Street address</Label>
                        <Input
                            id="lead-edit-address"
                            value={draft.address}
                            onChange={(event) => update("address")(event.target.value)}
                        />
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor="lead-edit-city">City</Label>
                        <Input
                            id="lead-edit-city"
                            value={draft.city}
                            onChange={(event) => update("city")(event.target.value)}
                        />
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor="lead-edit-state">State / region</Label>
                        <Input
                            id="lead-edit-state"
                            value={draft.state}
                            onChange={(event) => update("state")(event.target.value)}
                        />
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor="lead-edit-country">Country</Label>
                        <Input
                            id="lead-edit-country"
                            value={draft.country}
                            onChange={(event) => update("country")(event.target.value)}
                            placeholder="US"
                        />
                    </div>
                    <div className="space-y-1.5">
                        <Label htmlFor="lead-edit-postal">Postal code</Label>
                        <Input
                            id="lead-edit-postal"
                            value={draft.postal_code}
                            onChange={(event) => update("postal_code")(event.target.value)}
                        />
                    </div>
                    <DialogFooter className="sm:col-span-2">
                        <Button
                            type="button"
                            variant="outline"
                            onClick={() => onOpenChange(false)}
                        >
                            Cancel
                        </Button>
                        <Button type="submit" disabled={saving || !draft.title.trim()}>
                            {saving && <Loader2 className="h-4 w-4 animate-spin" />}
                            Save changes
                        </Button>
                    </DialogFooter>
                </form>
            </DialogContent>
        </Dialog>
    );
}
