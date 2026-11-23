"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { downloadLocalModel, getLocalModelDownload, listLocalModels } from "@/client/sdk.gen";
import type { LocalModelInfo, LocalModelsResponse } from "@/client/types.gen";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

import type { LocalModelFamily } from "./configuration";

export interface LocalInstallState {
    status: "running" | "done" | "error";
    progress?: number;
    detail?: string;
}

type Job = LocalInstallState & { job_id: string; title: string };

const keyFor = (family: LocalModelFamily, model: string) => `${family}:${model}`;

/**
 * Installs for the models a configuration names, so the editor can offer a
 * download when the machine does not have one yet. The download itself runs
 * on the server, so it survives the editor closing; progress is read back
 * until the model appears.
 */
export function useLocalModelInstalls(enabled: boolean) {
    const { user, loading: authLoading } = useAuth();
    const [data, setData] = useState<LocalModelsResponse | null>(null);
    const [jobs, setJobs] = useState<Record<string, Job>>({});
    // The list needs a session; asking before auth resolves only earns a 401.
    const ready = enabled && Boolean(user?.id) && !authLoading;

    const load = useCallback(async () => {
        try {
            const response = await listLocalModels();
            if (response.data) setData(response.data);
        } catch { /* the download offer is an extra; the editor works without it */ }
    }, []);

    useEffect(() => {
        if (ready) void load();
    }, [ready, load]);

    // Ask the server how each download is doing until it lands or fails.
    useEffect(() => {
        const running = Object.entries(jobs).filter(([, job]) => job.status === "running");
        if (!running.length) return;
        let cancelled = false;
        let timer: ReturnType<typeof setTimeout>;
        const tick = async () => {
            for (const [key, job] of running) {
                try {
                    const response = await getLocalModelDownload({ path: { job_id: job.job_id } });
                    if (cancelled) return;
                    if (response.data?.status === "done") {
                        setJobs(previous => ({ ...previous, [key]: { ...previous[key], status: "done" } }));
                        toast.success(`${job.title} is ready`);
                        void load();
                    } else if (response.data?.status === "error") {
                        setJobs(previous => ({ ...previous, [key]: { ...previous[key], status: "error", detail: response.data?.detail || "The download failed." } }));
                        void load();
                    } else if (response.error) {
                        // The job is gone (a restart); the model list tells the truth.
                        setJobs(previous => {
                            const next = { ...previous };
                            delete next[key];
                            return next;
                        });
                        void load();
                    } else if (response.data?.total) {
                        const progress = Math.min((response.data.completed ?? 0) / response.data.total, 1);
                        setJobs(previous => ({ ...previous, [key]: { ...previous[key], progress } }));
                    }
                } catch { /* keep polling */ }
            }
            if (!cancelled) timer = setTimeout(tick, 1500);
        };
        timer = setTimeout(tick, 1500);
        return () => { cancelled = true; clearTimeout(timer); };
    }, [jobs, load]);

    const install = useCallback(async (family: LocalModelFamily, model: string, title?: string) => {
        const key = keyFor(family, model);
        const name = title || model;
        setJobs(previous => ({ ...previous, [key]: { job_id: "", title: name, status: "running" } }));
        try {
            const response = await downloadLocalModel({ body: { family, model } });
            if (response.error || !response.data) throw new Error(detailFromError(response.error, "Could not start the download"));
            setJobs(previous => ({ ...previous, [key]: { job_id: response.data!.job_id, title: name, status: "running" } }));
            toast.success(`Downloading ${name}`);
            void load();
        } catch (cause) {
            setJobs(previous => ({ ...previous, [key]: { job_id: "", title: name, status: "error", detail: cause instanceof Error ? cause.message : "Could not start the download" } }));
        }
    }, [load]);

    const entry = useCallback((family: LocalModelFamily, model: string): LocalModelInfo | undefined =>
        data?.models.find(item => item.family === family && item.id === model), [data]);

    const installed = useCallback((family: LocalModelFamily, model: string): boolean =>
        Boolean(entry(family, model)?.installed), [entry]);

    const state = useCallback((family: LocalModelFamily, model: string): LocalInstallState | null => {
        const job = jobs[keyFor(family, model)];
        if (job) return { status: job.status, progress: job.progress, detail: job.detail };
        const known = entry(family, model);
        if (known?.downloading) return { status: "running", progress: known.progress ?? undefined };
        return null;
    }, [jobs, entry]);

    return { data, install, installed, state, entry };
}
