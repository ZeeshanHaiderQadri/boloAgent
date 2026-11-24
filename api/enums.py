from enum import Enum


class IntegrationAction(Enum):
    ALL_CALLS = "All Calls"
    QUALIFIED_CALLS = "Qualified Calls"


class Environment(Enum):
    LOCAL = "local"
    PRODUCTION = "production"
    TEST = "test"


class CallType(Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class AnswerAction(str, Enum):
    # Start speaking after silence, while continuing to supervise the answer.
    START_OPENING = "start_opening"
    # Play the workflow opening, then allow normal conversation.
    RELEASE = "release"
    # Play the configured voicemail message, then disconnect.
    LEAVE_MESSAGE = "leave_message"
    # Disconnect without playing a message.
    DROP = "drop"
    # Play the screening introduction, then listen again for the subscriber.
    SCREEN_THEN_REARM = "screen_then_rearm"
    # Stop a provisional greeting and wait silently for a screened subscriber.
    WAIT_FOR_SCREENING = "wait_for_screening"
    # Stop answer handling because the pipeline has ended.
    CANCELLED = "cancelled"


class TelephonyCallStatus(str, Enum):
    INITIATED = "initiated"
    RINGING = "ringing"
    IN_PROGRESS = "in-progress"
    ANSWERED = "answered"
    COMPLETED = "completed"
    FAILED = "failed"
    BUSY = "busy"
    NO_ANSWER = "no-answer"
    CANCELED = "canceled"
    ERROR = "error"

    @classmethod
    def from_raw(cls, value: object) -> "TelephonyCallStatus | None":
        if isinstance(value, cls):
            return value

        if value in (None, ""):
            return None

        try:
            return cls(str(value).lower())
        except ValueError:
            return None


class WorkflowRunMode(Enum):
    ARI = "ari"
    PLIVO = "plivo"
    TWILIO = "twilio"
    VONAGE = "vonage"
    VOBIZ = "vobiz"
    CLOUDONIX = "cloudonix"
    EXOTEL = "exotel"
    TELNYX = "telnyx"
    WHATSAPP = "whatsapp"
    WEBRTC = "webrtc"
    SMALLWEBRTC = "smallwebrtc"
    TEXTCHAT = "textchat"

    # Historical, not used anymore. Don't
    # use and don't remove
    STASIS = "stasis"
    VOICE = "VOICE"
    CHAT = "CHAT"


class WorkflowRunChannel(Enum):
    """How a run reached the agent, coarser than the provider-level mode.

    `WorkflowRunMode` records the specific transport (twilio, telnyx, ...);
    this groups those into the three channels users think in terms of when
    filtering their runs.
    """

    TELEPHONY = "telephony"
    WEB = "web"
    CHAT = "chat"


# Every WorkflowRunMode belongs to exactly one channel. Historical modes are
# mapped too, so filtering never silently drops old runs.
WORKFLOW_RUN_MODES_BY_CHANNEL: dict[str, tuple[str, ...]] = {
    WorkflowRunChannel.TELEPHONY.value: (
        WorkflowRunMode.ARI.value,
        WorkflowRunMode.PLIVO.value,
        WorkflowRunMode.TWILIO.value,
        WorkflowRunMode.VONAGE.value,
        WorkflowRunMode.VOBIZ.value,
        WorkflowRunMode.CLOUDONIX.value,
        WorkflowRunMode.EXOTEL.value,
        WorkflowRunMode.TELNYX.value,
        WorkflowRunMode.WHATSAPP.value,
        WorkflowRunMode.STASIS.value,
        WorkflowRunMode.VOICE.value,
    ),
    WorkflowRunChannel.WEB.value: (
        WorkflowRunMode.WEBRTC.value,
        WorkflowRunMode.SMALLWEBRTC.value,
    ),
    WorkflowRunChannel.CHAT.value: (
        WorkflowRunMode.TEXTCHAT.value,
        WorkflowRunMode.CHAT.value,
    ),
}


class StorageBackend(Enum):
    """Storage backend enumeration.

    Currently supported backends:
    - S3: Amazon S3
    - MINIO: MinIO

    Future extensibility: Additional backends like GCS, Azure can be added by:
    1. Adding new enum values as strings
    2. Implementing storage logic in services/storage.py
    3. Database will automatically support new values via SQLAlchemy Enum type
    """

    # Currently implemented backends
    S3 = "s3"  # AWS S3 for cloud deployments
    MINIO = "minio"  # MinIO for local/OSS deployments

    @classmethod
    def get_current_backend(cls):
        """Get current backend based on ENABLE_AWS_S3 flag."""
        from api.constants import ENABLE_AWS_S3

        if ENABLE_AWS_S3:
            return cls.S3
        else:
            return cls.MINIO


class WorkflowRunState(Enum):
    INITIALIZED = "initialized"  # Workflow run created, ready for connection
    RUNNING = "running"  # Websocket connected and pipeline active
    COMPLETED = "completed"  # Workflow run finished


class WorkflowRunStatus(Enum):
    # historical modes
    VOICE = "VOICE"
    CHAT = "CHAT"


class OrganizationConfigurationKey(Enum):
    CALL_EVENTS = "CALL_EVENTS"
    CONCURRENT_CALL_LIMIT = "CONCURRENT_CALL_LIMIT"
    TELEPHONY_CONFIGURATION = (
        "TELEPHONY_CONFIGURATION"  # Stores all providers + active one
    )
    TWILIO_CONFIGURATION = (
        "TWILIO_CONFIGURATION"  # Deprecated - for backward compatibility
    )
    LANGFUSE_CREDENTIALS = (
        "LANGFUSE_CREDENTIALS"  # Org-level Langfuse tracing credentials
    )
    MODEL_CONFIGURATION_DEFAULT_UUID = "MODEL_CONFIGURATION_DEFAULT_UUID"
    MODEL_CONFIGURATION_V2 = "MODEL_CONFIGURATION_V2"  # Retired; rows kept for audit after the catalog backfill, nothing reads them
    ORGANIZATION_PREFERENCES = "ORGANIZATION_PREFERENCES"  # Org-level defaults such as timezone/test call number
    COPILOT_WEB_ACCESS = (
        "COPILOT_WEB_ACCESS"  # Bolo Agent web connectors and encrypted provider keys
    )
    MODEL_CONFIGURATION_PREFERENCES = "MODEL_CONFIGURATION_PREFERENCES"  # Deprecated; read fallback for old org preferences
    ORGANIZATION_BOOTSTRAP = (
        "ORGANIZATION_BOOTSTRAP"  # Single-winner lease for post-signup provisioning
    )
    MPS_SIGNUP_GRANT = "MPS_SIGNUP_GRANT"  # Team grant / per-actor claim results


class UserConfigurationKey(Enum):
    """Keys for the per-user keyed JSON store (user_configurations)."""

    MODEL_CONFIGURATION = "MODEL_CONFIGURATION"  # Retired per-user v1 model configuration; rows kept, nothing reads them
    ONBOARDING = "ONBOARDING"  # Post-signup onboarding state (gate, tooltips, actions)
    AVATAR = "AVATAR"  # Bolo Agent pet: character, look, position and voice


class WorkflowStatus(Enum):
    """Workflow status values"""

    ACTIVE = "active"
    ARCHIVED = "archived"
    # Future statuses can be added here like:
    # DRAFT = "draft"
    # PAUSED = "paused"


class RedisChannel(Enum):
    """Redis pub/sub channel names"""

    CAMPAIGN_EVENTS = "campaign_events"
    WORKER_SYNC = "worker_sync"


class TriggerState(Enum):
    """Agent trigger state values"""

    ACTIVE = "active"
    ARCHIVED = "archived"


class WebhookCredentialType(Enum):
    """Webhook credential authentication types"""

    NONE = "none"  # No authentication
    API_KEY = "api_key"  # API key in header
    BEARER_TOKEN = "bearer_token"  # Bearer token auth
    BASIC_AUTH = "basic_auth"  # Username/password
    CUSTOM_HEADER = "custom_header"  # Custom header key-value


class ToolCategory(Enum):
    """Tool category types"""

    HTTP_API = "http_api"  # Custom HTTP API calls (implemented)
    END_CALL = "end_call"  # End call tool
    TRANSFER_CALL = "transfer_call"  # Transfer call to phone number (Twilio only)
    TRANSFER_AGENT = "transfer_agent"  # Hand the live call to another Bolo agent
    CALCULATOR = "calculator"  # Built-in calculator tool
    NATIVE = "native"  # Built-in integrations (future: dtmf_input)
    INTEGRATION = "integration"  # Third-party integrations (future: Google Calendar, Salesforce, etc.)
    MCP = "mcp"  # Customer-provided MCP server exposing a tool catalog


class ToolStatus(Enum):
    """Tool status values"""

    ACTIVE = "active"  # Tool is available for use
    ARCHIVED = "archived"  # Tool is soft-deleted
    DRAFT = "draft"  # Tool is being configured (not ready for use)


class PostHogEvent(str, Enum):
    """PostHog event names — backend events only."""

    WORKFLOW_CREATED = "workflow_created"
    WORKFLOW_PUBLISHED = "workflow_published"
    WORKFLOW_DUPLICATED = "workflow_duplicated"
    # Agent-builder lifecycle (fired from the builder worker, survives stream
    # disconnects). Dedupe by `session_id` for funnels -- a multi-turn build can
    # emit BUILDER_AGENT_BUILT on each successful turn.
    BUILDER_AGENT_BUILT = "builder_agent_built"
    BUILDER_SAVED = "builder_saved"
    BUILDER_ERROR = "builder_error"
    CALL_STARTED = "call_started"
    CALL_COMPLETED = "call_completed"
    CALL_FAILED = "call_failed"
    TELEPHONY_CONFIGURED = "telephony_configured"
    KNOWLEDGE_BASE_CREATED = "knowledge_base_created"
    KNOWLEDGE_BASE_UPDATED = "knowledge_base_updated"
    TOOL_CREATED = "tool_created"
    TOOL_UPDATED = "tool_updated"
    AGENT_EMBEDDED = "agent_embedded"
    SIGNED_UP = "signed_up"
    SIGNED_IN = "signed_in"
    ORGANIZATION_CREATED = "organization_created"
    ORGANIZATION_USER_ASSOCIATED = "organization_user_associated"
    # usage_* events track orgs hitting capacity/limit boundaries
    USAGE_CONCURRENT_CALL_LIMIT_REACHED = "usage_concurrent_call_limit_reached"


class EmailProvider(Enum):
    """Outbound email providers. Every sender brings its own account."""

    SMTP = "smtp"  # Universal SMTP, including Hostinger's preset
    SES = "ses"  # Amazon SES
    MAILGUN = "mailgun"
    MANDRILL = "mandrill"  # Mailchimp Transactional


class EmailSenderStatus(Enum):
    """Lifecycle of a configured sending identity."""

    PENDING = "pending"  # Saved, credentials not yet verified
    VERIFIED = "verified"  # Ready to send
    DISABLED = "disabled"  # Kept for history, never used for sending


class EmailTemplateFormat(Enum):
    """Authoring format stored for an email template."""

    HTML = "html"
    MJML = "mjml"


class EmailDomainStatus(Enum):
    """Verification state of a sending domain.

    SMTP hosts verify domains in their own dashboard and expose no status API,
    so a domain attached to an SMTP sender stays ``pending`` until the operator
    handles it there. Amazon SES domains are checked through the API.
    """

    PENDING = "pending"  # DNS records not published, or not checked yet
    VERIFIED = "verified"  # Provider confirms the domain may send
    FAILED = "failed"  # Provider rejected the domain


class EmailMessageStatus(Enum):
    """Lifecycle of one outbound message."""

    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    BOUNCED = "bounced"
    COMPLAINED = "complained"
    CANCELLED = "cancelled"
    # A frozen campaign recipient. Held rows are never claimed by the send
    # worker; the campaign launch step moves them to ``queued``.
    HELD = "held"


class EmailCampaignStatus(Enum):
    """Lifecycle of one email campaign.

    Only ``draft`` is reachable while sending is behind its feature flag.
    Transitions are enforced by ``api.services.email.campaign_state``.
    """

    DRAFT = "draft"
    READY = "ready"  # sender, template and audience chosen; preview has recipients
    LAUNCHING = "launching"  # audience frozen, recipient rows created as held
    SENDING = "sending"  # held rows released to queued for the worker
    PAUSED = "paused"  # worker stops claiming rows; queued rows stay queued
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class EmailCampaignAudienceType(Enum):
    """How a campaign's audience rule refers to leads."""

    LEADS = "leads"  # explicit lead ids
    PROJECT = "project"
    TAG = "tag"
    FILTER = "filter"  # the leads-list filter object, all matches


class EmailCampaignConsentPolicy(Enum):
    """Which consent states an audience may include."""

    OPTED_IN_ONLY = "opted_in_only"
    # Including unknown consent needs a written reason and a visible warning.
    INCLUDE_UNKNOWN = "include_unknown"


class EmailSuppressionReason(Enum):
    """Why an address must not receive further marketing email."""

    UNSUBSCRIBED = "unsubscribed"
    HARD_BOUNCE = "hard_bounce"
    COMPLAINT = "complaint"
    MANUAL = "manual"


class LeadEmailConsent(Enum):
    """Marketing-email consent recorded for a lead."""

    UNKNOWN = "unknown"  # No consent captured yet; never marketing-eligible
    OPTED_IN = "opted_in"
    OPTED_OUT = "opted_out"


class CredentialPurpose(Enum):
    """What an external credential is for, so lists stay separate."""

    WEBHOOK = "webhook"
    EMAIL_SENDER = "email_sender"
