"""Language selection for copilot voice calls."""

import pytest
from pydantic import BaseModel

from api.services.copilot import prompts, voice_language
from api.services.copilot.voice_language import (
    apply_voice_language,
    normalize_voice_language,
)


class _Section(BaseModel):
    provider: str
    model: str = "default-model"
    language: str = ""
    voice: str = ""


class _Config(BaseModel):
    stt: _Section | None = None
    tts: _Section | None = None


def _config(stt: dict, tts: dict) -> _Config:
    return _Config(stt=_Section(**stt), tts=_Section(**tts))


@pytest.mark.parametrize(
    "value, expected",
    [
        ("en", "en"),
        ("EN-us", "en"),
        ("ur-PK", "ur"),
        ("ur-IN", "ur"),
        ("hi-IN", "hi"),
        ("auto", "auto"),
        ("fr", "auto"),
        (None, "auto"),
        (42, "auto"),
    ],
)
def test_normalize_voice_language(value, expected):
    assert normalize_voice_language(value) == expected


def test_auto_leaves_the_configured_services_alone():
    config = _config(
        stt={"provider": "sarvam", "language": "hi-IN"},
        tts={"provider": "sarvam", "language": "hi-IN", "voice": "shubh"},
    )

    resolved, warning = apply_voice_language(config, "auto")

    assert resolved is config
    assert warning is None


def test_sarvam_urdu_switches_stt_model_and_warns_for_tts():
    config = _config(
        stt={"provider": "sarvam", "model": "saarika:v2.5", "language": "hi-IN"},
        tts={"provider": "sarvam", "language": "hi-IN", "voice": "shubh"},
    )

    resolved, warning = apply_voice_language(config, "ur")

    assert resolved.stt.language == "ur-IN"
    assert resolved.stt.model == "saaras:v3"
    # Sarvam voices have no Urdu, so the voice service must stay untouched.
    assert resolved.tts.language == "hi-IN"
    assert resolved.tts.voice == "shubh"
    assert "Sarvam voices have no Urdu" in warning


def test_azure_urdu_switches_language_and_default_voice():
    config = _config(
        stt={"provider": "azure_speech", "language": "en-US"},
        tts={
            "provider": "azure_speech",
            "language": "en-US",
            "voice": "en-US-AriaNeural",
        },
    )

    resolved, warning = apply_voice_language(config, "ur")

    assert resolved.stt.language == "ur-PK"
    assert resolved.tts.language == "ur-PK"
    assert resolved.tts.voice == "ur-PK-UzmaNeural"
    assert warning is None


def test_azure_keeps_a_voice_that_already_matches_the_language():
    config = _config(
        stt={"provider": "azure_speech", "language": "en-US"},
        tts={
            "provider": "azure_speech",
            "language": "hi-IN",
            "voice": "hi-IN-SwaraNeural",
        },
    )

    resolved, _ = apply_voice_language(config, "hi")

    assert resolved.tts.voice == "hi-IN-SwaraNeural"


def test_google_hindi_rewrites_the_voice_locale():
    config = _config(
        stt={"provider": "google", "language": "en-US"},
        tts={
            "provider": "google",
            "language": "en-US",
            "voice": "en-US-Chirp3-HD-Charon",
        },
    )

    resolved, warning = apply_voice_language(config, "hi")

    assert resolved.stt.language == "hi-IN"
    assert resolved.tts.language == "hi-IN"
    assert resolved.tts.voice == "hi-IN-Chirp3-HD-Charon"
    assert warning is None


def test_google_urdu_is_reported_instead_of_switched():
    config = _config(
        stt={"provider": "google", "language": "en-US"},
        tts={
            "provider": "google",
            "language": "en-US",
            "voice": "en-US-Chirp3-HD-Charon",
        },
    )

    resolved, warning = apply_voice_language(config, "ur")

    assert resolved.stt.language == "ur-PK"
    assert resolved.tts.language == "en-US"
    assert resolved.tts.voice == "en-US-Chirp3-HD-Charon"
    assert "Chirp 3 HD has no Urdu" in warning


def test_deepgram_flux_cannot_transcribe_urdu():
    config = _config(
        stt={
            "provider": "deepgram",
            "model": "flux-general-multi",
            "language": "multi",
        },
        tts={"provider": "cartesia", "language": "en", "voice": "v1"},
    )

    resolved, warning = apply_voice_language(config, "ur")

    assert resolved.stt.language == "multi"
    assert "Deepgram Flux cannot transcribe Urdu" in warning


def test_deepgram_nova_accepts_urdu():
    config = _config(
        stt={"provider": "deepgram", "model": "nova-3", "language": "en-US"},
        tts={"provider": "cartesia", "language": "en", "voice": "v1"},
    )

    resolved, warning = apply_voice_language(config, "ur")

    assert resolved.stt.language == "ur"
    assert warning is None


@pytest.mark.parametrize("language, expected", [("ur", "ur"), ("hi", "hi")])
def test_openai_transcribes_the_language_the_caller_selected(language, expected):
    config = _config(
        stt={"provider": "openai", "language": "en"},
        tts={"provider": "openai", "language": ""},
    )

    resolved, warning = apply_voice_language(config, language)

    assert resolved.stt.language == expected
    # OpenAI voices follow the text they read, so the TTS stays untouched.
    assert resolved.tts.language == ""
    assert warning is None


def test_speaches_urdu_switches_to_the_piper_model():
    config = _config(
        stt={
            "provider": "speaches",
            "model": "Systran/faster-distil-whisper-small.en",
            "language": "en",
        },
        tts={"provider": "speaches", "model": "kokoro", "voice": "af_heart"},
    )

    resolved, warning = apply_voice_language(config, "ur")

    assert warning is None
    # An English-only distil model cannot hear Urdu.
    assert resolved.stt.model == "Systran/faster-whisper-large-v3"
    assert resolved.stt.language == "ur"
    assert resolved.tts.model == "Keshavji/piper-ur_PK-fasih-medium"
    assert resolved.tts.voice == "fasih"


def test_speaches_hindi_uses_the_kokoro_hindi_voice():
    config = _config(
        stt={
            "provider": "speaches",
            "model": "Systran/faster-whisper-large-v3",
            "language": "en",
        },
        tts={
            "provider": "speaches",
            "model": "Kokoro-82M-v1.0-ONNX",
            "voice": "af_heart",
        },
    )

    resolved, warning = apply_voice_language(config, "hi")

    assert warning is None
    assert resolved.stt.model == "Systran/faster-whisper-large-v3"
    assert resolved.stt.language == "hi"
    assert resolved.tts.model == "speaches-ai/Kokoro-82M-v1.0-ONNX"
    assert resolved.tts.voice == "hf_alpha"


def test_speaches_english_keeps_a_multilingual_model():
    config = _config(
        stt={
            "provider": "speaches",
            "model": "Systran/faster-whisper-large-v3",
            "language": "ur",
        },
        tts={
            "provider": "speaches",
            "model": "Keshavji/piper-ur_PK-fasih-medium",
            "voice": "fasih",
        },
    )

    resolved, _ = apply_voice_language(config, "en")

    assert resolved.stt.model == "Systran/faster-whisper-large-v3"
    assert resolved.stt.language == "en"
    assert resolved.tts.model == "speaches-ai/Kokoro-82M-v1.0-ONNX"
    assert resolved.tts.voice == "af_heart"


def test_cartesia_ink_2_cannot_transcribe_urdu():
    config = _config(
        stt={"provider": "cartesia", "model": "ink-2", "language": "en"},
        tts={"provider": "cartesia", "language": "en", "voice": "v1"},
    )

    _, warning = apply_voice_language(config, "ur")

    assert "ink-2 transcribes English only" in warning


def test_urdu_and_hindi_greetings_and_prompts():
    assert "السلام علیکم" in prompts.greeting_for("ur")
    assert "नमस्ते" in prompts.greeting_for("hi")
    assert prompts.greeting_for("auto") == prompts.VOICE_GREETING

    hindi = prompts.voice_system_prompt(model_label="m", language="hi")
    urdu = prompts.voice_system_prompt(model_label="m", language="ur")
    auto = prompts.voice_system_prompt(model_label="m")

    assert "Devanagari" in hindi
    assert "Urdu script" in urdu
    assert "language of their" in auto
    assert voice_language.normalize_voice_language("hi-IN") == "hi"
