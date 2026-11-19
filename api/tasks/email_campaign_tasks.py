"""ARQ worker that delivers a launched email campaign.

The launch endpoint freezes the audience into ``email_messages`` rows and
releases them to ``queued`` before enqueuing this job. The worker claims a
batch, re-checks consent and suppression (both can change between launch and
send), delivers through the campaign's verified sender, and records the
outcome per row.

This is the only place that hands a campaign message to a provider. It never
claims ``held`` rows, and it never sends to an address that has since been
suppressed or whose lead has opted out.

Sending limits are enforced per campaign run: once the campaign's
``sent_count`` reaches the sender's daily cap, the job defers itself by a day
instead of failing the remaining rows.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from loguru import logger

from api.db import db_client
from api.enums import EmailCampaignStatus, EmailMessageStatus, EmailSenderStatus
from api.services.email.adapter import (
    EmailConfigurationError,
    EmailDeliveryError,
    OutboundEmail,
)
from api.services.email.campaign_state import (
    CampaignStateError,
    assert_campaign_transition,
)
from api.services.email.factory import build_adapter
from api.services.email.limits import effective_limits
from api.tasks.function_names import FunctionNames

BATCH_SIZE = 25
MAX_SEND_ATTEMPTS = 3
STALE_SENDING_AFTER = timedelta(minutes=15)
RETRY_BACKOFF_SECONDS = (5.0, 20.0)
RETRY_DEFER_AFTER_CAP = timedelta(days=1)


async def _fail_campaign(campaign, *, reason: str) -> None:
    """Stop a campaign that cannot continue and cancel everything pending."""
    cancelled = await db_client.cancel_pending_messages(
        campaign.organization_id, campaign.id, reason=reason
    )
    try:
        assert_campaign_transition(campaign.status, EmailCampaignStatus.FAILED.value)
    except CampaignStateError:
        # Already terminal (cancelled / failed); never rewrite that state.
        _ = cancelled
        return
    await db_client.set_campaign_launch_fields(
        campaign.organization_id,
        campaign.id,
        status=EmailCampaignStatus.FAILED.value,
        failure_reason=reason,
    )
    logger.warning(
        "Email campaign {} failed: {} ({} pending row(s) cancelled)",
        campaign.id,
        reason,
        cancelled,
    )


async def _requeue(
    ctx: dict, campaign_id: int, organization_id: int, *, delay: timedelta | None = None
) -> None:
    redis = (ctx or {}).get("redis")
    if redis is None:
        logger.error(
            "No Redis in worker context; campaign {} will not resume", campaign_id
        )
        return
    kwargs = {"_defer_by": delay} if delay else {}
    await redis.enqueue_job(
        FunctionNames.SEND_EMAIL_CAMPAIGN,
        campaign_id,
        organization_id,
        **kwargs,
    )


async def _deliver(
    adapter, message, *, delay_seconds: float
) -> tuple[bool, str | None]:
    """Send one message, retrying transient failures a bounded number of times."""
    attempt = 1
    while True:
        try:
            result = await adapter.send(
                OutboundEmail(
                    to_email=message.to_email,
                    to_name=message.to_name,
                    subject=message.subject,
                    html_body=message.html_body,
                    text_body=message.text_body,
                    headers=message.headers,
                )
            )
            return True, result.provider_message_id
        except EmailConfigurationError as exc:
            # Retrying cannot fix credentials or a rejected sender.
            return False, f"Configuration error: {exc}"
        except EmailDeliveryError as exc:
            if attempt >= MAX_SEND_ATTEMPTS:
                return False, str(exc)
            backoff = RETRY_BACKOFF_SECONDS[
                min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)
            ]
            if exc.retry_after_seconds:
                backoff = max(backoff, exc.retry_after_seconds)
            logger.info(
                "Retrying message {} after transient error (attempt {}): {}",
                message.message_uuid,
                attempt,
                exc,
            )
            await asyncio.sleep(backoff + delay_seconds)
            attempt += 1
        except Exception as exc:  # an adapter bug must not kill the whole batch
            logger.exception(
                "Unexpected adapter error for message {}", message.message_uuid
            )
            return False, f"Unexpected error: {exc}"


async def send_email_campaign(
    ctx: dict, campaign_id: int, organization_id: int
) -> None:
    """Deliver every due recipient row for one campaign.

    ARQ passes ``organization_id`` too so the worker reloads the campaign with
    the same scope the launch endpoint used, never trusting the id alone.
    """
    campaign = await db_client.get_email_campaign_by_id(organization_id, campaign_id)
    if campaign is None:
        logger.warning(
            "Email campaign job for campaign {} in organization {} found no row",
            campaign_id,
            organization_id,
        )
        return
    if campaign.status not in {
        EmailCampaignStatus.LAUNCHING.value,
        EmailCampaignStatus.SENDING.value,
    }:
        # Cancelled before the worker started, or a duplicate job.
        return

    sender = (
        await db_client.get_email_sender_by_id(organization_id, campaign.sender_id)
        if campaign.sender_id
        else None
    )
    if sender is None or sender.status != EmailSenderStatus.VERIFIED.value:
        await _fail_campaign(
            campaign,
            reason="The campaign's sender is missing or no longer verified; "
            "nothing further was sent.",
        )
        return

    limits = effective_limits(sender)

    stale = await db_client.fail_stale_sending(
        campaign.id, older_than=datetime.now(UTC) - STALE_SENDING_AFTER
    )
    if stale:
        await db_client.add_campaign_counters(
            organization_id, campaign.id, failed=stale
        )

    if campaign.status == EmailCampaignStatus.LAUNCHING.value:
        await db_client.set_campaign_launch_fields(
            organization_id,
            campaign.id,
            status=EmailCampaignStatus.SENDING.value,
        )

    delay_seconds = 1.0 / max(limits.rate_per_second, 0.1)

    while True:
        campaign = await db_client.get_email_campaign_by_id(
            organization_id, campaign.id
        )
        if campaign is None or campaign.status not in {
            EmailCampaignStatus.LAUNCHING.value,
            EmailCampaignStatus.SENDING.value,
        }:
            return
        if campaign.sent_count >= limits.daily_cap:
            logger.info(
                "Campaign {} reached its daily cap of {}; resuming tomorrow",
                campaign.id,
                limits.daily_cap,
            )
            await _requeue(
                ctx, campaign.id, organization_id, delay=RETRY_DEFER_AFTER_CAP
            )
            return

        batch = await db_client.claim_queued_messages(campaign.id, limit=BATCH_SIZE)
        if not batch:
            break

        try:
            credentials = await db_client.get_sender_credential_data(
                organization_id, sender
            )
            adapter = await asyncio.to_thread(build_adapter, sender, credentials)
        except EmailConfigurationError as exc:
            await _fail_campaign(campaign, reason=f"The sender cannot be used: {exc}")
            return
        except Exception:
            logger.exception(
                "Could not load credentials for sender {}", sender.sender_uuid
            )
            await _fail_campaign(
                campaign,
                reason="The sender's credential could not be read; nothing "
                "further was sent.",
            )
            return

        suppressed = await db_client.suppressed_audience_addresses(organization_id)
        consents = await db_client.lead_consent_map(
            organization_id,
            [message.lead_id for message in batch if message.lead_id is not None],
        )

        sent = failed = 0
        for index, message in enumerate(batch):
            # Pause/stop must take effect between two messages, not after the
            # whole claimed batch. Rows already claimed are ``sending``: a
            # pause returns them to ``queued`` so resume delivers them, while
            # a stop cancels them.
            fresh = await db_client.get_email_campaign_by_id(
                organization_id, campaign.id
            )
            if fresh is None or fresh.status not in {
                EmailCampaignStatus.LAUNCHING.value,
                EmailCampaignStatus.SENDING.value,
            }:
                remaining = [item.id for item in batch[index:]]
                if (
                    fresh is not None
                    and fresh.status == EmailCampaignStatus.PAUSED.value
                ):
                    await db_client.requeue_claimed_messages(organization_id, remaining)
                    logger.info(
                        "Campaign {} paused; {} claimed row(s) returned to the queue",
                        campaign.id,
                        len(remaining),
                    )
                else:
                    await db_client.cancel_messages_by_ids(
                        organization_id,
                        remaining,
                        reason="Cancelled: the campaign was stopped before this send.",
                    )
                if sent or failed:
                    await db_client.add_campaign_counters(
                        organization_id, campaign.id, sent=sent, failed=failed
                    )
                return

            if (
                message.to_email in suppressed
                or consents.get(message.lead_id) == "opted_out"
            ):
                await db_client.mark_message_cancelled(
                    message.id,
                    "Skipped: the address was unsubscribed before this send.",
                )
                continue

            ok, detail = await _deliver(adapter, message, delay_seconds=delay_seconds)
            if ok:
                await db_client.mark_message_sent(message.id, detail)
                sent += 1
            else:
                await db_client.mark_message_failed(
                    message.id, detail or "Send failed."
                )
                failed += 1
                logger.warning(
                    "Message {} failed permanently: {}", message.message_uuid, detail
                )

            await asyncio.sleep(delay_seconds)

        if sent or failed:
            await db_client.add_campaign_counters(
                organization_id, campaign.id, sent=sent, failed=failed
            )

    counts = await db_client.count_campaign_messages(organization_id, campaign.id)
    if counts.get(EmailMessageStatus.QUEUED.value) or counts.get(
        EmailMessageStatus.SENDING.value
    ):
        # Rows were scheduled for later, or another worker is mid-batch.
        await _requeue(ctx, campaign.id, organization_id, delay=timedelta(seconds=60))
        return

    completed = await db_client.mark_campaign_completed(organization_id, campaign.id)
    if completed:
        logger.info("Email campaign {} completed", campaign.id)
