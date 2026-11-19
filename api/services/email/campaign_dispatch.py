"""Freeze a campaign audience into recipient rows and release them to the queue.

Called by the launch endpoint after every precondition passed. Renders one
message per winning address with that recipient's merge values and a signed
unsubscribe link, stores the rows as ``held``, then releases them to
``queued`` for the send worker. Rows are the immutable send record.
"""

from __future__ import annotations

from datetime import UTC, datetime

from api import constants
from api.db import db_client
from api.db.models import EmailCampaignModel, EmailSenderModel, EmailTemplateModel
from api.services.email.audience import (
    AudienceRecipient,
    AudienceRule,
    resolve_audience_recipients,
)
from api.services.email.templates import render_html, render_subject, render_text
from api.services.email.unsubscribe import (
    build_unsubscribe_token,
    unsubscribe_headers,
    unsubscribe_url,
)


def _message_rows(
    *,
    organization_id: int,
    campaign: EmailCampaignModel,
    recipients: list[AudienceRecipient],
    subject_template: str,
    html_template: str,
    text_template: str | None,
) -> list[dict]:
    base_url = (constants.PUBLIC_BASE_URL or "").rstrip("/")
    rows: list[dict] = []
    for recipient in recipients:
        token = build_unsubscribe_token(
            organization_id=organization_id,
            email=recipient.email,
            campaign_id=campaign.id,
        )
        link = unsubscribe_url(base_url, token)
        values = {**recipient.values, "unsubscribe_link": link}
        rows.append(
            {
                "sender_id": campaign.sender_id,
                "template_id": campaign.template_id,
                "lead_id": recipient.lead_id,
                "to_email": recipient.email,
                "to_name": recipient.values.get("business_name") or None,
                "subject": render_subject(subject_template, values),
                "html_body": render_html(html_template, values),
                "text_body": render_text(text_template, values) if text_template else None,
                "headers": unsubscribe_headers(link),
            }
        )
    return rows


async def freeze_and_queue_campaign(
    *,
    organization_id: int,
    campaign: EmailCampaignModel,
    sender: EmailSenderModel,
    template: EmailTemplateModel,
    rule: AudienceRule,
    user_id: int,
) -> int:
    """Persist the audience snapshot and queue it. Returns rows queued."""
    resolution, recipients = await resolve_audience_recipients(
        organization_id, rule, consent_policy=campaign.consent_policy
    )

    now = datetime.now(UTC)
    subject_template = campaign.frozen_subject or template.subject
    html_template = campaign.frozen_html_body or template.html_body
    text_template = campaign.frozen_text_body or template.text_body
    rows = _message_rows(
        organization_id=organization_id,
        campaign=campaign,
        recipients=recipients,
        subject_template=subject_template,
        html_template=html_template,
        text_template=text_template,
    )

    await db_client.set_campaign_launch_fields(
        organization_id,
        campaign.id,
        frozen_subject=subject_template,
        frozen_html_body=html_template,
        frozen_text_body=text_template,
        frozen_has_unsubscribe=template.has_unsubscribe_placeholder,
        frozen_at=now,
        sender_from_name=sender.from_name,
        sender_from_email=sender.from_email,
        sender_reply_to_email=sender.reply_to_email,
        launched_by=user_id,
        launched_at=now,
        failure_reason=None,
        **resolution.counters(),
    )
    await db_client.create_held_messages(organization_id, campaign.id, rows)
    return await db_client.release_held_messages(organization_id, campaign.id)
