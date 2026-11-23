"use client";

import * as RadioGroupPrimitive from "@radix-ui/react-radio-group";
import { Download } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

import { boloConfiguration, compatibleConnections, configurationForMode, configurationMode, isLocalConnection, localModelFamily, resetDependentFields, selectionForConnection } from "./configuration";
import { LocalModelNotice } from "./LocalModelNotice";
import { AddProviderDialog, type ProviderOption, ProviderSelect } from "./ProviderSelect";
import { SchemaFields } from "./SchemaFields";
import { applySpeechPreset, fillLocalSpeech, localSpeechAvailable, missingPresetProviders, SPEECH_PRESETS, type SpeechPreset } from "./speechPresets";
import type { ConfigurationEditorMode, ConfigurationSpec, FieldSchema, ModelConnectionCatalog, ProviderConnection, ServiceRole, ServiceSelection } from "./types";
import { MODE_LABELS, ROLE_LABELS } from "./types";
import { useLocalModelInstalls } from "./useLocalModelInstalls";

export type ConfigurationFieldsDensity = "full" | "compact";

// Bolo manages its services; only these settings are the user's to change,
// listed in the order they are shown.
const BOLO_FIELDS: [ServiceRole, string[]][] = [["tts", ["voice", "speed"]], ["stt", ["language", "language_hints"]], ["llm", ["model", "temperature"]]];
const MODES: { value: ConfigurationEditorMode; description: string }[] = [
    { value: "realtime", description: "One speech-to-speech model listens and replies." },
    { value: "bolo", description: "Bolo runs the transcriber, LLM and voice for you." },
    { value: "cascade", description: "Pick your own LLM, STT and TTS providers." },
];
const SERVICE_TAB = "-mb-px h-auto min-w-0 flex-1 flex-col items-start gap-0.5 rounded-none border-0 border-b-2 border-transparent px-3 py-2.5 text-left sm:px-4 text-muted-foreground data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:text-foreground data-[state=active]:shadow-none dark:data-[state=active]:border-primary dark:data-[state=active]:bg-transparent";

// Read aloud, "LLM", "STT" and "Embedding" take "an".
const ROLE_ARTICLES: Record<ServiceRole, string> = { llm: "an", stt: "an", tts: "a", realtime: "a", embeddings: "an" };

type AddTarget = ServiceRole | "bolo";

function restrictSchema(schema: FieldSchema | undefined, allowed?: string[]): FieldSchema | undefined {
    if (!schema || !allowed) return schema;
    return { ...schema, properties: Object.fromEntries(Object.entries(schema.properties || {}).filter(([name]) => allowed.includes(name))) };
}

function hasFields(schema: FieldSchema | undefined): schema is FieldSchema {
    return Boolean(schema && Object.keys(schema.properties || {}).length);
}

function listNames(names: string[]): string | undefined {
    if (!names.length) return undefined;
    return names.length > 3 ? `${names.slice(0, 3).join(", ")} and ${names.length - 3} more` : names.join(", ");
}

function ModeChoice({ value, onChange }: { value: ConfigurationEditorMode; onChange: (value: string) => void }) {
    const id = useId();
    return <RadioGroupPrimitive.Root aria-label="Mode" value={value} onValueChange={onChange} className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {MODES.map(option => <RadioGroupPrimitive.Item key={option.value} value={option.value} aria-labelledby={`${id}-${option.value}`} aria-describedby={`${id}-${option.value}-description`}
            className="group flex items-start gap-3 rounded-lg border bg-card p-3 text-left outline-none transition-[color,border-color,box-shadow] hover:border-foreground/25 focus-visible:ring-[3px] focus-visible:ring-ring/50 data-[state=checked]:border-primary data-[state=checked]:ring-1 data-[state=checked]:ring-primary">
            <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border border-input group-data-[state=checked]:border-primary">
                <RadioGroupPrimitive.Indicator className="size-2 rounded-full bg-primary" />
            </span>
            <span className="min-w-0 space-y-0.5">
                <span id={`${id}-${option.value}`} className="block text-sm font-medium">{MODE_LABELS[option.value]}</span>
                <span id={`${id}-${option.value}-description`} className="block text-xs leading-snug text-muted-foreground">{option.description}</span>
            </span>
        </RadioGroupPrimitive.Item>)}
    </RadioGroupPrimitive.Root>;
}

/**
 * The one editor for a model configuration spec.
 *
 * The mode comes first: Realtime, Bolo or BYOK. Bolo shows the few
 * settings Bolo leaves to the user. Realtime and BYOK show one tab per
 * service, each naming its provider so the whole setup reads at a glance,
 * with the provider and its settings inside. Every provider list ends with
 * a way to add a provider in place.
 *
 * "full" is the Models page: every setting, embeddings and fallbacks.
 * "compact" is the agent editor's Model view: the settings named in
 * `fields` (all of them when omitted), and no embeddings or fallback
 * actions. Both produce the same ConfigurationSpec.
 */
export function ConfigurationFields({ configuration, catalog, connections, onChange, onConnectionAdded, llmActions, density = "full", fields }: {
    configuration: ConfigurationSpec;
    catalog: ModelConnectionCatalog;
    connections: ProviderConnection[];
    onChange: (configuration: ConfigurationSpec) => void;
    /** Add a connection created from a provider list to `connections`. */
    onConnectionAdded?: (connection: ProviderConnection) => void;
    llmActions?: ReactNode;
    density?: ConfigurationFieldsDensity;
    /** Setting names to show for each service; everything else stays as it is. */
    fields?: string[];
}) {
    const compact = density === "compact";
    const [unconfiguredMode, setUnconfiguredMode] = useState<ConfigurationEditorMode>("cascade");
    // The tab the user opened; until then, the mode's first service.
    const [activeRole, setActiveRole] = useState<ServiceRole | null>(null);
    // Opening the speech tabs for a local stack is a deliberate opt-out of
    // the automatic setup, so it lasts until the LLM connection changes.
    const [showSpeech, setShowSpeech] = useState(false);
    const [adding, setAdding] = useState<AddTarget | null>(null);
    const root = useRef<HTMLDivElement>(null);
    const id = useId();
    const mode = configuration.mode === "realtime" || configuration.llm.provider_connection_uuid
        ? configurationMode(configuration, connections) : unconfiguredMode;
    const boloConnections = connections.filter(connection => connection.is_active && connection.provider === "bolo");
    const roles = (mode === "realtime" ? ["realtime", "llm", "embeddings"] as ServiceRole[] : ["llm", "stt", "tts", "embeddings"] as ServiceRole[])
        .filter(role => !compact || role !== "embeddings");
    // An LLM running on this machine brings its speech with it: the editor
    // fills STT and TTS from the local stack and folds those tabs away.
    const localLlm = mode === "cascade"
        && isLocalConnection(connections.find(connection => connection.uuid === configuration.llm.provider_connection_uuid));
    const speechAutomatic = localLlm && localSpeechAvailable(catalog, connections)
        // A speech role the user pointed at another provider stays visible.
        && (["stt", "tts"] as const).every(role => {
            const uuid = configuration[role]?.provider_connection_uuid;
            return !uuid || isLocalConnection(connections.find(connection => connection.uuid === uuid));
        });
    const shownRoles = speechAutomatic && !showSpeech ? roles.filter(role => role !== "stt" && role !== "tts") : roles;
    const currentRole = activeRole && shownRoles.includes(activeRole) ? activeRole : shownRoles[0];
    // A service on this machine can name a model the machine does not have
    // yet; its install state lets the editor offer that download in place.
    const localInstalls = useLocalModelInstalls(mode !== "bolo" && roles.some(role => role !== "embeddings"
        && isLocalConnection(connections.find(connection => connection.uuid === configuration[role]?.provider_connection_uuid))));
    // The automatic speech setup still needs its models on disk to make calls.
    const missingSpeechModels = !speechAutomatic || showSpeech ? [] : (["stt", "tts"] as const).flatMap(role => {
        const selection = configuration[role];
        const connection = connections.find(item => item.uuid === selection?.provider_connection_uuid);
        const model = String(selection?.settings.model || "");
        const family = localModelFamily(connection, role, model);
        return family && localInstalls.data?.servers.includes(family) && !localInstalls.installed(family, model)
            ? [{ family, model, title: localInstalls.entry(family, model)?.title }] : [];
    }).filter((item, index, all) => all.findIndex(other => other.family === item.family && other.model === item.model) === index);
    const speechInstalling = missingSpeechModels.some(item => localInstalls.state(item.family, item.model)?.status === "running");

    useEffect(() => {
        if (!localLlm) return;
        const next = fillLocalSpeech(configuration, catalog, connections);
        if (next) onChange(next);
    }, [localLlm, configuration, catalog, connections, onChange]);

    useEffect(() => { setShowSpeech(false); }, [configuration.llm.provider_connection_uuid]);

    // Every service tab stays mounted so the form still validates the hidden
    // ones. When a hidden service is missing its provider, open its tab.
    useEffect(() => {
        const node = root.current;
        if (!node) return;
        let handled = false;
        const reveal = (event: Event) => {
            if (handled) return;
            handled = true;
            queueMicrotask(() => { handled = false; });
            const panel = (event.target as Element).closest<HTMLElement>("[data-service-role]");
            if (!panel?.hidden) return;
            setActiveRole(panel.dataset.serviceRole as ServiceRole);
            // The browser could not point at a control it could not see; ask again once it shows.
            const form = (event.target as HTMLInputElement).form;
            requestAnimationFrame(() => form?.reportValidity());
        };
        node.addEventListener("invalid", reveal, true);
        return () => node.removeEventListener("invalid", reveal, true);
    }, []);

    const providerTitle = (role: ServiceRole, provider: string) => catalog.services[role]?.[provider]?.title || provider;
    const providerOption = (role: ServiceRole, connection: ProviderConnection): ProviderOption => {
        const label = providerTitle(role, connection.provider);
        const name = connection.name.trim();
        return { value: connection.uuid, label, detail: name.toLowerCase() === label.toLowerCase() ? undefined : name };
    };
    const changeSelection = (role: ServiceRole, selection: ServiceSelection | null) => {
        onChange({ ...configuration, [role]: selection });
    };
    const changeSetting = (role: ServiceRole, field: string, value: unknown, schema?: FieldSchema) => {
        const selection = configuration[role];
        if (!selection) return;
        const settings = resetDependentFields({ ...selection.settings, [field]: value }, schema, field);
        if (value === undefined) delete settings[field];
        onChange({ ...configuration, [role]: { ...selection, settings } });
    };
    const changeMode = (value: string) => {
        const nextMode = value as ConfigurationEditorMode;
        setUnconfiguredMode(nextMode);
        setActiveRole(null);
        setShowSpeech(false);
        onChange(configurationForMode(nextMode, configuration, catalog, connections));
    };
    const changeBoloConnection = (value: string) => {
        const connection = boloConnections.find(item => item.uuid === value);
        if (connection) onChange(boloConfiguration(catalog, connection, configuration, connections));
    };
    const connected = new Set(connections.filter(item => item.is_active).map(item => item.provider));
    // A preset needs both of its providers connected; credentials live on the
    // connection, so say which one to add instead of silently doing nothing.
    const applyPreset = (preset: SpeechPreset) => {
        const next = applySpeechPreset(preset, configuration, catalog, connections);
        if (!next) {
            const missing = missingPresetProviders(preset, catalog, connections);
            const providers = [...new Set(missing.flatMap(role => {
                const target = preset[role];
                return target ? [providerTitle(role, target.provider)] : [];
            }))];
            const tabs = missing.map(role => ROLE_LABELS[role]);
            const tabList = tabs.length > 1 ? `${tabs.slice(0, -1).join(", ")} or ${tabs[tabs.length - 1]}` : tabs[0];
            toast.error(`Add ${providers.join(" and ")} in the ${tabList} tab first.`);
            return;
        }
        onChange(next);
    };
    // Providers a service could add, in catalog order (the most used first),
    // the ones without a connection ahead of the rest.
    const addProviderKeys = (target: AddTarget) => target === "bolo" ? ["bolo"]
        : Object.keys(catalog.services[target] || {}).filter(provider => provider !== "bolo")
            .sort((a, b) => Number(connected.has(a)) - Number(connected.has(b)));
    // Named under the add entry, so the list shows what else is available.
    const addHint = (role: ServiceRole) => listNames(addProviderKeys(role).filter(provider => !connected.has(provider)).map(provider => providerTitle(role, provider)));
    const connectionAdded = (connection: ProviderConnection) => {
        const target = adding;
        // Added to the list in the same update that chooses it, so the mode
        // is never read without it.
        onConnectionAdded?.(connection);
        setAdding(null);
        toast.success("Provider connection saved");
        if (target === "bolo") onChange(boloConfiguration(catalog, connection, configuration, connections));
        else if (target) changeSelection(target, selectionForConnection(catalog, target, connection, configuration[target], connections));
    };
    // The connections a service's picker offers, which its tab summary also reads.
    const serviceOptions = (role: ServiceRole) => {
        const options = compatibleConnections(catalog, connections, role);
        // Existing mixed configurations may include a managed service.
        // Keep that selection visible without replacing it during edits.
        const managed = connections.find(item => item.is_active && item.uuid === configuration[role]?.provider_connection_uuid && item.provider === "bolo");
        if (managed) options.push(managed);
        return options;
    };

    const boloValue = boloConnections.some(item => item.uuid === configuration.llm.provider_connection_uuid) ? configuration.llm.provider_connection_uuid : "";
    const boloPanel = <div className={cn("grid grid-cols-1 gap-4 rounded-lg border bg-card sm:grid-cols-2", compact ? "p-4" : "p-5")}>
        {BOLO_FIELDS.map(([role, names]) => {
            const fullSchema = catalog.services[role]?.bolo?.settings_schema;
            const schema = restrictSchema(fullSchema, names.filter(name => !fields || fields.includes(name)));
            if (!hasFields(schema)) return null;
            return <SchemaFields key={role} className="contents" provider="bolo" role={role} schema={schema}
                values={configuration[role]?.settings || {}} onChange={(name, value) => changeSetting(role, name, value, fullSchema)} />;
        })}
        <div className="space-y-1.5">
            <div className="flex min-h-6 items-center justify-between gap-2">
                <Label htmlFor="bolo-provider-connection">Bolo account</Label>
            </div>
            <ProviderSelect id="bolo-provider-connection" required value={boloValue} onValueChange={changeBoloConnection}
                placeholder="Choose an account" addLabel="Add a Bolo account" onAdd={() => setAdding("bolo")}
                options={boloConnections.map(connection => ({ value: connection.uuid, label: connection.name }))} />
        </div>
    </div>;

    const servicePanel = (role: ServiceRole) => {
        const selection = configuration[role];
        const options = serviceOptions(role);
        const connection = options.find(item => item.uuid === selection?.provider_connection_uuid);
        // A partial editor may hide a dependent field; it still has to reset.
        const fullSchema = connection ? catalog.services[role]?.[connection.provider]?.settings_schema : undefined;
        const schema = restrictSchema(fullSchema, fields);
        const isEmbedding = role === "embeddings";
        return <div className="space-y-4">
            {role === "llm" && mode === "realtime" && <p className="text-xs text-muted-foreground">The realtime model holds the conversation. This text LLM handles variable extraction and other background tasks.</p>}
            {isEmbedding && <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={Boolean(selection)} onChange={event => changeSelection(role,
                    event.target.checked ? (options[0] ? selectionForConnection(catalog, role, options[0]) : { provider_connection_uuid: "", settings: {} }) : null)} />
                Enable embeddings
            </label>}
            {(!isEmbedding || selection) && <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                    <div className="flex min-h-6 items-center justify-between gap-2">
                        <Label htmlFor={`connection-${role}`}>Provider</Label>
                    </div>
                    <ProviderSelect id={`connection-${role}`} required value={connection?.uuid || ""} placeholder="Choose a provider"
                        options={options.map(item => providerOption(role, item))} addLabel="Add a provider" addHint={addHint(role)} onAdd={() => setAdding(role)}
                        onValueChange={value => {
                            const next = connections.find(item => item.uuid === value);
                            if (next) changeSelection(role, selectionForConnection(catalog, role, next, selection, connections));
                        }} />
                </div>
                {schema && selection && <SchemaFields key={`${connection?.uuid}:${connection?.revision}`} className="contents" provider={connection?.provider} providerConnectionUuid={connection?.uuid} connectionRevision={connection?.revision} role={role} schema={schema}
                    values={selection.settings} context={connection?.connection_settings} onChange={(name, value) => changeSetting(role, name, value, fullSchema)} />}
            </div>}
            {selection && <LocalModelNotice role={role} connection={connection} model={String(selection.settings.model || "")} installs={localInstalls} />}
            {isEmbedding && <p className="text-xs text-muted-foreground">Use the embedding model that indexed this workflow’s knowledge base. Changing it does not reindex documents.</p>}
            {role === "llm" && llmActions && connection?.provider !== "bolo" && <div className="flex justify-end border-t pt-4">{llmActions}</div>}
        </div>;
    };

    // Each tab names the provider its picker shows, or that it needs one.
    const tabSummary = (role: ServiceRole): { text: string; missing: boolean } => {
        const selection = configuration[role];
        if (role === "embeddings" && !selection) return { text: "Off", missing: false };
        const connection = serviceOptions(role).find(item => item.uuid === selection?.provider_connection_uuid);
        return connection ? { text: providerTitle(role, connection.provider), missing: false } : { text: "Choose a provider", missing: true };
    };

    return <div ref={root} className={compact ? "space-y-3" : "space-y-4"}>
        <ModeChoice value={mode} onChange={changeMode} />
        {mode === "cascade" && !compact && <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-muted-foreground">Speech preset</span>
            {SPEECH_PRESETS.map(preset => <button key={preset.id} type="button" title={preset.description}
                onClick={() => applyPreset(preset)}
                className="rounded-full border border-border/70 bg-background/50 px-2.5 py-1 text-xs font-medium text-muted-foreground transition-[color,border-color,background-color] duration-300 hover:border-cta/50 hover:bg-cta/5 hover:text-foreground focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">
                {preset.label}
            </button>)}
        </div>}
        {mode === "bolo" ? boloPanel : <Tabs value={currentRole} onValueChange={value => setActiveRole(value as ServiceRole)} className="gap-0 overflow-hidden rounded-lg border bg-card">
            {speechAutomatic && !showSpeech && <div className="flex items-center justify-between gap-3 border-b bg-muted/40 px-3 py-2 sm:px-4">
                <span className="text-xs text-muted-foreground">
                    {missingSpeechModels.length
                        ? <><span className="font-medium text-foreground">Listening and voice</span> need a one-time download before calls can run on this machine.</>
                        : <><span className="font-medium text-foreground">Listening and voice</span> are set up automatically on this machine.</>}
                </span>
                <div className="flex shrink-0 items-center gap-2">
                    {missingSpeechModels.length > 0 && <Button type="button" size="sm" variant="outline" disabled={speechInstalling}
                        onClick={() => missingSpeechModels.forEach(item => void localInstalls.install(item.family, item.model, item.title))}>
                        <Download className="h-3.5 w-3.5" />{speechInstalling ? "Downloading…" : "Download models"}
                    </Button>}
                    <button type="button" onClick={() => { setShowSpeech(true); setActiveRole("stt"); }}
                        className="shrink-0 rounded-full border border-border/70 bg-background px-2.5 py-1 text-xs font-medium text-muted-foreground transition-[color,border-color] duration-300 hover:border-cta/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">
                        Change
                    </button>
                </div>
            </div>}
            <TabsList aria-label="Services" className="flex h-auto w-full justify-start rounded-none border-b bg-transparent p-0">
                {shownRoles.map(role => {
                    const summary = tabSummary(role);
                    return <TabsTrigger key={role} value={role} className={SERVICE_TAB} aria-labelledby={`${id}-${role}`} aria-describedby={`${id}-${role}-summary`}>
                        <span id={`${id}-${role}`} className="text-sm font-medium">{ROLE_LABELS[role]}</span>{" "}
                        <span id={`${id}-${role}-summary`} className={cn("max-w-full truncate text-xs font-normal", summary.missing ? "text-amber-700 dark:text-amber-400" : "text-muted-foreground")}>{summary.text}</span>
                    </TabsTrigger>;
                })}
            </TabsList>
            {roles.map(role => <TabsContent key={role} value={role} forceMount hidden={role !== currentRole} data-service-role={role} className={compact ? "p-4" : "p-5"}>
                {servicePanel(role)}
            </TabsContent>)}
        </Tabs>}
        {adding && <AddProviderDialog open onOpenChange={open => { if (!open) setAdding(null); }} catalog={catalog}
            title={adding === "bolo" ? "Add a Bolo account" : `Add ${ROLE_ARTICLES[adding]} ${ROLE_LABELS[adding]} provider`}
            providerKeys={addProviderKeys(adding)} role={adding === "bolo" ? undefined : adding} onSaved={connectionAdded} />}
    </div>;
}
