"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import type {
  AvatarProfile,
  AvatarProfileUpdate,
  AvatarVoiceOptions,
} from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

import { AgentAvatar, avatarName, avatarPreview } from "./AgentAvatar";

type Character = "male" | "female";
type Action = "idle" | "typing" | "analyzing" | "presenting";

const CHARACTERS: {
  value: Character;
  name: string;
  blurb: string;
}[] = [
  { value: "female", name: "Bolo Bibi", blurb: "Warm, clear and quick." },
  { value: "male", name: "Bolo Agent", blurb: "Focused, precise and calm." },
];

const ACTIONS: { value: Action; label: string }[] = [
  { value: "idle", label: "Idle" },
  { value: "typing", label: "Laptop typing" },
  { value: "analyzing", label: "AR analyze" },
  { value: "presenting", label: "Presenting" },
];

interface Props {
  open: boolean;
  profile: AvatarProfile | null;
  onClose: () => void;
  onSave: (update: AvatarProfileUpdate) => Promise<boolean>;
  loadVoices: (character: Character) => Promise<AvatarVoiceOptions | null>;
}

function ToggleRow({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-xl border border-border/60 bg-background/40 px-3 py-2.5">
      <div className="min-w-0">
        <Label className="text-sm">{label}</Label>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </div>
      <Switch checked={checked} onCheckedChange={onChange} />
    </div>
  );
}

export function AgentAvatarSetup({
  open,
  profile,
  onClose,
  onSave,
  loadVoices,
}: Props) {
  const [character, setCharacter] = useState<Character>("female");
  const [glasses, setGlasses] = useState(true);
  const [hood, setHood] = useState(true);
  const [hud, setHud] = useState(true);
  const [action, setAction] = useState<Action>("idle");
  const [options, setOptions] = useState<AvatarVoiceOptions | null>(null);
  const [optionsLoading, setOptionsLoading] = useState(false);
  const [voice, setVoice] = useState<{
    provider: string;
    voiceId: string;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setCharacter(profile?.character ?? "female");
    setGlasses(profile?.appearance?.glasses ?? true);
    setHood(profile?.appearance?.hood ?? true);
    setHud(profile?.appearance?.hud ?? true);
    setAction(profile?.action ?? "idle");
    setError(null);
    // Re-seeding on the stored character only; user changes stay local.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, profile?.character]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setOptionsLoading(true);
    setOptions(null);
    setVoice(null);
    void loadVoices(character)
      .then((result) => {
        if (cancelled || !result) return;
        setOptions(result);
        if (!result.configured || !result.voices?.length) return;
        const stored = profile?.voice ?? null;
        const keep =
          stored?.provider === result.provider &&
          stored?.voice_id &&
          result.voices.some((item) => item.voice_id === stored.voice_id);
        if (keep && stored?.provider && stored.voice_id) {
          setVoice({ provider: stored.provider, voiceId: stored.voice_id });
        } else if (result.provider) {
          setVoice({
            provider: result.provider,
            voiceId: result.voices[0].voice_id,
          });
        }
      })
      .finally(() => {
        if (!cancelled) setOptionsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, character, loadVoices, profile?.voice]);

  const preview: AvatarProfile = {
    character,
    appearance: { glasses, hood, hud },
    action,
  };

  const commit = async (update: AvatarProfileUpdate) => {
    setBusy(true);
    setError(null);
    const ok = await onSave(update);
    setBusy(false);
    if (ok) onClose();
    else setError("Could not save your avatar. Try again.");
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !busy) onClose();
      }}
    >
      <DialogContent className="max-h-[92dvh] max-w-3xl overflow-y-auto p-0">
        <div className="grid md:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
          <div className="relative flex flex-col justify-between overflow-hidden bg-gradient-to-b from-slate-950 via-slate-900 to-teal-950/70 p-5">
            <div
              aria-hidden
              className="pointer-events-none absolute -left-16 top-1/3 h-64 w-64 rounded-full bg-teal-500/20 blur-3xl"
            />
            <div className="relative">
              <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-teal-200/70">
                Bolo Agent companion
              </p>
              <h3 className="mt-1 text-2xl font-semibold text-white">
                {avatarName(character)}
              </h3>
            </div>
            <AgentAvatar
              profile={preview}
              posterUrl={avatarPreview(character)}
              className="relative mx-auto my-4 w-full max-w-[260px]"
              style={{ aspectRatio: "4 / 5" }}
            />
            <p className="relative text-xs leading-relaxed text-teal-100/70">
              Sits on top while you talk, follows the conversation, and can be
              moved, minimized or dismissed at any time.
            </p>
          </div>

          <div className="flex flex-col gap-5 p-6">
            <DialogHeader>
              <DialogTitle>Choose your agent&apos;s look</DialogTitle>
              <DialogDescription>
                Pick a character, gadgets and the voice it speaks with. You can
                change this later from the avatar itself.
              </DialogDescription>
            </DialogHeader>

            <div className="grid grid-cols-2 gap-3">
              {CHARACTERS.map((item) => {
                const selected = item.value === character;
                return (
                  <button
                    key={item.value}
                    type="button"
                    onClick={() => setCharacter(item.value)}
                    aria-pressed={selected}
                    className={cn(
                      "group overflow-hidden rounded-2xl border p-2 text-left transition-all duration-500",
                      selected
                        ? "border-teal-400/70 bg-teal-500/10 shadow-[0_0_35px_-12px_rgba(45,212,191,0.9)]"
                        : "border-border/70 hover:border-teal-300/40",
                    )}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={avatarPreview(item.value)}
                      alt=""
                      className="h-24 w-full rounded-xl bg-slate-950/60 object-cover object-top"
                    />
                    <span className="mt-2 block text-sm font-medium">
                      {item.name}
                    </span>
                    <span className="block text-xs text-muted-foreground">
                      {item.blurb}
                    </span>
                  </button>
                );
              })}
            </div>

            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                Gadgets
              </p>
              <ToggleRow
                label="Glasses"
                hint={
                  character === "male"
                    ? "Neon visor while it works."
                    : "Bibi's reading glasses."
                }
                checked={glasses}
                onChange={setGlasses}
              />
              {character === "male" && (
                <>
                  <ToggleRow
                    label="Hood"
                    hint="Hood up when it faces you."
                    checked={hood}
                    onChange={setHood}
                  />
                  <ToggleRow
                    label="AR HUD"
                    hint="Heads-up display over the visor."
                    checked={hud}
                    onChange={setHud}
                  />
                </>
              )}
            </div>

            {character === "male" && (
              <div className="space-y-2">
                <p className="text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                  Pose
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {ACTIONS.map((item) => (
                    <button
                      key={item.value}
                      type="button"
                      onClick={() => setAction(item.value)}
                      aria-pressed={action === item.value}
                      className={cn(
                        "rounded-full border px-3 py-1 text-xs transition-colors duration-300",
                        action === item.value
                          ? "border-teal-400/70 bg-teal-500/15 text-teal-100"
                          : "border-border/70 text-muted-foreground hover:border-teal-300/40 hover:text-foreground",
                      )}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                  Voice
                </p>
                {options?.configured && options.voices?.length ? (
                  <select
                    value={voice?.voiceId ?? ""}
                    onChange={(event) => {
                      const chosen = options.voices?.find(
                        (item) => item.voice_id === event.target.value,
                      );
                      if (chosen && options.provider) {
                        setVoice({
                          provider: options.provider,
                          voiceId: chosen.voice_id,
                        });
                      }
                    }}
                    className="h-8 max-w-[60%] rounded-lg border border-border/70 bg-background px-2 text-sm outline-none focus-visible:border-ring"
                    aria-label="Avatar voice"
                  >
                    {!voice && <option value="">Default voice</option>}
                    {options.voices.map((item) => (
                      <option key={item.voice_id} value={item.voice_id}>
                        {item.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <span className="text-xs text-muted-foreground">
                    {optionsLoading ? "Loading voices…" : "Using default voice"}
                  </span>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                {options?.configured
                  ? `${avatarName(character)} uses this voice on agent calls. Workflows keep their own voice.`
                  : (options?.message ??
                    "Connect your own voice provider to pick a voice for this avatar.")}
              </p>
              {options && !options.configured && (
                <Link
                  href="/model-configurations"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs font-medium text-cta underline underline-offset-2"
                >
                  Open Model connections
                </Link>
              )}
            </div>

            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}

            <div className="mt-auto flex flex-wrap items-center justify-end gap-2">
              <Button
                variant="ghost"
                onClick={() =>
                  void commit({ setup_completed: true, enabled: false })
                }
                disabled={busy}
              >
                Not now
              </Button>
              <Button
                onClick={() =>
                  void commit({
                    character,
                    setup_completed: true,
                    enabled: true,
                    pet_state: "expanded",
                    appearance: {
                      glasses,
                      hood: character === "male" ? hood : false,
                      hud: character === "male" ? hud : false,
                    },
                    action: character === "male" ? action : "idle",
                    ...(voice
                      ? {
                          voice: {
                            provider: voice.provider,
                            voice_id: voice.voiceId,
                          },
                        }
                      : {}),
                  })
                }
                disabled={busy}
              >
                {busy ? "Saving…" : `Use ${avatarName(character)}`}
              </Button>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
