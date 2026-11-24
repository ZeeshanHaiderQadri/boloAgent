from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from api.schemas.voices import VoiceInfo

AvatarCharacter = Literal["male", "female"]
AvatarAction = Literal["idle", "typing", "analyzing", "presenting"]
AvatarSize = Literal["compact", "large"]
PetState = Literal["expanded", "minimized", "hidden"]


class AvatarAppearance(BaseModel):
    """Which gadgets the 3D kit renders."""

    glasses: bool = True
    hood: bool = True
    hud: bool = True


class AvatarVoice(BaseModel):
    """The user's pick from their own TTS connection.

    ``provider`` is stored next to the id so a later switch of the voice
    connection cannot silently read the id against a different catalog: the
    override only applies while the agent's TTS provider still matches.
    """

    provider: str | None = None
    voice_id: str | None = None


class AvatarPosition(BaseModel):
    """Viewport-relative position, so the pet lands in the same spot on any
    screen size. Null means the UI's default corner."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class AvatarProfile(BaseModel):
    """Per-user Bolo Agent avatar, stored under UserConfigurationKey.AVATAR."""

    character: AvatarCharacter | None = None
    # setup_completed covers both a saved choice and "not now", so the
    # first-visit prompt does not reappear on every conversation.
    setup_completed: bool = False
    enabled: bool = False
    appearance: AvatarAppearance = Field(default_factory=AvatarAppearance)
    # Male kit only: laptop / AR HUD poses. The female kit ignores it.
    action: AvatarAction = "idle"
    voice: AvatarVoice = Field(default_factory=AvatarVoice)
    # How large the floating companion renders; the user can grow it while a
    # call is running for a closer view of the face.
    size: AvatarSize = "compact"
    pet_state: PetState = "expanded"
    position: AvatarPosition | None = None

    @field_validator("voice")
    @classmethod
    def _voice_needs_both(cls, value: AvatarVoice) -> AvatarVoice:
        if bool(value.provider) != bool(value.voice_id):
            raise ValueError("provider and voice_id must be set together")
        return value


class AvatarVoiceOptions(BaseModel):
    """Voices from the user's own TTS connection, filtered by gender."""

    configured: bool = False
    provider: str | None = None
    model: str | None = None
    current_voice_id: str | None = None
    voices: list[VoiceInfo] = Field(default_factory=list)
    message: str | None = None


class AvatarAppearanceUpdate(BaseModel):
    glasses: bool | None = None
    hood: bool | None = None
    hud: bool | None = None


class AvatarVoiceUpdate(BaseModel):
    provider: str | None = None
    voice_id: str | None = None

    @model_validator(mode="after")
    def _replace_both_or_neither(self):
        if bool(self.provider) != bool(self.voice_id):
            raise ValueError("provider and voice_id must be set together")
        return self


class AvatarProfileUpdate(BaseModel):
    """Partial update merged into the stored profile."""

    character: AvatarCharacter | None = None
    setup_completed: bool | None = None
    enabled: bool | None = None
    appearance: AvatarAppearanceUpdate | None = None
    action: AvatarAction | None = None
    voice: AvatarVoiceUpdate | None = None
    size: AvatarSize | None = None
    pet_state: PetState | None = None
    position: AvatarPosition | None = None

    def apply_to(self, profile: AvatarProfile) -> AvatarProfile:
        merged = profile.model_copy(deep=True)
        if self.character is not None:
            merged.character = self.character
        if self.setup_completed is not None:
            merged.setup_completed = self.setup_completed
        if self.enabled is not None:
            merged.enabled = self.enabled
        if self.pet_state is not None:
            merged.pet_state = self.pet_state
            if self.enabled is None:
                merged.enabled = self.pet_state != "hidden"
        if self.position is not None:
            merged.position = self.position
        if self.appearance is not None:
            for field, value in self.appearance.model_dump().items():
                if value is not None:
                    setattr(merged.appearance, field, value)
        if self.action is not None:
            merged.action = self.action
        if self.size is not None:
            merged.size = self.size
        if self.voice is not None and self.voice.provider:
            # A non-empty update replaces the pair; omitting both keeps the
            # stored pick.
            merged.voice = AvatarVoice(
                provider=self.voice.provider,
                voice_id=self.voice.voice_id,
            )
        return merged
