"""Install and remove models on the machine's local Ollama and Speaches."""

from fastapi import APIRouter, Depends, HTTPException, Response

from api.db.models import UserModel
from api.schemas.local_models import (
    LocalModelDownloadRequest,
    LocalModelDownloadResponse,
    LocalModelDownloadStatus,
    LocalModelFamily,
    LocalModelsResponse,
)
from api.services import local_models
from api.services.auth.depends import get_user_with_selected_organization

router = APIRouter(prefix="/local-models", tags=["local-models"])
Auth = Depends(get_user_with_selected_organization)


def _error(exc: local_models.LocalModelError) -> HTTPException:
    if isinstance(exc, local_models.LocalModelInvalid):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, local_models.LocalModelConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, local_models.LocalModelNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, local_models.LocalModelUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


@router.get("", response_model=LocalModelsResponse, operation_id="list_local_models")
async def list_local_models(user: UserModel = Auth):
    return await local_models.list_local_models()


@router.post(
    "/downloads",
    status_code=202,
    response_model=LocalModelDownloadResponse,
    operation_id="download_local_model",
)
async def download_local_model(
    request: LocalModelDownloadRequest, user: UserModel = Auth
):
    try:
        return await local_models.download_model(request.family, request.model)
    except local_models.LocalModelError as exc:
        raise _error(exc) from None


@router.get(
    "/downloads/{job_id}",
    response_model=LocalModelDownloadStatus,
    operation_id="get_local_model_download",
)
async def get_local_model_download(job_id: str, user: UserModel = Auth):
    job = await local_models.download_status(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Download not found")
    return job


@router.delete(
    "/{family}/{model:path}",
    status_code=204,
    operation_id="delete_local_model",
)
async def delete_local_model(
    family: LocalModelFamily, model: str, user: UserModel = Auth
):
    try:
        await local_models.remove_model(family, model)
    except local_models.LocalModelError as exc:
        raise _error(exc) from None
    return Response(status_code=204)
