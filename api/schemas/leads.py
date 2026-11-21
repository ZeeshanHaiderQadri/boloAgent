"""Request/response models for lead generation and the lightweight CRM."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from api.constants import LEADS_MAX_TARGET_RESULTS
from api.utils.email_validation import EmailValidationError, validate_email_address

LeadCrmStatus = Literal[
    "new",
    "contacted",
    "interested",
    "not_interested",
    "won",
    "lost",
    "do_not_call",
]

WhatsAppReachability = Literal["unavailable", "likely", "unverified", "unlikely"]

LEAD_CRM_STATUSES: list[str] = [
    "new",
    "contacted",
    "interested",
    "not_interested",
    "won",
    "lost",
    "do_not_call",
]

LeadEmailConsentValue = Literal["unknown", "opted_in", "opted_out"]


class LeadSearchParams(BaseModel):
    """Query settings shared by search creation and editing."""

    name: str | None = Field(default=None, max_length=255)
    category: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=200)
    country_code: str = Field(default="US", min_length=2, max_length=2)
    language: str = Field(default="en", min_length=2, max_length=2)
    provider: str | None = Field(default=None, max_length=32)
    # Scraper "depth" is roughly result pages per query. Keep the default low:
    # results stream in as pages finish, and the worker stops at target_results.
    depth: int = Field(default=3, ge=1, le=20)
    radius_km: int = Field(default=10, ge=1, le=100)
    email: bool = False
    max_time_seconds: int = Field(default=600, ge=60, le=900)
    zoom: int = Field(default=12, ge=3, le=21)
    # Keep only businesses with or without a website. The scraper cannot filter
    # at query time, so matching is applied to each batch before it is stored.
    website_filter: Literal["any", "has_website", "no_website"] = "any"
    # Stop collecting as soon as this many unique businesses are found.
    target_results: int = Field(default=25, ge=1, le=max(1, LEADS_MAX_TARGET_RESULTS))
    # Optionally start recruiting calls the moment the search finishes.
    auto_call: bool = False
    workflow_id: int | None = None
    telephony_configuration_id: int | None = None

    @model_validator(mode="after")
    def _auto_call_needs_workflow(self):
        if self.auto_call and not self.workflow_id:
            raise ValueError("Choose a voice agent to call the leads automatically")
        return self

    @field_validator("country_code", "language")
    @classmethod
    def _lower_or_upper(cls, value: str, info):
        value = value.strip()
        return value.upper() if info.field_name == "country_code" else value.lower()

    @field_validator("category", "location")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class LeadSearchCreateRequest(LeadSearchParams):
    pass


class LeadSearchUpdateRequest(LeadSearchParams):
    """Edit a finished search and run it again.

    Changing the category or location means the previously collected rows no
    longer match the query, so callers may ask for a clean slate. By default
    the existing leads are kept and the new run simply adds to them.
    """

    keep_existing_results: bool = True
    # Append every lead collected by this search (including results from the
    # new run) to this existing outbound campaign.
    add_to_campaign_id: int | None = None


class LeadSearchExtendRequest(BaseModel):
    """Collect more leads with the same query and filters."""

    additional_results: int = Field(ge=1, le=max(1, LEADS_MAX_TARGET_RESULTS))


class LeadSearchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: str | None = None
    location: str | None = None
    country_code: str | None = None
    language: str
    provider: str
    status: str
    target_results: int | None = None
    external_job_id: str | None = None
    results_count: int
    new_leads_count: int
    auto_call_campaign_id: int | None = None
    auto_call_error: str | None = None
    error_message: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    created_by: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class LeadSearchListResponse(BaseModel):
    searches: list[LeadSearchResponse]
    total_count: int
    page: int
    limit: int
    total_pages: int


class LeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    category: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    phone_e164: str | None = None
    # True when the WhatsApp chooser can build a link for this lead.
    whatsapp_available: bool = False
    # Offline hint; "unavailable" when no link can be built. Never proof of
    # registration — see api/services/whatsapp/reachability.py.
    whatsapp_reachability: WhatsAppReachability = "unavailable"
    email: str | None = None
    website: str | None = None
    social_links: dict[str, str] = Field(default_factory=dict)
    socials_checked_at: datetime | None = None
    place_id: str | None = None
    cid: str | None = None
    rating: float | None = None
    review_count: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    open_hours: dict[str, Any] = Field(default_factory=dict)
    crm_status: str
    owner_user_id: int | None = None
    project_id: int | None = None
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    source: str
    last_contacted_at: datetime | None = None
    next_follow_up_at: datetime | None = None
    email_consent: LeadEmailConsentValue = "unknown"
    email_consent_source: str | None = None
    email_consent_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class LeadListResponse(BaseModel):
    leads: list[LeadResponse]
    total_count: int
    page: int
    limit: int
    total_pages: int


class LeadFacetsResponse(BaseModel):
    """Filter values available in the CRM for the current organization."""

    countries: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)


class LeadUpdateRequest(BaseModel):
    crm_status: LeadCrmStatus | None = None
    tags: list[str] | None = None
    notes: str | None = Field(default=None, max_length=20000)
    next_follow_up_at: datetime | None = None
    # Contact fields are editable inline from a lead sheet. An empty string
    # clears the value (``None`` means "leave unchanged").
    title: str | None = Field(default=None, min_length=1, max_length=512)
    category: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=320)
    website: str | None = Field(default=None, max_length=1024)
    address: str | None = Field(default=None, max_length=2000)
    city: str | None = Field(default=None, max_length=255)
    state: str | None = Field(default=None, max_length=255)
    country: str | None = Field(default=None, max_length=255)
    postal_code: str | None = Field(default=None, max_length=32)

    @field_validator(
        "title",
        "category",
        "phone",
        "website",
        "address",
        "city",
        "state",
        "country",
        "postal_code",
    )
    @classmethod
    def _strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return ""
        try:
            return validate_email_address(text)
        except EmailValidationError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned: list[str] = []
        seen: set[str] = set()
        for tag in value:
            tag = str(tag).strip()
            if not tag:
                continue
            tag = tag[:40]
            lowered = tag.lower()
            if lowered not in seen:
                seen.add(lowered)
                cleaned.append(tag)
        return cleaned[:20]

    @model_validator(mode="after")
    def _at_least_one(self):
        if all(
            value is None
            for value in (
                self.crm_status,
                self.tags,
                self.notes,
                self.next_follow_up_at,
                self.title,
                self.category,
                self.phone,
                self.email,
                self.website,
                self.address,
                self.city,
                self.state,
                self.country,
                self.postal_code,
            )
        ):
            raise ValueError("Provide at least one field to update")
        return self


class BulkLeadUpdateRequest(BaseModel):
    lead_ids: list[int] = Field(min_length=1, max_length=1000)
    crm_status: LeadCrmStatus | None = None
    tags: list[str] | None = None
    # Append tags while keeping each lead's existing labels. Used by the CRM
    # and project sheets so a tag can be added to a filtered set in one go.
    add_tags: list[str] | None = None

    @field_validator("tags", "add_tags")
    @classmethod
    def _clean_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [str(tag).strip()[:40] for tag in value if str(tag).strip()][:20]

    @model_validator(mode="after")
    def _at_least_one(self):
        if self.crm_status is None and self.tags is None and self.add_tags is None:
            raise ValueError("Provide crm_status, tags or add_tags")
        return self


class BulkLeadDeleteRequest(BaseModel):
    lead_ids: list[int] = Field(min_length=1, max_length=1000)


class LeadEmailConsentUpdateRequest(BaseModel):
    """Record a marketing-email consent decision for a lead.

    Consent lives outside the general lead patch so the decision is always
    explicit and carries who recorded it, when, and (for opt-ins) why.
    """

    email_consent: LeadEmailConsentValue
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def _reason_required_for_opt_in(self):
        if self.email_consent == "opted_in" and not self.reason:
            raise ValueError(
                "A written reason is required when recording email consent"
            )
        return self


class BulkLeadEmailConsentRequest(LeadEmailConsentUpdateRequest):
    lead_ids: list[int] = Field(min_length=1, max_length=1000)


class BulkLeadEmailConsentResponse(BaseModel):
    matched: int
    changed: int


class LeadActivityCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    body: str = Field(min_length=1, max_length=20000)


class LeadActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    activity_type: str
    title: str | None = None
    body: str | None = None
    metadata: dict[str, Any] = Field(
        default_factory=dict, validation_alias="extra_metadata"
    )
    user_id: int | None = None
    created_at: datetime


class LeadCallResponse(BaseModel):
    workflow_run_id: int
    state: str
    created_at: datetime | None = None
    campaign_id: int
    campaign_name: str
    retry_count: int | None = None
    recording_url: str | None = None
    transcript_url: str | None = None
    call_id: str | None = None
    call_status: str | None = None
    call_disposition: str | None = None
    mapped_call_disposition: str | None = None
    call_tags: list[str] = Field(default_factory=list)
    summary: str | None = None


class LeadSearchLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: datetime
    provider: str
    status: str


class LeadDetailResponse(BaseModel):
    lead: LeadResponse
    activities: list[LeadActivityResponse]
    calls: list[LeadCallResponse]
    searches: list[LeadSearchLinkResponse]


class LeadStatsResponse(BaseModel):
    total: int
    with_phone: int
    with_email: int
    new: int
    contacted: int
    interested: int
    won: int
    do_not_call: int
    searches: int


class LeadDispatchRequest(BaseModel):
    lead_ids: list[int] = Field(min_length=1, max_length=1000)
    name: str = Field(min_length=1, max_length=255)
    workflow_id: int
    telephony_configuration_id: int | None = None
    max_concurrency: int | None = Field(default=None, ge=1, le=100)
    rate_limit_per_second: int = Field(default=1, ge=1, le=50)
    start_immediately: bool = True


class LeadDispatchSkipped(BaseModel):
    lead_id: int
    reason: str


class LeadDispatchResponse(BaseModel):
    campaign_id: int
    campaign_name: str
    dispatched: int
    skipped: list[LeadDispatchSkipped] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LeadMetaResponse(BaseModel):
    statuses: list[str]
    providers: list[str]
    default_provider: str
    demo_enabled: bool
    export_formats: list[str]
    max_dispatch_leads: int


# ---------------------------------------------------------------------------
# Saved lead projects (lists)
# ---------------------------------------------------------------------------
class LeadProjectFilters(BaseModel):
    """The CRM filter set used to collect a project's leads.

    Mirrors the lead list filters so "save these results as a project" assigns
    exactly the leads the operator is looking at, without client-side paging.
    """

    search_id: int | None = None
    status: LeadCrmStatus | None = None
    q: str | None = Field(default=None, max_length=200)
    has_phone: bool | None = None
    has_website: bool | None = None
    has_email: bool | None = None
    country: str | None = Field(default=None, max_length=80)
    state: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=120)
    tag: str | None = Field(default=None, max_length=40)
    email_consent: LeadEmailConsentValue | None = None


class LeadProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # Optionally collect the current CRM filter result into the new project.
    filters: LeadProjectFilters | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class LeadProjectUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class LeadProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    lead_count: int = 0
    callable_count: int = 0
    created_at: datetime
    updated_at: datetime | None = None


class LeadProjectListResponse(BaseModel):
    projects: list[LeadProjectResponse]
    total: int


class LeadProjectAssignRequest(BaseModel):
    lead_ids: list[int] = Field(min_length=1, max_length=1000)


class LeadProjectAssignmentResponse(BaseModel):
    assigned: int
    project: LeadProjectResponse
