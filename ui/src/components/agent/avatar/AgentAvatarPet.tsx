"use client";

import { Minimize2, Settings2, X } from "lucide-react";
import {
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import type { AvatarProfile, AvatarProfileUpdate } from "@/client/types.gen";
import { cn } from "@/lib/utils";

import { AgentAvatar, avatarName, avatarPreview } from "./AgentAvatar";

const CARD_WIDTH = 176;
const CHIP_SIZE = 56;
const EDGE = 10;

export interface AgentAvatarPetProps {
  profile: AvatarProfile;
  audio: HTMLAudioElement | null;
  readSpeechLevel: () => number;
  live: boolean;
  onSave: (update: AvatarProfileUpdate) => Promise<boolean>;
  onCustomize: () => void;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function centerFor(profile: AvatarProfile, size: number) {
  const x = profile.position?.x ?? 0.86;
  const y = profile.position?.y ?? 0.74;
  const width = typeof window === "undefined" ? 1280 : window.innerWidth;
  const height = typeof window === "undefined" ? 800 : window.innerHeight;
  return {
    x: clamp(x * width, size / 2 + EDGE, width - size / 2 - EDGE),
    y: clamp(y * height, size / 2 + EDGE, height - size / 2 - EDGE),
  };
}

/**
 * Floating Bolo Agent companion. Fixed above every layout layer, dragged by
 * its header, and persisted per user so it reappears where it was left.
 */
export function AgentAvatarPet({
  profile,
  audio,
  readSpeechLevel,
  live,
  onSave,
  onCustomize,
}: AgentAvatarPetProps) {
  const minimized = profile.pet_state === "minimized";
  const size = minimized ? CHIP_SIZE : CARD_WIDTH;
  const [center, setCenter] = useState(() => centerFor(profile, size));
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    centerX: number;
    centerY: number;
    moved: boolean;
  } | null>(null);

  useEffect(() => {
    if (dragging) return;
    const size = minimized ? CHIP_SIZE : CARD_WIDTH;
    const width = window.innerWidth;
    const height = window.innerHeight;
    setCenter({
      x: clamp(
        (profile.position?.x ?? 0.86) * width,
        size / 2 + EDGE,
        width - size / 2 - EDGE,
      ),
      y: clamp(
        (profile.position?.y ?? 0.74) * height,
        size / 2 + EDGE,
        height - size / 2 - EDGE,
      ),
    });
  }, [profile.position?.x, profile.position?.y, minimized, dragging]);

  useEffect(() => {
    const onResize = () =>
      setCenter((current) => ({
        x: clamp(
          current.x,
          size / 2 + EDGE,
          window.innerWidth - size / 2 - EDGE,
        ),
        y: clamp(
          current.y,
          size / 2 + EDGE,
          window.innerHeight - size / 2 - EDGE,
        ),
      }));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [size]);

  const startDrag = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      if (event.button !== 0) return;
      dragRef.current = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        centerX: center.x,
        centerY: center.y,
        moved: false,
      };
      event.currentTarget.setPointerCapture(event.pointerId);
      setDragging(true);
    },
    [center.x, center.y],
  );

  const moveDrag = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      const dx = event.clientX - drag.startX;
      const dy = event.clientY - drag.startY;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      setCenter({
        x: clamp(
          drag.centerX + dx,
          size / 2 + EDGE,
          window.innerWidth - size / 2 - EDGE,
        ),
        y: clamp(
          drag.centerY + dy,
          size / 2 + EDGE,
          window.innerHeight - size / 2 - EDGE,
        ),
      });
    },
    [size],
  );

  const endDrag = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      dragRef.current = null;
      setDragging(false);
      if (!drag.moved) return;
      void onSave({
        position: {
          x: center.x / window.innerWidth,
          y: center.y / window.innerHeight,
        },
      });
    },
    [center.x, center.y, onSave],
  );

  if (!profile.character || !profile.enabled || profile.pet_state === "hidden") {
    return null;
  }

  const name = avatarName(profile.character);

  if (minimized) {
    return (
      <div
        className="fixed z-[80] touch-none select-none"
        style={{ left: center.x, top: center.y, transform: "translate(-50%, -50%)" }}
      >
        <button
          type="button"
          onPointerDown={startDrag}
          onPointerMove={moveDrag}
          onPointerUp={(event) => {
            const moved = dragRef.current?.moved ?? false;
            endDrag(event);
            if (!moved) void onSave({ pet_state: "expanded" });
          }}
          onClick={(event) => {
            if (event.detail === 0) void onSave({ pet_state: "expanded" });
          }}
          title={`${name} is listening. Click to expand, drag to move.`}
          aria-label={`Expand ${name}`}
          className={cn(
            "relative flex h-14 w-14 cursor-grab items-center justify-center overflow-hidden rounded-full",
            "border border-teal-300/40 bg-slate-950/70 shadow-[0_10px_35px_-10px_rgba(20,184,166,0.9)] backdrop-blur-md",
            "transition-shadow duration-500 hover:shadow-[0_10px_45px_-8px_rgba(45,212,191,0.95)]",
            dragging && "cursor-grabbing",
          )}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={avatarPreview(profile.character)}
            alt=""
            className="h-full w-full object-cover opacity-90"
            draggable={false}
          />
          <span
            className={cn(
              "absolute bottom-0.5 right-0.5 h-3 w-3 rounded-full ring-2 ring-slate-950",
              live ? "bg-emerald-400" : "bg-amber-300",
            )}
          />
        </button>
      </div>
    );
  }

  return (
    <div
      className="fixed z-[80] touch-none select-none"
      style={{ left: center.x, top: center.y, transform: "translate(-50%, -50%)" }}
    >
      <div
        className={cn(
          "w-[176px] overflow-hidden rounded-[26px] border border-teal-300/30 bg-slate-950/60",
          "shadow-[0_24px_70px_-24px_rgba(13,148,136,0.9)] backdrop-blur-xl",
          "animate-in fade-in zoom-in-95 duration-500 ease-out",
          dragging && "cursor-grabbing",
        )}
      >
        <div
          onPointerDown={startDrag}
          onPointerMove={moveDrag}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          className="flex cursor-grab items-center justify-between gap-1 px-2.5 pt-2"
        >
          <span className="flex items-center gap-1.5">
            <span
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                live ? "animate-pulse bg-emerald-400" : "bg-amber-300",
              )}
            />
            <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-teal-100/80">
              {name}
            </span>
          </span>
          <span className="flex items-center gap-0.5">
            <button
              type="button"
              onClick={() => void onSave({ pet_state: "minimized" })}
              aria-label={`Minimize ${name}`}
              title="Minimize"
              className="rounded-full p-1 text-teal-100/70 transition-colors duration-300 hover:bg-white/10 hover:text-white"
            >
              <Minimize2 className="h-3 w-3" />
            </button>
            <button
              type="button"
              onClick={onCustomize}
              aria-label={`Customize ${name}`}
              title="Customize avatar"
              className="rounded-full p-1 text-teal-100/70 transition-colors duration-300 hover:bg-white/10 hover:text-white"
            >
              <Settings2 className="h-3 w-3" />
            </button>
            <button
              type="button"
              onClick={() =>
                void onSave({ pet_state: "hidden", enabled: false })
              }
              aria-label={`Hide ${name}`}
              title="Hide avatar"
              className="rounded-full p-1 text-teal-100/70 transition-colors duration-300 hover:bg-rose-500/20 hover:text-rose-200"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        </div>
        <div className="px-1 pb-1.5 pt-0.5">
          <AgentAvatar
            profile={profile}
            audio={audio}
            readSpeechLevel={readSpeechLevel}
            idleMotion
            interactive
            className="rounded-[22px]"
            style={{ width: "100%", height: 186, aspectRatio: "auto" }}
          />
        </div>
      </div>
      <span className="sr-only" role="status">
        {live ? `${name} is on the call.` : `${name} is connecting.`}
      </span>
    </div>
  );
}
