"use client";

import { useEffect, useMemo, useState } from "react";

import { getDefaultConfigurationsApiV1UserConfigurationsDefaultsGet } from "@/client/sdk.gen";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";

const CUSTOM_MODEL = "__custom_model__";

interface SchemaProperty {
    type?: string;
    default?: string | number | boolean;
    enum?: string[];
    examples?: string[];
    $ref?: string;
}

interface ProviderSchema {
    properties: Record<string, SchemaProperty>;
    required?: string[];
    $defs?: Record<string, SchemaProperty>;
}

interface LLMConfigSelectorProps {
    provider: string;
    onProviderChange: (provider: string) => void;
    model: string;
    onModelChange: (model: string) => void;
    apiKey: string;
    onApiKeyChange: (apiKey: string) => void;
}

export function LLMConfigSelector({
    provider,
    onProviderChange,
    model,
    onModelChange,
    apiKey,
    onApiKeyChange,
}: LLMConfigSelectorProps) {
    const [schemas, setSchemas] = useState<Record<string, ProviderSchema>>({});
    const [isManualModelInput, setIsManualModelInput] = useState(false);

    useEffect(() => {
        const fetchSchemas = async () => {
            const response =
                await getDefaultConfigurationsApiV1UserConfigurationsDefaultsGet();
            if (response.data?.llm) {
                setSchemas(response.data.llm as unknown as Record<string, ProviderSchema>);
            }
        };
        fetchSchemas();
    }, []);

    const availableProviders = Object.keys(schemas);
    const providerSchema = schemas[provider];

    // The curated suggestions in the backend schema seed the picker; any slug
    // the provider accepts can still be typed through custom entry.
    const resolveModelSchema = (schema?: ProviderSchema) => {
        if (!schema) return undefined;
        const modelSchema = schema.properties.model;
        return modelSchema?.$ref && schema.$defs
            ? schema.$defs[modelSchema.$ref.split("/").pop() || ""]
            : modelSchema;
    };

    const modelOptions = useMemo(() => {
        const modelSchema = resolveModelSchema(providerSchema);
        return modelSchema?.enum || modelSchema?.examples || [];
    }, [providerSchema]);

    // Check if current model is not in options (custom model)
    useEffect(() => {
        if (model && modelOptions.length > 0 && !modelOptions.includes(model)) {
            setIsManualModelInput(true);
        }
    }, [model, modelOptions]);

    const handleProviderChange = (newProvider: string) => {
        if (!newProvider) return;
        onProviderChange(newProvider);
        const actualSchema = resolveModelSchema(schemas[newProvider]);
        onModelChange(
            (actualSchema?.default as string) ||
                actualSchema?.enum?.[0] ||
                actualSchema?.examples?.[0] ||
                "",
        );
        setIsManualModelInput(false);
    };

    return (
        <div className="space-y-4 p-3 border rounded-md bg-muted/10">
            <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                    <Label>Provider</Label>
                    <Select value={provider} onValueChange={handleProviderChange}>
                        <SelectTrigger className="w-full">
                            <SelectValue placeholder="Select provider" />
                        </SelectTrigger>
                        <SelectContent>
                            {availableProviders.map((p) => (
                                <SelectItem key={p} value={p}>
                                    {p}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>

                <div className="space-y-2">
                    <Label>Model</Label>
                    {isManualModelInput || modelOptions.length === 0 ? (
                        <div className="space-y-1">
                            <Input
                                type="text"
                                placeholder="Enter model name"
                                value={model}
                                onChange={(e) => onModelChange(e.target.value)}
                            />
                            {modelOptions.length > 0 && (
                                <button
                                    type="button"
                                    className="text-xs underline"
                                    onClick={() => {
                                        setIsManualModelInput(false);
                                        onModelChange(modelOptions[0]);
                                    }}
                                >
                                    Choose from the list
                                </button>
                            )}
                        </div>
                    ) : (
                        <Select
                            value={model}
                            onValueChange={(v) => {
                                if (v === CUSTOM_MODEL) {
                                    setIsManualModelInput(true);
                                    onModelChange("");
                                    return;
                                }
                                if (v) onModelChange(v);
                            }}
                        >
                            <SelectTrigger className="w-full">
                                <SelectValue placeholder="Select model" />
                            </SelectTrigger>
                            <SelectContent>
                                {[...modelOptions, ...(!model || modelOptions.includes(model) ? [] : [model])].map((m) => (
                                    <SelectItem key={m} value={m}>
                                        {m}
                                    </SelectItem>
                                ))}
                                <SelectItem value={CUSTOM_MODEL}>
                                    Enter a custom model ID…
                                </SelectItem>
                            </SelectContent>
                        </Select>
                    )}
                </div>
            </div>

            <div className="space-y-2">
                <Label>API Key</Label>
                <Input
                    type="password"
                    autoComplete="new-password"
                    placeholder="Enter API key"
                    value={apiKey}
                    onChange={(e) => onApiKeyChange(e.target.value)}
                />
            </div>
        </div>
    );
}
