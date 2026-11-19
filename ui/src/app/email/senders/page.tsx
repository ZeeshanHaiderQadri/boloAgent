"use client";

import {
    AlertCircle,
    CheckCircle2,
    Loader2,
    Mail,
    Pencil,
    Plus,
    Search,
    Send,
    ShieldCheck,
    Trash2,
} from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
    deleteEmailSenderApiV1EmailSendersSenderUuidDelete,
    listEmailSendersApiV1EmailSendersGet,
    testSendEmailApiV1EmailSendersSenderUuidTestSendPost,
    verifyEmailSenderApiV1EmailSendersSenderUuidVerifyPost,
} from "@/client";
import type { EmailSenderResponse } from "@/client/types.gen";
import { SenderDialog } from "@/components/email/sender-dialog";
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
import { ListRowSkeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

const PROVIDER_LABELS: Record<string, string> = {
    smtp: "SMTP",
    ses: "Amazon SES (untested)",
    mailgun: "Mailgun",
    mandrill: "Mandrill",
};

function statusBadge(status: string) {
    if (status === "verified") {
        return <Badge>Verified</Badge>;
    }
    if (status === "disabled") {
        return <Badge variant="outline">Disabled</Badge>;
    }
    return <Badge variant="secondary">Not verified</Badge>;
}

export default function EmailSendersPage() {
    const { user, loading: authLoading, getAccessToken, redirectToLogin } = useAuth();
    const [senders, setSenders] = useState<EmailSenderResponse[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [reload, setReload] = useState(0);
    const [search, setSearch] = useState("");
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editTarget, setEditTarget] = useState<EmailSenderResponse | null>(null);
    const [deleteTarget, setDeleteTarget] = useState<EmailSenderResponse | null>(null);
    const [deleting, setDeleting] = useState(false);
    const [deleteError, setDeleteError] = useState<string | null>(null);
    const [verifying, setVerifying] = useState<string | null>(null);
    const [testTarget, setTestTarget] = useState<EmailSenderResponse | null>(null);

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
                const response = await listEmailSendersApiV1EmailSendersGet({
                    headers: { Authorization: `Bearer ${token}` },
                });
                if (response.error) {
                    throw new Error(detailFromError(response.error, "Failed to load senders"));
                }
                if (!cancelled) setSenders(response.data ?? []);
            } catch (cause) {
                if (!cancelled) {
                    setError(cause instanceof Error ? cause.message : "Failed to load senders");
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

    const upsertSender = (sender: EmailSenderResponse) => {
        setSenders((current) => {
            const exists = current.some((item) => item.uuid === sender.uuid);
            return exists
                ? current.map((item) => (item.uuid === sender.uuid ? sender : item))
                : [sender, ...current];
        });
    };

    const verifySender = async (sender: EmailSenderResponse) => {
        setVerifying(sender.uuid);
        try {
            const token = await getAccessToken();
            const response = await verifyEmailSenderApiV1EmailSendersSenderUuidVerifyPost({
                headers: { Authorization: `Bearer ${token}` },
                path: { sender_uuid: sender.uuid },
            });
            if (response.error) {
                throw new Error(detailFromError(response.error, "Verification failed"));
            }
            toast.success(response.data?.message ?? "Sender verified");
            setReload((value) => value + 1);
        } catch (cause) {
            toast.error(cause instanceof Error ? cause.message : "Verification failed");
        } finally {
            setVerifying(null);
        }
    };

    const deleteSender = async () => {
        if (!deleteTarget || deleting) return;
        setDeleting(true);
        setDeleteError(null);
        try {
            const token = await getAccessToken();
            const response = await deleteEmailSenderApiV1EmailSendersSenderUuidDelete({
                headers: { Authorization: `Bearer ${token}` },
                path: { sender_uuid: deleteTarget.uuid },
            });
            if (response.error) {
                throw new Error(detailFromError(response.error, "Failed to delete sender"));
            }
            setSenders((current) => current.filter((item) => item.uuid !== deleteTarget.uuid));
            setDeleteTarget(null);
            toast.success("Sender deleted");
        } catch (cause) {
            setDeleteError(cause instanceof Error ? cause.message : "Failed to delete sender");
        } finally {
            setDeleting(false);
        }
    };

    const query = search.trim().toLowerCase();
    const filtered = senders.filter((sender) =>
        [sender.name, sender.from_email, sender.from_name, sender.provider].some((value) =>
            value?.toLowerCase().includes(query)
        )
    );
    const hasSmtpSender = senders.some((sender) => sender.provider === "smtp");

    return (
        <>
            <PageLayout
                title="Email senders"
                description="Connect the mailboxes and accounts Bolo sends email through."
            >
                <PageSection
                    title="Sending identities"
                    description="Each sender uses your own provider account. Secrets are encrypted and never shown again."
                    actions={
                        <Button
                            onClick={() => {
                                setEditTarget(null);
                                setDialogOpen(true);
                            }}
                            disabled={authLoading || !user || loading || !!error}
                        >
                            <Plus className="h-4 w-4" />
                            Add sender
                        </Button>
                    }
                    toolbar={
                        <div className="relative">
                            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                            <Input
                                aria-label="Search senders"
                                placeholder="Search senders..."
                                value={search}
                                onChange={(event) => setSearch(event.target.value)}
                                className="pl-10"
                            />
                        </div>
                    }
                >
                    {authLoading || !user || loading ? (
                        <div className="space-y-4" role="status" aria-label="Loading senders">
                            {[1, 2, 3].map((item) => (
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
                                <Mail className="h-6 w-6 text-primary" aria-hidden />
                            </div>
                            <h3 className="text-base font-semibold text-foreground">
                                {query ? "No senders match your search" : "No email senders yet"}
                            </h3>
                            <p className="mx-auto mt-1 max-w-sm text-sm text-muted-foreground">
                                {query
                                    ? "Try a different name, address, or provider."
                                    : "Add an SMTP or Amazon SES sender to deliver campaigns from your own domain."}
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
                                    Add your first sender
                                </Button>
                            )}
                        </div>
                    ) : (
                        <div className="space-y-4">
                            {filtered.map((sender, index) => (
                                <div
                                    key={sender.uuid}
                                    style={{ animationDelay: `${Math.min(index, 6) * 40}ms` }}
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
                                                    {sender.name}
                                                </button>
                                            </h3>
                                            {sender.is_default && (
                                                <Badge variant="outline">Default</Badge>
                                            )}
                                            {statusBadge(sender.status)}
                                            <Badge variant="secondary">
                                                {PROVIDER_LABELS[sender.provider] ?? sender.provider}
                                            </Badge>
                                        </div>
                                        <p className="break-words text-sm text-muted-foreground">
                                            {sender.from_name} &lt;{sender.from_email}&gt;
                                        </p>
                                        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                                            <span>
                                                Limit {sender.limits.daily_cap}/day ·{" "}
                                                {sender.limits.rate_per_second}/second
                                            </span>
                                            <span>
                                                {sender.last_verified_at
                                                    ? `Checked ${new Date(
                                                        sender.last_verified_at
                                                    ).toLocaleDateString()}`
                                                    : "Never checked"}
                                            </span>
                                            {sender.credential_hint?.username && (
                                                <span>Login {sender.credential_hint.username}</span>
                                            )}
                                            {sender.credential_hint?.access_key_id && (
                                                <span>
                                                    Access key {sender.credential_hint.access_key_id}
                                                </span>
                                            )}
                                            {!sender.credentials_configured && (
                                                <span className="text-destructive">
                                                    Credentials missing
                                                </span>
                                            )}
                                        </div>
                                    </div>
                                    <div className={`${LIST_ROW_ACTIONS_CLASS} shrink-0 self-end sm:self-auto`}>
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            disabled={verifying === sender.uuid}
                                            onClick={() => void verifySender(sender)}
                                        >
                                            {verifying === sender.uuid ? (
                                                <Loader2 className="h-4 w-4 animate-spin" />
                                            ) : sender.status === "verified" ? (
                                                <CheckCircle2 className="h-4 w-4" />
                                            ) : (
                                                <ShieldCheck className="h-4 w-4" />
                                            )}
                                            {verifying === sender.uuid ? "Checking..." : "Verify"}
                                        </Button>
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            onClick={() => setTestTarget(sender)}
                                        >
                                            <Send className="h-4 w-4" />
                                            Send test
                                        </Button>
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            aria-label={`Edit ${sender.name}`}
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
                                            aria-label={`Delete ${sender.name}`}
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

                {hasSmtpSender && (
                    <div className="flex items-start gap-2 rounded-lg border bg-muted/40 p-4 text-sm text-muted-foreground">
                        <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
                        <p>
                            SMTP hosts do not report bounces or complaints back to Bolo.
                            Monitor the mailbox you send from and remove bad addresses manually;
                            SES senders report these events through AWS.
                        </p>
                    </div>
                )}
            </PageLayout>

            <SenderDialog
                open={dialogOpen}
                sender={editTarget ?? undefined}
                onOpenChange={(open) => {
                    setDialogOpen(open);
                    if (!open) setEditTarget(null);
                }}
                onSaved={(sender) => {
                    upsertSender(sender);
                    toast.success(editTarget ? "Sender updated" : "Sender added");
                }}
            />

            <TestSendDialog
                sender={testTarget}
                onClose={() => setTestTarget(null)}
            />

            <AlertDialog
                open={!!deleteTarget}
                onOpenChange={(open) => {
                    if (!open && !deleting) setDeleteTarget(null);
                }}
            >
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Delete sender?</AlertDialogTitle>
                        <AlertDialogDescription>
                            Delete &quot;{deleteTarget?.name}&quot;? Its stored credentials are
                            deactivated. A sender that already sent messages must be disabled
                            instead, so delivery history keeps its origin.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    {deleteError && (
                        <p role="alert" className="text-sm text-destructive">
                            {deleteError}
                        </p>
                    )}
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
                        <Button variant="destructive" disabled={deleting} onClick={deleteSender}>
                            {deleting && <Loader2 className="h-4 w-4 animate-spin" />}
                            {deleting ? "Deleting..." : "Delete"}
                        </Button>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </>
    );
}

function TestSendDialog({
    sender,
    onClose,
}: {
    sender: EmailSenderResponse | null;
    onClose: () => void;
}) {
    const { getAccessToken } = useAuth();
    const [recipient, setRecipient] = useState("");
    const [sending, setSending] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        setRecipient("");
        setError(null);
    }, [sender?.uuid]);

    if (!sender) return null;

    const send = async () => {
        if (!recipient.trim() || sending) return;
        setSending(true);
        setError(null);
        try {
            const token = await getAccessToken();
            const response = await testSendEmailApiV1EmailSendersSenderUuidTestSendPost({
                headers: { Authorization: `Bearer ${token}` },
                path: { sender_uuid: sender.uuid },
                body: { to_email: recipient.trim() },
            });
            if (response.error) {
                throw new Error(detailFromError(response.error, "Test send failed"));
            }
            toast.success("Test message accepted. Check the inbox.");
            onClose();
        } catch (cause) {
            setError(cause instanceof Error ? cause.message : "Test send failed");
        } finally {
            setSending(false);
        }
    };

    return (
        <Dialog open onOpenChange={(open) => !open && !sending && onClose()}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle>Send a test message</DialogTitle>
                    <DialogDescription>
                        Send one message through &quot;{sender.name}&quot; right now. This does
                        not use a campaign or write a message record.
                    </DialogDescription>
                </DialogHeader>
                {error && (
                    <div
                        role="alert"
                        className="flex items-start gap-2 rounded-md border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive"
                    >
                        <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
                        <span>{error}</span>
                    </div>
                )}
                <div className="grid gap-2 py-2">
                    <Label htmlFor="test-recipient">Send to *</Label>
                    <Input
                        id="test-recipient"
                        type="email"
                        value={recipient}
                        onChange={(event) => setRecipient(event.target.value)}
                        placeholder="you@example.com"
                    />
                    {sender.provider === "ses" && (
                        <p className="text-xs text-muted-foreground">
                            In the SES sandbox, this address must already be verified in AWS.
                        </p>
                    )}
                </div>
                <DialogFooter>
                    <Button variant="outline" onClick={onClose} disabled={sending}>
                        Cancel
                    </Button>
                    <Button onClick={send} disabled={!recipient.trim() || sending}>
                        {sending && <Loader2 className="h-4 w-4 animate-spin" />}
                        {sending ? "Sending..." : "Send test"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
