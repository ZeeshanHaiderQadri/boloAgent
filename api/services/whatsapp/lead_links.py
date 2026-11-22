"""Build click-to-chat links for one lead from the organization's templates.

Shared by the REST route and the workspace copilot so both apply the same
disclaimer, phone-resolution and template rules. Nothing here sends a message:
a link only pre-fills the operator's own WhatsApp client.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.db import db_client
from api.services.whatsapp.countries import resolve_lead_phone
from api.services.whatsapp.disclaimer import WHATSAPP_DISCLAIMER_VERSION
from api.services.whatsapp.templates import (
    WhatsAppTemplateError,
    build_wa_me_url,
    lead_values,
    render_body,
)


class LeadLinksLeadNotFoundError(Exception):
    """The lead does not exist in this organization."""


class LeadLinksDisclaimerRequiredError(Exception):
    """The operator has not accepted the current WhatsApp disclaimer."""


@dataclass(frozen=True)
class LeadLink:
    template_uuid: str
    name: str
    language: str
    body: str
    url: str


@dataclass(frozen=True)
class LeadLinks:
    available: bool
    reason: str | None
    phone_e164: str | None
    country: str | None
    links: list[LeadLink]


async def build_lead_links(
    *, organization_id: int, user_id: int, lead_id: int
) -> LeadLinks:
    lead = await db_client.get_lead(lead_id, organization_id)
    if lead is None:
        raise LeadLinksLeadNotFoundError

    acceptance = await db_client.get_whatsapp_disclaimer(organization_id, user_id)
    if acceptance is None or acceptance.text_version != WHATSAPP_DISCLAIMER_VERSION:
        raise LeadLinksDisclaimerRequiredError

    resolution = resolve_lead_phone(lead.phone, lead.phone_e164, lead.country)
    if resolution.failure == "country_unknown":
        # The lead's country was the first hint and it was missing, so try the
        # default sender's country. It is never used to override a lead country.
        sender = await db_client.get_default_whatsapp_sender(organization_id)
        if sender is not None:
            resolution = resolve_lead_phone(
                lead.phone, lead.phone_e164, lead.country, sender.country
            )
    if not resolution.phone_e164:
        reason = {
            "missing": "no_phone",
            "country_unknown": "country_unknown",
        }.get(resolution.failure or "", "unrecognized_number")
        return LeadLinks(False, reason, None, None, [])

    templates = await db_client.list_whatsapp_templates(organization_id)
    values = lead_values(
        business_name=lead.title, category=lead.category, city=lead.city
    )
    links: list[LeadLink] = []
    for template in templates:
        rendered = render_body(template.body, values)
        try:
            url = build_wa_me_url(resolution.phone_e164, rendered)
        except WhatsAppTemplateError:
            continue
        links.append(
            LeadLink(
                template_uuid=template.template_uuid,
                name=template.name,
                language=template.language,
                body=rendered,
                url=url,
            )
        )
    return LeadLinks(
        available=True,
        reason=None,
        phone_e164=resolution.phone_e164,
        country=resolution.country,
        links=links,
    )
