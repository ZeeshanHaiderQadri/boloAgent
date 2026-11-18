"""Lead generation + lightweight CRM API."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from loguru import logger

from api.constants import (
    EMAIL_CONSENT_WARNING,
    LEADS_DEFAULT_PROVIDER,
    LEADS_DISPATCH_MAX_LEADS,
    LEADS_ENABLE_DEMO_PROVIDER,
    LEADS_EXPORT_MAX_ROWS,
    LEADS_MAX_TARGET_RESULTS,
)
from api.db import db_client
from api.db.models import UserModel
from api.schemas.leads import (
    LEAD_CRM_STATUSES,
    BulkLeadEmailConsentRequest,
    BulkLeadEmailConsentResponse,
    BulkLeadUpdateRequest,
    LeadActivityCreateRequest,
    LeadActivityResponse,
    LeadCallResponse,
    LeadDetailResponse,
    LeadDispatchRequest,
    LeadDispatchResponse,
    LeadEmailConsentUpdateRequest,
    LeadEmailConsentValue,
    LeadFacetsResponse,
    LeadListResponse,
    LeadMetaResponse,
    LeadResponse,
    LeadSearchCreateRequest,
    LeadSearchExtendRequest,
    LeadSearchLinkResponse,
    LeadSearchListResponse,
    LeadSearchParams,
    LeadSearchResponse,
    LeadSearchUpdateRequest,
    LeadStatsResponse,
    LeadUpdateRequest,
)
from api.services.auth.depends import get_user
from api.services.leads.call_outcomes import call_outcome
from api.services.leads.consent import (
    consent_reset_values,
    email_address_changed,
    email_change_activity,
)
from api.services.leads.dispatch import (
    DispatchError,
    DispatchQuotaError,
    add_leads_to_campaign,
    dispatch_leads_to_campaign,
)
from api.services.leads.exports import EXPORT_FORMATS, build_export
from api.services.leads.normalization import normalize_lead_phone
from api.services.leads.providers import available_providers
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames

router = APIRouter(prefix="/leads")

LeadStatusQuery = Literal[
    "new",
    "contacted",
    "interested",
    "not_interested",
    "won",
    "lost",
    "do_not_call",
]
LeadSortQuery = Literal["created_at", "rating", "title", "status"]
ExportFormatQuery = Literal["csv", "xlsx", "json", "pdf", "png"]


def _organization_id(user: UserModel) -> int:
    if not user.selected_organization_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    return user.selected_organization_id


def _total_pages(total: int, limit: int) -> int:
    return math.ceil(total / limit) if total and limit else 0


ACTIVE_SEARCH_STATUSES = {"queued", "running", "stopping"}

EMAIL_CONSENT_LABELS = {
    "unknown": "Unknown",
    "opted_in": "Opted in",
    "opted_out": "Opted out",
}


def _email_consent_values(
    consent: str, *, source: str, now: datetime
) -> dict[str, object]:
    """Columns to write for a consent decision.

    ``unknown`` means no decision has been captured, so the source and
    timestamp are cleared rather than leaving a stale record behind.
    """
    if consent == "unknown":
        return {
            "email_consent": consent,
            "email_consent_source": None,
            "email_consent_at": None,
        }
    return {
        "email_consent": consent,
        "email_consent_source": source,
        "email_consent_at": now,
    }


def _email_consent_activity(
    lead,
    *,
    organization_id: int,
    user_id: int,
    consent: str,
    source: str,
    reason: str | None,
    bulk: bool,
    changed: bool = True,
) -> dict:
    before = EMAIL_CONSENT_LABELS.get(lead.email_consent, lead.email_consent)
    after = EMAIL_CONSENT_LABELS.get(consent, consent)
    if changed:
        title = f"Email consent: {before} → {after}"
    else:
        # The lead already had this decision. The operator still supplied a
        # reason, so keep it on the timeline instead of silently dropping it.
        title = f"Email consent confirmed: {after}"
    return {
        "organization_id": organization_id,
        "lead_id": lead.id,
        "user_id": user_id,
        "activity_type": "email_consent",
        "title": title,
        "body": reason,
        "extra_metadata": {
            "from": lead.email_consent,
            "to": consent,
            "source": source,
            "bulk": bulk,
            "changed": changed,
            "warning": EMAIL_CONSENT_WARNING,
        },
    }


def _available_provider(provider: str | None) -> str:
    resolved = (provider or LEADS_DEFAULT_PROVIDER).strip()
    if resolved not in available_providers(LEADS_ENABLE_DEMO_PROVIDER):
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{resolved}' is not available on this deployment",
        )
    return resolved


def _search_params(request: LeadSearchParams) -> dict:
    return {
        "depth": request.depth,
        "radius_km": request.radius_km,
        "email": request.email,
        "max_time_seconds": request.max_time_seconds,
        "zoom": request.zoom,
        "website_filter": request.website_filter,
        "auto_call": request.auto_call,
        "workflow_id": request.workflow_id,
        "telephony_configuration_id": request.telephony_configuration_id,
    }


def _search_name(request: LeadSearchParams) -> str:
    return (request.name or f"{request.category} in {request.location}").strip()[:255]


async def _enqueue_search(search_id: int, organization_id: int) -> None:
    """Queue a search run, marking it failed if the queue is unavailable."""
    try:
        await enqueue_job(FunctionNames.RUN_LEAD_SEARCH, search_id)
    except Exception as exc:
        logger.exception("Could not enqueue lead search {}", search_id)
        await db_client.update_lead_search(
            search_id,
            organization_id,
            status="failed",
            completed_at=datetime.now(UTC),
            error_message="Background queue unavailable; try again shortly.",
        )
        raise HTTPException(
            status_code=503, detail="Background queue unavailable; try again shortly."
        ) from exc


@router.get("/meta", response_model=LeadMetaResponse)
async def get_leads_meta(user: UserModel = Depends(get_user)) -> LeadMetaResponse:
    return LeadMetaResponse(
        statuses=LEAD_CRM_STATUSES,
        providers=available_providers(LEADS_ENABLE_DEMO_PROVIDER),
        default_provider=LEADS_DEFAULT_PROVIDER,
        demo_enabled=LEADS_ENABLE_DEMO_PROVIDER,
        export_formats=list(EXPORT_FORMATS),
        max_dispatch_leads=LEADS_DISPATCH_MAX_LEADS,
    )


@router.get("/stats", response_model=LeadStatsResponse)
async def get_lead_stats(
    search_id: int | None = Query(default=None),
    project_id: int | None = Query(default=None, ge=1),
    user: UserModel = Depends(get_user),
) -> LeadStatsResponse:
    stats = await db_client.lead_stats(
        _organization_id(user), search_id=search_id, project_id=project_id
    )
    return LeadStatsResponse(**stats)


@router.get("/facets", response_model=LeadFacetsResponse)
async def get_lead_facets(
    country: str | None = Query(default=None, max_length=80),
    state: str | None = Query(default=None, max_length=80),
    user: UserModel = Depends(get_user),
) -> LeadFacetsResponse:
    """Distinct countries, states, and cities available for CRM filters.

    State options narrow to the chosen country and city options narrow to the
    chosen country and state, so the UI can show dependent dropdowns.
    """
    facets = await db_client.lead_facets(
        _organization_id(user),
        country=country,
        state=state,
    )
    return LeadFacetsResponse(**facets)


# ---------------------------------------------------------------------------
# Searches
# ---------------------------------------------------------------------------
@router.post("/searches", response_model=LeadSearchResponse)
async def create_lead_search(
    request: LeadSearchCreateRequest,
    user: UserModel = Depends(get_user),
) -> LeadSearchResponse:
    organization_id = _organization_id(user)
    provider = _available_provider(request.provider)

    search = await db_client.create_lead_search(
        organization_id=organization_id,
        created_by=user.id,
        name=_search_name(request),
        category=request.category,
        location=request.location,
        country_code=request.country_code,
        language=request.language,
        provider=provider,
        params=_search_params(request),
        target_results=request.target_results,
    )

    await _enqueue_search(search.id, organization_id)

    await db_client.update_lead_search(search.id, organization_id, status="queued")
    refreshed = await db_client.get_lead_search(search.id, organization_id)
    return LeadSearchResponse.model_validate(refreshed or search)


@router.get("/searches", response_model=LeadSearchListResponse)
async def list_lead_searches(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    user: UserModel = Depends(get_user),
) -> LeadSearchListResponse:
    organization_id = _organization_id(user)
    offset = (page - 1) * limit
    searches, total = await db_client.list_lead_searches(
        organization_id, limit=limit, offset=offset
    )
    return LeadSearchListResponse(
        searches=[LeadSearchResponse.model_validate(item) for item in searches],
        total_count=total,
        page=page,
        limit=limit,
        total_pages=_total_pages(total, limit),
    )


@router.get("/searches/{search_id}", response_model=LeadSearchResponse)
async def get_lead_search(
    search_id: int, user: UserModel = Depends(get_user)
) -> LeadSearchResponse:
    search = await db_client.get_lead_search(search_id, _organization_id(user))
    if search is None:
        raise HTTPException(status_code=404, detail="Search not found")
    return LeadSearchResponse.model_validate(search)


@router.post("/searches/{search_id}/cancel", response_model=LeadSearchResponse)
async def cancel_lead_search(
    search_id: int, user: UserModel = Depends(get_user)
) -> LeadSearchResponse:
    organization_id = _organization_id(user)
    search = await db_client.get_lead_search(search_id, organization_id)
    if search is None:
        raise HTTPException(status_code=404, detail="Search not found")
    if search.status not in {"queued", "running", "stopping"}:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel a search in '{search.status}' state",
        )
    updated = await db_client.cancel_lead_search(search_id, organization_id)
    return LeadSearchResponse.model_validate(updated or search)


@router.post("/searches/{search_id}/stop", response_model=LeadSearchResponse)
async def stop_lead_search(
    search_id: int, user: UserModel = Depends(get_user)
) -> LeadSearchResponse:
    """Finish a running search now, keeping every lead collected so far."""
    organization_id = _organization_id(user)
    search = await db_client.get_lead_search(search_id, organization_id)
    if search is None:
        raise HTTPException(status_code=404, detail="Search not found")
    if search.status not in {"queued", "running", "stopping"}:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot stop a search in '{search.status}' state",
        )
    updated = await db_client.update_lead_search(
        search_id, organization_id, status="stopping"
    )
    return LeadSearchResponse.model_validate(updated or search)


@router.post("/searches/{search_id}/retry", response_model=LeadSearchResponse)
async def retry_lead_search(
    search_id: int, user: UserModel = Depends(get_user)
) -> LeadSearchResponse:
    organization_id = _organization_id(user)
    search = await db_client.get_lead_search(search_id, organization_id)
    if search is None:
        raise HTTPException(status_code=404, detail="Search not found")
    if search.status not in {"failed", "cancelled", "stopped"}:
        raise HTTPException(
            status_code=400,
            detail="Only failed, cancelled, or stopped searches can be retried",
        )

    updated = await db_client.update_lead_search(
        search_id,
        organization_id,
        status="queued",
        external_job_id=None,
        error_message=None,
        auto_call_campaign_id=None,
        auto_call_error=None,
        started_at=None,
        completed_at=None,
    )
    await _enqueue_search(search_id, organization_id)
    return LeadSearchResponse.model_validate(updated or search)


@router.patch("/searches/{search_id}", response_model=LeadSearchResponse)
async def update_lead_search(
    search_id: int,
    request: LeadSearchUpdateRequest,
    user: UserModel = Depends(get_user),
) -> LeadSearchResponse:
    """Edit a finished search and run it again with the new settings.

    Unlike Retry, this lets the operator fix the query that came up short
    (category, location, country, target, filters). Existing leads are kept
    by default so tweaking one setting does not throw away good rows.
    """
    organization_id = _organization_id(user)
    search = await db_client.get_lead_search(search_id, organization_id)
    if search is None:
        raise HTTPException(status_code=404, detail="Search not found")
    if search.status in ACTIVE_SEARCH_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Stop or cancel the search before editing it",
        )

    existing_count = int(search.results_count or 0)
    if request.keep_existing_results and existing_count >= request.target_results:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This search already has {existing_count} leads. Raise the "
                "target above that, or start the search fresh."
            ),
        )

    if request.add_to_campaign_id is not None:
        campaign = await db_client.get_campaign(
            request.add_to_campaign_id, organization_id
        )
        if campaign is None:
            raise HTTPException(status_code=404, detail="Campaign not found")
        if campaign.source_type != "csv":
            raise HTTPException(
                status_code=400,
                detail="Only CSV-based outbound campaigns can receive CRM leads",
            )
        if campaign.state in {"created", "syncing"}:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Start the campaign before adding leads, or wait for its "
                    "sync to finish"
                ),
            )
        if existing_count:
            leads, _total = await db_client.list_leads(
                organization_id,
                search_id=search_id,
                has_phone=True,
                limit=LEADS_DISPATCH_MAX_LEADS,
            )
            lead_ids = [lead.id for lead in leads if lead.crm_status != "do_not_call"]
            if lead_ids:
                try:
                    await add_leads_to_campaign(
                        user=user,
                        organization_id=organization_id,
                        lead_ids=lead_ids,
                        campaign_id=request.add_to_campaign_id,
                    )
                except DispatchError as exc:
                    # Already-queued leads are expected on repeated edits; the
                    # rerun should still proceed.
                    logger.info(
                        "Could not pre-add search {} leads to campaign {}: {}",
                        search_id,
                        request.add_to_campaign_id,
                        exc,
                    )

    params = _search_params(request)
    params["add_to_campaign_id"] = request.add_to_campaign_id

    values: dict = {
        "name": _search_name(request),
        "category": request.category,
        "location": request.location,
        "country_code": request.country_code,
        "language": request.language,
        "provider": _available_provider(request.provider or search.provider),
        "params": params,
        "target_results": request.target_results,
        "status": "queued",
        "external_job_id": None,
        "error_message": None,
        "auto_call_error": None,
        "started_at": None,
        "completed_at": None,
    }
    if not request.keep_existing_results:
        await db_client.clear_search_results(search_id, organization_id)
        values["results_count"] = 0
        values["new_leads_count"] = 0
    # auto_call_campaign_id is deliberately preserved: re-running a search
    # must not dial leads that a previous campaign already called.

    updated = await db_client.update_lead_search(search_id, organization_id, **values)
    await _enqueue_search(search_id, organization_id)
    return LeadSearchResponse.model_validate(updated or search)


@router.post("/searches/{search_id}/extend", response_model=LeadSearchResponse)
async def extend_lead_search(
    search_id: int,
    request: LeadSearchExtendRequest,
    user: UserModel = Depends(get_user),
) -> LeadSearchResponse:
    """Keep the same query and collect more leads.

    The target becomes "everything collected so far + the requested extra".
    Depth is raised as well, because the scraper only discovers new
    businesses on deeper pages of the same query.
    """
    organization_id = _organization_id(user)
    search = await db_client.get_lead_search(search_id, organization_id)
    if search is None:
        raise HTTPException(status_code=404, detail="Search not found")
    if search.status in ACTIVE_SEARCH_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="This search is still running; wait for it to finish",
        )

    current_target = max(
        int(search.target_results or 0), int(search.results_count or 0)
    )
    new_target = current_target + request.additional_results
    if new_target > LEADS_MAX_TARGET_RESULTS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This search would reach {new_target} leads, above the "
                f"limit of {LEADS_MAX_TARGET_RESULTS}. Extend by "
                f"{max(1, LEADS_MAX_TARGET_RESULTS - current_target)} or less."
            ),
        )

    params = dict(search.params or {})
    cached_depth = int(params.get("depth", 3) or 3)
    # Roughly 20 businesses per depth page; more pages are required to reach
    # a larger target. Never lower the previous depth.
    params["depth"] = min(20, max(cached_depth, math.ceil(new_target / 20)))

    updated = await db_client.update_lead_search(
        search_id,
        organization_id,
        params=params,
        target_results=new_target,
        status="queued",
        external_job_id=None,
        error_message=None,
        auto_call_error=None,
        started_at=None,
        completed_at=None,
    )
    await _enqueue_search(search_id, organization_id)
    return LeadSearchResponse.model_validate(updated or search)


# ---------------------------------------------------------------------------
# Leads / CRM
# ---------------------------------------------------------------------------
async def _query_leads(
    user: UserModel,
    *,
    search_id: int | None,
    project_id: int | None = None,
    status: str | None,
    q: str | None,
    has_phone: bool | None,
    has_website: bool | None,
    has_email: bool | None,
    country: str | None,
    state: str | None,
    city: str | None,
    category: str | None,
    tag: str | None,
    email_consent: str | None,
    sort: str,
    page: int,
    limit: int,
):
    organization_id = _organization_id(user)
    offset = (page - 1) * limit
    return await db_client.list_leads(
        organization_id,
        search_id=search_id,
        project_id=project_id,
        crm_status=status,
        query=q,
        has_phone=has_phone,
        has_website=has_website,
        has_email=has_email,
        country=country,
        state=state,
        city=city,
        category=category,
        tag=tag,
        email_consent=email_consent,
        sort=sort,
        limit=limit,
        offset=offset,
    )


@router.get("", response_model=LeadListResponse)
async def list_leads(
    search_id: int | None = Query(default=None),
    project_id: int | None = Query(default=None, ge=1),
    status: LeadStatusQuery | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    has_phone: bool | None = Query(default=None),
    has_website: bool | None = Query(default=None),
    has_email: bool | None = Query(default=None),
    country: str | None = Query(default=None, max_length=80),
    state: str | None = Query(default=None, max_length=80),
    city: str | None = Query(default=None, max_length=120),
    category: str | None = Query(default=None, max_length=120),
    tag: str | None = Query(default=None, max_length=40),
    email_consent: LeadEmailConsentValue | None = Query(default=None),
    sort: LeadSortQuery = Query(default="created_at"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    user: UserModel = Depends(get_user),
) -> LeadListResponse:
    leads, total = await _query_leads(
        user,
        search_id=search_id,
        project_id=project_id,
        status=status,
        q=q,
        has_phone=has_phone,
        has_website=has_website,
        has_email=has_email,
        country=country,
        state=state,
        city=city,
        category=category,
        tag=tag,
        email_consent=email_consent,
        sort=sort,
        page=page,
        limit=limit,
    )
    return LeadListResponse(
        leads=[LeadResponse.model_validate(lead) for lead in leads],
        total_count=total,
        page=page,
        limit=limit,
        total_pages=_total_pages(total, limit),
    )


@router.get("/export")
async def export_leads(
    export_format: ExportFormatQuery = Query(default="csv", alias="format"),
    search_id: int | None = Query(default=None),
    project_id: int | None = Query(default=None, ge=1),
    status: LeadStatusQuery | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    has_phone: bool | None = Query(default=None),
    has_website: bool | None = Query(default=None),
    has_email: bool | None = Query(default=None),
    country: str | None = Query(default=None, max_length=80),
    state: str | None = Query(default=None, max_length=80),
    city: str | None = Query(default=None, max_length=120),
    category: str | None = Query(default=None, max_length=120),
    tag: str | None = Query(default=None, max_length=40),
    email_consent: LeadEmailConsentValue | None = Query(default=None),
    sort: LeadSortQuery = Query(default="created_at"),
    user: UserModel = Depends(get_user),
):
    organization_id = _organization_id(user)
    leads, total = await db_client.list_leads(
        organization_id,
        search_id=search_id,
        project_id=project_id,
        crm_status=status,
        query=q,
        has_phone=has_phone,
        has_website=has_website,
        has_email=has_email,
        country=country,
        state=state,
        city=city,
        category=category,
        tag=tag,
        email_consent=email_consent,
        sort=sort,
        limit=LEADS_EXPORT_MAX_ROWS,
        offset=0,
    )
    if not leads:
        raise HTTPException(status_code=400, detail="No leads match these filters")

    title = "Bolo leads"
    if search_id is not None:
        search = await db_client.get_lead_search(search_id, organization_id)
        if search:
            title = search.name
    elif project_id is not None:
        project_row = await db_client.get_lead_project(project_id, organization_id)
        if project_row is None:
            raise HTTPException(status_code=404, detail="Project not found")
        title = project_row[0].name
    try:
        content, filename, media_type = build_export(
            leads,
            export_format,
            title=title,
            subtitle=f"{total} matching lead(s)",
        )
    except ImportError as exc:
        logger.exception("Export dependency missing")
        raise HTTPException(
            status_code=501,
            detail="This export format is not available on this deployment",
        ) from exc

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/bulk")
async def bulk_update_leads(
    request: BulkLeadUpdateRequest,
    user: UserModel = Depends(get_user),
) -> dict[str, int]:
    organization_id = _organization_id(user)
    lead_ids = list(dict.fromkeys(request.lead_ids))
    existing = await db_client.get_leads_by_ids(lead_ids, organization_id)
    if not existing:
        raise HTTPException(status_code=404, detail="No matching leads")

    values: dict = {}
    now = datetime.now(UTC)
    if request.crm_status is not None:
        values["crm_status"] = request.crm_status
        if request.crm_status in {
            "contacted",
            "interested",
            "not_interested",
            "won",
            "lost",
        }:
            values["last_contacted_at"] = now
    if request.tags is not None:
        values["tags"] = request.tags

    updated = await db_client.bulk_update_leads(
        organization_id, [lead.id for lead in existing], **values
    )
    valid_ids = [lead.id for lead in existing]
    if request.add_tags:
        await db_client.add_tags_to_leads(organization_id, valid_ids, request.add_tags)

    if request.crm_status is not None:
        activities = []
        for lead in existing:
            if lead.crm_status == request.crm_status:
                continue
            activities.append(
                {
                    "organization_id": organization_id,
                    "lead_id": lead.id,
                    "user_id": user.id,
                    "activity_type": "status_change",
                    "title": (
                        "Status changed: "
                        f"{lead.crm_status.replace('_', ' ')} → "
                        f"{request.crm_status.replace('_', ' ')}"
                    ),
                    "extra_metadata": {
                        "from": lead.crm_status,
                        "to": request.crm_status,
                        "bulk": True,
                    },
                }
            )
        await db_client.add_lead_activities_bulk(activities)

    return {"updated": updated}


@router.post("/email-consent/bulk", response_model=BulkLeadEmailConsentResponse)
async def bulk_update_lead_email_consent(
    request: BulkLeadEmailConsentRequest,
    user: UserModel = Depends(get_user),
) -> BulkLeadEmailConsentResponse:
    organization_id = _organization_id(user)
    lead_ids = list(dict.fromkeys(request.lead_ids))
    existing = await db_client.get_leads_by_ids(lead_ids, organization_id)
    if not existing:
        raise HTTPException(status_code=404, detail="No matching leads")

    now = datetime.now(UTC)
    changed_ids = [
        lead.id for lead in existing if lead.email_consent != request.email_consent
    ]
    # Record the reason for every matched lead, including those already in the
    # chosen state: the operator's explanation is part of the audit trail even
    # when no column changes.
    activities = [
        _email_consent_activity(
            lead,
            organization_id=organization_id,
            user_id=user.id,
            consent=request.email_consent,
            source="bulk",
            reason=request.reason,
            bulk=True,
            changed=lead.email_consent != request.email_consent,
        )
        for lead in existing
    ]
    changed = await db_client.bulk_update_leads(
        organization_id,
        changed_ids,
        activities=activities,
        **_email_consent_values(request.email_consent, source="bulk", now=now),
    )
    return BulkLeadEmailConsentResponse(matched=len(existing), changed=changed)


@router.post("/dispatch-campaign", response_model=LeadDispatchResponse)
async def dispatch_leads(
    request: LeadDispatchRequest,
    user: UserModel = Depends(get_user),
) -> LeadDispatchResponse:
    try:
        result = await dispatch_leads_to_campaign(
            user=user,
            lead_ids=request.lead_ids,
            name=request.name,
            workflow_id=request.workflow_id,
            telephony_configuration_id=request.telephony_configuration_id,
            max_concurrency=request.max_concurrency,
            rate_limit_per_second=request.rate_limit_per_second,
            start_immediately=request.start_immediately,
        )
    except DispatchQuotaError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    except DispatchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return LeadDispatchResponse(
        campaign_id=result.campaign_id,
        campaign_name=result.campaign_name,
        dispatched=result.dispatched,
        skipped=result.skipped,
        warnings=result.warnings,
    )


@router.patch("/{lead_id}/email-consent", response_model=LeadResponse)
async def update_lead_email_consent(
    lead_id: int,
    request: LeadEmailConsentUpdateRequest,
    user: UserModel = Depends(get_user),
) -> LeadResponse:
    organization_id = _organization_id(user)
    lead = await db_client.get_lead(lead_id, organization_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    now = datetime.now(UTC)
    activity = _email_consent_activity(
        lead,
        organization_id=organization_id,
        user_id=user.id,
        consent=request.email_consent,
        source="manual",
        reason=request.reason,
        bulk=False,
    )
    updated = await db_client.update_lead(
        lead_id,
        organization_id,
        activities=[activity],
        **_email_consent_values(request.email_consent, source="manual", now=now),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    return LeadResponse.model_validate(updated)


@router.get("/{lead_id}", response_model=LeadDetailResponse)
async def get_lead_detail(
    lead_id: int, user: UserModel = Depends(get_user)
) -> LeadDetailResponse:
    organization_id = _organization_id(user)
    lead = await db_client.get_lead(lead_id, organization_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    activities, calls, searches = await _lead_relations(lead_id, organization_id)
    return LeadDetailResponse(
        lead=LeadResponse.model_validate(lead),
        activities=activities,
        calls=calls,
        searches=searches,
    )


async def _lead_relations(lead_id: int, organization_id: int):
    activities = await db_client.list_lead_activities(lead_id, organization_id)
    calls = await db_client.list_lead_calls(lead_id, organization_id)
    searches = await db_client.list_lead_search_links(lead_id, organization_id)
    call_models = []
    for call in calls:
        outcome = call_outcome(call)
        call.pop("extracted_variables", None)
        call_models.append(LeadCallResponse(**{**call, **outcome}))
    return (
        [LeadActivityResponse.model_validate(item) for item in activities],
        call_models,
        [LeadSearchLinkResponse.model_validate(item) for item in searches],
    )


@router.patch("/{lead_id}", response_model=LeadResponse)
async def update_lead(
    lead_id: int,
    request: LeadUpdateRequest,
    user: UserModel = Depends(get_user),
) -> LeadResponse:
    organization_id = _organization_id(user)
    lead = await db_client.get_lead(lead_id, organization_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    values: dict = {}
    now = datetime.now(UTC)
    status_changed = (
        request.crm_status is not None and request.crm_status != lead.crm_status
    )
    if request.crm_status is not None:
        values["crm_status"] = request.crm_status
        if request.crm_status in {
            "contacted",
            "interested",
            "not_interested",
            "won",
            "lost",
        }:
            values["last_contacted_at"] = now
    if request.tags is not None:
        values["tags"] = request.tags
    if request.notes is not None:
        values["notes"] = request.notes
    if request.next_follow_up_at is not None:
        values["next_follow_up_at"] = request.next_follow_up_at

    # Inline contact edits from the CRM / project sheet. ``None`` means "leave
    # unchanged"; an empty string clears the value.
    contact_fields = {
        "title": request.title,
        "category": request.category,
        "phone": request.phone,
        "email": request.email,
        "website": request.website,
        "address": request.address,
        "city": request.city,
        "state": request.state,
        "country": request.country,
        "postal_code": request.postal_code,
    }
    changed_contact_fields: list[str] = []
    for field, value in contact_fields.items():
        if value is None:
            continue
        if field == "title" and not value:
            raise HTTPException(status_code=422, detail="Business name cannot be empty")
        values[field] = value or None
        changed_contact_fields.append(field)
    if request.phone is not None:
        values["phone_e164"] = normalize_lead_phone(
            request.phone or None, request.country or lead.country
        )

    # Consent belongs to the address it was recorded for. If the operator
    # changes the address, the old decision no longer covers this mailbox.
    consent_activities: list[dict] = []
    if request.email is not None and email_address_changed(
        lead.email, request.email or None
    ):
        values.update(consent_reset_values())
        consent_activities.append(
            email_change_activity(
                organization_id=organization_id,
                lead_id=lead_id,
                user_id=user.id,
                previous_email=lead.email,
                new_email=request.email or None,
                previous_consent=lead.email_consent,
                source="manual",
            )
        )

    updated = await db_client.update_lead(
        lead_id,
        organization_id,
        activities=consent_activities or None,
        **values,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    if changed_contact_fields:
        await db_client.add_lead_activity(
            organization_id=organization_id,
            lead_id=lead_id,
            user_id=user.id,
            activity_type="lead_edit",
            title="Contact details updated",
            metadata={"fields": changed_contact_fields},
        )

    if status_changed:
        await db_client.add_lead_activity(
            organization_id=organization_id,
            lead_id=lead_id,
            user_id=user.id,
            activity_type="status_change",
            title=(
                "Status changed: "
                f"{(lead.crm_status or 'new').replace('_', ' ')} → "
                f"{(request.crm_status or '').replace('_', ' ')}"
            ),
            metadata={"from": lead.crm_status, "to": request.crm_status},
        )
    if request.notes is not None:
        await db_client.add_lead_activity(
            organization_id=organization_id,
            lead_id=lead_id,
            user_id=user.id,
            activity_type="note",
            title="Notes updated",
            body=request.notes,
        )

    return LeadResponse.model_validate(updated)


@router.delete("/{lead_id}")
async def delete_lead(
    lead_id: int, user: UserModel = Depends(get_user)
) -> dict[str, bool]:
    deleted = await db_client.delete_lead(lead_id, _organization_id(user))
    if not deleted:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"deleted": True}


@router.post("/{lead_id}/activities", response_model=LeadActivityResponse)
async def add_lead_note(
    lead_id: int,
    request: LeadActivityCreateRequest,
    user: UserModel = Depends(get_user),
) -> LeadActivityResponse:
    organization_id = _organization_id(user)
    lead = await db_client.get_lead(lead_id, organization_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    activity = await db_client.add_lead_activity(
        organization_id=organization_id,
        lead_id=lead_id,
        user_id=user.id,
        activity_type="note",
        title=request.title or "Note",
        body=request.body,
    )
    return LeadActivityResponse.model_validate(activity)
