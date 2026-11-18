"use client";

import { AlertCircle, CheckCircle2, Info, Loader2, Wand2 } from "lucide-react";
import { useState } from "react";

import {
    createEmailSenderApiV1EmailSendersPost,
    updateEmailSenderApiV1EmailSendersSenderUuidPatch,
} from "@/client";
import type { EmailCredentials, EmailProvider, EmailSenderResponse } from "@/client/types.gen";
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
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

const HOSTINGER_PRESET = {
    host: "smtp.hostinger.com",
    port: "465",
    security: "ssl",
};

const PROVIDERS: { value: EmailProvider; label: string; supported: boolean }[] = [
    { value: "smtp", label: "SMTP (Hostinger or any mail host)", supported: true },
    { value: "ses", label: "Amazon SES", supported: true },
    { value: "mailgun", label: "Mailgun (coming soon)", supported: false },
    { value: "mandrill", label: "Mandrill (coming soon)", supported: false },
];

interface SenderDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    sender?: EmailSenderResponse;
    onSaved: (sender: EmailSenderResponse) => void;
}

export function SenderDialog(props: SenderDialogProps) {
    // Remount on close so a typed password never survives reopening.
    return props.open ? (
        <SenderDialogForm key={props.sender?.uuid ?? "new"} {...props} />
    ) : null;
}

function SenderDialogForm({ open, onOpenChange, sender, onSaved }: SenderDialogProps) {
    const { getAccessToken } = useAuth();
    const editing = !!sender;

    const [name, setName] = useState(sender?.name ?? "");
    const [provider, setProvider] = useState<EmailProvider>(
        (sender?.provider as EmailProvider) ?? "smtp"
    );
    const [fromName, setFromName] = useState(sender?.from_name ?? "");
    const [fromEmail, setFromEmail] = useState(sender?.from_email ?? "");
    const [replyTo, setReplyTo] = useState(sender?.reply_to_email ?? "");
    const [host, setHost] = useState(String(sender?.settings?.host ?? ""));
    const [port, setPort] = useState(String(sender?.settings?.port ?? "465"));
    const [security, setSecurity] = useState(
        String(sender?.settings?.security ?? "ssl")
    );
    const [region, setRegion] = useState(String(sender?.settings?.region ?? "eu-west-1"));
    const [configurationSet, setConfigurationSet] = useState(
        String(sender?.settings?.configuration_set ?? "")
    );
    const [username, setUsername] = useState("");
    const [password, setPassword] = useState("");
    const [accessKeyId, setAccessKeyId] = useState("");
    const [secretAccessKey, setSecretAccessKey] = useState("");
    const [sessionToken, setSessionToken] = useState("");
    const [replaceCredentials, setReplaceCredentials] = useState(false);
    const [isDefault, setIsDefault] = useState(sender?.is_default ?? false);
    const [dailyCap, setDailyCap] = useState(
        sender?.daily_cap_override != null ? String(sender.daily_cap_override) : ""
    );
    const [ratePerSecond, setRatePerSecond] = useState(
        sender?.rate_per_second_override != null
            ? String(sender.rate_per_second_override)
            : ""
    );
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const providerSupported = PROVIDERS.find((item) => item.value === provider)?.supported;
    const presetActive =
        host === HOSTINGER_PRESET.host &&
        port === HOSTINGER_PRESET.port &&
        security === HOSTINGER_PRESET.security;
    const credentialsNeeded =
        !editing || replaceCredentials;

    const credentials: EmailCredentials | null =
        provider === "smtp"
            ? { username: username.trim(), password }
            : provider === "ses"
                ? {
                    access_key_id: accessKeyId.trim(),
                    secret_access_key: secretAccessKey,
                    session_token: sessionToken.trim() || null,
                }
                : null;

    const credentialsComplete =
        provider === "smtp"
            ? !!username.trim() && !!password
            : provider === "ses"
                ? !!accessKeyId.trim() && !!secretAccessKey
                : false;

    const settingsFor = (): Record<string, unknown> =>
        provider === "smtp"
            ? { host: host.trim(), port: Number(port), security }
            : {
                region: region.trim(),
                ...(configurationSet.trim()
                    ? { configuration_set: configurationSet.trim() }
                    : {}),
            };

    const settingsChanged =
        !editing ||
        JSON.stringify(settingsFor()) !== JSON.stringify(cleanSettings(sender));

    const dailyCapValid =
        !dailyCap.trim() || (Number.isFinite(Number(dailyCap)) && Number(dailyCap) >= 1);
    const rateValid =
        !ratePerSecond.trim() ||
        (Number.isFinite(Number(ratePerSecond)) && Number(ratePerSecond) > 0);
    const connectionValid =
        provider === "smtp"
            ? !!host.trim() && Number(port) > 0
            : !!region.trim();
    const canSave =
        !!providerSupported &&
        !!name.trim() &&
        !!fromName.trim() &&
        !!fromEmail.trim() &&
        connectionValid &&
        dailyCapValid &&
        rateValid &&
        (!credentialsNeeded || credentialsComplete);

    const applyHostingerPreset = () => {
        setHost(HOSTINGER_PRESET.host);
        setPort(HOSTINGER_PRESET.port);
        setSecurity(HOSTINGER_PRESET.security);
    };

    const handleSave = async () => {
        if (!canSave || saving) return;
        setSaving(true);
        setError(null);
        try {
            const headers = {
                Authorization: `Bearer ${await getAccessToken()}`,
            };
            const limits = {
                daily_cap: dailyCap.trim() ? Number(dailyCap) : null,
                rate_per_second: ratePerSecond.trim()
                    ? Number(ratePerSecond)
                    : null,
            };

            if (editing) {
                const body = {
                    name: name.trim(),
                    from_name: fromName.trim(),
                    from_email: fromEmail.trim(),
                    reply_to_email: replyTo.trim() || null,
                    ...(settingsChanged ? { settings: settingsFor() } : {}),
                    ...(replaceCredentials && credentials ? { credentials } : {}),
                    is_default: isDefault,
                    ...limits,
                };
                const response = await updateEmailSenderApiV1EmailSendersSenderUuidPatch({
                    headers,
                    path: { sender_uuid: sender.uuid },
                    body,
                });
                if (response.error) {
                    setError(detailFromError(response.error, "Failed to save sender"));
                    return;
                }
                onSaved(response.data as EmailSenderResponse);
            } else {
                const response = await createEmailSenderApiV1EmailSendersPost({
                    headers,
                    body: {
                        name: name.trim(),
                        provider,
                        from_name: fromName.trim(),
                        from_email: fromEmail.trim(),
                        reply_to_email: replyTo.trim() || null,
                        settings: settingsFor(),
                        credentials: credentials ?? {},
                        is_default: isDefault,
                        ...limits,
                    },
                });
                if (response.error) {
                    setError(detailFromError(response.error, "Failed to save sender"));
                    return;
                }
                onSaved(response.data as EmailSenderResponse);
            }
            onOpenChange(false);
        } catch (cause) {
            setError(cause instanceof Error ? cause.message : "Failed to save sender");
        } finally {
            setSaving(false);
        }
    };

    const serverDefaults =
        provider === "smtp"
            ? { daily_cap: 100, rate_per_second: 1 }
            : { daily_cap: 200, rate_per_second: 1 };

    return (
        <Dialog open={open} onOpenChange={(next) => !saving && onOpenChange(next)}>
            <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
                <DialogHeader>
                    <DialogTitle>{editing ? "Edit sender" : "Add sender"}</DialogTitle>
                    <DialogDescription>
                        Bolo sends through your own account. Credentials are encrypted and
                        never shown again after saving.
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

                <fieldset disabled={saving} className="min-w-0 space-y-4 py-2">
                    <div className="grid gap-2">
                        <Label htmlFor="sender-name">Sender name *</Label>
                        <Input
                            id="sender-name"
                            value={name}
                            onChange={(event) => setName(event.target.value)}
                            placeholder="Support"
                        />
                        <p className="text-xs text-muted-foreground">
                            Only used inside Bolo; recipients see the From name below.
                        </p>
                    </div>

                    <div className="grid gap-2">
                        <Label htmlFor="sender-provider">Provider</Label>
                        <Select
                            value={provider}
                            onValueChange={(value) => {
                                setProvider(value as EmailProvider);
                                setReplaceCredentials(false);
                            }}
                            disabled={editing}
                        >
                            <SelectTrigger id="sender-provider" className="w-full">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                {PROVIDERS.map((item) => (
                                    <SelectItem
                                        key={item.value}
                                        value={item.value}
                                        disabled={!item.supported}
                                    >
                                        {item.label}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        {editing && (
                            <p className="text-xs text-muted-foreground">
                                The provider cannot be changed; add a new sender instead.
                            </p>
                        )}
                    </div>

                    <div className="grid gap-4 sm:grid-cols-2">
                        <div className="grid gap-2">
                            <Label htmlFor="sender-from-name">From name *</Label>
                            <Input
                                id="sender-from-name"
                                value={fromName}
                                onChange={(event) => setFromName(event.target.value)}
                                placeholder="Bolo Support"
                            />
                        </div>
                        <div className="grid gap-2">
                            <Label htmlFor="sender-from-email">From address *</Label>
                            <Input
                                id="sender-from-email"
                                type="email"
                                value={fromEmail}
                                onChange={(event) => setFromEmail(event.target.value)}
                                placeholder="support@yourdomain.com"
                            />
                        </div>
                    </div>

                    <div className="grid gap-2">
                        <Label htmlFor="sender-reply-to">Reply-to address</Label>
                        <Input
                            id="sender-reply-to"
                            type="email"
                            value={replyTo}
                            onChange={(event) => setReplyTo(event.target.value)}
                            placeholder="Optional; defaults to the From address"
                        />
                    </div>

                    {provider === "smtp" && (
                        <div className="space-y-3 rounded-lg border p-4">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <div>
                                    <p className="text-sm font-medium">Mail server</p>
                                    <p className="text-xs text-muted-foreground">
                                        Hostinger shared mailboxes use port 465 with SSL.
                                    </p>
                                </div>
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    onClick={applyHostingerPreset}
                                >
                                    {presetActive ? (
                                        <CheckCircle2 className="h-4 w-4" />
                                    ) : (
                                        <Wand2 className="h-4 w-4" />
                                    )}
                                    {presetActive ? "Hostinger preset applied" : "Use Hostinger preset"}
                                </Button>
                            </div>
                            <div className="grid gap-3 sm:grid-cols-[1fr_7rem_9rem]">
                                <div className="grid gap-2">
                                    <Label htmlFor="smtp-host">Host *</Label>
                                    <Input
                                        id="smtp-host"
                                        value={host}
                                        onChange={(event) => setHost(event.target.value)}
                                        placeholder="smtp.hostinger.com"
                                    />
                                </div>
                                <div className="grid gap-2">
                                    <Label htmlFor="smtp-port">Port *</Label>
                                    <Input
                                        id="smtp-port"
                                        inputMode="numeric"
                                        value={port}
                                        onChange={(event) => setPort(event.target.value)}
                                    />
                                </div>
                                <div className="grid gap-2">
                                    <Label htmlFor="smtp-security">Security</Label>
                                    <Select value={security} onValueChange={setSecurity}>
                                        <SelectTrigger id="smtp-security" className="w-full">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="ssl">SSL (implicit TLS)</SelectItem>
                                            <SelectItem value="starttls">STARTTLS</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                            </div>
                            <div className="flex items-start gap-2 rounded-md bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-400">
                                <Info className="mt-0.5 h-4 w-4 flex-shrink-0" />
                                <span>
                                    Plain SMTP does not report bounces or complaints back to Bolo.
                                    Use an address you monitor, and keep your list clean manually.
                                </span>
                            </div>
                        </div>
                    )}

                    {provider === "ses" && (
                        <div className="space-y-3 rounded-lg border p-4">
                            <div className="grid gap-3 sm:grid-cols-2">
                                <div className="grid gap-2">
                                    <Label htmlFor="ses-region">Region *</Label>
                                    <Input
                                        id="ses-region"
                                        value={region}
                                        onChange={(event) => setRegion(event.target.value)}
                                        placeholder="eu-west-1"
                                    />
                                </div>
                                <div className="grid gap-2">
                                    <Label htmlFor="ses-config-set">Configuration set</Label>
                                    <Input
                                        id="ses-config-set"
                                        value={configurationSet}
                                        onChange={(event) => setConfigurationSet(event.target.value)}
                                        placeholder="Optional"
                                    />
                                </div>
                            </div>
                            <p className="text-xs text-muted-foreground">
                                The From address or its whole domain must first be verified in
                                SES. A new account stays in the sandbox and can only send to
                                verified addresses.
                            </p>
                        </div>
                    )}

                    {credentialsNeeded ? (
                        <div className="space-y-3 rounded-lg border p-4">
                            <div>
                                <p className="text-sm font-medium">
                                    {provider === "smtp" ? "SMTP login" : "AWS access key"}
                                </p>
                                <p className="text-xs text-muted-foreground">
                                    Encrypted at rest. Leave blank on edit to keep the current
                                    secret.
                                </p>
                            </div>
                            {provider === "smtp" ? (
                                <div className="grid gap-3 sm:grid-cols-2">
                                    <div className="grid gap-2">
                                        <Label htmlFor="smtp-username">Username *</Label>
                                        <Input
                                            id="smtp-username"
                                            autoComplete="off"
                                            value={username}
                                            onChange={(event) => setUsername(event.target.value)}
                                            placeholder="mailbox@yourdomain.com"
                                        />
                                    </div>
                                    <div className="grid gap-2">
                                        <Label htmlFor="smtp-password">Password *</Label>
                                        <Input
                                            id="smtp-password"
                                            type="password"
                                            autoComplete="new-password"
                                            value={password}
                                            onChange={(event) => setPassword(event.target.value)}
                                        />
                                    </div>
                                </div>
                            ) : (
                                <div className="space-y-3">
                                    <div className="grid gap-2">
                                        <Label htmlFor="ses-access-key">Access key ID *</Label>
                                        <Input
                                            id="ses-access-key"
                                            autoComplete="off"
                                            value={accessKeyId}
                                            onChange={(event) => setAccessKeyId(event.target.value)}
                                        />
                                    </div>
                                    <div className="grid gap-2">
                                        <Label htmlFor="ses-secret">Secret access key *</Label>
                                        <Input
                                            id="ses-secret"
                                            type="password"
                                            autoComplete="new-password"
                                            value={secretAccessKey}
                                            onChange={(event) => setSecretAccessKey(event.target.value)}
                                        />
                                    </div>
                                    <div className="grid gap-2">
                                        <Label htmlFor="ses-token">Session token</Label>
                                        <Input
                                            id="ses-token"
                                            type="password"
                                            autoComplete="new-password"
                                            value={sessionToken}
                                            onChange={(event) => setSessionToken(event.target.value)}
                                            placeholder="Only for temporary credentials"
                                        />
                                    </div>
                                </div>
                            )}
                            {editing && (
                                <Button
                                    type="button"
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => setReplaceCredentials(false)}
                                >
                                    Keep current credentials
                                </Button>
                            )}
                        </div>
                    ) : (
                        <Button
                            type="button"
                            variant="outline"
                            onClick={() => setReplaceCredentials(true)}
                        >
                            Replace credentials
                        </Button>
                    )}

                    <div className="space-y-3 rounded-lg border p-4">
                        <div>
                            <p className="text-sm font-medium">Sending limits</p>
                            <p className="text-xs text-muted-foreground">
                                Blank uses the conservative provider default shown below.
                            </p>
                        </div>
                        <div className="grid gap-3 sm:grid-cols-2">
                            <div className="grid gap-2">
                                <Label htmlFor="daily-cap">Daily cap</Label>
                                <Input
                                    id="daily-cap"
                                    inputMode="numeric"
                                    value={dailyCap}
                                    onChange={(event) => setDailyCap(event.target.value)}
                                    placeholder={String(serverDefaults.daily_cap)}
                                />
                            </div>
                            <div className="grid gap-2">
                                <Label htmlFor="rate-per-second">Messages per second</Label>
                                <Input
                                    id="rate-per-second"
                                    inputMode="decimal"
                                    value={ratePerSecond}
                                    onChange={(event) => setRatePerSecond(event.target.value)}
                                    placeholder={String(serverDefaults.rate_per_second)}
                                />
                            </div>
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        <Checkbox
                            id="sender-default"
                            checked={isDefault}
                            onCheckedChange={(checked) => setIsDefault(checked === true)}
                        />
                        <Label htmlFor="sender-default">Use as the default sender</Label>
                    </div>
                </fieldset>

                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
                        Cancel
                    </Button>
                    <Button onClick={handleSave} disabled={!canSave || saving}>
                        {saving && <Loader2 className="h-4 w-4 animate-spin" />}
                        {saving ? "Saving..." : editing ? "Save changes" : "Add sender"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

function cleanSettings(sender: EmailSenderResponse | undefined): Record<string, unknown> {
    if (!sender) return {};
    const settings = sender.settings ?? {};
    if (sender.provider === "smtp") {
        return { host: settings.host, port: settings.port, security: settings.security };
    }
    return {
        region: settings.region,
        ...(settings.configuration_set ? { configuration_set: settings.configuration_set } : {}),
    };
}
