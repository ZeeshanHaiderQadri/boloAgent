"use client";

import { Download } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";

import { localModelFamily } from "./configuration";
import type { ProviderConnection, ServiceRole } from "./types";
import type { useLocalModelInstalls } from "./useLocalModelInstalls";

const SERVER_LABELS = { ollama: "Ollama", speaches: "Hugging Face" } as const;

function formatBytes(bytes?: number | null): string | null {
    if (!bytes) return null;
    if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
    return `${Math.max(1, Math.round(bytes / 1e6))} MB`;
}

/**
 * Explains that the chosen model is not on this machine yet and starts the
 * download, so a model picked from a provider list never fails silently on
 * the first call. Renders nothing for cloud models or once the model is here.
 */
export function LocalModelNotice({ connection, role, model, installs }: {
    connection?: ProviderConnection;
    role: ServiceRole;
    model: string;
    installs: ReturnType<typeof useLocalModelInstalls>;
}) {
    const family = localModelFamily(connection, role, model);
    // Before the first listing arrives, or without that server connected,
    // there is nothing truthful to offer.
    if (!family || !installs.data?.servers.includes(family) || installs.installed(family, model)) return null;
    const state = installs.state(family, model);
    const info = installs.entry(family, model);
    const name = info?.title || model;
    const size = formatBytes(info?.size_bytes);

    if (state?.status === "running") return <div className="space-y-1.5 rounded-md border bg-muted/40 p-3 text-sm">
        <p>
            Downloading <span className="font-medium">{name}</span>
            <span className="text-muted-foreground"> from {SERVER_LABELS[family]}{size ? ` · ${size}` : ""}</span>
        </p>
        <div className="flex items-center gap-3">
            <Progress className="max-w-60" value={state.progress == null ? undefined : Math.round(state.progress * 100)} />
            <span className="shrink-0 text-xs text-muted-foreground">{state.progress == null ? "Starting…" : `${Math.round(state.progress * 100)}%`}</span>
        </div>
        <p className="text-xs text-muted-foreground">You can leave this page; the download keeps running and the model is ready when you come back.</p>
    </div>;

    if (state?.status === "error") return <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-destructive/40 p-3 text-sm text-destructive">
        <span>{state.detail || "The download failed."}</span>
        <Button type="button" size="sm" variant="outline" onClick={() => void installs.install(family, model, name)}>Try again</Button>
    </div>;

    return <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-dashed p-3 text-sm">
        <div className="min-w-0 space-y-0.5">
            <p><span className="font-medium">{model}</span> is not on this machine yet.</p>
            <p className="text-xs text-muted-foreground">
                {info?.description || `Download it from ${SERVER_LABELS[family]} and it appears here once it's ready.`}
                {size ? ` ${size}.` : ""}
            </p>
        </div>
        <Button type="button" size="sm" onClick={() => void installs.install(family, model, name)}>
            <Download className="h-3.5 w-3.5" />Download
        </Button>
    </div>;
}
