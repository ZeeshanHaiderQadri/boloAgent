"use client";

import { useCallback, useContext, useEffect, useState } from "react";

import { listConnectionModels } from "@/client/sdk.gen";
import { AuthContext } from "@/lib/auth/providers/AuthProvider";

type CatalogStatus = "idle" | "loading" | "ready" | "error";

interface CatalogState {
    status: CatalogStatus;
    models?: string[];
    error?: string;
}

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
                : { status: "ready" as const, models: (response.data?.models || []).map(model => model.id) },
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

export function useConnectionModels(connectionUuid?: string, connectionRevision?: number) {
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
        setState(previous => ({ status: "loading", models: previous.models }));
        loadModels(key, connectionUuid!).then(result => { if (active) setState(result); });
        return () => { active = false; };
    }, [key, connectionUuid, attempt]);

    const reload = useCallback(() => {
        if (key) connectionModels.delete(key);
        setAttempt(count => count + 1);
    }, [key]);

    return { ...state, reload };
}
