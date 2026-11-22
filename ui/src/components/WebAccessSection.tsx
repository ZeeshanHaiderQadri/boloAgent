"use client";

import { ExternalLink, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import {
  getWebAccessApiV1CopilotWebAccessGet,
  updateWebAccessApiV1CopilotWebAccessPut,
} from "@/client/sdk.gen";
import type {
  CopilotWebAccessSettings,
  CopilotWebProvider,
  CopilotWebProviderUpdate,
} from "@/client/types.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

export function WebAccessSection() {
  const { user, loading: authLoading } = useAuth();
  const [settings, setSettings] = useState<CopilotWebAccessSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const hasFetched = useRef(false);

  useEffect(() => {
    if (authLoading || !user || hasFetched.current) {
      return;
    }
    hasFetched.current = true;
    void fetchSettings();
  }, [authLoading, user]);

  async function fetchSettings() {
    try {
      const response = await getWebAccessApiV1CopilotWebAccessGet();
      if (response.error) {
        setError(detailFromError(response.error, "Could not load web access."));
        return;
      }
      setSettings(response.data ?? null);
      setError(null);
    } finally {
      setLoading(false);
    }
  }

  async function save(providerName: string, update: CopilotWebProviderUpdate) {
    setSaving(providerName);
    try {
      const response = await updateWebAccessApiV1CopilotWebAccessPut({
        body: { providers: [update] },
      });
      if (response.error) {
        toast.error(
          detailFromError(response.error, "Could not save web access."),
        );
        return false;
      }
      setSettings(response.data ?? null);
      return true;
    } finally {
      setSaving(null);
    }
  }

  async function toggle(provider: CopilotWebProvider, enabled: boolean) {
    const saved = await save(provider.name, {
      name: provider.name,
      enabled,
    });
    if (saved) {
      toast.success(`${provider.label} ${enabled ? "enabled" : "disabled"}`);
    }
  }

  async function saveKey(provider: CopilotWebProvider) {
    const apiKey = (drafts[provider.name] ?? "").trim();
    if (!apiKey) return;
    const saved = await save(provider.name, {
      name: provider.name,
      api_key: apiKey,
    });
    if (saved) {
      setDrafts((current) => {
        const next = { ...current };
        delete next[provider.name];
        return next;
      });
      toast.success(`${provider.label} key saved`);
    }
  }

  async function clearKey(provider: CopilotWebProvider) {
    const saved = await save(provider.name, {
      name: provider.name,
      clear_key: true,
      enabled: false,
    });
    if (saved) {
      toast.success(`${provider.label} key removed`);
    }
  }

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading...</p>;
  }

  if (error || !settings) {
    return (
      <div className="space-y-3 text-sm">
        <p className="text-destructive">{error ?? "Could not load web access."}</p>
        <Button variant="outline" size="sm" onClick={() => void fetchSettings()}>
          Try again
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Give the Bolo Agent live web search{settings.any_enabled ? "" : " and page reading"}.
        Search runs through the first enabled provider; page reading uses
        Firecrawl when it is on and a direct fetch otherwise. Keys are encrypted
        at rest and never shown again.
      </p>

      <div className="grid gap-3">
        {settings.providers.map((provider) => {
          const isSaving = saving === provider.name;
          const requiresKey = !provider.keyless && !provider.configured;
          return (
            <div
              key={provider.name}
              className="rounded-lg border border-border/70 p-4"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{provider.label}</span>
                    {provider.keyless ? (
                      <Badge variant="secondary">No key needed</Badge>
                    ) : provider.configured ? (
                      <Badge variant="outline">Key saved</Badge>
                    ) : (
                      <Badge variant="outline">Key required</Badge>
                    )}
                    {provider.supports_fetch ? (
                      <Badge variant="outline">Search + page reading</Badge>
                    ) : null}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {provider.enabled && provider.configured
                      ? "Active for the Bolo Agent."
                      : provider.keyless
                        ? "Free rate limit; no account needed."
                        : provider.configured
                          ? "Off until you turn it on."
                          : "Add an API key, then turn it on."}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  {isSaving ? (
                    <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                  ) : null}
                  <Switch
                    id={`web-access-${provider.name}`}
                    aria-label={`Enable ${provider.label}`}
                    checked={provider.enabled}
                    disabled={isSaving || requiresKey}
                    onCheckedChange={(checked) => void toggle(provider, checked)}
                  />
                </div>
              </div>

              {!provider.keyless ? (
                <div className="mt-3 space-y-2">
                  <Label htmlFor={`web-access-key-${provider.name}`}>
                    API key
                  </Label>
                  <div className="flex flex-col gap-2 sm:flex-row">
                    <Input
                      id={`web-access-key-${provider.name}`}
                      type="password"
                      autoComplete="off"
                      placeholder={
                        provider.configured
                          ? "Enter a new key to replace the saved one"
                          : "Paste your API key"
                      }
                      value={drafts[provider.name] ?? ""}
                      onChange={(event) =>
                        setDrafts((current) => ({
                          ...current,
                          [provider.name]: event.target.value,
                        }))
                      }
                    />
                    <Button
                      type="button"
                      variant="secondary"
                      disabled={isSaving || !(drafts[provider.name] ?? "").trim()}
                      onClick={() => void saveKey(provider)}
                    >
                      Save key
                    </Button>
                    {provider.configured ? (
                      <Button
                        type="button"
                        variant="ghost"
                        disabled={isSaving}
                        onClick={() => void clearKey(provider)}
                      >
                        Remove
                      </Button>
                    ) : null}
                  </div>
                </div>
              ) : null}

              <a
                href={provider.docs_url}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-3 inline-flex items-center gap-0.5 text-xs text-muted-foreground underline"
              >
                {provider.label} docs <ExternalLink className="h-3 w-3" />
              </a>
            </div>
          );
        })}
      </div>

      <p className="text-xs text-muted-foreground">
        Web access only covers public pages. Private networks, localhost and
        non-standard ports are always refused.
      </p>
    </div>
  );
}
