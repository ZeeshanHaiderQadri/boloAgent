"""Per-call language selection for the copilot voice surface.

A caller can pin a call to English, Urdu or Hindi, or leave it on auto-detect.
The organization's BYOK services decide what a language costs: the chosen codes
are written into the STT and TTS configurations before the services are built.
A provider that cannot transcribe or speak the language is left untouched and
reported as a warning instead of failing the call, so a working stack degrades
to its configured language rather than going silent mid-conversation.
"""

from __future__ import annotations

from typing import Any

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration.options.deepgram import DEEPGRAM_FLUX_MODELS
from api.services.configuration.registry import ServiceProviders

AUTO = "auto"

VOICE_LANGUAGE_LABELS: dict[str, str] = {
    AUTO: "Auto-detect",
    "en": "English",
    "ur": "Urdu",
    "hi": "Hindi",
}

_ALIASES = {
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "en-in": "en",
    "ur": "ur",
    "ur-pk": "ur",
    "ur-in": "ur",
    "hi": "hi",
    "hi-in": "hi",
}

_STT_LANGUAGE_CODES: dict[str, dict[str, str]] = {
    ServiceProviders.OPENAI.value: {"en": "en", "hi": "hi", "ur": "ur"},
    ServiceProviders.GOOGLE.value: {"en": "en-US", "hi": "hi-IN", "ur": "ur-PK"},
    ServiceProviders.AZURE_SPEECH.value: {
        "en": "en-US",
        "hi": "hi-IN",
        "ur": "ur-PK",
    },
    ServiceProviders.SARVAM.value: {"en": "en-IN", "hi": "hi-IN", "ur": "ur-IN"},
    ServiceProviders.DEEPGRAM.value: {"en": "en-US", "hi": "hi", "ur": "ur"},
    ServiceProviders.SPEECHMATICS.value: {"en": "en", "hi": "hi", "ur": "ur"},
    ServiceProviders.GLADIA.value: {"en": "en", "hi": "hi", "ur": "ur"},
    ServiceProviders.SONIOX.value: {"en": "en", "hi": "hi", "ur": "ur"},
    ServiceProviders.CARTESIA.value: {"en": "en", "hi": "hi", "ur": "ur"},
    ServiceProviders.ASSEMBLYAI.value: {"en": "en", "hi": "hi"},
    ServiceProviders.SMALLEST.value: {"en": "en", "hi": "hi"},
    ServiceProviders.SPEACHES.value: {"en": "en", "hi": "hi", "ur": "ur"},
}

# Code plus the voice name to switch to when the configured voice belongs to a
# different locale. Only Azure embeds the locale in every voice name; the other
# multilingual providers keep one voice across languages.
_TTS_LANGUAGE_CODES: dict[str, dict[str, str]] = {
    ServiceProviders.AZURE_SPEECH.value: {
        "en": "en-US",
        "hi": "hi-IN",
        "ur": "ur-PK",
    },
    ServiceProviders.SARVAM.value: {"en": "en-IN", "hi": "hi-IN"},
    ServiceProviders.CARTESIA.value: {"en": "en", "hi": "hi", "ur": "ur"},
    ServiceProviders.INWORLD.value: {"en": "en-US", "hi": "hi-IN"},
    ServiceProviders.CAMB.value: {"en": "en-us", "hi": "hi-in"},
    ServiceProviders.RIME.value: {"en": "en", "hi": "hi"},
    ServiceProviders.SMALLEST.value: {"en": "en", "hi": "hi"},
    ServiceProviders.SPEECHIFY.value: {"en": "en", "hi": "hi"},
    ServiceProviders.GOOGLE.value: {"en": "en-US", "hi": "hi-IN"},
}

_AZURE_DEFAULT_VOICES = {
    "en-US": "en-US-AriaNeural",
    "hi-IN": "hi-IN-SwaraNeural",
    "ur-PK": "ur-PK-UzmaNeural",
}

# A local Speaches server picks the voice set by model, so a language switch
# also switches the model. Kokoro covers English and Hindi; the Piper voice is
# the free Urdu option.
_SPEACHES_TTS_MODELS: dict[str, tuple[str, str]] = {
    "en": ("speaches-ai/Kokoro-82M-v1.0-ONNX", "af_heart"),
    "hi": ("speaches-ai/Kokoro-82M-v1.0-ONNX", "hf_alpha"),
    "ur": ("Keshavji/piper-ur_PK-fasih-medium", "fasih"),
}

# Replaces an English-only distil model when the caller chose another language.
_SPEACHES_MULTILINGUAL_STT = "Systran/faster-whisper-large-v3"

_STT_UNSUPPORTED: dict[tuple[str, str], str] = {
    (ServiceProviders.SMALLEST.value, "ur"): (
        "Smallest.ai speech-to-text does not support Urdu. Urdu calls will "
        "transcribe with your configured language; pick Google, Azure Speech, "
        "Sarvam or Deepgram in Model connections for Urdu."
    ),
    (ServiceProviders.ASSEMBLYAI.value, "ur"): (
        "AssemblyAI's realtime models do not list Urdu. Pick Google, Azure "
        "Speech, Sarvam or Deepgram in Model connections for Urdu calls."
    ),
}

_TTS_UNSUPPORTED: dict[tuple[str, str], str] = {
    (ServiceProviders.SARVAM.value, "ur"): (
        "Sarvam voices have no Urdu, so the agent will read Urdu with a Hindi "
        "voice. Switch your voice connection to Azure Speech or ElevenLabs for "
        "Urdu calls."
    ),
    (ServiceProviders.SMALLEST.value, "ur"): (
        "Smallest.ai voices do not support Urdu. Switch your voice connection "
        "to Azure Speech or ElevenLabs for Urdu calls."
    ),
    (ServiceProviders.GOOGLE.value, "ur"): (
        "Google Chirp 3 HD has no Urdu voice. Switch your voice connection to "
        "Azure Speech or ElevenLabs for Urdu calls."
    ),
    (ServiceProviders.RIME.value, "ur"): (
        "Rime voices do not list Urdu. Switch your voice connection to Azure "
        "Speech or ElevenLabs for Urdu calls."
    ),
    (ServiceProviders.INWORLD.value, "ur"): (
        "Inworld voices do not list Urdu. Switch your voice connection to Azure "
        "Speech or ElevenLabs for Urdu calls."
    ),
    (ServiceProviders.DEEPGRAM.value, "ur"): (
        "Deepgram Aura voices are English and Spanish only. Switch your voice "
        "connection to Azure Speech or ElevenLabs for Urdu calls."
    ),
    (ServiceProviders.DEEPGRAM.value, "hi"): (
        "Deepgram Aura voices are English and Spanish only. Switch your voice "
        "connection to Sarvam, Azure Speech or Google for Hindi calls."
    ),
}


def normalize_voice_language(value: object) -> str:
    """Map a client-supplied code onto one of the supported languages."""
    if not isinstance(value, str):
        return AUTO
    return _ALIASES.get(value.strip().lower(), AUTO)


def _copy_fields(config: Any, **updates: Any) -> Any:
    """Copy a pydantic config, keeping only fields the model actually has."""
    fields = getattr(type(config), "model_fields", None)
    if config is None or fields is None:
        return config
    allowed = {key: value for key, value in updates.items() if key in fields}
    return config.model_copy(update=allowed) if allowed else config


def _stt_unsupported(provider: str, model: str | None, language: str) -> str | None:
    known = _STT_UNSUPPORTED.get((provider, language))
    if known:
        return known
    if (
        provider == ServiceProviders.DEEPGRAM.value
        and language == "ur"
        and model in DEEPGRAM_FLUX_MODELS
    ):
        return (
            "Deepgram Flux cannot transcribe Urdu. Switch the Deepgram model to "
            "Nova-3 in Model connections for Urdu calls."
        )
    if (
        provider == ServiceProviders.CARTESIA.value
        and language == "ur"
        and model == "ink-2"
    ):
        return (
            "Cartesia ink-2 transcribes English only. Switch the Cartesia model "
            "to ink-whisper in Model connections for Urdu calls."
        )
    return None


def _azure_voice_for(voice: str, code: str) -> str:
    """Keep the configured voice when its locale matches, else use the default."""
    if voice.lower().startswith(code.lower()):
        return voice
    return _AZURE_DEFAULT_VOICES[code]


def _google_voice_for(voice: str, code: str) -> str:
    parts = voice.split("-", 2)
    if len(parts) != 3:
        return voice
    return f"{code}-{parts[2]}"


def apply_voice_language(
    config: EffectiveAIModelConfiguration, language: object
) -> tuple[EffectiveAIModelConfiguration, str | None]:
    """Return the config aimed at ``language`` and an optional warning.

    ``auto`` leaves every connection alone. A language a provider cannot serve
    is reported instead of written into the config, because a rejected language
    code usually fails at connect time and ends the call.
    """
    language = normalize_voice_language(language)
    if language == AUTO or config is None:
        return config, None

    warning: str | None = None
    stt = getattr(config, "stt", None)
    tts = getattr(config, "tts", None)

    if stt is not None:
        provider = getattr(stt, "provider", "")
        model = getattr(stt, "model", None)
        unsupported = _stt_unsupported(provider, model, language)
        if unsupported:
            warning = unsupported
        else:
            code = _STT_LANGUAGE_CODES.get(provider, {}).get(language)
            if code:
                updates: dict[str, Any] = {"language": code}
                if provider == ServiceProviders.SARVAM.value and language == "ur":
                    # saarika does not list Urdu; saaras:v3 does.
                    updates["model"] = "saaras:v3"
                if (
                    provider == ServiceProviders.SPEACHES.value
                    and language != "en"
                    and model
                    and model.lower().endswith(".en")
                ):
                    # A distil English model transcribes every language as
                    # English, which is not what the caller asked for.
                    updates["model"] = _SPEACHES_MULTILINGUAL_STT
                config = _copy_fields(config, stt=_copy_fields(stt, **updates))

    if tts is not None:
        provider = getattr(tts, "provider", "")
        unsupported = _TTS_UNSUPPORTED.get((provider, language))
        if unsupported:
            warning = f"{warning} {unsupported}" if warning else unsupported
        elif provider == ServiceProviders.SPEACHES.value:
            model, voice = _SPEACHES_TTS_MODELS[language]
            config = _copy_fields(
                config, tts=_copy_fields(tts, model=model, voice=voice)
            )
        else:
            code = _TTS_LANGUAGE_CODES.get(provider, {}).get(language)
            if code:
                updates = {"language": code}
                voice = getattr(tts, "voice", None) or ""
                if provider == ServiceProviders.AZURE_SPEECH.value:
                    updates["voice"] = _azure_voice_for(voice, code)
                elif provider == ServiceProviders.GOOGLE.value:
                    updates["voice"] = _google_voice_for(voice, code)
                config = _copy_fields(config, tts=_copy_fields(tts, **updates))

    return config, warning
