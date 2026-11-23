from pydantic import BaseModel


class ModelInfo(BaseModel):
    id: str
    name: str | None = None
    # Speech servers label their models ("text-to-speech",
    # "automatic-speech-recognition") so pickers can keep roles apart.
    task: str | None = None


class ModelsResponse(BaseModel):
    provider: str
    models: list[ModelInfo]
