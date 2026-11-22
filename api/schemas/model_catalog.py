from pydantic import BaseModel


class ModelInfo(BaseModel):
    id: str
    name: str | None = None


class ModelsResponse(BaseModel):
    provider: str
    models: list[ModelInfo]
