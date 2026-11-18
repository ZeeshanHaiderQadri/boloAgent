"""Amazon SES v2 adapter.

Request signing is delegated to ``aioboto3`` (boto3's async facade), so this
module never hand-rolls SigV4. Messages go out as **raw MIME**
(``Content.Raw``) rather than SES's templated fields: the headers built
upstream — ``List-Unsubscribe``, ``List-Unsubscribe-Post`` — must reach the
recipient exactly as written.

BYO sender: credentials are passed explicitly to the client. The adapter never
falls back to an instance role or a shared profile, so one organization's mail
can never be sent through the platform's own AWS account.

Required IAM permissions: ``ses:SendEmail``. ``verify()`` additionally needs
``ses:GetAccount`` and ``ses:GetEmailIdentity``.

Sandbox detection: a fresh SES account is limited to 200 messages/day and
1/second. That quota is how the adapter recognises sandbox mode; the account is
still a legal sender, so ``verify()`` succeeds and ``sandbox_mode`` is exposed
for the UI to explain that only verified recipients can receive mail.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from typing import Any, ClassVar

import aioboto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from api.enums import EmailProvider
from api.services.email.adapter import (
    EmailAdapter,
    EmailAdapterError,
    EmailConfigurationError,
    EmailDeliveryError,
    EmailSenderConfig,
    OutboundEmail,
    ProviderSendResult,
)
from api.services.email.mime import build_mime_message, serialize_mime
from api.utils.url_security import (
    ServiceHostResolutionError,
    resolve_user_configured_service_host,
)

# The SES sandbox is exactly 200 messages per 24 hours. A higher quota means the
# account has production access; anything at or below it is treated as sandbox.
SES_SANDBOX_DAILY_LIMIT = 200.0

# Regions look like eu-west-1, ap-southeast-3, us-gov-west-1, cn-north-1.
# Validating the shape stops a settings value from smuggling a host or port into
# the endpoint name that the egress policy then checks.
_REGION_PATTERN = re.compile(r"[a-z]{2,4}(?:-[a-z]+){1,3}-\d{1,2}")
_CONFIGURATION_SET_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")

_CONNECT_TIMEOUT_SECONDS = 10
_READ_TIMEOUT_SECONDS = 30

# Throttling and quota exhaustion are temporary: the provider will accept the
# message later, so the queue should retry with backoff.
_TRANSIENT_ERROR_CODES = frozenset(
    {
        "Throttling",
        "ThrottlingException",
        "TooManyRequests",
        "TooManyRequestsException",
        "LimitExceeded",
        "LimitExceededException",
        "RequestTimeout",
        "SendingPausedException",
        "AccountSendingPausedException",
        "ServiceUnavailable",
        "ServiceUnavailableException",
        "InternalFailure",
        "InternalServerError",
        "InternalServerErrorException",
    }
)

# Retrying cannot fix these: the sender, message, or credential is rejected.
_PERMANENT_ERROR_CODES = frozenset(
    {
        "AccessDenied",
        "AccessDeniedException",
        "AccountSuspendedException",
        "ConfigurationSetDoesNotExist",
        "ConfigurationSetDoesNotExistException",
        "ExpiredToken",
        "ExpiredTokenException",
        "InvalidAccessKeyId",
        "InvalidClientTokenId",
        "InvalidParameterValue",
        "InvalidParameterValueException",
        "MailFromDomainNotVerified",
        "MailFromDomainNotVerifiedException",
        "MessageRejected",
        "NotFoundException",
        "SignatureDoesNotMatch",
        "UnrecognizedClientException",
        "ValidationException",
    }
)

_MISSING_CREDENTIAL_ERRORS = frozenset(
    {"NoCredentialsError", "PartialCredentialsError", "CredentialUnavailableError"}
)

_TRANSIENT_CONNECTION_ERRORS = frozenset(
    {
        "ConnectionClosedError",
        "ConnectTimeoutError",
        "EndpointConnectionError",
        "HTTPClientError",
        "ProxyConnectionError",
        "ReadTimeoutError",
    }
)


class SesEmailAdapter(EmailAdapter):
    """Amazon SES v2 sender that transmits pre-rendered raw MIME."""

    provider: ClassVar[EmailProvider] = EmailProvider.SES

    def __init__(self, config: EmailSenderConfig, credentials: Mapping[str, Any]):
        super().__init__(config, credentials)

        settings = config.settings or {}
        region = str(settings.get("region") or "").strip().lower()
        if not region:
            raise EmailConfigurationError("SES settings must include a region")
        if not _REGION_PATTERN.fullmatch(region):
            raise EmailConfigurationError(
                "SES region looks invalid (expected e.g. eu-west-1)"
            )
        self._region = region

        configuration_set = str(settings.get("configuration_set") or "").strip()
        if configuration_set and not _CONFIGURATION_SET_PATTERN.fullmatch(
            configuration_set
        ):
            raise EmailConfigurationError(
                "SES configuration_set may only contain letters, digits, "
                "underscores and hyphens"
            )
        self._configuration_set = configuration_set or None

        access_key_id = str(credentials.get("access_key_id") or "").strip()
        secret_access_key = str(credentials.get("secret_access_key") or "")
        session_token = str(credentials.get("session_token") or "").strip()
        if not access_key_id or not secret_access_key:
            raise EmailConfigurationError(
                "SES credentials must include an access_key_id and secret_access_key"
            )
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._session_token = session_token or None

        # The adapter owns retries; botocore's default would retry four times
        # behind the queue's back and could duplicate a hand-off.
        self._client_config = Config(
            retries={"total_max_attempts": 1},
            connect_timeout=_CONNECT_TIMEOUT_SECONDS,
            read_timeout=_READ_TIMEOUT_SECONDS,
        )
        self._session = aioboto3.Session()

        self._sandbox_mode: bool | None = None
        self._send_quota: dict[str, float] | None = None

    @property
    def sandbox_mode(self) -> bool | None:
        """``True``/``False`` after :meth:`verify`; ``None`` before it."""
        return self._sandbox_mode

    @property
    def send_quota(self) -> dict[str, float] | None:
        """Last quota read by :meth:`verify`, if any."""
        return dict(self._send_quota) if self._send_quota is not None else None

    @property
    def endpoint_host(self) -> str:
        suffix = (
            ".amazonaws.com.cn" if self._region.startswith("cn-") else ".amazonaws.com"
        )
        return f"email.{self._region}{suffix}"

    async def verify(self) -> None:
        await self._assert_egress_allowed()
        try:
            async with self._client() as client:
                account = await client.get_account()
                identity_verified = await self._identity_is_verified(
                    client, self._config.from_email
                )
                if not identity_verified:
                    domain = self._config.from_email.rpartition("@")[2]
                    identity_verified = bool(
                        domain
                    ) and await self._identity_is_verified(client, domain)
        except ClientError as exc:
            raise self._map_client_error(exc) from exc
        except BotoCoreError as exc:
            raise self._map_botocore_error(exc) from exc

        if not account.get("SendingEnabled", False):
            raise EmailConfigurationError("SES account has sending disabled")

        quota = account.get("SendQuota") or {}
        try:
            self._send_quota = {
                "max_24_hour_send": float(quota.get("Max24HourSend") or 0.0),
                "max_send_rate": float(quota.get("MaxSendRate") or 0.0),
                "sent_last_24_hours": float(quota.get("SentLast24Hours") or 0.0),
            }
        except (TypeError, ValueError):
            raise EmailConfigurationError(
                "SES returned a malformed sending quota"
            ) from None
        self._sandbox_mode = (
            self._send_quota["max_24_hour_send"] <= SES_SANDBOX_DAILY_LIMIT
        )

        if not identity_verified:
            raise EmailConfigurationError("SES sender identity is not verified")

    async def send(self, message: OutboundEmail) -> ProviderSendResult:
        await self._assert_egress_allowed()
        mime = build_mime_message(self._config, message)

        request: dict[str, Any] = {
            "FromEmailAddress": self._config.from_email,
            "Destination": {"ToAddresses": [message.to_email]},
            "Content": {"Raw": {"Data": serialize_mime(mime)}},
        }
        if self._configuration_set:
            request["ConfigurationSetName"] = self._configuration_set

        try:
            async with self._client() as client:
                response = await client.send_email(**request)
        except ClientError as exc:
            raise self._map_client_error(exc) from exc
        except BotoCoreError as exc:
            raise self._map_botocore_error(exc) from exc

        message_id = str(response.get("MessageId") or "").strip()
        if not message_id:
            raise EmailDeliveryError("SES returned no message id")
        return ProviderSendResult(provider_message_id=message_id)

    def _client(self) -> Any:
        kwargs: dict[str, Any] = {
            "region_name": self._region,
            "aws_access_key_id": self._access_key_id,
            "aws_secret_access_key": self._secret_access_key,
            "config": self._client_config,
        }
        if self._session_token:
            kwargs["aws_session_token"] = self._session_token
        return self._session.client("sesv2", **kwargs)

    async def _assert_egress_allowed(self) -> None:
        """Apply the shared egress policy to the SES endpoint.

        The host is derived from a validated region rather than a free-form
        setting, so there is no attacker-controlled name to rebind; the check
        runs per call to keep one policy for every outbound destination. DNS
        resolution is offloaded so the event loop is never blocked.
        """
        try:
            await asyncio.to_thread(
                resolve_user_configured_service_host,
                self.endpoint_host,
                field_name="SES endpoint",
                port=443,
            )
        except ServiceHostResolutionError as exc:
            raise EmailDeliveryError("SES endpoint could not be resolved") from exc
        except ValueError as exc:
            raise EmailConfigurationError(str(exc)) from exc

    async def _identity_is_verified(self, client: Any, identity: str) -> bool:
        try:
            response = await client.get_email_identity(EmailIdentity=identity)
        except ClientError as exc:
            if _error_code(exc) == "NotFoundException":
                return False
            raise
        return bool(response.get("VerifiedForSendingStatus"))

    @staticmethod
    def _map_client_error(exc: ClientError) -> EmailAdapterError:
        code = _error_code(exc)
        status = _error_status(exc)
        if code in _TRANSIENT_ERROR_CODES:
            return EmailDeliveryError(f"SES temporary failure: {code}")
        if code in _PERMANENT_ERROR_CODES:
            return EmailConfigurationError(f"SES rejected the request: {code}")
        if status in {408, 429} or status >= 500:
            return EmailDeliveryError(f"SES temporary failure: HTTP {status}")
        if status >= 400:
            # A 4xx response means SES did not accept the message, so failing
            # the message outright cannot duplicate a delivery.
            return EmailConfigurationError(
                f"SES rejected the request: {code or status}"
            )
        return EmailDeliveryError(f"SES request failed: {code or 'unknown error'}")

    @staticmethod
    def _map_botocore_error(exc: BotoCoreError) -> EmailAdapterError:
        name = type(exc).__name__
        if name in _MISSING_CREDENTIAL_ERRORS:
            return EmailConfigurationError("SES credentials are missing or incomplete")
        if name in _TRANSIENT_CONNECTION_ERRORS:
            return EmailDeliveryError(f"SES connection failed: {name}")
        return EmailDeliveryError(f"SES request failed: {name}")


def _error_code(exc: ClientError) -> str:
    error = exc.response.get("Error") if isinstance(exc.response, Mapping) else None
    if not isinstance(error, Mapping):
        return ""
    return str(error.get("Code") or "")


def _error_status(exc: ClientError) -> int:
    metadata = (
        exc.response.get("ResponseMetadata")
        if isinstance(exc.response, Mapping)
        else None
    )
    if not isinstance(metadata, Mapping):
        return 0
    try:
        return int(metadata.get("HTTPStatusCode") or 0)
    except (TypeError, ValueError):
        return 0
