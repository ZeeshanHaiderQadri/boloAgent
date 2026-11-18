"""API routes for email senders and sending domains.

Senders point at an encrypted credential the organization supplied, so this
module never accepts or returns a secret in plaintext. Creation and updates
validate the provider settings and credentials by constructing the real
adapter; nothing is sent until ``verify`` or ``test-send`` is called
explicitly.
"""

from __future__ import annotations

import asyncio
import html
import re
from datetime import UTC, datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db import db_client
from api.db.email_sender_client import (
    UNSET,
    EmailResourceConflictError,
    EmailSenderInUseError,
)
from api.db.models import EmailDomainModel, EmailSenderModel, UserModel
from api.enums import (
    EmailDomainStatus,
    EmailProvider,
    EmailSenderStatus,
    WebhookCredentialType,
)
from api.sdk_expose import sdk_expose
from api.services.auth.depends import get_user
from api.services.email.adapter import (
    EmailAdapter,
    EmailConfigurationError,
    EmailDeliveryError,
    EmailSenderConfig,
    OutboundEmail,
)
from api.services.email.limits import effective_limits, provider_limits
from api.services.email.ses import SesEmailAdapter
from api.services.email.smtp import SmtpEmailAdapter

router = APIRouter(prefix="/email")

SUPPORTED_PROVIDERS = {EmailProvider.SMTP, EmailProvider.SES}

_DOMAIN_LABEL = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)")


# ----------------------------------------------------------------------
# Request / response schemas
# ----------------------------------------------------------------------


class EmailCredentials(BaseModel):
    """Secret half of a sender. Never returned by any endpoint."""

    username: Optional[str] = None
    password: Optional[str] = None
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    session_token: Optional[str] = None


class CreateEmailSenderRequest(BaseModel):
    name: str
    provider: EmailProvider
    from_name: str
    from_email: str
    reply_to_email: Optional[str] = None
    # Non-secret provider settings. ``{"preset": "hostinger"}`` expands to the
    # documented Hostinger SMTP host, port and TLS mode.
    settings: Dict[str, Any] = Field(default_factory=dict)
    credentials: EmailCredentials
    is_default: bool = False
    daily_cap: Optional[int] = None
    rate_per_second: Optional[float] = None


class UpdateEmailSenderRequest(BaseModel):
    name: Optional[str] = None
    from_name: Optional[str] = None
    from_email: Optional[str] = None
    reply_to_email: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None
    # Only the supplied keys rotate; a new SMTP password alone is enough.
    credentials: Optional[EmailCredentials] = None
    is_default: Optional[bool] = None
    daily_cap: Optional[int] = None
    rate_per_second: Optional[float] = None


class SenderLimitsResponse(BaseModel):
    daily_cap: int
    rate_per_second: float


class EmailSenderResponse(BaseModel):
    uuid: str
    name: str
    provider: str
    from_name: str
    from_email: str
    reply_to_email: Optional[str]
    settings: Dict[str, Any]
    status: str
    is_default: bool
    limits: SenderLimitsResponse
    # Effective limits are returned above; the overrides below let an edit form
    # distinguish "using the provider default" from an explicit choice.
    daily_cap_override: Optional[int]
    rate_per_second_override: Optional[float]
    credentials_configured: bool
    credential_hint: Dict[str, str]
    last_verified_at: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]


class VerifySenderResponse(BaseModel):
    status: str
    last_verified_at: Optional[datetime]
    sandbox_mode: Optional[bool] = None
    send_quota: Optional[Dict[str, float]] = None
    message: str


class TestSendRequest(BaseModel):
    to_email: str


class TestSendResponse(BaseModel):
    provider_message_id: str
    message: str


class CreateEmailDomainRequest(BaseModel):
    domain: str
    sender_uuid: str


class UpdateEmailDomainRequest(BaseModel):
    domain: Optional[str] = None
    sender_uuid: Optional[str] = None


class EmailDomainResponse(BaseModel):
    uuid: str
    domain: str
    provider: str
    sender_uuid: Optional[str]
    status: str
    verification: Dict[str, Any]
    last_checked_at: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]


# ----------------------------------------------------------------------
# Validation helpers
# ----------------------------------------------------------------------


def _require_organization(user: UserModel) -> int:
    if not user.selected_organization_id:
        raise HTTPException(
            status_code=400, detail="No organization selected for the user"
        )
    return user.selected_organization_id


def _clean_text(value: str, *, field: str, max_length: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail=f"{field} cannot be empty")
    if len(cleaned) > max_length:
        raise HTTPException(
            status_code=400, detail=f"{field} must be at most {max_length} characters"
        )
    return cleaned


def _clean_email(value: str, *, field: str) -> str:
    cleaned = value.strip()
    local, _, domain = cleaned.rpartition("@")
    if (
        not local
        or not domain
        or "@" in local
        or any(character.isspace() for character in cleaned)
    ):
        raise HTTPException(status_code=400, detail=f"{field} is not a valid address")
    if len(cleaned) > 320:
        raise HTTPException(status_code=400, detail=f"{field} is too long")
    return cleaned


def _normalize_domain(value: str) -> str:
    """Lowercase, strip a trailing dot, and reject URLs or email addresses."""
    domain = value.strip().lower().rstrip(".")
    if not domain:
        raise HTTPException(status_code=400, detail="Domain cannot be empty")
    if (
        "://" in domain
        or "/" in domain
        or "@" in domain
        or any(character.isspace() for character in domain)
    ):
        raise HTTPException(
            status_code=400,
            detail="Enter a bare domain name such as example.com, not a URL or address",
        )
    if len(domain) > 253:
        raise HTTPException(status_code=400, detail="Domain name is too long")
    labels = domain.split(".")
    if len(labels) < 2 or any(
        not label or not _DOMAIN_LABEL.fullmatch(label) for label in labels
    ):
        raise HTTPException(
            status_code=400,
            detail="Domain must look like example.com; each part may contain "
            "letters, digits and inner hyphens",
        )
    return domain


def _validate_limits(
    daily_cap: Optional[int], rate_per_second: Optional[float]
) -> None:
    if daily_cap is not None and daily_cap < 1:
        raise HTTPException(status_code=400, detail="Daily cap must be at least 1")
    if rate_per_second is not None and rate_per_second <= 0:
        raise HTTPException(
            status_code=400, detail="Rate per second must be greater than zero"
        )


def _require_supported_provider(provider: EmailProvider) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The {provider.value} adapter is not available yet. "
                "Choose smtp or ses."
            ),
        )


def _credential_shape(provider: EmailProvider, data: Dict[str, Any]) -> Dict[str, str]:
    """Return only the fields this provider uses, rejecting missing ones."""
    required = {
        EmailProvider.SMTP: ("username", "password"),
        EmailProvider.SES: ("access_key_id", "secret_access_key"),
    }[provider]
    allowed = set(required) | {"session_token"}
    cleaned = {
        key: str(data.get(key) or "")
        for key in allowed
        if data.get(key) not in (None, "")
    }
    missing = [key for key in required if not cleaned.get(key)]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{provider.value.upper()} credentials must include: "
                + ", ".join(missing)
            ),
        )
    return cleaned


def _credential_type_for(provider: EmailProvider) -> str:
    return (
        WebhookCredentialType.BASIC_AUTH.value
        if provider is EmailProvider.SMTP
        else WebhookCredentialType.API_KEY.value
    )


def _sender_config(sender: EmailSenderModel) -> EmailSenderConfig:
    return EmailSenderConfig(
        provider=EmailProvider(sender.provider),
        from_name=sender.from_name,
        from_email=sender.from_email,
        reply_to=sender.reply_to_email,
        settings=sender.settings or {},
    )


def _build_adapter(
    sender: EmailSenderModel, credentials: Dict[str, Any]
) -> EmailAdapter:
    provider = EmailProvider(sender.provider)
    config = _sender_config(sender)
    if provider is EmailProvider.SMTP:
        return SmtpEmailAdapter(config, credentials)
    if provider is EmailProvider.SES:
        return SesEmailAdapter(config, credentials)
    raise EmailConfigurationError(f"{provider.value} is not available yet")


async def _validate_sender_settings(
    *,
    provider: EmailProvider,
    from_name: str,
    from_email: str,
    reply_to_email: Optional[str],
    settings: Dict[str, Any],
    credentials: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the adapter to validate settings, returning normalized settings.

    SMTP presets are expanded here so the response shows the real host and
    port. Adapter construction performs provider validation but no I/O; the
    DNS-based egress check only runs in SaaS mode, off the event loop.
    """
    candidate = EmailSenderModel(
        provider=provider.value,
        from_name=from_name,
        from_email=from_email,
        reply_to_email=reply_to_email,
        settings=settings,
        credential_id=0,
    )
    try:
        adapter = await asyncio.to_thread(_build_adapter, candidate, credentials)
    except EmailConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EmailDeliveryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if isinstance(adapter, SmtpEmailAdapter):
        return dict(adapter.settings)
    normalized = {"region": str(settings.get("region") or "").strip().lower()}
    configuration_set = str(settings.get("configuration_set") or "").strip()
    if configuration_set:
        normalized["configuration_set"] = configuration_set
    return normalized


def _mask_secret(value: str) -> str:
    if len(value) <= 4:
        return "•" * len(value)
    return "•" * max(4, len(value) - 4) + value[-4:]


def _credential_hint(provider: str, data: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Show enough to recognise a credential, never enough to use it."""
    if not data:
        return {}
    if provider == EmailProvider.SMTP.value:
        username = str(data.get("username") or "")
        return {"username": username} if username else {}
    access_key_id = str(data.get("access_key_id") or "")
    hint = {"access_key_id": _mask_secret(access_key_id)} if access_key_id else {}
    if data.get("session_token"):
        hint["session_token"] = "configured"
    return hint


def _sender_response(
    sender: EmailSenderModel, credentials: Optional[Dict[str, Any]]
) -> EmailSenderResponse:
    limits = effective_limits(sender)
    return EmailSenderResponse(
        uuid=sender.sender_uuid,
        name=sender.name,
        provider=sender.provider,
        from_name=sender.from_name,
        from_email=sender.from_email,
        reply_to_email=sender.reply_to_email,
        settings=sender.settings or {},
        status=sender.status,
        is_default=sender.is_default,
        limits=SenderLimitsResponse(
            daily_cap=limits.daily_cap, rate_per_second=limits.rate_per_second
        ),
        daily_cap_override=sender.daily_cap,
        rate_per_second_override=sender.rate_per_second,
        credentials_configured=credentials is not None,
        credential_hint=_credential_hint(sender.provider, credentials),
        last_verified_at=sender.last_verified_at,
        created_at=sender.created_at,
        updated_at=sender.updated_at,
    )


def _domain_response(domain: EmailDomainModel) -> EmailDomainResponse:
    return EmailDomainResponse(
        uuid=domain.domain_uuid,
        domain=domain.domain,
        provider=domain.provider,
        sender_uuid=domain.sender.sender_uuid if domain.sender else None,
        status=domain.status,
        verification=domain.verification or {},
        last_checked_at=domain.last_checked_at,
        created_at=domain.created_at,
        updated_at=domain.updated_at,
    )


async def _credentials_map(organization_id: int) -> Dict[int, Dict[str, Any]]:
    return await db_client.get_email_sender_credentials(organization_id)


async def _sender_or_404(organization_id: int, sender_uuid: str) -> EmailSenderModel:
    sender = await db_client.get_email_sender(organization_id, sender_uuid)
    if sender is None:
        raise HTTPException(status_code=404, detail="Email sender not found")
    return sender


async def _domain_or_404(organization_id: int, domain_uuid: str) -> EmailDomainModel:
    domain = await db_client.get_email_domain(organization_id, domain_uuid)
    if domain is None:
        raise HTTPException(status_code=404, detail="Email domain not found")
    return domain


async def _adapter_for_sender(
    organization_id: int, sender: EmailSenderModel
) -> EmailAdapter:
    try:
        credentials = await db_client.get_sender_credential_data(
            organization_id, sender
        )
        return await asyncio.to_thread(_build_adapter, sender, credentials)
    except EmailResourceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EmailConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EmailDeliveryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ----------------------------------------------------------------------
# Sender routes
# ----------------------------------------------------------------------


@router.get(
    "/senders",
    **sdk_expose(
        method="list_email_senders",
        description="List the organization's email senders without secrets.",
    ),
)
async def list_email_senders(
    user: UserModel = Depends(get_user),
) -> list[EmailSenderResponse]:
    organization_id = _require_organization(user)
    senders = await db_client.list_email_senders(organization_id)
    credentials = await _credentials_map(organization_id)
    return [
        _sender_response(sender, credentials.get(sender.credential_id))
        for sender in senders
    ]


@router.post("/senders", status_code=201)
async def create_email_sender(
    request: CreateEmailSenderRequest,
    user: UserModel = Depends(get_user),
) -> EmailSenderResponse:
    organization_id = _require_organization(user)
    _require_supported_provider(request.provider)

    name = _clean_text(request.name, field="Name", max_length=255)
    from_name = _clean_text(request.from_name, field="From name", max_length=255)
    from_email = _clean_email(request.from_email, field="From address")
    reply_to_email = (
        _clean_email(request.reply_to_email, field="Reply-to address")
        if request.reply_to_email
        else None
    )
    _validate_limits(request.daily_cap, request.rate_per_second)

    credentials = _credential_shape(request.provider, request.credentials.model_dump())
    settings = await _validate_sender_settings(
        provider=request.provider,
        from_name=from_name,
        from_email=from_email,
        reply_to_email=reply_to_email,
        settings=request.settings,
        credentials=credentials,
    )

    try:
        sender = await db_client.create_email_sender(
            organization_id=organization_id,
            user_id=user.id,
            name=name,
            provider=request.provider,
            from_name=from_name,
            from_email=from_email,
            reply_to_email=reply_to_email,
            settings=settings,
            credential_type=_credential_type_for(request.provider),
            credential_data=credentials,
            is_default=request.is_default,
            daily_cap=request.daily_cap,
            rate_per_second=request.rate_per_second,
        )
    except EmailResourceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _sender_response(sender, credentials)


@router.get("/senders/{sender_uuid}")
async def get_email_sender(
    sender_uuid: str,
    user: UserModel = Depends(get_user),
) -> EmailSenderResponse:
    organization_id = _require_organization(user)
    sender = await _sender_or_404(organization_id, sender_uuid)
    credentials = await _credentials_map(organization_id)
    return _sender_response(sender, credentials.get(sender.credential_id))


@router.patch("/senders/{sender_uuid}")
async def update_email_sender(
    sender_uuid: str,
    request: UpdateEmailSenderRequest,
    user: UserModel = Depends(get_user),
) -> EmailSenderResponse:
    organization_id = _require_organization(user)
    sender = await _sender_or_404(organization_id, sender_uuid)

    fields_set = request.model_fields_set
    name = (
        _clean_text(request.name, field="Name", max_length=255)
        if request.name is not None
        else None
    )
    from_name = (
        _clean_text(request.from_name, field="From name", max_length=255)
        if request.from_name is not None
        else None
    )
    from_email = (
        _clean_email(request.from_email, field="From address")
        if request.from_email is not None
        else None
    )
    reply_to_email = UNSET
    if "reply_to_email" in fields_set:
        reply_to_email = (
            _clean_email(request.reply_to_email, field="Reply-to address")
            if request.reply_to_email
            else None
        )
    _validate_limits(request.daily_cap, request.rate_per_second)

    provider = EmailProvider(sender.provider)
    credential_data: Optional[Dict[str, Any]] = None
    credential_type: Optional[str] = None
    if request.credentials is not None:
        existing = await db_client.get_sender_credential_data(organization_id, sender)
        merged = existing | request.credentials.model_dump(exclude_none=True)
        credential_data = _credential_shape(provider, merged)
        credential_type = _credential_type_for(provider)

    settings = request.settings
    if settings is not None or credential_data is not None:
        credentials_for_check = (
            credential_data
            if credential_data is not None
            else await db_client.get_sender_credential_data(organization_id, sender)
        )
        settings = await _validate_sender_settings(
            provider=provider,
            from_name=from_name or sender.from_name,
            from_email=from_email or sender.from_email,
            reply_to_email=(
                sender.reply_to_email if reply_to_email is UNSET else reply_to_email
            ),
            settings=settings if settings is not None else (sender.settings or {}),
            credentials=credentials_for_check,
        )

    try:
        updated = await db_client.update_email_sender(
            organization_id=organization_id,
            sender_uuid=sender_uuid,
            name=name,
            from_name=from_name,
            from_email=from_email,
            reply_to_email=reply_to_email,
            settings=settings,
            is_default=request.is_default,
            daily_cap=(request.daily_cap if "daily_cap" in fields_set else UNSET),
            rate_per_second=(
                request.rate_per_second if "rate_per_second" in fields_set else UNSET
            ),
            credential_type=credential_type,
            credential_data=credential_data,
        )
    except EmailResourceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if updated is None:
        raise HTTPException(status_code=404, detail="Email sender not found")

    credentials = await _credentials_map(organization_id)
    return _sender_response(updated, credentials.get(updated.credential_id))


@router.delete("/senders/{sender_uuid}")
async def delete_email_sender(
    sender_uuid: str,
    user: UserModel = Depends(get_user),
) -> dict:
    organization_id = _require_organization(user)
    try:
        deleted = await db_client.delete_email_sender(organization_id, sender_uuid)
    except EmailSenderInUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Email sender not found")
    return {"status": "deleted", "uuid": sender_uuid}


@router.post("/senders/{sender_uuid}/verify")
async def verify_email_sender(
    sender_uuid: str,
    user: UserModel = Depends(get_user),
) -> VerifySenderResponse:
    organization_id = _require_organization(user)
    sender = await _sender_or_404(organization_id, sender_uuid)
    adapter = await _adapter_for_sender(organization_id, sender)

    try:
        await adapter.verify()
    except EmailConfigurationError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"The sender could not be verified: {exc}",
        ) from exc
    except EmailDeliveryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    updated = await db_client.mark_email_sender_verified(organization_id, sender_uuid)
    sandbox_mode = getattr(adapter, "sandbox_mode", None)
    quota = getattr(adapter, "send_quota", None)
    message = "Sender verified."
    if sandbox_mode:
        message = (
            "Sender verified, but this SES account is still in the sandbox: "
            "it can only send to verified addresses."
        )
    return VerifySenderResponse(
        status=updated.status if updated else EmailSenderStatus.VERIFIED.value,
        last_verified_at=updated.last_verified_at if updated else None,
        sandbox_mode=sandbox_mode,
        send_quota=quota,
        message=message,
    )


@router.post("/senders/{sender_uuid}/test-send")
async def test_send_email(
    sender_uuid: str,
    request: TestSendRequest,
    user: UserModel = Depends(get_user),
) -> TestSendResponse:
    """Send one message immediately, outside the queue.

    A test send proves the credential works end to end without writing a
    message row or consuming a campaign's quota.
    """
    organization_id = _require_organization(user)
    sender = await _sender_or_404(organization_id, sender_uuid)
    to_email = _clean_email(request.to_email, field="Recipient address")
    adapter = await _adapter_for_sender(organization_id, sender)

    sent_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    safe_name = html.escape(sender.from_name)
    message = OutboundEmail(
        to_email=to_email,
        subject=f"Test message from {sender.from_name}",
        html_body=(
            f"<p>This is a test message sent from <strong>{safe_name}</strong>.</p>"
            f"<p>Sent at {sent_at}.</p>"
        ),
        text_body=(
            f"This is a test message sent from {sender.from_name}.\nSent at {sent_at}."
        ),
    )
    try:
        result = await adapter.send(message)
    except EmailConfigurationError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"The test message was rejected: {exc}",
        ) from exc
    except EmailDeliveryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return TestSendResponse(
        provider_message_id=result.provider_message_id,
        message=f"Test message accepted by {sender.provider}.",
    )


# ----------------------------------------------------------------------
# Sending-domain routes
# ----------------------------------------------------------------------


@router.get("/domains")
async def list_email_domains(
    user: UserModel = Depends(get_user),
) -> list[EmailDomainResponse]:
    organization_id = _require_organization(user)
    domains = await db_client.list_email_domains(organization_id)
    return [_domain_response(domain) for domain in domains]


@router.post("/domains", status_code=201)
async def create_email_domain(
    request: CreateEmailDomainRequest,
    user: UserModel = Depends(get_user),
) -> EmailDomainResponse:
    organization_id = _require_organization(user)
    domain = _normalize_domain(request.domain)
    sender = await _sender_or_404(organization_id, request.sender_uuid)
    try:
        row = await db_client.create_email_domain(
            organization_id=organization_id,
            user_id=user.id,
            domain=domain,
            provider=EmailProvider(sender.provider),
            sender_id=sender.id,
        )
    except EmailResourceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _domain_response(row)


@router.get("/domains/{domain_uuid}")
async def get_email_domain(
    domain_uuid: str,
    user: UserModel = Depends(get_user),
) -> EmailDomainResponse:
    organization_id = _require_organization(user)
    return _domain_response(await _domain_or_404(organization_id, domain_uuid))


@router.patch("/domains/{domain_uuid}")
async def update_email_domain(
    domain_uuid: str,
    request: UpdateEmailDomainRequest,
    user: UserModel = Depends(get_user),
) -> EmailDomainResponse:
    organization_id = _require_organization(user)
    await _domain_or_404(organization_id, domain_uuid)

    domain = _normalize_domain(request.domain) if request.domain is not None else None
    sender_id: Optional[int] = None
    provider: Optional[EmailProvider] = None
    if request.sender_uuid is not None:
        sender = await _sender_or_404(organization_id, request.sender_uuid)
        sender_id = sender.id
        provider = EmailProvider(sender.provider)

    try:
        updated = await db_client.update_email_domain(
            organization_id=organization_id,
            domain_uuid=domain_uuid,
            domain=domain,
            provider=provider,
            sender_id=sender_id,
        )
    except EmailResourceConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Email domain not found")
    return _domain_response(updated)


@router.delete("/domains/{domain_uuid}")
async def delete_email_domain(
    domain_uuid: str,
    user: UserModel = Depends(get_user),
) -> dict:
    organization_id = _require_organization(user)
    deleted = await db_client.delete_email_domain(organization_id, domain_uuid)
    if not deleted:
        raise HTTPException(status_code=404, detail="Email domain not found")
    return {"status": "deleted", "uuid": domain_uuid}


@router.post("/domains/{domain_uuid}/verify")
async def verify_email_domain(
    domain_uuid: str,
    user: UserModel = Depends(get_user),
) -> EmailDomainResponse:
    """Ask the provider whether the domain's DNS records are in place."""
    organization_id = _require_organization(user)
    domain = await _domain_or_404(organization_id, domain_uuid)
    if domain.sender is None:
        raise HTTPException(
            status_code=400,
            detail="Link a sender before verifying this domain",
        )
    if domain.provider != EmailProvider.SES.value:
        raise HTTPException(
            status_code=400,
            detail=(
                "Automatic domain verification is only available for Amazon SES. "
                "SMTP hosts verify domains in their own dashboard."
            ),
        )

    adapter = await _adapter_for_sender(organization_id, domain.sender)
    if not isinstance(adapter, SesEmailAdapter):
        raise HTTPException(
            status_code=400,
            detail="Automatic domain verification is only available for Amazon SES",
        )
    try:
        result = await adapter.get_domain_verification(domain.domain)
    except EmailConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except EmailDeliveryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    status = (
        EmailDomainStatus.VERIFIED
        if result["verified"]
        else (
            EmailDomainStatus.FAILED
            if result["verification_status"] == "Failed"
            else EmailDomainStatus.PENDING
        )
    )
    updated = await db_client.record_email_domain_verification(
        organization_id=organization_id,
        domain_uuid=domain_uuid,
        status=status,
        verification=result["details"],
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Email domain not found")
    return _domain_response(updated)


# Keep the provider-default limits visible to the UI through one call.
@router.get("/providers")
async def list_email_providers() -> list[dict]:
    return [
        {
            "provider": provider.value,
            "supported": provider in SUPPORTED_PROVIDERS,
            "defaults": {
                "daily_cap": provider_limits(provider).daily_cap,
                "rate_per_second": provider_limits(provider).rate_per_second,
            },
        }
        for provider in EmailProvider
    ]
