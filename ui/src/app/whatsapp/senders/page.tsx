"use client";

import { AlertTriangle, Info, Loader2, MessageCircle, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
    deleteWhatsappSenderApiV1WhatsappSendersSenderUuidDelete,
    listWhatsappSendersApiV1WhatsappSendersGet,
} from "@/client";
import type { WhatsAppSenderResponse } from "@/client/types.gen";
import {
    LIST_ROW_ACTIONS_CLASS,
    LIST_ROW_CLASS,
    LIST_ROW_TARGET_CLASS,
} from "@/components/layout/ListRow";
import { PageLayout, PageSection } from "@/components/layout/PageLayout";
import {
    AlertDialog,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ListRowSkeleton } from "@/components/ui/skeleton";
import { WhatsAppSenderDialog } from "@/components/whatsapp/sender-dialog";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

export default function WhatsAppSendersPage() {
    const { user, loading: authLoading, getAccessToken, redirectToLogin } = useAuth();
    const [senders, setSenders] = useState<WhatsAppSenderResponse[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [reload, setReload] = useState(0);
    const [search, setSearch] = useState("");
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editTarget, setEditTarget] = useState<WhatsAppSenderResponse | null>(null);
    const [deleteTarget, setDeleteTarget] = useState<WhatsAppSenderResponse | null>(null);
    const [deleting, setDeleting] = useState(false);
    const [deleteError, setDeleteError] = useState<string | null>(null);

    useEffect(() => {
        if (!authLoading && !user) redirectToLogin();
    }, [authLoading, user, redirectToLogin]);

    useEffect(() => {
        if (authLoading || !user) return;
        let cancelled = false;
        const load = async () => {
            setLoading(true);
            setError(null);
            try {
                const token = await getAccessToken();
                const response = await listWhatsappSendersApiV1WhatsappSendersGet({
                    headers: { Authorization: `Bearer ${token}` },
                });
                if (response.error) {
                    throw new Error(
                        detailFromError(response.error, "Failed to load numbers")
                    );
                }
                if (!cancelled) setSenders(response.data ?? []);
            } catch (cause) {
                if (!cancelled) {
                    setError(cause instanceof Error ? cause.message : "Failed to load numbers");
                }
            } finally {
                if (!cancelled) setLoading(false);
            }
        };
        void load();
        return () => {
            cancelled = true;
        };
    }, [authLoading, user, getAccessToken, reload]);

    const applySaved = (sender: WhatsAppSenderResponse) => {
        setReload((value) => value + 1);
        setSenders((current) => {
            const exists = current.some((item) => item.uuid === sender.uuid);
            if (!exists) return [sender, ...current];
            return current.map((item) => {
                if (item.uuid === sender.uuid) return sender;
                // A promoted default clears the flag on the others.
                return sender.is_default ? { ...item, is_default: false } : item;
            });
        });
    };

    const deleteSender = async () => {
        if (!deleteTarget || deleting) return;
        setDeleting(true);
        setDeleteError(null);
        try {
            const token = await getAccessToken();
            const response = await deleteWhatsappSenderApiV1WhatsappSendersSenderUuidDelete({
                headers: { Authorization: `Bearer ${token}` },
                path: { sender_uuid: deleteTarget.uuid },
            });
            if (response.error) {
                throw new Error(detailFromError(response.error, "Failed to delete number"));
            }
            toast.success("Number removed");
            setDeleteTarget(null);
            setReload((value) => value + 1);
        } catch (cause) {
            setDeleteError(
                cause instanceof Error ? cause.message : "Failed to delete number"
            );
        } finally {
            setDeleting(false);
        }
    };

    const query = search.trim().toLowerCase();
    const filtered = senders.filter((sender) =>
        [sender.label, sender.phone_e164, sender.country].some((value) =>
            value?.toLowerCase().includes(query)
        )
    );

    return (
        <>
            <PageLayout
                title="WhatsApp numbers"
                description="Keep your own WhatsApp number on file for reference. Bolo never connects to WhatsApp and never sends messages."
            >
                <div
                    role="note"
                    className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-4 text-sm"
                >
                    <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-600" />
                    <p>
                        WhatsApp can ban or restrict a number that sends unsolicited or
                        bulk messages. If that happens it is your number and your
                        responsibility; Bolo is not responsible for any action WhatsApp
                        takes against your account or number.
                    </p>
                </div>

                <PageSection
                    title="Saved numbers"
                    description="Used only as a reference and as the country hint for lead numbers saved without a country code."
                    actions={
                        <Button
                            onClick={() => {
                                setEditTarget(null);
                                setDialogOpen(true);
                            }}
                            disabled={authLoading || !user || loading || !!error}
                        >
                            <Plus className="h-4 w-4" />
                            Add number
                        </Button>
                    }
                    toolbar={
                        <div className="relative">
                            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                            <Input
                                aria-label="Search numbers"
                                placeholder="Search numbers..."
                                value={search}
                                onChange={(event) => setSearch(event.target.value)}
                                className="pl-10"
                            />
                        </div>
                    }
                >
                    {authLoading || !user || loading ? (
                        <div className="space-y-4" role="status" aria-label="Loading numbers">
                            {[1, 2].map((item) => (
                                <ListRowSkeleton key={item} />
                            ))}
                        </div>
                    ) : error ? (
                        <div
                            role="alert"
                            className="rounded-lg border border-destructive/20 bg-destructive/10 p-4 text-sm text-destructive"
                        >
                            <p>{error}</p>
                            <Button
                                variant="outline"
                                size="sm"
                                className="mt-3"
                                onClick={() => setReload((value) => value + 1)}
                            >
                                Retry
                            </Button>
                        </div>
                    ) : filtered.length === 0 ? (
                        <div className="list-enter rounded-xl border border-dashed py-14 text-center">
                            <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
                                <MessageCircle className="h-6 w-6 text-primary" aria-hidden />
                            </div>
                            <h3 className="text-base font-semibold text-foreground">
                                {query ? "No numbers match your search" : "No WhatsApp numbers yet"}
                            </h3>
                            <p className="mx-auto mt-1 max-w-sm text-sm text-muted-foreground">
                                {query
                                    ? "Try a different label, number, or country."
                                    : "Add the number you use for WhatsApp so lead numbers without a country code can be matched to the right country."}
                            </p>
                            {query ? (
                                <Button
                                    variant="outline"
                                    className="mt-5"
                                    onClick={() => setSearch("")}
                                >
                                    Clear search
                                </Button>
                            ) : (
                                <Button
                                    className="mt-5"
                                    onClick={() => {
                                        setEditTarget(null);
                                        setDialogOpen(true);
                                    }}
                                >
                                    Add your number
                                </Button>
                            )}
                        </div>
                    ) : (
                        <div className="space-y-4">
                            {filtered.map((sender, index) => (
                                <div
                                    key={sender.uuid}
                                    style={{ animationDelay: `${Math.min(index, 6) * 60}ms` }}
                                    className={`list-enter ${LIST_ROW_CLASS}`}
                                >
                                    <div className="min-w-0 space-y-2">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <h3 className="break-words font-medium">
                                                <button
                                                    type="button"
                                                    className={LIST_ROW_TARGET_CLASS}
                                                    onClick={() => {
                                                        setEditTarget(sender);
                                                        setDialogOpen(true);
                                                    }}
                                                >
                                                    {sender.label}
                                                </button>
                                            </h3>
                                            {sender.is_default && (
                                                <Badge variant="outline">Default</Badge>
                                            )}
                                            <Badge variant="secondary">{sender.country}</Badge>
                                        </div>
                                        <p className="text-sm text-muted-foreground">
                                            {sender.phone_e164}
                                        </p>
                                    </div>
                                    <div
                                        className={`${LIST_ROW_ACTIONS_CLASS} shrink-0 self-end sm:self-auto`}
                                    >
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            aria-label={`Edit ${sender.label}`}
                                            onClick={() => {
                                                setEditTarget(sender);
                                                setDialogOpen(true);
                                            }}
                                        >
                                            <Pencil className="h-4 w-4" />
                                            Edit
                                        </Button>
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            aria-label={`Delete ${sender.label}`}
                                            className="text-destructive hover:text-destructive"
                                            onClick={() => {
                                                setDeleteError(null);
                                                setDeleteTarget(sender);
                                            }}
                                        >
                                            <Trash2 className="h-4 w-4" />
                                        </Button>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </PageSection>

                <div
                    role="note"
                    className="flex items-start gap-2 rounded-lg border bg-muted/40 p-4 text-sm text-muted-foreground"
                >
                    <Info className="mt-0.5 h-4 w-4 flex-shrink-0" />
                    <p>
                        A click-to-chat link opens whichever WhatsApp account you are
                        signed into on your device, in the app, desktop app or WhatsApp
                        Web. The number saved here is informational only; it is not the
                        account used to send.
                    </p>
                </div>
            </PageLayout>

            <WhatsAppSenderDialog
                open={dialogOpen}
                sender={editTarget}
                onOpenChange={(open) => {
                    setDialogOpen(open);
                    if (!open) setEditTarget(null);
                }}
                onSaved={applySaved}
            />

            <AlertDialog
                open={!!deleteTarget}
                onOpenChange={(open) => {
                    if (!open && !deleting) setDeleteTarget(null);
                }}
            >
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Remove number?</AlertDialogTitle>
                        <AlertDialogDescription>
                            Remove &quot;{deleteTarget?.label}&quot;? Any other saved number
                            becomes the default. Nothing is sent or changed in WhatsApp.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    {deleteError && (
                        <p role="alert" className="text-sm text-destructive">
                            {deleteError}
                        </p>
                    )}
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
                        <Button
                            variant="destructive"
                            disabled={deleting}
                            onClick={() => void deleteSender()}
                        >
                            {deleting && <Loader2 className="h-4 w-4 animate-spin" />}
                            {deleting ? "Removing..." : "Remove"}
                        </Button>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </>
    );
}
