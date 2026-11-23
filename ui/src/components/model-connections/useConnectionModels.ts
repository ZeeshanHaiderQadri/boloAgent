"use client";

import { useCallback, useContext, useEffect, useMemo, useState } from "react";

import { listConnectionModels } from "@/client/sdk.gen";
import { AuthContext } from "@/lib/auth/providers/AuthProvider";

import type { ServiceRole } from "./types";

type CatalogStatus = "idle" | "loading" | "ready" | "error";

interface ModelEntry {
    id: string;
    task?: string;
}

interface CatalogState {
    status: CatalogStatus;
    entries?: ModelEntry[];
    error?: string;
}

// A speech server lists everything it hosts; each role only accepts some of
// it. Models without a task (chat servers, cloud routers) are never hidden.
const ROLE_EXCLUDED_TASKS: Partial<Record<ServiceRole, string[]>> = {
    llm: ["text-to-speech", "automatic-speech-recognition"],
    realtime: ["text-to-speech", "automatic-speech-recognition"],
    stt: ["text-to-speech"],
    tts: ["automatic-speech-recognition"],
};

const connectionModels = new Map<string, Promise<CatalogState>>();

function failure(error: unknown): CatalogState {
    const detail = (error as { detail?: unknown } | undefined)?.detail;
    return {
        status: "error",
        error: typeof detail === "string" ? detail : "Could not load models from the provider.",
    };
}

function loadModels(key: string, connectionUuid: string): Promise<CatalogState> {
    const existing = connectionModels.get(key);
    if (existing) return existing;
    const request = listConnectionModels({ path: { connection_uuid: connectionUuid } })
        .then(response =>
            response.error
                ? failure(response.error)
                : {
                    status: "ready" as const,
                    entries: (response.data?.models || []).map(model => ({ id: model.id, task: model.task ?? undefined })),
                },
        )
        .catch(() => failure(undefined));
    connectionModels.set(key, request);
    // A failure (provider down, unreachable endpoint) must not stick: the next
    // mount retries instead of showing built-in suggestions forever.
    request.then(result => {
        if (result.status === "error") connectionModels.delete(key);
    });
    return request;
}

export function useConnectionModels(connectionUuid?: string, connectionRevision?: number, role?: ServiceRole) {
    const auth = useContext(AuthContext);
    const [attempt, setAttempt] = useState(0);
    const [state, setState] = useState<CatalogState>({ status: "idle" });
    const key = auth?.user?.id && connectionUuid ? `${auth.user.id}:${connectionUuid}:${connectionRevision ?? ""}` : null;

    useEffect(() => {
        if (!key) {
            setState({ status: "idle" });
            return;
        }
        let active = true;
        setState(previous => ({ status: "loading", entries: previous.entries }));
        loadModels(key, connectionUuid!).then(result => { if (active) setState(result); });
        return () => { active = false; };
    }, [key, connectionUuid, attempt]);

    const models = useMemo(() => {
        if (!state.entries) return undefined;
        const excluded = new Set(role ? ROLE_EXCLUDED_TASKS[role] ?? [] : []);
        return state.entries
            .filter(entry => !entry.task || !excluded.has(entry.task))
            .map(entry => entry.id);
    }, [state.entries, role]);

    const reload = useCallback(() => {
        if (key) connectionModels.delete(key);
        setAttempt(count => count + 1);
    }, [key]);

    return { status: state.status, error: state.error, models, reload };
}
