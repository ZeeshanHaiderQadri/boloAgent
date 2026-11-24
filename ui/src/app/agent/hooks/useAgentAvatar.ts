"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  getUserAvatarApiV1UserAvatarGet,
  getUserAvatarVoicesApiV1UserAvatarVoicesGet,
  updateUserAvatarApiV1UserAvatarPut,
} from "@/client/sdk.gen";
import type {
  AvatarProfile,
  AvatarProfileUpdate,
  AvatarVoiceOptions,
} from "@/client/types.gen";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

export type AvatarCharacter = "male" | "female";

function mergeProfile(
  profile: AvatarProfile,
  update: AvatarProfileUpdate,
): AvatarProfile {
  const next: AvatarProfile = { ...profile };
  if (update.character !== undefined && update.character !== null) {
    next.character = update.character;
  }
  if (update.enabled !== undefined && update.enabled !== null) {
    next.enabled = update.enabled;
  }
  if (
    update.setup_completed !== undefined &&
    update.setup_completed !== null
  ) {
    next.setup_completed = update.setup_completed;
  }
  if (update.action !== undefined && update.action !== null) {
    next.action = update.action;
  }
  if (update.size !== undefined && update.size !== null) {
    next.size = update.size;
  }
  if (update.pet_state !== undefined && update.pet_state !== null) {
    next.pet_state = update.pet_state;
  }
  if (update.position !== undefined && update.position !== null) {
    next.position = update.position;
  }
  if (update.appearance) {
    const appearance = { ...profile.appearance };
    for (const key of ["glasses", "hood", "hud"] as const) {
      const value = update.appearance[key];
      if (value !== undefined && value !== null) appearance[key] = value;
    }
    next.appearance = appearance;
  }
  if (update.voice?.provider && update.voice.voice_id) {
    next.voice = {
      provider: update.voice.provider,
      voice_id: update.voice.voice_id,
    };
  }
  return next;
}

export function useAgentAvatar() {
  const { user, loading: authLoading, getAccessToken } = useAuth();
  const [profile, setProfile] = useState<AvatarProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const loadedRef = useRef(false);

  const authHeaders = useCallback(
    async () => ({ Authorization: `Bearer ${await getAccessToken()}` }),
    [getAccessToken],
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await getUserAvatarApiV1UserAvatarGet({
        headers: await authHeaders(),
      });
      if (response.error) {
        setError(detailFromError(response.error, "Could not load your avatar."));
        return;
      }
      setProfile(response.data ?? {});
    } catch {
      setError("Could not load your avatar.");
    } finally {
      setLoading(false);
    }
  }, [authHeaders]);

  useEffect(() => {
    if (authLoading || !user || loadedRef.current) return;
    loadedRef.current = true;
    void load();
  }, [authLoading, user, load]);

  const save = useCallback(
    async (update: AvatarProfileUpdate) => {
      setError(null);
      setSaving(true);
      setProfile((current) => (current ? mergeProfile(current, update) : current));
      try {
        const response = await updateUserAvatarApiV1UserAvatarPut({
          body: update,
          headers: await authHeaders(),
        });
        if (response.error) {
          setError(detailFromError(response.error, "Could not save your avatar."));
          return false;
        }
        if (response.data) setProfile(response.data);
        return true;
      } catch {
        setError("Could not save your avatar.");
        return false;
      } finally {
        setSaving(false);
      }
    },
    [authHeaders],
  );

  const loadVoices = useCallback(
    async (character: AvatarCharacter): Promise<AvatarVoiceOptions | null> => {
      try {
        const response = await getUserAvatarVoicesApiV1UserAvatarVoicesGet({
          query: { character },
          headers: await authHeaders(),
        });
        if (response.error) {
          return {
            configured: false,
            message: detailFromError(response.error, "Could not load voices."),
          };
        }
        return response.data ?? null;
      } catch {
        return { configured: false, message: "Could not load voices." };
      }
    },
    [authHeaders],
  );

  return {
    profile,
    loading,
    saving,
    error,
    clearError: () => setError(null),
    save,
    loadVoices,
    reload: load,
  };
}
