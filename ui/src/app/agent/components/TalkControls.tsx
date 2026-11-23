"use client";

import { AudioLines, Gauge, Hand, Languages, Mic, MicOff } from "lucide-react";
import type { ReactNode, RefObject } from "react";
import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils";

import type {
    AudioLevels,
    MicMode,
    TurnLatency,
    VoiceLanguage,
} from "../hooks/useAgentVoice";

interface Props {
    micMode: MicMode;
    onMicModeChange: (mode: MicMode) => void;
    micMuted: boolean;
    onToggleMute: () => void;
    holding: boolean;
    onStartTalking: () => void;
    onStopTalking: () => void;
    levels: RefObject<AudioLevels>;
    latency: TurnLatency | null;
}

/**
 * Level bars are written straight to the DOM: the hook updates the meters
 * ~16 times a second and routing that through React state would re-render
 * the whole conversation on every voice packet.
 */
function LevelBars({ levels }: { levels: RefObject<AudioLevels> }) {
    const micRef = useRef<HTMLSpanElement | null>(null);
    const agentRef = useRef<HTMLSpanElement | null>(null);

    useEffect(() => {
        const timer = window.setInterval(() => {
            const current = levels.current;
            if (!current) return;
            if (micRef.current) {
                micRef.current.style.transform = `scaleX(${Math.min(1, current.mic * 2.2)})`;
            }
            if (agentRef.current) {
                agentRef.current.style.transform = `scaleX(${Math.min(1, current.agent * 2.2)})`;
            }
        }, 90);
        return () => window.clearInterval(timer);
    }, [levels]);

    return (
        <span className="hidden items-center gap-2 sm:flex" aria-hidden>
            <span className="flex items-center gap-1" title="Your microphone">
                <Mic className="h-3 w-3 text-muted-foreground" />
                <span className="relative h-1 w-10 overflow-hidden rounded-full bg-border/70">
                    <span
                        ref={micRef}
                        className="absolute inset-0 origin-left rounded-full bg-teal-500 transition-transform duration-150 ease-out"
                        style={{ transform: "scaleX(0)" }}
                    />
                </span>
            </span>
            <span className="flex items-center gap-1" title="Bolo Agent">
                <AudioLines className="h-3 w-3 text-muted-foreground" />
                <span className="relative h-1 w-10 overflow-hidden rounded-full bg-border/70">
                    <span
                        ref={agentRef}
                        className="absolute inset-0 origin-left rounded-full bg-cta transition-transform duration-150 ease-out"
                        style={{ transform: "scaleX(0)" }}
                    />
                </span>
            </span>
        </span>
    );
}

function ModeButton({
    active,
    icon,
    label,
    onClick,
    disabled,
}: {
    active: boolean;
    icon: ReactNode;
    label: string;
    onClick: () => void;
    disabled?: boolean;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            disabled={disabled}
            aria-pressed={active}
            className={cn(
                "inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-medium transition-all duration-300",
                active
                    ? "bg-cta/15 text-foreground shadow-[inset_0_0_0_1px_rgba(45,212,191,0.35)]"
                    : "text-muted-foreground hover:text-foreground",
                disabled && "cursor-not-allowed opacity-50",
            )}
        >
            {icon}
            {label}
        </button>
    );
}

const LANGUAGE_OPTIONS: { value: VoiceLanguage; label: string }[] = [
    { value: "auto", label: "Auto" },
    { value: "en", label: "English" },
    { value: "ur", label: "اردو" },
    { value: "hi", label: "हिन्दी" },
];

/** Language is read when the call starts, so the choice is disabled on a call. */
export function VoiceLanguageSelect({
    language,
    onLanguageChange,
    disabled,
}: {
    language: VoiceLanguage;
    onLanguageChange: (language: VoiceLanguage) => void;
    disabled?: boolean;
}) {
    return (
        <label
            className={cn(
                "inline-flex items-center gap-1 rounded-full border border-border/70 bg-background/50 py-1 pl-2 pr-1 text-muted-foreground transition-colors duration-300",
                disabled && "opacity-60",
            )}
            title="Language the agent speaks and listens in"
        >
            <Languages className="h-3.5 w-3.5" />
            <span className="sr-only">Voice language</span>
            <select
                value={language}
                onChange={(event) =>
                    onLanguageChange(event.target.value as VoiceLanguage)
                }
                disabled={disabled}
                className="cursor-pointer bg-transparent pr-0.5 text-[11px] font-medium text-foreground outline-none disabled:cursor-not-allowed"
            >
                {LANGUAGE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                        {option.label}
                    </option>
                ))}
            </select>
        </label>
    );
}

function formatSeconds(ms: number) {
    return `${(ms / 1000).toFixed(1)}s`;
}

/** Last turn's response time, from end of speech to first audio back. */
export function LatencyBadge({ latency }: { latency: TurnLatency | null }) {
    if (!latency) return null;
    const { stt, llm, tts } = latency;
    const parts = [
        stt !== undefined ? `listen ${stt}` : null,
        llm !== undefined ? `think ${llm}` : null,
        tts !== undefined ? `speak ${tts}` : null,
    ].filter((part): part is string => part !== null);
    if (parts.length === 0) return null;

    const total = (stt ?? 0) + (llm ?? 0) + (tts ?? 0);
    const slow = total >= 2000;

    return (
        <span
            title={`Delay before the agent answered this turn: ${parts.join(", ")} ms`}
            className={cn(
                "inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-medium tabular-nums transition-colors duration-300",
                slow
                    ? "border border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300"
                    : "text-muted-foreground",
            )}
        >
            <Gauge className="h-3.5 w-3.5" />
            {formatSeconds(total)}
            <span className="hidden text-muted-foreground sm:inline">
                {parts.join(" · ")}
            </span>
        </span>
    );
}

export function TalkControls({
    micMode,
    onMicModeChange,
    micMuted,
    onToggleMute,
    holding,
    onStartTalking,
    onStopTalking,
    levels,
    latency,
}: Props) {
    const pushToTalk = micMode === "push-to-talk";

    return (
        <div className="flex flex-wrap items-center gap-2">
            <div className="inline-flex items-center rounded-full border border-border/70 bg-background/50 p-0.5">
                <ModeButton
                    active={!pushToTalk}
                    icon={<AudioLines className="h-3.5 w-3.5" />}
                    label="Hands-free"
                    onClick={() => onMicModeChange("hands-free")}
                />
                <ModeButton
                    active={pushToTalk}
                    icon={<Hand className="h-3.5 w-3.5" />}
                    label="Push to talk"
                    onClick={() => onMicModeChange("push-to-talk")}
                />
            </div>

            {pushToTalk ? (
                <button
                    type="button"
                    onPointerDown={(event) => {
                        onStartTalking();
                        try {
                            event.currentTarget.setPointerCapture(event.pointerId);
                        } catch {
                            // Capture is only a convenience for pointer-up outside the button.
                        }
                    }}
                    onPointerUp={onStopTalking}
                    onPointerCancel={onStopTalking}
                    onLostPointerCapture={onStopTalking}
                    onContextMenu={(event) => event.preventDefault()}
                    aria-pressed={holding}
                    className={cn(
                        "touch-none select-none inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[11px] font-semibold transition-all duration-300",
                        holding
                            ? "scale-105 bg-teal-500 text-zinc-950 shadow-[0_0_24px_-4px_rgba(45,212,191,0.9)]"
                            : "border border-teal-500/50 text-teal-700 hover:bg-teal-500/10 dark:text-teal-300",
                    )}
                >
                    <Hand className="h-3.5 w-3.5" />
                    {holding ? "Talking…" : "Hold to talk"}
                    <kbd className="hidden rounded border border-teal-600/40 px-1 text-[10px] font-normal opacity-70 sm:inline dark:border-teal-300/40">
                        Space
                    </kbd>
                </button>
            ) : (
                <button
                    type="button"
                    onClick={onToggleMute}
                    aria-pressed={micMuted}
                    title={micMuted ? "Unmute your microphone" : "Mute your microphone"}
                    className={cn(
                        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium transition-all duration-300",
                        micMuted
                            ? "border border-destructive/50 bg-destructive/10 text-destructive"
                            : "text-muted-foreground hover:text-foreground",
                    )}
                >
                    {micMuted ? (
                        <MicOff className="h-3.5 w-3.5" />
                    ) : (
                        <Mic className="h-3.5 w-3.5" />
                    )}
                    {micMuted ? "Mic off" : "Mute"}
                </button>
            )}

            <LevelBars levels={levels} />
            <LatencyBadge latency={latency} />
        </div>
    );
}
