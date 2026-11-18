"""Lead projects (saved lead lists) API.

A project is a saved collection of CRM leads used to prepare campaigns. A lead
lives in at most one project, so assigning it elsewhere moves it instead of
creating a duplicate. Deleting a project only un-files its leads.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.db import db_client
from api.db.lead_client import LeadProjectNameConflictError
from api.db.models import LeadProjectModel, UserModel
from api.schemas.leads import (
    LeadProjectAssignmentResponse,
    LeadProjectAssignRequest,
    LeadProjectCreateRequest,
    LeadProjectFilters,
    LeadProjectListResponse,
    LeadProjectResponse,
    LeadProjectUpdateRequest,
)
from api.services.auth.depends import get_user

router = APIRouter(prefix="/lead-projects")


def _organization_id(user: UserModel) -> int:
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return user.selected_organization_id


def _project_response(
    project: LeadProjectModel, lead_count: int, callable_count: int
) -> LeadProjectResponse:
    return LeadProjectResponse(
        id=project.id,
        name=project.name,
        lead_count=int(lead_count),
        callable_count=int(callable_count),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _filter_kwargs(filters: LeadProjectFilters | None) -> dict:
    """Map the shared filter schema onto ``LeadClient._lead_filters`` keys."""
    if filters is None:
        return {}
    return {
        "search_id": filters.search_id,
        "crm_status": filters.status,
        "query": filters.q,
        "has_phone": filters.has_phone,
        "has_website": filters.has_website,
        "has_email": filters.has_email,
        "country": filters.country,
        "state": filters.state,
        "city": filters.city,
        "category": filters.category,
        "tag": filters.tag,
        "email_consent": filters.email_consent,
    }


async def _require_project(
    project_id: int, organization_id: int
) -> tuple[LeadProjectModel, int, int]:
    row = await db_client.get_lead_project(project_id, organization_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return row


@router.get("", response_model=LeadProjectListResponse)
async def list_lead_projects(
    user: UserModel = Depends(get_user),
) -> LeadProjectListResponse:
    rows = await db_client.list_lead_projects(_organization_id(user))
    projects = [_project_response(*row) for row in rows]
    return LeadProjectListResponse(projects=projects, total=len(projects))


@router.post("", response_model=LeadProjectResponse, status_code=201)
async def create_lead_project(
    request: LeadProjectCreateRequest,
    user: UserModel = Depends(get_user),
) -> LeadProjectResponse:
    organization_id = _organization_id(user)
    if await db_client.get_lead_project_by_name(organization_id, request.name):
        raise HTTPException(
            status_code=409, detail="A project with that name already exists"
        )
    try:
        project = await db_client.create_lead_project(
            organization_id, request.name, created_by=user.id
        )
    except LeadProjectNameConflictError as exc:
        raise HTTPException(
            status_code=409, detail="A project with that name already exists"
        ) from exc

    if request.filters is not None:
        await db_client.assign_leads_to_project_by_filters(
            organization_id,
            project.id,
            **_filter_kwargs(request.filters),
        )

    row = await _require_project(project.id, organization_id)
    return _project_response(*row)


@router.get("/{project_id}", response_model=LeadProjectResponse)
async def get_lead_project(
    project_id: int,
    user: UserModel = Depends(get_user),
) -> LeadProjectResponse:
    row = await _require_project(project_id, _organization_id(user))
    return _project_response(*row)


@router.patch("/{project_id}", response_model=LeadProjectResponse)
async def update_lead_project(
    project_id: int,
    request: LeadProjectUpdateRequest,
    user: UserModel = Depends(get_user),
) -> LeadProjectResponse:
    organization_id = _organization_id(user)
    await _require_project(project_id, organization_id)

    existing = await db_client.get_lead_project_by_name(organization_id, request.name)
    if existing is not None and existing.id != project_id:
        raise HTTPException(
            status_code=409, detail="A project with that name already exists"
        )
    try:
        renamed = await db_client.rename_lead_project(
            project_id, organization_id, request.name
        )
    except LeadProjectNameConflictError as exc:
        raise HTTPException(
            status_code=409, detail="A project with that name already exists"
        ) from exc
    if renamed is None:
        raise HTTPException(status_code=404, detail="Project not found")

    row = await _require_project(project_id, organization_id)
    return _project_response(*row)


@router.delete("/{project_id}")
async def delete_lead_project(
    project_id: int,
    user: UserModel = Depends(get_user),
) -> dict[str, int | bool]:
    unassigned = await db_client.delete_lead_project(project_id, _organization_id(user))
    if unassigned is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"deleted": True, "unassigned": unassigned}


@router.post("/{project_id}/leads", response_model=LeadProjectAssignmentResponse)
async def assign_leads_to_project(
    project_id: int,
    request: LeadProjectAssignRequest,
    user: UserModel = Depends(get_user),
) -> LeadProjectAssignmentResponse:
    organization_id = _organization_id(user)
    await _require_project(project_id, organization_id)
    lead_ids = list(dict.fromkeys(request.lead_ids))
    assigned = await db_client.assign_leads_to_project(
        organization_id, project_id, lead_ids
    )
    row = await _require_project(project_id, organization_id)
    return LeadProjectAssignmentResponse(
        assigned=assigned, project=_project_response(*row)
    )


@router.post("/{project_id}/assign", response_model=LeadProjectAssignmentResponse)
async def assign_filtered_leads_to_project(
    project_id: int,
    filters: LeadProjectFilters,
    user: UserModel = Depends(get_user),
) -> LeadProjectAssignmentResponse:
    """Move every lead matching CRM filters into the project.

    Leads already filed under another project move here rather than being
    copied, so the same row never appears twice.
    """
    organization_id = _organization_id(user)
    await _require_project(project_id, organization_id)
    assigned = await db_client.assign_leads_to_project_by_filters(
        organization_id, project_id, **_filter_kwargs(filters)
    )
    row = await _require_project(project_id, organization_id)
    return LeadProjectAssignmentResponse(
        assigned=assigned, project=_project_response(*row)
    )


@router.delete("/{project_id}/leads")
async def unassign_leads_from_project(
    project_id: int,
    request: LeadProjectAssignRequest,
    user: UserModel = Depends(get_user),
) -> dict[str, int]:
    organization_id = _organization_id(user)
    await _require_project(project_id, organization_id)
    lead_ids = list(dict.fromkeys(request.lead_ids))
    removed = await db_client.unassign_leads_from_project(
        organization_id, project_id, lead_ids
    )
    return {"removed": removed}
