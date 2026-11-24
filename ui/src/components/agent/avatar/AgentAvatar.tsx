"use client";

import dynamic from "next/dynamic";
import type { ComponentType, CSSProperties } from "react";

import type { AvatarProfile } from "@/client/types.gen";

interface KitProps {
  modelUrl?: string;
  posterUrl?: string;
  emotion?: string;
  intensity?: number;
  glasses?: boolean;
  eyewear?: string;
  hood?: boolean;
  action?: string;
  audio?: HTMLAudioElement | null;
  readSpeechLevel?: () => number;
  interactive?: boolean;
  idleMotion?: boolean;
  className?: string;
  style?: CSSProperties;
  onReady?: () => void;
  onError?: (error: unknown) => void;
}

// The kits are plain JS; their inferred props narrow defaults to `null`.
const HackerAvatar = dynamic(
  () => import("./hacker/BoloEthicalHackerAvatar"),
  { ssr: false },
) as unknown as ComponentType<KitProps>;
const BibiAvatar = dynamic(() => import("./bibi/BoloBibiAvatar"), {
  ssr: false,
}) as unknown as ComponentType<KitProps>;

export interface AgentAvatarProps {
  profile: AvatarProfile;
  audio?: HTMLAudioElement | null;
  readSpeechLevel?: () => number;
  posterUrl?: string;
  emotion?: string;
  interactive?: boolean;
  idleMotion?: boolean;
  className?: string;
  style?: CSSProperties;
  onReady?: () => void;
  onError?: (error: unknown) => void;
}

/** Both kits share the viseme rig; only their gadget set differs. */
export function AgentAvatar({
  profile,
  audio,
  readSpeechLevel,
  posterUrl,
  emotion = "neutral",
  interactive = true,
  idleMotion = true,
  className,
  style,
  onReady,
  onError,
}: AgentAvatarProps) {
  if (profile.character === "male") {
    return (
      <HackerAvatar
        modelUrl="/avatars/bolo-ethical-hacker.glb"
        posterUrl={posterUrl}
        emotion={emotion}
        glasses={profile.appearance?.glasses ?? true}
        hood={profile.appearance?.hood ?? true}
        eyewear={
          profile.appearance?.hud
            ? "hud"
            : profile.appearance?.glasses === false
              ? "off"
              : undefined
        }
        action={profile.action ?? "idle"}
        audio={audio ?? null}
        readSpeechLevel={readSpeechLevel}
        interactive={interactive}
        idleMotion={idleMotion}
        className={className}
        style={style}
        onReady={onReady}
        onError={onError}
      />
    );
  }
  return (
    <BibiAvatar
      modelUrl="/avatars/bolo-bibi.glb"
      posterUrl={posterUrl}
      emotion={emotion}
      glasses={profile.appearance?.glasses ?? true}
      audio={audio ?? null}
      readSpeechLevel={readSpeechLevel}
      interactive={interactive}
      idleMotion={idleMotion}
      className={className}
      style={style}
      onReady={onReady}
      onError={onError}
    />
  );
}

export function avatarPreview(character: AvatarProfile["character"]) {
  return character === "male"
    ? "/avatars/bolo-ethical-hacker-preview.png"
    : "/avatars/bolo-bibi-preview.png";
}

export function avatarName(character: AvatarProfile["character"]) {
  return character === "male" ? "Bolo Agent" : "Bolo Bibi";
}
