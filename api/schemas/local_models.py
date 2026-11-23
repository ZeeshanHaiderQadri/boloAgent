"""Contracts for installing and removing models on local model servers."""

from typing import Literal

from pydantic import BaseModel, Field

LocalModelFamily = Literal["ollama", "speaches"]
LocalModelKind = Literal["llm", "stt", "tts"]


class LocalModelInfo(BaseModel):
    family: LocalModelFamily
    id: str
    kind: LocalModelKind
    title: str
    description: str | None = None
    installed: bool
    size_bytes: int | None = None
    downloading: bool = False
    # 0..1 while the server reports byte counts; None when it downloads blind.
    progress: float | None = None


class LocalModelsResponse(BaseModel):
    # Local servers this deployment can reach; empty hides the panel.
    servers: list[LocalModelFamily]
    models: list[LocalModelInfo]


class LocalModelDownloadRequest(BaseModel):
    family: LocalModelFamily
    model: str = Field(min_length=1, max_length=200)


class LocalModelDownloadResponse(BaseModel):
    job_id: str
    family: LocalModelFamily
    model: str


class LocalModelDownloadStatus(BaseModel):
    status: Literal["running", "done", "error"]
    model: str
    completed: int | None = None
    total: int | None = None
    detail: str | None = None
