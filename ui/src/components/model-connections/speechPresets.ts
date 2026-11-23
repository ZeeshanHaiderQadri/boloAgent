import { compatibleConnections, schemaDefaults } from "./configuration";
import type { ConfigurationSpec, ModelConnectionCatalog, ProviderConnection, ServiceSelection } from "./types";

export interface SpeechPreset {
    id: string;
    label: string;
    description: string;
    /** Set only when the preset also chooses the LLM, not just the voices. */
    llm?: { provider: string; settings: Record<string, unknown> };
    stt: { provider: string; settings: Record<string, unknown> };
    tts: { provider: string; settings: Record<string, unknown> };
}

type PresetRole = "llm" | "stt" | "tts";

/**
 * One-click speech stacks for the BYOK editor. Each preset names the provider
 * it needs for listening and speaking: a provider connection must already
 * exist, because credentials live on the connection, not on the preset.
 */
export const SPEECH_PRESETS: SpeechPreset[] = [
    {
        id: "local-all",
        label: "All local",
        description: "Everything on this machine: LFM 3 replies, Whisper base listens, Piper speaks (the gateway picks the voice language).",
        llm: { provider: "openai", settings: { model: "lfm3:1.7b-q4_K_M", temperature: 0.1 } },
        stt: { provider: "speaches", settings: { model: "Systran/faster-whisper-base", language: "en" } },
        tts: { provider: "speaches", settings: { model: "speaches-ai/piper-en_US-lessac-medium", voice: "en_US-lessac-medium" } },
    },
    {
        id: "english-azure",
        label: "English",
        description: "Azure Speech with US English comprehension and voice.",
        stt: { provider: "azure_speech", settings: { language: "en-US" } },
        tts: { provider: "azure_speech", settings: { language: "en-US", voice: "en-US-AriaNeural" } },
    },
    {
        id: "urdu-azure",
        label: "اردو Urdu",
        description: "Azure Speech understands and speaks Urdu (ur-PK).",
        stt: { provider: "azure_speech", settings: { language: "ur-PK" } },
        tts: { provider: "azure_speech", settings: { language: "ur-PK", voice: "ur-PK-UzmaNeural" } },
    },
    {
        id: "hindi-azure",
        label: "हिन्दी Hindi",
        description: "Azure Speech understands and speaks Hindi (hi-IN).",
        stt: { provider: "azure_speech", settings: { language: "hi-IN" } },
        tts: { provider: "azure_speech", settings: { language: "hi-IN", voice: "hi-IN-SwaraNeural" } },
    },
    {
        id: "local-english",
        label: "English · local",
        description: "Free offline stack: Whisper base and Kokoro in Speaches. Download both models once with speaches-cli.",
        stt: { provider: "speaches", settings: { model: "Systran/faster-whisper-base", language: "en" } },
        tts: { provider: "speaches", settings: { model: "speaches-ai/Kokoro-82M-v1.0-ONNX", voice: "af_heart" } },
    },
    {
        id: "local-urdu",
        label: "اردو · local",
        description: "Free offline Urdu: Whisper base and the Piper Urdu voice in Speaches. Download both models once with speaches-cli.",
        stt: { provider: "speaches", settings: { model: "Systran/faster-whisper-base", language: "ur" } },
        tts: { provider: "speaches", settings: { model: "Keshavji/piper-ur_PK-fasih-medium", voice: "fasih" } },
    },
    {
        id: "local-hindi",
        label: "हिन्दी · local",
        description: "Free offline Hindi: Whisper base and the Kokoro Hindi voice in Speaches. Download both models once with speaches-cli.",
        stt: { provider: "speaches", settings: { model: "Systran/faster-whisper-base", language: "hi" } },
        tts: { provider: "speaches", settings: { model: "speaches-ai/Kokoro-82M-v1.0-ONNX", voice: "hf_alpha" } },
    },
];

const LOCAL_STACK = SPEECH_PRESETS.find(preset => preset.id === "local-all") as SpeechPreset;

/** Whether this machine can listen and speak for a local LLM without a paid key. */
export function localSpeechAvailable(catalog: ModelConnectionCatalog, connections: ProviderConnection[]): boolean {
    return missingPresetProviders(LOCAL_STACK, catalog, connections).every(role => role === "llm");
}

/**
 * Fill the speech roles the local stack can run itself. A role already bound
 * to another connection (say a cloud provider the user picked for Urdu) is
 * left alone. Returns null when nothing had to change.
 */
export function fillLocalSpeech(
    configuration: ConfigurationSpec,
    catalog: ModelConnectionCatalog,
    connections: ProviderConnection[],
): ConfigurationSpec | null {
    if (!localSpeechAvailable(catalog, connections)) return null;
    let next = configuration;
    let changed = false;
    for (const role of ["stt", "tts"] as const) {
        const selection = configuration[role];
        if (selection?.provider_connection_uuid && connections.some(item => item.uuid === selection.provider_connection_uuid)) continue;
        const filled = speechPresetSelection(LOCAL_STACK, role, catalog, connections, configuration);
        if (!filled) continue;
        next = { ...next, [role]: filled };
        changed = true;
    }
    return changed ? next : null;
}

export function speechPresetSelection(
    preset: SpeechPreset,
    role: PresetRole,
    catalog: ModelConnectionCatalog,
    connections: ProviderConnection[],
    current?: ConfigurationSpec,
): ServiceSelection | null {
    const target = preset[role];
    if (!target) return null;
    const compatible = compatibleConnections(catalog, connections, role);
    // Stay on the connection the configuration already uses for this role, so
    // switching presets keeps its endpoint and key.
    const currentSelection = current?.[role];
    const connection = compatible.find(item => item.uuid === currentSelection?.provider_connection_uuid
        && item.provider === target.provider)
        ?? compatible.find(item => item.provider === target.provider);
    if (!connection) return null;
    const defaults = schemaDefaults(catalog.services[role]?.[connection.provider]?.settings_schema);
    const existing = currentSelection && currentSelection.provider_connection_uuid === connection.uuid
        ? currentSelection.settings : {};
    return { provider_connection_uuid: connection.uuid, settings: { ...defaults, ...existing, ...target.settings } };
}

/** The providers a preset still needs a connection for. */
export function missingPresetProviders(
    preset: SpeechPreset,
    catalog: ModelConnectionCatalog,
    connections: ProviderConnection[],
): PresetRole[] {
    return (["llm", "stt", "tts"] as const)
        .filter(role => preset[role] && speechPresetSelection(preset, role, catalog, connections) === null);
}

export function applySpeechPreset(
    preset: SpeechPreset,
    configuration: ConfigurationSpec,
    catalog: ModelConnectionCatalog,
    connections: ProviderConnection[],
): ConfigurationSpec | null {
    const llm = preset.llm ? speechPresetSelection(preset, "llm", catalog, connections, configuration) : null;
    const stt = speechPresetSelection(preset, "stt", catalog, connections, configuration);
    const tts = speechPresetSelection(preset, "tts", catalog, connections, configuration);
    if (!stt || !tts || (preset.llm && !llm)) return null;
    return { ...configuration, ...(llm ? { llm } : {}), stt, tts };
}
