"""Lead search creation shared by the API routes and the copilot tools.

Keeping provider resolution, request shaping and queueing here means the
conversational agent starts searches exactly the way the UI does, including
the failure path that marks a search failed when the queue is down.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger

from api.constants import LEADS_ENABLE_DEMO_PROVIDER
from api.db import db_client
from api.schemas.leads import LeadSearchParams
from api.services.leads.providers import available_providers, default_provider_name
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames


class ProviderUnavailableError(ValueError):
    """The deployment cannot run the requested lead provider."""


class SearchQueueUnavailableError(Exception):
    """The background queue rejected the search."""


def resolve_provider(provider: str | None) -> str:
    providers = available_providers(LEADS_ENABLE_DEMO_PROVIDER)
    resolved = (provider or default_provider_name(LEADS_ENABLE_DEMO_PROVIDER)).strip()
    if resolved not in providers:
        raise ProviderUnavailableError(
            f"Provider '{resolved}' is not available on this deployment"
        )
    return resolved


def search_params(request: LeadSearchParams) -> dict:
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


def search_name(request: LeadSearchParams) -> str:
    return (request.name or f"{request.category} in {request.location}").strip()[:255]


async def enqueue_search(search_id: int, organization_id: int) -> None:
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
        raise SearchQueueUnavailableError(
            "Background queue unavailable; try again shortly."
        ) from exc
