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
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from api.constants import (
    LEADS_DISPATCH_MAX_LEADS,
    LEADS_ENABLE_DEMO_PROVIDER,
    LEADS_SCRAPER_PENDING_TIMEOUT_SECONDS,
    LEADS_SCRAPER_POLL_INTERVAL_SECONDS,
    LEADS_SCRAPER_TIMEOUT_SECONDS,
    LEADS_SCRAPER_UNAVAILABLE_TIMEOUT_SECONDS,
    LEADS_SOCIAL_ENRICHMENT_ENABLED,
)
from api.db import db_client
from api.services.call_concurrency.rate_limiter import RateLimiterUnavailable
from api.services.leads.call_outcomes import call_outcome, next_crm_status
from api.services.leads.fanout import plan as plan_fanout
from api.services.leads.normalization import normalize_rows
from api.services.leads.providers import (
    LeadProviderError,
    LeadProviderUnavailable,
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
        await redis.enqueue_job(
            FunctionNames.ENRICH_LEAD_SOCIALS,
            search_id,
            _job_id=f"lead-socials:{search_id}",
        )
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


async def cancel_lead_scraper(
    ctx: dict, provider_name: str, job_id: str | None
) -> None:
    if not job_id:
        return
    try:
        async with asyncio.timeout(5):
            provider = get_provider(provider_name)
            await provider.cancel(SimpleNamespace(external_job_id=job_id))
    except Exception as exc:
        logger.warning("Scraper cleanup failed for job {}: {}", job_id, exc)


async def sweep_lead_searches(ctx: dict) -> None:
    before = datetime.now(UTC) - timedelta(seconds=LEADS_SCRAPER_TIMEOUT_SECONDS + 120)
    for search in await db_client.list_lead_searches_needing_recovery(before):
        status = (
            "stopped"
            if search.results_count or search.status == "stopping"
            else "failed"
        )
        updated = await db_client.update_lead_search(
            search.id,
            search.organization_id,
            expected_run=(search.started_at, search.external_job_id),
            expected_statuses={search.status},
            status=status,
            completed_at=datetime.now(UTC),
            error_message="Search interrupted; saved leads were kept. You can extend or retry.",
        )
        if updated is not None:
            await ctx["redis"].enqueue_job(
                FunctionNames.CANCEL_LEAD_SCRAPER,
                search.provider,
                search.external_job_id,
            )
    if LEADS_SOCIAL_ENRICHMENT_ENABLED:
        for search in await db_client.list_searches_needing_socials():
            await _enqueue_social_enrichment(ctx, search.id)


async def run_lead_search(ctx: dict, search_id: int) -> dict[str, Any]:
    search = await db_client.get_lead_search_unscoped(search_id)
    if search is None:
        return {"status": "missing"}
    if search.status not in {"queued", "running", "stopping"}:
        return {"status": search.status}

    organization_id = search.organization_id
    original_run = (search.started_at, search.external_job_id)
    if search.status == "stopping":
        updated = await db_client.update_lead_search(
            search.id,
            organization_id,
            expected_run=original_run,
            expected_statuses={"stopping"},
            status="stopped",
            completed_at=datetime.now(UTC),
        )
        if updated is not None:
            await _enqueue_social_enrichment(ctx, search.id)
        await cancel_lead_scraper(ctx, search.provider, search.external_job_id)
        return {"status": "stopped"}

    started_at = search.started_at or datetime.now(UTC)
    claimed = await db_client.update_lead_search(
        search.id,
        organization_id,
        expected_run=original_run,
        expected_statuses={search.status},
        status="running",
        started_at=started_at,
        error_message=None,
    )
    if claimed is None:
        return {"status": "superseded"}
    search.started_at = started_at
    target = _target_results(search)
    website_filter = (search.params or {}).get("website_filter", "any")
    total_results = int(search.results_count or 0)
    new_total = int(search.new_leads_count or 0)

    async def update_run(**values):
        return await db_client.update_lead_search(
            search.id,
            organization_id,
            expected_run=(search.started_at, search.external_job_id),
            expected_statuses={"running", "stopping"},
            **values,
        )

    async def sync_results(*, final: bool) -> None:
        nonlocal total_results, new_total
        raw_rows = await provider.fetch_results(search, final=final)
        leads = normalize_rows(
            raw_rows, country_hint=search.country_code, source=search.provider
        )
        if website_filter == "has_website":
            leads = [lead for lead in leads if lead.get("website")]
        elif website_filter == "no_website":
            leads = [lead for lead in leads if not lead.get("website")]
        for offset in range(0, len(leads), 50):
            counts = await db_client.upsert_leads_for_search(
                search_id=search.id,
                organization_id=organization_id,
                leads=leads[offset : offset + 50],
                expected_run=(search.started_at, search.external_job_id),
            )
            if not counts["results_count"]:
                return
            total_results = await db_client.count_search_results(search.id)
            new_total += counts["new_leads_count"]

    status = "completed"
    error = None
    pending_since = started_at if search.external_job_id else None
    unavailable_since: datetime | None = None
    try:
        provider = get_provider(
            search.provider, include_demo=LEADS_ENABLE_DEMO_PROVIDER
        )
        elapsed = max(0, (datetime.now(UTC) - started_at).total_seconds())
        async with asyncio.timeout(max(0, LEADS_SCRAPER_TIMEOUT_SECONDS - elapsed)):
            if not search.external_job_id:
                plan = (
                    await plan_fanout(search)
                    if provider.name == "google_maps"
                    else None
                )
                current = await db_client.get_lead_search_unscoped(search.id)
                if (
                    current is None
                    or current.status != "running"
                    or (current.started_at, current.external_job_id)
                    != (search.started_at, search.external_job_id)
                ):
                    return {"status": "superseded"}
                if plan is not None:
                    search.params = {**(search.params or {}), "keywords": plan.keywords}
                external_job_id = await provider.submit(search)
                pending_since = datetime.now(UTC)
                values = {"external_job_id": external_job_id}
                if plan is not None:
                    params = dict(search.params)
                    params.pop("keywords", None)
                    params["fanout_places"] = plan.used_places
                    values["params"] = params
                updated = await update_run(**values)
                search.external_job_id = external_job_id
                if updated is None:
                    return {"status": "superseded"}
                if plan is not None:
                    search.params = params

            while True:
                current = await db_client.get_lead_search_unscoped(search.id)
                if current is None:
                    return {"status": "missing"}
                if (current.started_at, current.external_job_id) != (
                    search.started_at,
                    search.external_job_id,
                ):
                    return {"status": "superseded"}
                if current.status not in {"running", "stopping"}:
                    return {"status": current.status}
                if current.status == "stopping":
                    status = "stopped"
                    break
                try:
                    await sync_results(final=False)
                    if target and total_results >= target:
                        await sync_results(final=True)
                        break
                    provider_status = await provider.poll(search)
                except LeadProviderUnavailable as exc:
                    unavailable_since = unavailable_since or datetime.now(UTC)
                    down_for = (datetime.now(UTC) - unavailable_since).total_seconds()
                    if down_for > LEADS_SCRAPER_UNAVAILABLE_TIMEOUT_SECONDS:
                        raise
                    logger.warning(
                        "Scraper unreachable for search {} ({:.0f}s): {}",
                        search.id,
                        down_for,
                        exc,
                    )
                    await asyncio.sleep(LEADS_SCRAPER_POLL_INTERVAL_SECONDS)
                    continue
                unavailable_since = None
                if provider_status.status == "failed":
                    raise LeadProviderError(
                        provider_status.error or "Discovery job failed"
                    )
                if provider_status.status == "pending":
                    pending_since = pending_since or datetime.now(UTC)
                    pending_for = (datetime.now(UTC) - pending_since).total_seconds()
                    if pending_for > LEADS_SCRAPER_PENDING_TIMEOUT_SECONDS:
                        raise LeadProviderError(
                            "The Google Maps scraper has not started this job. "
                            "It may be stuck; restart the scraper service, then retry."
                        )
                else:
                    pending_since = None
                if provider_status.status == "completed":
                    await sync_results(final=True)
                    break
                await asyncio.sleep(LEADS_SCRAPER_POLL_INTERVAL_SECONDS)
    except TimeoutError:
        status = "completed" if total_results else "failed"
        error = "Search time limit reached; saved leads were kept. Extend to search more places."
    except asyncio.CancelledError:
        try:
            async with asyncio.timeout(10):
                await update_run(
                    status="stopped" if total_results else "failed",
                    completed_at=datetime.now(UTC),
                    error_message="Search interrupted; saved leads were kept. You can extend or retry.",
                )
                await _enqueue_social_enrichment(ctx, search.id)
        except Exception:
            logger.warning("Could not finalize interrupted lead search {}", search.id)
        raise
    except Exception as exc:
        status = "failed"
        error = str(exc)[:2000]
        logger.exception("Lead search {} failed", search.id)
    finally:
        await cancel_lead_scraper(ctx, search.provider, search.external_job_id)

    updated = await update_run(
        status=status, completed_at=datetime.now(UTC), error_message=error
    )
    if updated is None:
        return {"status": "superseded"}
    await _enqueue_social_enrichment(ctx, search.id)
    if status == "completed":
        if (search.params or {}).get("auto_call") and not getattr(
            search, "auto_call_campaign_id", None
        ):
            await _enqueue_auto_calls(ctx, search.id)
        if (search.params or {}).get("add_to_campaign_id"):
            await _append_search_leads_to_campaign(search)
    logger.info(
        "Lead search {} {}: {} results, {} new leads",
        search.id,
        status,
        total_results,
        new_total,
    )
    return {
        "status": status,
        "results_count": total_results,
        "new_leads_count": new_total,
    }


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
        # The recovery sweep retries pending leads without multiplying queued jobs.
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
                    scan = SocialScan(
                        {}, checked=False, replace_links=False, error=str(exc)
                    )
                for lead in group:
                    found += await _apply_scan_result(lead, scan, organization_id)
                scanned += len(group)

    try:
        async with asyncio.timeout_at(deadline):
            await asyncio.gather(
                *(worker() for _ in range(min(SOCIAL_ENRICH_CONCURRENCY, len(groups))))
            )
    except TimeoutError:
        logger.info("Social batch for search {} reached its time budget", search_id)

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
