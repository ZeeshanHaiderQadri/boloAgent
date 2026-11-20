"""API routes for WhatsApp click-to-chat templates and senders.

Nothing in this module sends a message. Templates are plain text the operator
opens in their own WhatsApp client through a ``wa.me`` link; senders are the
operator's own saved number, kept for reference and as the country hint for
raw phone numbers that have no country code.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.db import db_client
from api.db.models import UserModel, WhatsAppSenderModel, WhatsAppTemplateModel
from api.db.whatsapp_client import WhatsAppConflictError
from api.services.auth.depends import get_user
from api.services.leads.normalization import normalize_lead_phone
from api.services.whatsapp.templates import (
    MAX_TEMPLATE_NAME_CHARS,
    WhatsAppTemplateError,
    build_wa_me_url,
    lead_values,
    normalize_category,
    normalize_language,
    render_body,
    validate_template_body,
)

router = APIRouter(prefix="/whatsapp")


# ----------------------------------------------------------------------
# Request / response schemas
# ----------------------------------------------------------------------


class WhatsAppTemplateSummary(BaseModel):
    uuid: str
    name: str
    language: str
    category: str
    body: str
    variables: list[str]
    updated_at: Optional[datetime] = None


class WhatsAppTemplateResponse(WhatsAppTemplateSummary):
    created_at: Optional[datetime] = None


class CreateWhatsAppTemplateRequest(BaseModel):
    name: str
    body: str
    language: str = "en"
    category: str = "marketing"


class UpdateWhatsAppTemplateRequest(BaseModel):
    name: Optional[str] = None
    body: Optional[str] = None
    language: Optional[str] = None
    category: Optional[str] = None


class WhatsAppSenderResponse(BaseModel):
    uuid: str
    label: str
    phone_e164: str
    country: str
    is_default: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CreateWhatsAppSenderRequest(BaseModel):
    label: str
    phone: str
    country: str
    is_default: bool = False


class UpdateWhatsAppSenderRequest(BaseModel):
    label: Optional[str] = None
    phone: Optional[str] = None
    country: Optional[str] = None
    is_default: Optional[bool] = None


class WhatsAppTemplateLink(BaseModel):
    template_uuid: str
    name: str
    language: str
    body: str
    url: str


class WhatsAppLeadLinksResponse(BaseModel):
    available: bool
    reason: Optional[str] = None
    phone_e164: Optional[str] = None
    links: list[WhatsAppTemplateLink]


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _require_organization(user: UserModel) -> int:
    if not user.selected_organization_id:
        raise HTTPException(
            status_code=400, detail="No organization selected for the user"
        )
    return user.selected_organization_id


def _clean_name(value: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail="Template name cannot be empty.")
    if len(cleaned) > MAX_TEMPLATE_NAME_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"Template name must be {MAX_TEMPLATE_NAME_CHARS} characters or fewer.",
        )
    return cleaned


def _body_or_422(body: str) -> tuple[str, list[str]]:
    try:
        return validate_template_body(body)
    except WhatsAppTemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _category_or_422(value: str) -> str:
    try:
        return normalize_category(value)
    except WhatsAppTemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _language_or_422(value: str) -> str:
    try:
        return normalize_language(value)
    except WhatsAppTemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _country_or_422(value: str) -> str:
    cleaned = (value or "").strip().upper()
    if len(cleaned) != 2 or not cleaned.isalpha():
        raise HTTPException(
            status_code=422,
            detail="Country must be a two-letter code such as US or GB.",
        )
    return cleaned


def _phone_or_422(raw: str, country: str) -> str:
    """Normalize to E.164 using the sender's own country, never a hardcoded one."""
    normalized = normalize_lead_phone(raw, country)
    if not normalized:
        raise HTTPException(
            status_code=422,
            detail=(
                "That phone number cannot be read as an international number. "
                "Enter it with its country code or pick the matching country."
            ),
        )
    return normalized


def _template_summary(template: WhatsAppTemplateModel) -> WhatsAppTemplateSummary:
    return WhatsAppTemplateSummary(
        uuid=template.template_uuid,
        name=template.name,
        language=template.language,
        category=template.category,
        body=template.body,
        variables=list(template.variables or []),
        updated_at=template.updated_at,
    )


def _template_response(template: WhatsAppTemplateModel) -> WhatsAppTemplateResponse:
    return WhatsAppTemplateResponse(
        **_template_summary(template).model_dump(),
        created_at=template.created_at,
    )


def _sender_response(sender: WhatsAppSenderModel) -> WhatsAppSenderResponse:
    return WhatsAppSenderResponse(
        uuid=sender.sender_uuid,
        label=sender.label,
        phone_e164=sender.phone_e164,
        country=sender.country,
        is_default=bool(sender.is_default),
        created_at=sender.created_at,
        updated_at=sender.updated_at,
    )


async def _template_or_404(
    organization_id: int, template_uuid: str
) -> WhatsAppTemplateModel:
    template = await db_client.get_whatsapp_template(organization_id, template_uuid)
    if template is None:
        raise HTTPException(status_code=404, detail="WhatsApp template not found")
    return template


async def _sender_or_404(organization_id: int, sender_uuid: str) -> WhatsAppSenderModel:
    sender = await db_client.get_whatsapp_sender(organization_id, sender_uuid)
    if sender is None:
        raise HTTPException(status_code=404, detail="WhatsApp sender not found")
    return sender


# ----------------------------------------------------------------------
# Templates
# ----------------------------------------------------------------------


@router.get("/templates")
async def list_whatsapp_templates(
    user: UserModel = Depends(get_user),
) -> list[WhatsAppTemplateSummary]:
    organization_id = _require_organization(user)
    templates = await db_client.list_whatsapp_templates(organization_id)
    return [_template_summary(template) for template in templates]


@router.post("/templates", status_code=201)
async def create_whatsapp_template(
    request: CreateWhatsAppTemplateRequest,
    user: UserModel = Depends(get_user),
) -> WhatsAppTemplateResponse:
    organization_id = _require_organization(user)
    name = _clean_name(request.name)
    body, variables = _body_or_422(request.body)
    language = _language_or_422(request.language)
    category = _category_or_422(request.category)
    try:
        template = await db_client.create_whatsapp_template(
            organization_id=organization_id,
            user_id=user.id,
            name=name,
            language=language,
            category=category,
            body=body,
            variables=variables,
        )
    except WhatsAppConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _template_response(template)


@router.get("/templates/{template_uuid}")
async def get_whatsapp_template(
    template_uuid: str,
    user: UserModel = Depends(get_user),
) -> WhatsAppTemplateResponse:
    organization_id = _require_organization(user)
    return _template_response(await _template_or_404(organization_id, template_uuid))


@router.patch("/templates/{template_uuid}")
async def update_whatsapp_template(
    template_uuid: str,
    request: UpdateWhatsAppTemplateRequest,
    user: UserModel = Depends(get_user),
) -> WhatsAppTemplateResponse:
    organization_id = _require_organization(user)
    template = await _template_or_404(organization_id, template_uuid)

    fields_set = request.model_fields_set
    name = (
        _clean_name(request.name)
        if "name" in fields_set and request.name is not None
        else template.name
    )
    body, variables = _body_or_422(
        request.body
        if "body" in fields_set and request.body is not None
        else template.body
    )
    language = (
        _language_or_422(request.language)
        if "language" in fields_set and request.language is not None
        else template.language
    )
    category = (
        _category_or_422(request.category)
        if "category" in fields_set and request.category is not None
        else template.category
    )
    try:
        updated = await db_client.update_whatsapp_template(
            organization_id,
            template_uuid,
            name=name,
            language=language,
            category=category,
            body=body,
            variables=variables,
        )
    except WhatsAppConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="WhatsApp template not found")
    return _template_response(updated)


@router.delete("/templates/{template_uuid}")
async def delete_whatsapp_template(
    template_uuid: str,
    user: UserModel = Depends(get_user),
) -> dict:
    organization_id = _require_organization(user)
    deleted = await db_client.delete_whatsapp_template(organization_id, template_uuid)
    if not deleted:
        raise HTTPException(status_code=404, detail="WhatsApp template not found")
    return {"status": "deleted", "uuid": template_uuid}


# ----------------------------------------------------------------------
# Senders
# ----------------------------------------------------------------------


@router.get("/senders")
async def list_whatsapp_senders(
    user: UserModel = Depends(get_user),
) -> list[WhatsAppSenderResponse]:
    organization_id = _require_organization(user)
    senders = await db_client.list_whatsapp_senders(organization_id)
    return [_sender_response(sender) for sender in senders]


@router.post("/senders", status_code=201)
async def create_whatsapp_sender(
    request: CreateWhatsAppSenderRequest,
    user: UserModel = Depends(get_user),
) -> WhatsAppSenderResponse:
    organization_id = _require_organization(user)
    label = (request.label or "").strip()
    if not label:
        raise HTTPException(status_code=422, detail="Sender label cannot be empty.")
    country = _country_or_422(request.country)
    phone_e164 = _phone_or_422(request.phone, country)
    try:
        sender = await db_client.create_whatsapp_sender(
            organization_id=organization_id,
            user_id=user.id,
            label=label,
            phone_e164=phone_e164,
            country=country,
            is_default=request.is_default,
        )
    except WhatsAppConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _sender_response(sender)


@router.get("/senders/{sender_uuid}")
async def get_whatsapp_sender(
    sender_uuid: str,
    user: UserModel = Depends(get_user),
) -> WhatsAppSenderResponse:
    organization_id = _require_organization(user)
    return _sender_response(await _sender_or_404(organization_id, sender_uuid))


@router.patch("/senders/{sender_uuid}")
async def update_whatsapp_sender(
    sender_uuid: str,
    request: UpdateWhatsAppSenderRequest,
    user: UserModel = Depends(get_user),
) -> WhatsAppSenderResponse:
    organization_id = _require_organization(user)
    sender = await _sender_or_404(organization_id, sender_uuid)

    fields_set = request.model_fields_set
    label = (request.label or "").strip() if "label" in fields_set else sender.label
    if not label:
        raise HTTPException(status_code=422, detail="Sender label cannot be empty.")
    country = (
        _country_or_422(request.country)
        if "country" in fields_set and request.country is not None
        else sender.country
    )
    raw_phone = (
        request.phone
        if "phone" in fields_set and request.phone is not None
        else sender.phone_e164
    )
    phone_e164 = _phone_or_422(raw_phone, country)
    is_default = (
        bool(request.is_default)
        if "is_default" in fields_set and request.is_default is not None
        else bool(sender.is_default)
    )
    try:
        updated = await db_client.update_whatsapp_sender(
            organization_id,
            sender_uuid,
            label=label,
            phone_e164=phone_e164,
            country=country,
            is_default=is_default,
        )
    except WhatsAppConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="WhatsApp sender not found")
    return _sender_response(updated)


@router.delete("/senders/{sender_uuid}")
async def delete_whatsapp_sender(
    sender_uuid: str,
    user: UserModel = Depends(get_user),
) -> dict:
    organization_id = _require_organization(user)
    deleted = await db_client.delete_whatsapp_sender(organization_id, sender_uuid)
    if not deleted:
        raise HTTPException(status_code=404, detail="WhatsApp sender not found")
    return {"status": "deleted", "uuid": sender_uuid}


# ----------------------------------------------------------------------
# Lead links
# ----------------------------------------------------------------------


@router.get("/leads/{lead_id}/links")
async def whatsapp_lead_links(
    lead_id: int,
    user: UserModel = Depends(get_user),
) -> WhatsAppLeadLinksResponse:
    """Build click-to-chat URLs for one lead and every saved template.

    The response is a set of links only; opening one is the operator's action
    and Bolo records no send.
    """
    organization_id = _require_organization(user)
    lead = await db_client.get_lead(lead_id, organization_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    phone_e164 = lead.phone_e164
    if not phone_e164 and lead.phone:
        # The sender's own default country is the only hint used; nothing is
        # assumed when the organization has no sender or country.
        sender = await db_client.get_default_whatsapp_sender(organization_id)
        if sender is not None:
            phone_e164 = normalize_lead_phone(lead.phone, sender.country)
    if not phone_e164:
        return WhatsAppLeadLinksResponse(
            available=False,
            reason="no_phone" if not lead.phone else "country_unknown",
            links=[],
        )

    templates = await db_client.list_whatsapp_templates(organization_id)
    values = lead_values(
        business_name=lead.title, category=lead.category, city=lead.city
    )
    links: list[WhatsAppTemplateLink] = []
    for template in templates:
        rendered = render_body(template.body, values)
        try:
            url = build_wa_me_url(phone_e164, rendered)
        except WhatsAppTemplateError:
            continue
        links.append(
            WhatsAppTemplateLink(
                template_uuid=template.template_uuid,
                name=template.name,
                language=template.language,
                body=rendered,
                url=url,
            )
        )
    return WhatsAppLeadLinksResponse(available=True, phone_e164=phone_e164, links=links)
