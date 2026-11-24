"""Per-user Bolo Agent avatar preferences.

The profile is a small JSON document in the per-user keyed store. It only
affects the copilot voice surface: workflows keep the organization's voice.
"""

from fastapi import HTTPException
from loguru import logger
from pydantic import ValidationError

from api.db import db_client
from api.enums import UserConfigurationKey
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.schemas.avatar_profile import (
    AvatarCharacter,
    AvatarProfile,
    AvatarProfileUpdate,
    AvatarVoiceOptions,
)
from api.services.configuration.ai_model_configuration import (
    get_resolved_ai_model_configuration,
)
from api.services.configuration.model_connections import (
    get_default_model_configuration,
)
from api.services.configuration.registry import ServiceProviders
from api.services.configuration.voice_catalog import get_connection_voices


async def get_avatar_profile(user_id: int) -> AvatarProfile:
    value = await db_client.get_user_configuration_value(
        user_id, UserConfigurationKey.AVATAR.value
    )
    if not isinstance(value, dict):
        return AvatarProfile()
    try:
        return AvatarProfile.model_validate(value)
    except ValidationError as exc:
        logger.warning(
            f"Invalid avatar profile for user {user_id}: {exc}. Returning defaults."
        )
        return AvatarProfile()


async def update_avatar_profile(
    user_id: int, update: AvatarProfileUpdate
) -> AvatarProfile:
    profile = update.apply_to(await get_avatar_profile(user_id))
    await db_client.upsert_user_configuration_value(
        user_id,
        UserConfigurationKey.AVATAR.value,
        profile.model_dump(mode="json", exclude_none=True),
    )
    return profile


def apply_agent_voice(
    config: EffectiveAIModelConfiguration,
    profile: AvatarProfile,
) -> EffectiveAIModelConfiguration:
    """Swap in the avatar's voice when it fits the current TTS connection.

    Deliberately narrow: a provider switch (or a connection that dropped the
    chosen voice) leaves the organization's voice untouched instead of failing
    the call. Language pinning runs afterwards, so a provider that needs a
    locale-specific voice still wins for Urdu/Hindi calls.
    """
    tts = config.tts
    voice = profile.voice.voice_id
    if tts is None or not voice or profile.voice.provider != _provider(tts.provider):
        return config
    if not hasattr(tts, "voice") or tts.voice == voice:
        return config
    return config.model_copy(update={"tts": tts.model_copy(update={"voice": voice})})


async def avatar_voice_options(
    organization_id: int,
    character: AvatarCharacter,
    *,
    current_voice_id: str | None = None,
) -> AvatarVoiceOptions:
    """List voices for the avatar's gender from the user's own TTS connection.

    Uses the same catalog as Model connections, so previews and filters match
    what the settings screen shows.
    """
    resolved = await get_resolved_ai_model_configuration(
        organization_id=organization_id
    )
    tts = resolved.effective.tts
    if tts is None:
        return AvatarVoiceOptions(
            message="Add a voice provider in Model connections first."
        )
    provider = _provider(tts.provider)
    if provider == ServiceProviders.BOLO.value:
        return AvatarVoiceOptions(
            message=(
                "Bolo Agent voice runs on your own voice connection. Add a "
                "provider key in Model connections."
            )
        )

    default = await get_default_model_configuration(organization_id)
    saved = (default.configuration or {}).get("tts") if default else None
    connection_uuid = saved.get("provider_connection_uuid") if saved else None
    if not connection_uuid:
        return AvatarVoiceOptions(
            provider=provider,
            current_voice_id=current_voice_id,
            message="Your voice provider is not connected yet.",
        )

    model = getattr(tts, "model", None)
    try:
        response = await get_connection_voices(
            provider,
            organization_id,
            connection_uuid,
            model=model,
            gender=character,
        )
    except HTTPException as exc:
        # The catalog already turns provider failures into customer-safe text.
        return AvatarVoiceOptions(
            provider=provider,
            model=model,
            current_voice_id=current_voice_id,
            message=str(exc.detail),
        )

    voices = [
        voice
        for voice in response.voices
        if not voice.gender or voice.gender.lower() == character
    ]
    return AvatarVoiceOptions(
        configured=True,
        provider=response.provider,
        model=model,
        current_voice_id=current_voice_id,
        # Some catalogs do not expose gender; keep those so the picker is never
        # empty, but gender-tagged voices of the other gender are dropped.
        voices=voices or response.voices,
    )


def _provider(provider) -> str:
    return getattr(provider, "value", provider)
