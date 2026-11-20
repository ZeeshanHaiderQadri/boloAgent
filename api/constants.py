import os
from pathlib import Path

from api.enums import Environment

# Normalized once, here: every consumer compares against lowercase enum values,
# so " Production " and "production" must mean the same environment.
ENVIRONMENT = (os.getenv("ENVIRONMENT") or Environment.LOCAL.value).strip().lower()
# Absolute path to the project root directory (i.e. the directory containing
# the top-level api/ package). Having a single canonical location helps
# when constructing file-system paths elsewhere in the codebase.
APP_ROOT_DIR: Path = Path(__file__).resolve().parent

VOICEMAIL_RECORDING_DURATION = 5.0

# Shown in the UI and stored with every consent activity. Keep this as the one
# source of truth: ``scripts/sync_email_consent_warning.py`` copies it into the
# UI, and ``api/tests/test_shared_email_consent_warning.py`` fails if they drift.
EMAIL_CONSENT_WARNING = (
    "Only record consent you can prove. Marketing email without permission can "
    "breach anti-spam law and damage your sending domain."
)

# Campaign sending is off by default. The launch endpoint answers 403 while
# this is false. When true, launch freezes the audience, queues recipient rows
# and the ARQ worker delivers them through the campaign's verified sender.
EMAIL_CAMPAIGN_SENDING_ENABLED = os.getenv(
    "EMAIL_CAMPAIGN_SENDING_ENABLED", "false"
).strip().lower() in {"1", "true", "yes"}

# Langfuse Configuration
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST")
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY")
LANGFUSE_PROJECT_ID = os.getenv("LANGFUSE_PROJECT_ID")

# Tracing as a whole is optional, but a half-configured Langfuse silently
# produces dead trace links, so fail loudly at import instead.
if all([LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY]) and not (
    LANGFUSE_PROJECT_ID
):
    raise RuntimeError(
        "LANGFUSE_PROJECT_ID is required when LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY "
        "and LANGFUSE_SECRET_KEY are set. Find it in your Langfuse project URL "
        "(/project/<project_id>/...) or via GET <host>/api/public/projects."
    )

# URLs for deployment
#
# PUBLIC_BASE_URL is the single canonical origin a deployment is reached at
# (scheme + host, e.g. https://203-0-113-10.sslip.io). For a standard single-host
# install it is the only endpoint value an operator sets — the per-subsystem URLs
# below derive from it (and from PUBLIC_HOST for the TURN/ICE host). Each derived
# var can still be set explicitly to override it for a split deployment.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL") or None
PUBLIC_HOST = os.getenv("PUBLIC_HOST") or None

# The server's own IPv4 address, as the operator supplied it to the setup
# script. Usually identical to PUBLIC_HOST, but kept distinct because it must
# stay a raw IP (coturn's external-ip needs one) while PUBLIC_HOST may be a
# hostname such as an sslip.io name. resolve_ice_filter_policies() classifies
# it to detect a private-LAN or CGNAT deployment (e.g. Tailscale, no public
# IP) and pick ICE candidate filtering accordingly, so an empty value here is
# read as "public deployment" — every consumer of this must be wired through
# docker-compose, or that detection silently misfires.
SERVER_IP = os.getenv("SERVER_IP", "")

# Public URL the backend builds webhook/callback/embed links from. Derives from
# PUBLIC_BASE_URL (public IP / domain), falling back to localhost for local dev.
# When this is a non-public address (localhost or a private/reserved IP) the host
# isn't reachable from the internet, so get_backend_endpoints() resolves a running
# Cloudflare tunnel's URL at runtime instead (see api/utils/common.py).
BACKEND_API_ENDPOINT = (
    os.getenv("BACKEND_API_ENDPOINT") or PUBLIC_BASE_URL or "http://localhost:8000"
)
UI_APP_URL = os.getenv("UI_APP_URL", "http://localhost:3010")

DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]


def is_prompt_cache_disabled() -> bool:
    """Global kill switch, checked before cache creation or attachment."""
    return os.getenv("BOLO_PROMPT_CACHE_DISABLED", "").lower() in {"1", "true"}


_DEPLOYMENT_MODE_ENV = os.getenv("DEPLOYMENT_MODE")
_EXPLICIT_DEPLOYMENT_MODE = (_DEPLOYMENT_MODE_ENV or "").strip()
DEPLOYMENT_MODE = _EXPLICIT_DEPLOYMENT_MODE.lower() or "oss"
if DEPLOYMENT_MODE not in {"oss", "saas"}:
    raise RuntimeError(
        f"DEPLOYMENT_MODE must be 'oss' or 'saas' (got {_DEPLOYMENT_MODE_ENV!r})."
    )
if ENVIRONMENT == Environment.PRODUCTION.value and not _EXPLICIT_DEPLOYMENT_MODE:
    # The default (oss) turns off SaaS-only behaviour. A hosted deployment that
    # silently inherits that default is a security incident waiting to happen,
    # so production must choose explicitly. Egress blocking is controlled
    # separately by OUTBOUND_NETWORK_POLICY (below).
    raise RuntimeError(
        "DEPLOYMENT_MODE must be set explicitly when ENVIRONMENT=production. "
        "Use 'oss' for self-hosted installs or 'saas' for a hosted deployment."
    )

# Outbound network policy for user-configured destinations (model URLs, SMTP
# hosts and ports). This is deliberately independent of DEPLOYMENT_MODE:
#   * "strict" resolves user-configured hosts and rejects private, loopback,
#     link-local, CGNAT and reserved addresses, and limits SMTP to standard
#     submission ports.
#   * "off" leaves those destinations unrestricted, which self-hosted installs
#     need for local model servers and private SMTP relays.
# The default preserves the previous behaviour: strict in saas, off in oss.
# A customer-facing self-hosted install should set DEPLOYMENT_MODE=oss plus
# OUTBOUND_NETWORK_POLICY=strict explicitly.
_OUTBOUND_NETWORK_POLICY_ENV = os.getenv("OUTBOUND_NETWORK_POLICY")
_EXPLICIT_OUTBOUND_NETWORK_POLICY = (_OUTBOUND_NETWORK_POLICY_ENV or "").strip().lower()
OUTBOUND_NETWORK_POLICY = _EXPLICIT_OUTBOUND_NETWORK_POLICY or (
    "strict" if DEPLOYMENT_MODE == "saas" else "off"
)
if OUTBOUND_NETWORK_POLICY not in {"strict", "off"}:
    raise RuntimeError(
        "OUTBOUND_NETWORK_POLICY must be 'strict' or 'off' "
        f"(got {_OUTBOUND_NETWORK_POLICY_ENV!r})."
    )
CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
]
AUTH_PROVIDER = os.getenv("AUTH_PROVIDER", "local")
ENABLE_SIGNUP = os.getenv("ENABLE_SIGNUP", "true").lower() == "true"
# Stack Auth public client config. These are safe to expose to the browser (the
# publishable client key is public by design, and the project id is non-sensitive),
# and are served to the UI at runtime via /api/v1/health so the frontend no longer
# needs them baked into the bundle at build time.
STACK_AUTH_PROJECT_ID = os.getenv("STACK_AUTH_PROJECT_ID")
STACK_PUBLISHABLE_CLIENT_KEY = os.getenv("STACK_PUBLISHABLE_CLIENT_KEY")
BOLO_MPS_SECRET_KEY = os.getenv("BOLO_MPS_SECRET_KEY", None)
# Host of the upstream Managed Platform Services (MPS). This is an external
# service that Bolo consumes; it is not part of this repository. Override it to
# use a self-hosted MPS deployment instead.
MPS_API_URL = os.getenv("MPS_API_URL", "https://services.dograh.com")
BOLO_DEPLOYMENT_ID = os.getenv(
    "BOLO_DEPLOYMENT_ID", "oss" if DEPLOYMENT_MODE == "oss" else "us"
)
BOLO_DEVOPS_SECRET = os.getenv("BOLO_DEVOPS_SECRET") or None
ENABLE_PROMETHEUS_METRICS = (
    os.getenv("ENABLE_PROMETHEUS_METRICS", "false").lower() == "true"
)

# Storage Configuration
ENABLE_AWS_S3 = os.getenv("ENABLE_AWS_S3", "false").lower() == "true"

# MinIO Configuration
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
# Full URL (scheme + host) browsers use to reach object storage. Derives from
# PUBLIC_BASE_URL (remote nginx proxies /voice-audio/ to MinIO); set explicitly
# only to point object storage at a separate origin.
MINIO_PUBLIC_ENDPOINT = (
    os.getenv("MINIO_PUBLIC_ENDPOINT") or PUBLIC_BASE_URL or "http://localhost:9000"
)
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "voice-audio")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

# AWS S3 Configuration
S3_BUCKET = os.environ.get("S3_BUCKET")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
# Optional overrides for S3-compatible backends (e.g. MinIO, rustfs, Ceph).
# S3_ENDPOINT_URL: full URL of a custom S3 endpoint (e.g. "https://s3.example.com").
#   Leave unset to use AWS's default endpoint resolution.
# S3_SIGNATURE_VERSION: botocore signature version used to sign requests and
#   presigned URLs. Defaults to None (botocore's default, currently SigV2 for
#   presigned URLs). Set to "s3v4" for S3-compatible servers that require SigV4.
# S3_ADDRESSING_STYLE: "auto" (default), "path", or "virtual". Many S3-compatible
#   servers and TLS setups require "path".
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")
S3_SIGNATURE_VERSION = os.environ.get("S3_SIGNATURE_VERSION")
S3_ADDRESSING_STYLE = os.environ.get("S3_ADDRESSING_STYLE")

# Sentry configuration
SENTRY_DSN = os.getenv("SENTRY_DSN")

# PostHog configuration
POSTHOG_API_KEY = os.getenv("POSTHOG_API_KEY")
POSTHOG_HOST = os.getenv("POSTHOG_HOST", "https://us.i.posthog.com")


ENABLE_ARI_STASIS = os.getenv("ENABLE_ARI_STASIS", "false").lower() == "true"
SERIALIZE_LOG_OUTPUT = os.getenv("SERIALIZE_LOG_OUTPUT", "false").lower() == "true"

# Whether the end-of-call audio recordings (mixed / user / bot tracks) are
# uploaded to object storage. Deployments that must not retain call audio, or
# that simply do not want to pay for the storage, can turn this off. The
# transcript upload and every other artifact are unaffected. Audio is still
# buffered in memory during the call (integrations such as Noveum consume it);
# only the upload and the recording_url / recordings metadata are skipped.
ENABLE_CALL_RECORDING_UPLOAD = (
    os.getenv("ENABLE_CALL_RECORDING_UPLOAD", "true").lower() == "true"
)

# Telephony media WebSocket authentication.
# The carrier/connector dials back the media socket
# /api/v1/telephony/ws/{workflow_id}/{organization_id}/{workflow_run_id}, whose
# id triple is otherwise a guessable bearer capability. When a secret is set,
# providers append an HMAC token to that URL as a trailing path segment (carriers
# strip query strings; ARI is the exception and passes ?token=) and the handler
# verifies it (see api/services/telephony/ws_auth.py).
# Two-phase, backward-compatible rollout:
#   * secret unset            -> feature off, URLs unchanged (default)
#   * secret set, enforce off -> tokens minted + verified; invalid ones only logged
#   * secret set, enforce on  -> invalid/missing tokens rejected (WS close 4401)
TELEPHONY_WS_TOKEN_SECRET = os.getenv("TELEPHONY_WS_TOKEN_SECRET") or None
TELEPHONY_WS_TOKEN_ENFORCE = (
    os.getenv("TELEPHONY_WS_TOKEN_ENFORCE", "false").lower() == "true"
)

# Logging configuration
LOG_FILE_PATH = os.getenv("LOG_FILE_PATH", None)
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()

# Log rotation configuration
LOG_ROTATION_SIZE = os.getenv("LOG_ROTATION_SIZE", "100 MB")
LOG_RETENTION = os.getenv("LOG_RETENTION", "7 days")
LOG_COMPRESSION = os.getenv("LOG_COMPRESSION", "gz")
ENABLE_TELEMETRY = os.getenv("ENABLE_TELEMETRY", "true").lower() == "true"


def _get_version() -> str:
    """Read version from pyproject.toml."""
    try:
        import tomllib

        pyproject_path = APP_ROOT_DIR / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            pyproject = tomllib.load(f)
        return pyproject.get("project", {}).get("version", "dev")
    except Exception:
        return "dev"


# Application version (read from pyproject.toml)
APP_VERSION = _get_version()

# Country code mapping: ISO country code -> international dialing prefix
COUNTRY_CODES = {
    "US": "1",  # United States
    "CA": "1",  # Canada
    "GB": "44",  # United Kingdom
    "IN": "91",  # India
    "AU": "61",  # Australia
    "DE": "49",  # Germany
    "FR": "33",  # France
    "BR": "55",  # Brazil
    "MX": "52",  # Mexico
    "IT": "39",  # Italy
    "ES": "34",  # Spain
    "NL": "31",  # Netherlands
    "SE": "46",  # Sweden
    "NO": "47",  # Norway
    "DK": "45",  # Denmark
    "FI": "358",  # Finland
    "CH": "41",  # Switzerland
    "AT": "43",  # Austria
    "BE": "32",  # Belgium
    "LU": "352",  # Luxembourg
    "IE": "353",  # Ireland
}

# Floor at 1 so a misconfigured env var (0 or negative) can't silently block
# every call in the deployment.
DEFAULT_ORG_CONCURRENCY_LIMIT = max(
    1, int(os.getenv("DEFAULT_ORG_CONCURRENCY_LIMIT", "10"))
)
# Outlast both the dispatcher's 240-second timeout and ARQ's 300-second deadline.
CAMPAIGN_PROCESSING_CLAIM_TIMEOUT_SECONDS = 360

DEFAULT_CAMPAIGN_RETRY_CONFIG = {
    "enabled": True,
    "max_retries": 1,
    "retry_delay_seconds": 120,
    "retry_on_busy": True,
    "retry_on_no_answer": True,
    "retry_on_voicemail": False,
}


# Outbound webhook delivery: bounded retry with exponential backoff.
# Delivery is persisted (see WebhookDeliveryModel) and retried by an ARQ task so a
# transient network error can't permanently drop a final webhook. After
# ``max_attempts`` transient failures the delivery is parked as ``dead_letter``.
DEFAULT_WEBHOOK_DELIVERY_CONFIG = {
    "max_attempts": int(os.getenv("WEBHOOK_DELIVERY_MAX_ATTEMPTS", 5)),
    "base_delay_seconds": int(os.getenv("WEBHOOK_DELIVERY_BASE_DELAY_SECONDS", 30)),
    "max_delay_seconds": int(os.getenv("WEBHOOK_DELIVERY_MAX_DELAY_SECONDS", 600)),
    "timeout_seconds": int(os.getenv("WEBHOOK_DELIVERY_TIMEOUT_SECONDS", 30)),
}

# Text chats have no transport disconnect event, so a periodic worker closes
# sessions that have stopped changing. Workflows can override this default.
MIN_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS = 60
TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS = 1800
TEXT_CHAT_INACTIVITY_SWEEP_INTERVAL_MINUTES = 5
TEXT_CHAT_INACTIVITY_SWEEP_LOOKBACK_SECONDS = 3 * 60 * 60
MAX_TEXT_CHAT_INACTIVITY_TIMEOUT_SECONDS = (
    TEXT_CHAT_INACTIVITY_SWEEP_LOOKBACK_SECONDS
    - TEXT_CHAT_INACTIVITY_SWEEP_INTERVAL_MINUTES * 60
)
TEXT_CHAT_INACTIVITY_SWEEP_PAGE_SIZE = 500
TEXT_CHAT_INACTIVITY_SWEEP_MAX_PAGES = 10
TEXT_CHAT_INACTIVITY_SWEEP_ENQUEUE_LIMIT = 500


# Circuit breaker defaults for campaign call failure detection
DEFAULT_CIRCUIT_BREAKER_CONFIG = {
    "enabled": True,
    "failure_threshold": 0.5,  # 50% failure rate trips the breaker
    "window_seconds": 120,  # 2-minute sliding window
    "min_calls_in_window": 5,  # Don't trip until at least 5 outcomes
}


# Whether this deployment runs a TURN server (coturn).
ENABLE_COTURN = os.getenv("ENABLE_COTURN", "false").lower() == "true"
TURN_SECRET = os.getenv("TURN_SECRET")
# Host browsers dial for TURN/ICE. Derives from PUBLIC_HOST; set explicitly only
# when the TURN server runs on a separate host from the app.
TURN_HOST = os.getenv("TURN_HOST") or PUBLIC_HOST or "localhost"
TURN_PORT = int(os.getenv("TURN_PORT", "3478"))
TURN_TLS_PORT = int(os.getenv("TURN_TLS_PORT", "5349"))
TURN_CREDENTIAL_TTL = int(os.getenv("TURN_CREDENTIAL_TTL", "86400"))
# Diagnostic flag: when true, strip all non-relay ICE candidates from the
# answer SDP so every media path must traverse the TURN server. Use for
# verifying TURN connectivity end-to-end; expect connection failures if
# TURN is misconfigured or unreachable.
FORCE_TURN_RELAY = os.getenv("FORCE_TURN_RELAY", "false").lower() == "true"

# OSS Email/Password Auth
OSS_JWT_SECRET = os.getenv("OSS_JWT_SECRET", "change-me-in-production")
OSS_JWT_EXPIRY_HOURS = int(os.getenv("OSS_JWT_EXPIRY_HOURS", "720"))  # 30 days

# Unsubscribe links are signed so the public route needs no session, and a
# tampered address/campaign pair cannot suppress someone else's email.
EMAIL_UNSUBSCRIBE_SECRET = os.getenv("EMAIL_UNSUBSCRIBE_SECRET") or OSS_JWT_SECRET

TUNER_BASE_URL = os.getenv("TUNER_BASE_URL", "https://api.usetuner.ai")

# Lead Generation -----------------------------------------------------------
# Internal base URL of the gosom/google-maps-scraper web container. The service
# is not authenticated; keep it on the private compose network.
LEADS_SCRAPER_URL = os.getenv(
    "LEADS_SCRAPER_URL", "http://google-maps-scraper:8080"
).rstrip("/")
# Overall budget for one discovery job (the scraper itself takes >= 3 minutes).
LEADS_SCRAPER_TIMEOUT_SECONDS = int(os.getenv("LEADS_SCRAPER_TIMEOUT_SECONDS", "900"))
LEADS_SCRAPER_POLL_INTERVAL_SECONDS = float(
    os.getenv("LEADS_SCRAPER_POLL_INTERVAL_SECONDS", "5")
)
LEADS_MAX_SEARCH_DEPTH = int(os.getenv("LEADS_MAX_SEARCH_DEPTH", "10"))
# The operator asks for this many leads up front; the worker stops the scraper
# as soon as the search has that many results (instead of running to max_time).
LEADS_DEFAULT_TARGET_RESULTS = int(os.getenv("LEADS_DEFAULT_TARGET_RESULTS", "25"))
LEADS_MAX_TARGET_RESULTS = int(os.getenv("LEADS_MAX_TARGET_RESULTS", "1000"))
# "google_maps" submits jobs to the scraper container and is the default;
# "demo" generates deterministic sample businesses and is opt-in only.
LEADS_DEFAULT_PROVIDER = os.getenv("LEADS_DEFAULT_PROVIDER", "google_maps")
LEADS_ENABLE_DEMO_PROVIDER = (
    os.getenv("LEADS_ENABLE_DEMO_PROVIDER", "true").lower() == "true"
)
# Automatic social-profile enrichment fetches each business's own website.
# Operators whose install must not make third-party requests can disable the
# background pass; the per-lead refresh endpoint remains available.
LEADS_SOCIAL_ENRICHMENT_ENABLED = (
    os.getenv("LEADS_SOCIAL_ENRICHMENT_ENABLED", "true").lower() == "true"
)
# Hard caps so a wide filter cannot exhaust memory or fill a PDF/PNG file.
LEADS_EXPORT_MAX_ROWS = int(os.getenv("LEADS_EXPORT_MAX_ROWS", "5000"))
LEADS_EXPORT_MAX_TABLE_ROWS = int(os.getenv("LEADS_EXPORT_MAX_TABLE_ROWS", "250"))
LEADS_DISPATCH_MAX_LEADS = int(os.getenv("LEADS_DISPATCH_MAX_LEADS", "1000"))
