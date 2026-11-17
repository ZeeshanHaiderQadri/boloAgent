"""Create a normal Bolo outbound campaign from selected CRM leads."""

from __future__ import annotations

import csv
import hashlib
import io
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from loguru import logger

from api.constants import (
    DEFAULT_ORG_CONCURRENCY_LIMIT,
    LEADS_DISPATCH_MAX_LEADS,
)
from api.db import db_client
from api.db.models import UserModel
from api.enums import OrganizationConfigurationKey
from api.services.campaign.campaign_event_publisher import (
    get_campaign_event_publisher,
)
from api.services.campaign.runner import campaign_runner_service
from api.services.campaign.sources.csv import CSVSyncService
from api.services.quota_service import authorize_workflow_run_start
from api.services.storage import storage_fs
from api.services.telephony.outbound_readiness import (
    OutboundConfigurationNotFoundError,
    OutboundSetupIncompleteError,
    resolve_outbound_configuration_id,
)

MAX_LEADS_PER_DISPATCH = LEADS_DISPATCH_MAX_LEADS

BASE_HEADERS = [
    "phone_number",
    "lead_id",
    "business_name",
    "category",
    "address",
    "city",
    "state",
    "country",
    "website",
    "email",
    "lead_rating",
    "lead_review_count",
    "lead_status",
]


@dataclass
class DispatchResult:
    campaign_id: int
    campaign_name: str
    dispatched: int
    skipped: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class DispatchError(ValueError):
    pass


class DispatchQuotaError(DispatchError):
    pass


async def _required_template_variables(workflow: Any) -> set[str]:
    """Variables a workflow's prompt templates need, if we can determine them."""
    try:
        from api.services.workflow.dto import ReactFlowDTO
        from api.services.workflow.workflow_graph import WorkflowGraph

        definition = workflow.released_definition.workflow_json
        if not definition:
            return set()
        graph = WorkflowGraph(
            ReactFlowDTO(**definition),
            skip_instance_constraints_for={"trigger"},
        )
        required = graph.get_required_template_variables() or set()
        return {str(name) for name in required}
    except Exception as exc:  # template introspection must never block dispatch
        logger.debug("Could not introspect workflow template variables: {}", exc)
        return set()


async def _org_concurrency_limit(organization_id: int) -> int:
    try:
        config = await db_client.get_configuration(
            organization_id,
            OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT.value,
        )
        if config and config.value:
            return int(config.value.get("value", DEFAULT_ORG_CONCURRENCY_LIMIT))
    except Exception as exc:
        logger.debug(
            "Could not read concurrency limit for org {}: {}", organization_id, exc
        )
    return DEFAULT_ORG_CONCURRENCY_LIMIT


def _lead_row(lead: Any, headers: list[str]) -> dict[str, Any]:
    row = {header: "" for header in headers}
    row.update(
        {
            "phone_number": lead.phone_e164,
            "lead_id": lead.id,
            "business_name": lead.title or "",
            "category": lead.category or "",
            "address": lead.address or "",
            "city": lead.city or "",
            "state": lead.state or "",
            "country": lead.country or "",
            "website": lead.website or "",
            "email": lead.email or "",
            "lead_rating": lead.rating if lead.rating is not None else "",
            "lead_review_count": lead.review_count or "",
            "lead_status": lead.crm_status or "",
        }
    )
    return row


def _build_csv(leads: list[Any], extra_headers: set[str]) -> tuple[str, list[str]]:
    headers = list(BASE_HEADERS)
    for name in sorted(extra_headers):
        if name and name not in headers:
            headers.append(name)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for lead in leads:
        writer.writerow(_lead_row(lead, headers))
    return buffer.getvalue(), headers


async def _read_source_csv(file_key: str) -> list[list[str]]:
    """Download and parse an existing campaign CSV from storage."""
    signed_url = await storage_fs.aget_signed_url(
        file_key, expiration=3600, use_internal_endpoint=True
    )
    if not signed_url:
        raise DispatchError("Could not read the campaign's contact file")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(signed_url)
            response.raise_for_status()
            return list(csv.reader(io.StringIO(response.text)))
    except httpx.HTTPError as exc:
        logger.warning("Could not download campaign CSV {}: {}", file_key, exc)
        raise DispatchError("Could not read the campaign's contact file") from exc


async def add_leads_to_campaign(
    *,
    user: UserModel,
    organization_id: int,
    lead_ids: list[int],
    campaign_id: int,
) -> DispatchResult:
    """Append leads to an existing CSV-sourced outbound campaign.

    Existing campaign rows keep their source UUIDs; appended rows get the next
    CSV row indexes so call outcomes still join back to the lead. Leads already
    queued in the campaign are skipped, making repeated calls idempotent.
    """
    unique_ids = list(dict.fromkeys(lead_ids))
    if not unique_ids:
        raise DispatchError("There are no leads to add to the campaign")

    campaign = await db_client.get_campaign(campaign_id, organization_id)
    if campaign is None:
        raise DispatchError("Campaign not found")
    if campaign.source_type != "csv":
        raise DispatchError("Only CSV-sourced campaigns can receive CRM leads")
    if str(campaign.state) in {"created", "syncing"}:
        raise DispatchError(
            "Start the campaign before adding leads, or wait for its sync to finish"
        )

    workflow = await db_client.get_workflow(
        campaign.workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise DispatchError("The campaign's voice agent no longer exists")

    leads = await db_client.get_leads_by_ids(unique_ids, organization_id)
    by_id = {lead.id: lead for lead in leads}
    skipped: list[dict[str, Any]] = []
    dialable = []
    for lead_id in unique_ids:
        lead = by_id.get(lead_id)
        if lead is None:
            skipped.append({"lead_id": lead_id, "reason": "not_found"})
            continue
        if lead.crm_status == "do_not_call":
            skipped.append({"lead_id": lead_id, "reason": "do_not_call"})
            continue
        if not lead.phone_e164:
            skipped.append({"lead_id": lead_id, "reason": "missing_phone"})
            continue
        dialable.append(lead)

    already_dispatched = set(
        await db_client.list_dispatch_lead_ids(campaign_id, organization_id)
    )
    dialable = [
        lead for lead in dialable if lead.id not in already_dispatched
    ]
    if not dialable:
        raise DispatchError(
            "Every selected lead is already queued in that campaign"
        )

    csv_rows = await _read_source_csv(campaign.source_id)
    if not csv_rows:
        raise DispatchError("The campaign's contact file is empty")
    headers = list(csv_rows[0])
    existing_rows = [list(row) for row in csv_rows[1:]]
    existing_phones = {
        row[headers.index("phone_number")].strip()
        for row in existing_rows
        if "phone_number" in headers and len(row) > headers.index("phone_number")
    }

    required_variables = await _required_template_variables(workflow)
    added_headers = False
    for name in sorted(required_variables):
        if name and name not in headers:
            headers.append(name)
            added_headers = True
    if added_headers:
        existing_rows = [
            row + [""] * (len(headers) - len(row)) for row in existing_rows
        ]

    new_rows: list[dict[str, Any]] = []
    added_leads: list[Any] = []
    for lead in dialable:
        if lead.phone_e164 in existing_phones:
            skipped.append(
                {"lead_id": lead.id, "reason": "phone_already_in_campaign"}
            )
            continue
        row = _lead_row(lead, headers)
        new_rows.append(row)
        added_leads.append(lead)
        existing_phones.add(lead.phone_e164 or "")

    if not new_rows:
        raise DispatchError(
            "Every selected lead is already queued in that campaign"
        )

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in existing_rows:
        writer.writerow(dict(zip(headers, row)))
    writer.writerows(new_rows)

    uploaded = await storage_fs.acreate_file_from_bytes(
        campaign.source_id, buffer.getvalue().encode("utf-8")
    )
    if not uploaded:
        raise DispatchError("Could not update the campaign's contact file")

    file_hash = hashlib.md5(campaign.source_id.encode()).hexdigest()[:8]
    start_index = len(existing_rows) + 1
    dispatches = []
    queued_runs = []
    activities = []
    normalized_headers = CSVSyncService.normalize_headers(headers)
    for offset, (lead, row) in enumerate(zip(added_leads, new_rows)):
        source_uuid = f"csv_{file_hash}_row_{start_index + offset}"
        dispatches.append(
            {
                "organization_id": organization_id,
                "lead_id": lead.id,
                "campaign_id": campaign_id,
                "source_uuid": source_uuid,
            }
        )
        row_values = [str(row.get(header, "")) for header in headers]
        queued_runs.append(
            {
                "campaign_id": campaign_id,
                "source_uuid": source_uuid,
                "context_variables": CSVSyncService._build_context_variables(
                    normalized_headers, row_values
                ),
                "state": "queued",
            }
        )
        activities.append(
            {
                "organization_id": organization_id,
                "lead_id": lead.id,
                "user_id": user.id,
                "activity_type": "campaign",
                "title": f"Added to outbound campaign “{campaign.name}”",
                "extra_metadata": {
                    "campaign_id": campaign_id,
                    "campaign_name": campaign.name,
                    "workflow_id": campaign.workflow_id,
                    "source_uuid": source_uuid,
                },
            }
        )

    if not dispatches:
        raise DispatchError("No leads could be added to the campaign")

    await db_client.create_lead_dispatches(dispatches)
    await db_client.bulk_create_queued_runs(queued_runs)
    await db_client.add_lead_activities_bulk(activities)

    total_rows = len(existing_rows) + len(new_rows)
    values: dict[str, Any] = {
        "total_rows": total_rows,
        "source_last_synced_at": datetime.now(UTC),
    }
    was_completed = str(campaign.state) == "completed"
    if was_completed:
        values.update(state="running", completed_at=None)
    await db_client.update_campaign(campaign_id=campaign_id, **values)

    # Nudge the orchestrator so appended rows are scheduled without waiting for
    # the stale-campaign sweep.
    publisher = await get_campaign_event_publisher()
    await publisher.publish_sync_completed(
        campaign_id=campaign_id,
        total_rows=total_rows,
        source_type=campaign.source_type,
        source_id=campaign.source_id,
    )

    warnings: list[str] = []
    if skipped:
        warnings.append(
            f"{len(skipped)} lead(s) were skipped (already in the campaign, "
            "missing phone, or do-not-call)."
        )

    logger.info(
        "Appended {} leads to campaign {} (org {})",
        len(dispatches),
        campaign_id,
        organization_id,
    )
    return DispatchResult(
        campaign_id=campaign_id,
        campaign_name=campaign.name,
        dispatched=len(dispatches),
        skipped=skipped,
        warnings=warnings,
    )


async def dispatch_leads_to_campaign(
    *,
    user: UserModel,
    lead_ids: list[int],
    name: str,
    workflow_id: int,
    telephony_configuration_id: int | None,
    max_concurrency: int | None,
    rate_limit_per_second: int,
    start_immediately: bool,
    organization_id: int | None = None,
) -> DispatchResult:
    organization_id = organization_id or user.selected_organization_id
    unique_ids = list(dict.fromkeys(lead_ids))
    if not unique_ids:
        raise DispatchError("Select at least one lead")
    if len(unique_ids) > MAX_LEADS_PER_DISPATCH:
        raise DispatchError(
            f"A single dispatch can contain at most {MAX_LEADS_PER_DISPATCH} leads"
        )
    if not 1 <= rate_limit_per_second <= 50:
        raise DispatchError("Dial rate must be between 1 and 50 calls per second")

    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise DispatchError("Workflow not found")

    try:
        configuration_id = await resolve_outbound_configuration_id(
            telephony_configuration_id, organization_id, db=db_client
        )
    except OutboundSetupIncompleteError as exc:
        raise DispatchError(str(exc)) from exc
    except OutboundConfigurationNotFoundError as exc:
        raise DispatchError("telephony_configuration_not_found") from exc

    org_limit = await _org_concurrency_limit(organization_id)
    if max_concurrency is not None:
        if max_concurrency < 1:
            raise DispatchError("max_concurrency must be at least 1")
        if max_concurrency > org_limit:
            raise DispatchError(
                f"max_concurrency ({max_concurrency}) cannot exceed the "
                f"organization limit ({org_limit})"
            )

    leads = await db_client.get_leads_by_ids(unique_ids, organization_id)
    by_id = {lead.id: lead for lead in leads}
    skipped: list[dict[str, Any]] = []
    dialable = []
    for lead_id in unique_ids:
        lead = by_id.get(lead_id)
        if lead is None:
            skipped.append({"lead_id": lead_id, "reason": "not_found"})
            continue
        if lead.crm_status == "do_not_call":
            skipped.append({"lead_id": lead_id, "reason": "do_not_call"})
            continue
        if not lead.phone_e164:
            skipped.append({"lead_id": lead_id, "reason": "missing_phone"})
            continue
        dialable.append(lead)

    if not dialable:
        raise DispatchError(
            "None of the selected leads has a dialable phone number "
            "(and none are marked do-not-call-safe)"
        )

    if start_immediately:
        quota = await authorize_workflow_run_start(
            workflow_id=workflow_id,
            organization_id=organization_id,
            actor_user=user,
        )
        if not quota.has_quota:
            raise DispatchQuotaError(
                quota.error_message or "Not enough quota to start calls"
            )

    required_variables = await _required_template_variables(workflow)
    csv_content, _headers = _build_csv(dialable, required_variables)

    file_key = (
        f"campaigns/{organization_id}/{uuid.uuid4().hex}_leads_dispatch.csv"
    )
    uploaded = await storage_fs.acreate_file_from_bytes(
        file_key, csv_content.encode("utf-8")
    )
    if not uploaded:
        raise DispatchError("Could not store the generated campaign contact file")

    campaign_name = (name or f"{workflow.name} — leads")[:255]
    campaign = await db_client.create_campaign(
        name=campaign_name,
        workflow_id=workflow_id,
        traffic_split=None,
        source_type="csv",
        source_id=file_key,
        user_id=user.id,
        organization_id=organization_id,
        retry_config=None,
        max_concurrency=max_concurrency,
        schedule_config=None,
        circuit_breaker=None,
        telephony_configuration_id=configuration_id,
        rate_limit_per_second=rate_limit_per_second,
    )

    # Mirror the deterministic source_uuid the CSV sync service will generate so
    # call outcomes can be joined back to the lead (see CSVSyncService).
    file_hash = hashlib.md5(file_key.encode()).hexdigest()[:8]
    dispatches = []
    activities = []
    for index, lead in enumerate(dialable, 1):
        source_uuid = f"csv_{file_hash}_row_{index}"
        dispatches.append(
            {
                "organization_id": organization_id,
                "lead_id": lead.id,
                "campaign_id": campaign.id,
                "source_uuid": source_uuid,
            }
        )
        activities.append(
            {
                "organization_id": organization_id,
                "lead_id": lead.id,
                "user_id": user.id,
                "activity_type": "campaign",
                "title": f"Queued for outbound campaign “{campaign.name}”",
                "extra_metadata": {
                    "campaign_id": campaign.id,
                    "campaign_name": campaign.name,
                    "workflow_id": workflow_id,
                    "source_uuid": source_uuid,
                },
            }
        )
    await db_client.create_lead_dispatches(dispatches)
    await db_client.add_lead_activities_bulk(activities)

    warnings: list[str] = []
    missing_variables = required_variables - set(BASE_HEADERS)
    if missing_variables:
        warnings.append(
            "Workflow template variables not present in lead data were added as "
            "empty columns: "
            + ", ".join(sorted(missing_variables))
        )
    if skipped:
        warnings.append(
            f"{len(skipped)} lead(s) were skipped (missing phone or do-not-call)."
        )

    if start_immediately:
        try:
            await campaign_runner_service.start_campaign(campaign.id)
        except ValueError as exc:
            warnings.append(
                f"The campaign was created but could not be started: {exc}"
            )

    logger.info(
        "Dispatched {} leads to campaign {} (org {})",
        len(dialable),
        campaign.id,
        organization_id,
    )
    return DispatchResult(
        campaign_id=campaign.id,
        campaign_name=campaign.name,
        dispatched=len(dialable),
        skipped=skipped,
        warnings=warnings,
    )
