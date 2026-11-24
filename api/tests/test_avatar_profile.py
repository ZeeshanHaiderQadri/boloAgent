"""Unit tests for the per-user avatar profile and its voice override."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from api.schemas.avatar_profile import (
    AvatarAppearanceUpdate,
    AvatarProfile,
    AvatarProfileUpdate,
    AvatarVoice,
)
from api.schemas.voices import VoiceInfo, VoicesResponse
from api.services import avatar_profile
from api.services.copilot import voice


class _Connection(BaseModel):
    provider: str
    model: str
    voice: str = "default"
    api_key: str = "sk-test"
    base_url: str = ""


class _Pipeline(BaseModel):
    is_realtime: bool = False
    llm: _Connection
    stt: _Connection
    tts: _Connection


def _pipeline(tts: _Connection | None = None) -> _Pipeline:
    return _Pipeline(
        llm=_Connection(provider="openai", model="gpt-4o-mini"),
        stt=_Connection(provider="deepgram", model="nova-3"),
        tts=tts or _Connection(provider="cartesia", model="sonic-2"),
    )


def _resolved(config) -> SimpleNamespace:
    return SimpleNamespace(effective=config)


@pytest.mark.asyncio
async def test_update_merges_partial_profile_and_stores_it():
    with patch.object(avatar_profile, "db_client") as db:
        db.get_user_configuration_value = AsyncMock(return_value=None)
        db.upsert_user_configuration_value = AsyncMock()

        profile = await avatar_profile.update_avatar_profile(
            9,
            AvatarProfileUpdate(
                character="female",
                appearance=AvatarAppearanceUpdate(glasses=False),
                action="typing",
                position={"x": 0.2, "y": 0.8},
            ),
        )

    assert profile.character == "female"
    assert profile.appearance.glasses is False
    assert profile.appearance.hood is True
    assert profile.action == "typing"
    assert profile.position.x == 0.2
    args = db.upsert_user_configuration_value.await_args.args
    assert args[0] == 9
    assert args[1] == "AVATAR"
    assert args[2]["character"] == "female"
    assert args[2]["appearance"]["glasses"] is False


def test_pet_state_drives_enabled_unless_set_explicitly():
    hidden = AvatarProfileUpdate(pet_state="hidden").apply_to(AvatarProfile())
    assert hidden.pet_state == "hidden"
    assert hidden.enabled is False

    visible = AvatarProfileUpdate(pet_state="minimized").apply_to(AvatarProfile())
    assert visible.enabled is True

    restored = AvatarProfileUpdate(pet_state="minimized", enabled=False).apply_to(
        AvatarProfile()
    )
    assert restored.enabled is False


@pytest.mark.asyncio
async def test_invalid_stored_profile_falls_back_to_defaults():
    with patch.object(avatar_profile, "db_client") as db:
        db.get_user_configuration_value = AsyncMock(
            return_value={"voice": {"provider": "cartesia"}}
        )
        profile = await avatar_profile.get_avatar_profile(9)

    assert profile == AvatarProfile()


def test_agent_voice_only_applies_to_the_matching_provider():
    config = _pipeline()
    profile = AvatarProfile(voice=AvatarVoice(provider="cartesia", voice_id="aria"))

    swapped = avatar_profile.apply_agent_voice(config, profile)
    assert swapped is not config
    assert swapped.tts.voice == "aria"
    assert swapped.llm == config.llm

    other = AvatarProfile(voice=AvatarVoice(provider="elevenlabs", voice_id="rachel"))
    assert avatar_profile.apply_agent_voice(config, other) is config

    assert avatar_profile.apply_agent_voice(config, AvatarProfile()) is config


@pytest.mark.asyncio
async def test_voice_configuration_applies_the_users_avatar_voice():
    config = _pipeline()
    profile = AvatarProfile(voice=AvatarVoice(provider="cartesia", voice_id="aria"))

    with (
        patch.object(
            voice,
            "get_resolved_ai_model_configuration",
            AsyncMock(return_value=_resolved(config)),
        ),
        patch.object(
            voice,
            "get_avatar_profile",
            AsyncMock(return_value=profile),
        ),
    ):
        overridden = await voice.voice_configuration(42, user_id=9)
        untouched = await voice.voice_configuration(42)

    assert overridden.tts.voice == "aria"
    assert untouched is config
    assert untouched.tts.voice == "default"


@pytest.mark.asyncio
async def test_avatar_voice_options_filters_to_the_avatar_gender():
    effective = _pipeline(tts=_Connection(provider="elevenlabs", model="v3"))
    default = SimpleNamespace(
        configuration={"tts": {"provider_connection_uuid": "conn-1"}}
    )
    voices = VoicesResponse(
        provider="elevenlabs",
        voices=[
            VoiceInfo(voice_id="a", name="Aria", gender="female"),
            VoiceInfo(voice_id="b", name="Brian", gender="male"),
            VoiceInfo(voice_id="c", name="Unlabeled"),
        ],
    )

    with (
        patch.object(
            avatar_profile,
            "get_resolved_ai_model_configuration",
            AsyncMock(return_value=_resolved(effective)),
        ),
        patch.object(
            avatar_profile,
            "get_default_model_configuration",
            AsyncMock(return_value=default),
        ),
        patch.object(
            avatar_profile,
            "get_connection_voices",
            AsyncMock(return_value=voices),
        ) as catalog,
    ):
        options = await avatar_profile.avatar_voice_options(
            42, "female", current_voice_id="a"
        )

    assert options.configured is True
    assert options.provider == "elevenlabs"
    assert options.current_voice_id == "a"
    assert [item.voice_id for item in options.voices] == ["a", "c"]
    assert catalog.await_args.kwargs["gender"] == "female"


@pytest.mark.asyncio
async def test_avatar_voice_options_refuses_bolo_hosted_voice():
    effective = _pipeline(tts=_Connection(provider="bolo", model="managed"))

    with patch.object(
        avatar_profile,
        "get_resolved_ai_model_configuration",
        AsyncMock(return_value=_resolved(effective)),
    ):
        options = await avatar_profile.avatar_voice_options(42, "male")

    assert options.configured is False
    assert "your own voice connection" in options.message
