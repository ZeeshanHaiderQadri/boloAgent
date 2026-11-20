"""Background execution of a lead discovery search.

The scraper writes its CSV incrementally, so the worker syncs whatever is
available on every poll. Leads therefore show up in the dashboard while the
search runs, and the job can finish early when the operator's target is
reached or they press Stop. Ingesting in batches is safe: the upsert is
idempotent and partial trailing rows are dropped by the parser.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from api.constants import (
    LEADS_DISPATCH_MAX_LEADS,
    LEADS_ENABLE_DEMO_PROVIDER,
    LEADS_SCRAPER_POLL_INTERVAL_SECONDS,
    LEADS_SCRAPER_TIMEOUT_SECONDS,
    LEADS_SOCIAL_ENRICHMENT_ENABLED,
)
from api.db import db_client
from api.services.call_concurrency.rate_limiter import RateLimiterUnavailable
from api.services.leads.call_outcomes import call_outcome, next_crm_status
from api.services.leads.normalization import normalize_rows
from api.services.leads.providers import (
    LeadProviderError,
    ProviderJobStatus,
    get_provider,
)
from api.services.leads.social_scan_limits import (
    acquire_enrichment_job_lease,
    release_enrichment_job_lease,
)
from api.services.leads.socials import (
    SocialScan,
    discover_social_links,
    normalize_website,
)
from api.tasks.function_names import FunctionNames

SOCIAL_ENRICH_BATCH_SIZE = 25
SOCIAL_ENRICH_CONCURRENCY = 4
SOCIAL_ENRICH_MAX_RUNS = 40
# A batch may scan for at most this long. Leads not reached stay pending, so
# the next short job picks them up instead of holding a worker for minutes.
SOCIAL_ENRICH_BATCH_BUDGET_SECONDS = 100.0
SOCIAL_ENRICH_MAX_ATTEMPTS = 5
SOCIAL_ENRICH_BASE_BACKOFF_SECONDS = 3600
SOCIAL_ENRICH_MAX_BACKOFF_SECONDS = 86400
SOCIAL_ENRICH_CHAIN_DELAY_SECONDS = 5
SOCIAL_ENRICH_DEFER_SECONDS = 30


async def _set_status(search_id: int, organization_id: int, **values: Any) -> None:
    await db_client.update_lead_search(search_id, organization_id, **values)


def _site_key(website: str | None) -> str:
    """Canonical key so trailing-slash and host-case variants scan once."""
    normalized = normalize_website(website)
    if not normalized:
        return (website or "").strip()
    parsed = urlparse(normalized)
    return f"{parsed.scheme.lower()}://{parsed.hostname or ''}{parsed.path.rstrip('/')}"


def _target_results(search: Any) -> int | None:
    target = getattr(search, "target_results", None)
    if target is None:
        target = (search.params or {}).get("target_results")
    try:
        parsed = int(target) if target is not None else 0
    except (TypeError, ValueError):
        return None
    return parsed or None


async def _enqueue_auto_calls(ctx: dict, search_id: int) -> None:
    redis = (ctx or {}).get("redis")
    if redis is None:
        return
    try:
        await redis.enqueue_job(FunctionNames.RUN_LEAD_AUTO_DISPATCH, search_id)
    except Exception as exc:  # queue outage must not fail a finished search
        logger.warning("Could not queue auto calls for search {}: {}", search_id, exc)


async def _enqueue_social_enrichment(ctx: dict, search_id: int) -> None:
    if not LEADS_SOCIAL_ENRICHMENT_ENABLED:
        return
    redis = (ctx or {}).get("redis")
    if redis is None:
        return
    try:
        await redis.enqueue_job(FunctionNames.ENRICH_LEAD_SOCIALS, search_id)
    except Exception as exc:  # enrichment is cosmetic; never fail the search
        logger.warning(
            "Could not queue social enrichment for search {}: {}", search_id, exc
        )


async def _append_search_leads_to_campaign(search: Any) -> None:
    """Add every lead collected by a rerun to the selected existing campaign.

    The edit dialog can add the already-collected leads immediately; this final
    pass picks up businesses discovered by the new run. Repeats are harmless
    because the dispatch layer skips leads already queued in the campaign.
    """
    try:
        campaign_id = int((search.params or {}).get("add_to_campaign_id"))
    except (TypeError, ValueError):
        return

    user = (
        await db_client.get_user_by_id(search.created_by) if search.created_by else None
    )
    if user is None:
        logger.warning(
            "Cannot add search {} leads to campaign {}: creator is missing",
            search.id,
            campaign_id,
        )
        return

    leads, _total = await db_client.list_leads(
        search.organization_id,
        search_id=search.id,
        has_phone=True,
        limit=LEADS_DISPATCH_MAX_LEADS,
    )
    lead_ids = [lead.id for lead in leads if lead.crm_status != "do_not_call"]
    if not lead_ids:
        return

    # Imported lazily to avoid a module cycle with the campaign runner.
    from api.services.leads.dispatch import add_leads_to_campaign

    try:
        result = await add_leads_to_campaign(
            user=user,
            organization_id=search.organization_id,
            lead_ids=lead_ids,
            campaign_id=campaign_id,
        )
        logger.info(
            "Lead search {} appended {} leads to campaign {}",
            search.id,
            result.dispatched,
            campaign_id,
        )
    except Exception as exc:
        logger.warning(
            "Could not append leads from search {} to campaign {}: {}",
            search.id,
            campaign_id,
            exc,
        )


async def run_lead_search(ctx: dict, search_id: int) -> dict[str, Any]:
    search = await db_client.get_lead_search_unscoped(search_id)
    if search is None:
        logger.warning("Lead search {} no longer exists", search_id)
        return {"status": "missing"}

    if search.status in {"completed", "stopped", "cancelled"}:
        return {"status": search.status}

    organization_id = search.organization_id

    if search.status == "stopping":
        await _set_status(
            search.id,
            organization_id,
            status="stopped",
            completed_at=datetime.now(UTC),
        )
        await _enqueue_social_enrichment(ctx, search.id)
        return {"status": "stopped"}

    search.started_at = search.started_at or datetime.now(UTC)
    await _set_status(
        search.id,
        organization_id,
        status="running",
        started_at=search.started_at,
        error_message=None,
    )

    try:
        provider = get_provider(
            search.provider, include_demo=LEADS_ENABLE_DEMO_PROVIDER
        )
    except LeadProviderError as exc:
        await _set_status(
            search.id,
            organization_id,
            status="failed",
            completed_at=datetime.now(UTC),
            error_message=str(exc),
        )
        return {"status": "failed", "error": str(exc)}

    target = _target_results(search)
    website_filter = (search.params or {}).get("website_filter", "any")
    total_results = await db_client.count_search_results(search.id)
    new_total = int(search.new_leads_count or 0)

    async def sync_results(*, final: bool) -> int:
        """Ingest the rows written to the scraper CSV so far."""
        nonlocal total_results, new_total
        raw_rows = await provider.fetch_results(search, final=final)
        leads = normalize_rows(
            raw_rows,
            country_hint=search.country_code,
            source=search.provider,
        )
        if website_filter == "has_website":
            leads = [lead for lead in leads if lead.get("website")]
        elif website_filter == "no_website":
            leads = [lead for lead in leads if not lead.get("website")]
        if not leads:
            return total_results
        counts = await db_client.upsert_leads_for_search(
            search_id=search.id,
            organization_id=organization_id,
            leads=leads,
        )
        total_results = await db_client.count_search_results(search.id)
        new_total += counts["new_leads_count"]
        await _set_status(
            search.id,
            organization_id,
            results_count=total_results,
            new_leads_count=new_total,
        )
        return total_results

    try:
        if not search.external_job_id:
            external_job_id = await provider.submit(search)
            await _set_status(
                search.id, organization_id, external_job_id=external_job_id
            )
            search.external_job_id = external_job_id

        deadline_seconds = LEADS_SCRAPER_TIMEOUT_SECONDS
        waited = 0.0
        final_status: ProviderJobStatus | None = None
        stopped = False
        # Cancelling a scraper job deletes its CSV, so the final ingest has to
        # happen first. Track the cases where a cancel is still owed.
        cancel_after_sync = False

        while True:
            current = await db_client.get_lead_search_unscoped(search.id)
            if current is None:
                return {"status": "missing"}
            if current.status == "cancelled":
                await provider.cancel(search)
                logger.info("Lead search {} cancelled", search.id)
                return {"status": "cancelled"}
            if current.status == "stopping":
                stopped = True
                # Ingest first: cancelling the scraper job also removes its
                # CSV, and the operator wants the businesses found so far.
                try:
                    await sync_results(final=False)
                except Exception as exc:
                    logger.warning(
                        "Could not sync results while stopping search {}: {}",
                        search.id,
                        exc,
                    )
                await provider.cancel(search)
                final_status = ProviderJobStatus(
                    status="stopped", result_count=total_results
                )
                break

            total_results = await sync_results(final=False)

            if target and total_results >= target:
                # The operator asked for this many leads; no need to keep the
                # scraper running for the rest of its budget. The cancel below
                # happens only after the final ingest, because it deletes the
                # job's CSV.
                cancel_after_sync = True
                final_status = ProviderJobStatus(
                    status="completed", result_count=total_results
                )
                logger.info(
                    "Lead search {} reached its target of {} leads",
                    search.id,
                    target,
                )
                break

            final_status = await provider.poll(search)
            if final_status.status in {"completed", "failed"}:
                break

            if waited >= deadline_seconds:
                raise LeadProviderError(
                    "The discovery job did not finish within "
                    f"{deadline_seconds} seconds"
                )

            await asyncio.sleep(LEADS_SCRAPER_POLL_INTERVAL_SECONDS)
            waited += LEADS_SCRAPER_POLL_INTERVAL_SECONDS

        if final_status is None or final_status.status == "failed":
            raise LeadProviderError(
                final_status.error if final_status else "Discovery job failed"
            )

        if stopped:
            status = "stopped"
        else:
            # A finished job has a complete file; ingest the tail exactly once.
            # This must precede the cancel for a target-triggered stop.
            total_results = await sync_results(final=True)
            status = "completed"

        if cancel_after_sync:
            await provider.cancel(search)

        await _set_status(
            search.id,
            organization_id,
            status=status,
            results_count=total_results,
            new_leads_count=new_total,
            completed_at=datetime.now(UTC),
            error_message=None,
        )
        logger.info(
            "Lead search {} {}: {} results, {} new leads",
            search.id,
            status,
            total_results,
            new_total,
        )

        await _enqueue_social_enrichment(ctx, search.id)

        if (search.params or {}).get("auto_call") and not getattr(
            search, "auto_call_campaign_id", None
        ):
            await _enqueue_auto_calls(ctx, search.id)

        if (search.params or {}).get("add_to_campaign_id"):
            await _append_search_leads_to_campaign(search)

        return {
            "status": status,
            "results_count": total_results,
            "new_leads_count": new_total,
        }

    except asyncio.CancelledError:
        await provider.cancel(search)
        raise
    except Exception as exc:
        logger.exception("Lead search {} failed", search.id)
        await _set_status(
            search.id,
            organization_id,
            status="failed",
            results_count=total_results,
            new_leads_count=new_total,
            completed_at=datetime.now(UTC),
            error_message=str(exc)[:2000],
        )
        return {"status": "failed", "error": str(exc)}


async def run_lead_auto_dispatch(ctx: dict, search_id: int) -> dict[str, Any]:
    """Create a normal outbound campaign for a search that just finished."""
    search = await db_client.get_lead_search_unscoped(search_id)
    if search is None:
        return {"status": "missing"}
    if search.status not in {"completed", "stopped"}:
        return {"status": "skipped", "reason": f"search {search.status}"}

    params = search.params or {}
    if not params.get("auto_call") or search.auto_call_campaign_id:
        return {"status": "skipped", "reason": "auto call not requested"}
    workflow_id = params.get("workflow_id")
    if not workflow_id:
        await _set_status(
            search.id,
            search.organization_id,
            auto_call_error="No voice agent was selected for automatic calling.",
        )
        return {"status": "failed", "error": "missing workflow"}

    user = (
        await db_client.get_user_by_id(search.created_by) if search.created_by else None
    )
    if user is None:
        await _set_status(
            search.id,
            search.organization_id,
            auto_call_error="The user who started this search no longer exists.",
        )
        return {"status": "failed", "error": "missing user"}

    leads, _total = await db_client.list_leads(
        search.organization_id,
        search_id=search.id,
        has_phone=True,
        limit=LEADS_DISPATCH_MAX_LEADS,
    )
    lead_ids = [lead.id for lead in leads if lead.crm_status != "do_not_call"]
    if not lead_ids:
        await _set_status(
            search.id,
            search.organization_id,
            auto_call_error=(
                "No dialable phone numbers were found, so no calls were started."
            ),
        )
        return {"status": "skipped", "reason": "no dialable leads"}

    # Imported lazily: dispatch pulls in the campaign runner, which itself
    # imports worker tasks. A module-level import would create a cycle.
    from api.services.leads.dispatch import dispatch_leads_to_campaign

    try:
        result = await dispatch_leads_to_campaign(
            user=user,
            organization_id=search.organization_id,
            lead_ids=lead_ids,
            name=f"{search.name} — calls"[:255],
            workflow_id=int(workflow_id),
            telephony_configuration_id=params.get("telephony_configuration_id"),
            max_concurrency=None,
            rate_limit_per_second=1,
            start_immediately=True,
        )
    except Exception as exc:
        logger.warning("Auto call campaign for search {} failed: {}", search.id, exc)
        await _set_status(
            search.id,
            search.organization_id,
            auto_call_error=str(exc)[:1000],
        )
        return {"status": "failed", "error": str(exc)}

    await _set_status(
        search.id,
        search.organization_id,
        auto_call_campaign_id=result.campaign_id,
        auto_call_error=None,
    )
    logger.info(
        "Lead search {} auto-dispatched {} leads to campaign {}",
        search.id,
        result.dispatched,
        result.campaign_id,
    )
    return {
        "status": "completed",
        "campaign_id": result.campaign_id,
        "dispatched": result.dispatched,
    }


async def sync_lead_call_outcomes(ctx: dict) -> dict[str, Any]:
    """Copy completed outbound call results into the lead CRM timeline.

    Runs on a cron. Each workflow run is recorded at most once (the DB query
    excludes runs that already have a ``call`` activity), so retries are safe.
    """
    rows = await db_client.list_pending_lead_call_outcomes(limit=200)
    if not rows:
        return {"status": "ok", "recorded": 0}

    activities: list[dict[str, Any]] = []
    lead_targets: dict[tuple[int, int], str] = {}
    now = datetime.now(UTC)

    for row in rows:
        outcome = call_outcome(row)
        target = next_crm_status(outcome["mapped_call_disposition"], row["crm_status"])
        key = (row["organization_id"], row["lead_id"])
        # Rows are newest first: keep the latest outcome, but a do-not-call
        # request must win regardless of ordering.
        if key not in lead_targets or target == "do_not_call":
            lead_targets[key] = target

        label = (
            outcome["mapped_call_disposition"] or outcome["call_status"] or row["state"]
        )
        activities.append(
            {
                "organization_id": row["organization_id"],
                "lead_id": row["lead_id"],
                "user_id": None,
                "activity_type": "call",
                "title": f"Call completed · {label}"[:255],
                "body": outcome["summary"],
                "extra_metadata": {
                    "workflow_run_id": row["workflow_run_id"],
                    "campaign_id": row["campaign_id"],
                    "mapped_call_disposition": outcome["mapped_call_disposition"],
                    "call_status": outcome["call_status"],
                    "call_tags": outcome["call_tags"],
                },
            }
        )

    await db_client.add_lead_activities_bulk(activities)

    grouped: dict[tuple[int, str], list[int]] = {}
    for (organization_id, lead_id), status in lead_targets.items():
        grouped.setdefault((organization_id, status), []).append(lead_id)
    for (organization_id, status), lead_ids in grouped.items():
        await db_client.bulk_update_leads(
            organization_id,
            lead_ids,
            crm_status=status,
            last_contacted_at=now,
        )

    logger.info("Recorded {} lead call outcome(s)", len(activities))
    return {"status": "ok", "recorded": len(activities)}


async def enrich_lead_socials(
    ctx: dict, search_id: int, run: int = 0
) -> dict[str, Any]:
    """Scan each lead's own website for its social profiles.

    One global lease means the fleet runs at most one enrichment job at a
    time, leaving ARQ slots for email campaigns, webhooks and call sync. A
    transient failure leaves the old links in place and schedules a backed-off
    retry; after ``SOCIAL_ENRICH_MAX_ATTEMPTS`` the lead stops retrying but
    keeps whatever was found before.
    """
    search = await db_client.get_lead_search_unscoped(search_id)
    if search is None:
        return {"status": "missing"}
    if search.provider == "demo":
        # Demo websites are generated placeholders, not real sites.
        return {"status": "skipped", "reason": "demo provider"}
    if not LEADS_SOCIAL_ENRICHMENT_ENABLED:
        return {"status": "disabled"}

    redis = (ctx or {}).get("redis")
    if redis is None:
        return {"status": "skipped", "reason": "queue unavailable"}

    try:
        lease = await acquire_enrichment_job_lease(redis)
    except RateLimiterUnavailable:
        lease = None
    if lease is None:
        # Another enrichment job holds the fleet-wide slot. Try again later
        # rather than scanning alongside it.
        try:
            await redis.enqueue_job(
                FunctionNames.ENRICH_LEAD_SOCIALS,
                search_id,
                run,
                _defer_by=timedelta(seconds=SOCIAL_ENRICH_DEFER_SECONDS),
            )
        except Exception as exc:
            logger.warning(
                "Could not defer social enrichment for search {}: {}",
                search_id,
                exc,
            )
        return {"status": "deferred"}

    try:
        return await _scan_pending_socials(
            ctx, search.organization_id, search.id, run=run
        )
    finally:
        await release_enrichment_job_lease(redis, lease)


async def _apply_scan_result(lead: Any, scan: SocialScan, organization_id: int) -> int:
    """Persist one scan without ever destroying a previously good result."""
    now = datetime.now(UTC)
    if scan.checked:
        if scan.replace_links:
            await db_client.save_lead_socials(
                lead.id,
                organization_id,
                social_links=scan.links,
                checked_at=now,
            )
            return len(scan.links)
        await db_client.mark_lead_socials_checked(
            lead.id, organization_id, checked_at=now
        )
        return 0

    attempts = (lead.socials_attempts or 0) + 1
    if attempts >= SOCIAL_ENRICH_MAX_ATTEMPTS:
        await db_client.record_lead_socials_failure(
            lead.id,
            organization_id,
            next_attempt_at=None,
            terminal_at=now,
        )
    else:
        delay = min(
            SOCIAL_ENRICH_BASE_BACKOFF_SECONDS * (2 ** (attempts - 1)),
            SOCIAL_ENRICH_MAX_BACKOFF_SECONDS,
        )
        await db_client.record_lead_socials_failure(
            lead.id,
            organization_id,
            next_attempt_at=now + timedelta(seconds=delay),
        )
    return 0


async def _scan_pending_socials(
    ctx: dict, organization_id: int, search_id: int, *, run: int
) -> dict[str, Any]:
    leads = await db_client.list_leads_needing_socials(
        organization_id, search_id=search_id, limit=SOCIAL_ENRICH_BATCH_SIZE
    )
    if not leads:
        return {"status": "completed", "scanned": 0, "found": 0, "remaining": 0}

    # Leads that share the exact same website are scanned once; different
    # URLs on the same host are serialised so we never hammer one site.
    groups: dict[str, list[Any]] = {}
    for lead in leads:
        groups.setdefault(_site_key(lead.website), []).append(lead)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + SOCIAL_ENRICH_BATCH_BUDGET_SECONDS
    queue: asyncio.Queue = asyncio.Queue()
    for item in groups.items():
        queue.put_nowait(item)
    semaphore = asyncio.Semaphore(SOCIAL_ENRICH_CONCURRENCY)
    host_locks: dict[str, asyncio.Lock] = {}
    scanned = 0
    found = 0

    async def worker() -> None:
        nonlocal scanned, found
        while loop.time() < deadline:
            try:
                url, group = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            host = (urlparse(url).hostname or url).lower()
            lock = host_locks.setdefault(host, asyncio.Lock())
            async with semaphore, lock:
                if loop.time() >= deadline:
                    return
                try:
                    scan = await discover_social_links(
                        url, business_name=group[0].title
                    )
                except Exception as exc:  # one hostile page must not fail the batch
                    logger.warning("Social scan failed for {}: {}", url, exc)
                    continue
                for lead in group:
                    found += await _apply_scan_result(lead, scan, organization_id)
                scanned += len(group)

    await asyncio.gather(
        *(worker() for _ in range(min(SOCIAL_ENRICH_CONCURRENCY, len(groups))))
    )

    remaining = await db_client.count_leads_needing_socials(
        organization_id, search_id=search_id
    )
    if remaining and run + 1 < SOCIAL_ENRICH_MAX_RUNS:
        redis = (ctx or {}).get("redis")
        if redis is not None:
            try:
                await redis.enqueue_job(
                    FunctionNames.ENRICH_LEAD_SOCIALS,
                    search_id,
                    run + 1,
                    _defer_by=timedelta(seconds=SOCIAL_ENRICH_CHAIN_DELAY_SECONDS),
                )
            except Exception as exc:
                logger.warning(
                    "Could not continue social enrichment for search {}: {}",
                    search_id,
                    exc,
                )

    logger.info(
        "Social enrichment for search {}: scanned {}, found {}, remaining {}",
        search_id,
        scanned,
        found,
        remaining,
    )
    return {
        "status": "completed",
        "scanned": scanned,
        "found": found,
        "remaining": remaining,
    }
