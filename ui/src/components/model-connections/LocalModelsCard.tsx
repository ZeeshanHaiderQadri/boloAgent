"use client";

import { Download, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { deleteLocalModel, downloadLocalModel, getLocalModelDownload, listLocalModels } from "@/client/sdk.gen";
import type { LocalModelInfo, LocalModelsResponse } from "@/client/types.gen";
import { LIST_ROW_ACTIONS_CLASS, LIST_ROW_CLASS } from "@/components/layout/ListRow";
import { PageSection } from "@/components/layout/PageLayout";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Progress } from "@/components/ui/progress";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

const KIND_LABELS: Record<LocalModelInfo["kind"], string> = { llm: "Replies", stt: "Listening", tts: "Voice" };
const FAMILY_LABELS: Record<LocalModelInfo["family"], string> = {
    ollama: "Chat model on this machine",
    speaches: "Speech model on this machine",
};

function formatBytes(bytes: number | null | undefined): string | null {
    if (!bytes) return null;
    // Decimal units, so the number matches what `ollama list` shows.
    if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
    return `${Math.max(1, Math.round(bytes / 1e6))} MB`;
}

const modelKey = (model: LocalModelInfo) => `${model.family}:${model.id}`;

type DownloadJob = { job_id: string; title: string };

/**
 * Install and remove the models that run on this machine. The panel appears
 * only when the API can reach a local model server, which keeps it out of
 * cloud-only deployments.
 */
export function LocalModelsCard() {
    const { user, loading: authLoading } = useAuth();
    const [data, setData] = useState<LocalModelsResponse | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [removing, setRemoving] = useState<LocalModelInfo | null>(null);
    const [jobs, setJobs] = useState<DownloadJob[]>([]);
    const userId = user?.id;

    const load = useCallback(async () => {
        const response = await listLocalModels();
        if (response.data) setData(response.data);
    }, []);

    useEffect(() => {
        if (authLoading || !userId) return;
        void load();
    }, [authLoading, userId, load]);

    const finishJob = useCallback((jobId: string) => {
        setJobs(previous => previous.filter(job => job.job_id !== jobId));
    }, []);

    // Ask the server how each download is doing until it finishes or fails.
    useEffect(() => {
        if (!jobs.length) return;
        let cancelled = false;
        const check = async () => {
            for (const job of jobs) {
                const response = await getLocalModelDownload({ path: { job_id: job.job_id } });
                if (cancelled) return;
                if (response.data?.status === "done") {
                    toast.success(`${job.title} is ready`);
                    finishJob(job.job_id);
                    void load();
                } else if (response.data?.status === "error") {
                    toast.error(response.data.detail || `${job.title} could not be installed`);
                    finishJob(job.job_id);
                    void load();
                } else if (response.error) {
                    // The job is gone (a restart, or another worker) but the
                    // model list below still tells the truth.
                    finishJob(job.job_id);
                }
            }
        };
        const timer = setInterval(() => void check(), 1500);
        void check();
        return () => { cancelled = true; clearInterval(timer); };
    }, [jobs, finishJob, load]);

    // Keep progress fresh for downloads started before this page opened.
    const anyDownloading = data?.models.some(model => model.downloading) ?? false;
    useEffect(() => {
        if (!anyDownloading) return;
        const timer = setInterval(() => void load(), 3000);
        return () => clearInterval(timer);
    }, [anyDownloading, load]);

    const install = async (model: LocalModelInfo) => {
        setBusy(modelKey(model));
        try {
            const response = await downloadLocalModel({ body: { family: model.family, model: model.id } });
            if (response.error || !response.data) throw new Error(detailFromError(response.error, "Could not start the download"));
            setJobs(previous => [...previous, { job_id: response.data.job_id, title: model.title }]);
            await load();
            toast.success(`Downloading ${model.title}`);
        } catch (cause) {
            toast.error(cause instanceof Error ? cause.message : "Could not start the download");
        } finally {
            setBusy(null);
        }
    };

    const removalBusy = removing !== null && busy === modelKey(removing);
    const remove = async () => {
        if (!removing) return;
        const model = removing;
        setBusy(modelKey(model));
        try {
            const response = await deleteLocalModel({ path: { family: model.family, model: model.id } });
            if (response.error) throw new Error(detailFromError(response.error, "Could not remove the model"));
            setRemoving(null);
            await load();
            toast.success(`${model.title} removed`);
        } catch (cause) {
            toast.error(cause instanceof Error ? cause.message : "Could not remove the model");
        } finally {
            setBusy(null);
        }
    };

    if (!data || data.servers.length === 0) return null;
    const installed = data.models.filter(model => model.installed);
    const available = data.models.filter(model => !model.installed);

    return <>
        <PageSection title="Models on this machine"
            description="Install a replies, listening or voice model, or remove one you no longer use. Calls can use these without a cloud key.">
            {installed.length > 0 && <div className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Installed</p>
                {installed.map(model => <div key={modelKey(model)} className={LIST_ROW_CLASS}>
                    <div className="min-w-0 flex-1 space-y-1">
                        <h3 className="flex flex-wrap items-center gap-2 font-medium">
                            {model.title}<Badge variant="secondary" className="font-normal">{KIND_LABELS[model.kind]}</Badge>
                        </h3>
                        <p className="text-sm text-muted-foreground">
                            {[formatBytes(model.size_bytes), FAMILY_LABELS[model.family]].filter(Boolean).join(" · ")}
                        </p>
                    </div>
                    <div className={LIST_ROW_ACTIONS_CLASS}>
                        <Button type="button" variant="ghost" size="sm" disabled={busy !== null} onClick={() => setRemoving(model)}>
                            <Trash2 className="h-3.5 w-3.5" />Remove
                        </Button>
                    </div>
                </div>)}
            </div>}
            {available.length > 0 && <div className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Add a model</p>
                {available.map(model => <div key={modelKey(model)} className={LIST_ROW_CLASS}>
                    <div className="min-w-0 flex-1 space-y-1.5">
                        <h3 className="flex flex-wrap items-center gap-2 font-medium">
                            {model.title}<Badge variant="secondary" className="font-normal">{KIND_LABELS[model.kind]}</Badge>
                        </h3>
                        {model.description && <p className="text-sm text-muted-foreground">{model.description}</p>}
                        {model.downloading && <div className="flex items-center gap-3 pt-1">
                            <Progress className="max-w-60" value={model.progress == null ? undefined : Math.round(model.progress * 100)} />
                            <span className="shrink-0 text-xs text-muted-foreground">
                                {model.progress == null ? "Downloading…" : `${Math.round(model.progress * 100)}%`}
                            </span>
                        </div>}
                    </div>
                    <div className={LIST_ROW_ACTIONS_CLASS}>
                        <Button type="button" size="sm" disabled={model.downloading || busy !== null} onClick={() => void install(model)}>
                            <Download className="h-3.5 w-3.5" />{model.downloading ? "Installing…" : "Install"}
                        </Button>
                    </div>
                </div>)}
            </div>}
            <p className="text-xs text-muted-foreground">Downloads and removals run on this machine. Calls keep working while a model installs.</p>
        </PageSection>
        <Dialog open={removing !== null} onOpenChange={open => { if (!open && !removalBusy) setRemoving(null); }}>
            <DialogContent onOpenAutoFocus={() => undefined}>
                <DialogHeader>
                    <DialogTitle>Remove {removing?.title}?</DialogTitle>
                    <DialogDescription>This deletes the model files from this machine. Anything using them needs another model until you install it again.</DialogDescription>
                </DialogHeader>
                <div className="flex justify-end gap-2">
                    <Button variant="outline" disabled={removalBusy} onClick={() => setRemoving(null)}>Cancel</Button>
                    <Button variant="destructive" disabled={removalBusy} onClick={() => void remove()}>{removalBusy ? "Removing…" : "Remove model"}</Button>
                </div>
            </DialogContent>
        </Dialog>
    </>;
}
