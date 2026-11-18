"""Unit tests for the SES v2 adapter. No AWS SDK call leaves the process.

``aioboto3.Session`` is replaced with a fake whose ``client`` returns a fake
SES v2 client; the real botocore ``Config`` and exception types are still used,
so the assertions cover the settings and error mapping that matter.
"""

from typing import Any

import pytest
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
    ParamValidationError,
)

from api.enums import EmailProvider
from api.services.email import ses as ses_module
from api.services.email.adapter import (
    EmailConfigurationError,
    EmailDeliveryError,
    EmailSenderConfig,
    OutboundEmail,
)
from api.services.email.ses import SES_SANDBOX_DAILY_LIMIT, SesEmailAdapter
from api.utils.url_security import ServiceHostResolutionError

RECORDS: dict[str, Any] = {}


def client_error(code: str, status: int = 400) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "test"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "SendEmail",
    )


class FakeSESClient:
    async def __aenter__(self) -> "FakeSESClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        return False

    async def send_email(self, **kwargs: Any) -> dict[str, Any]:
        RECORDS["send_email"] = kwargs
        error = RECORDS.get("send_error")
        if error is not None:
            raise error
        return RECORDS.get("send_response", {"MessageId": "ses-message-1"})

    async def get_account(self) -> dict[str, Any]:
        error = RECORDS.get("account_error")
        if error is not None:
            raise error
        return RECORDS["account"]

    async def get_email_identity(self, *, EmailIdentity: str) -> dict[str, Any]:
        RECORDS.setdefault("identity_lookups", []).append(EmailIdentity)
        identities = RECORDS["identities"]
        if EmailIdentity not in identities:
            raise client_error("NotFoundException", status=404)
        value = identities[EmailIdentity]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, dict):
            return value
        return {"VerifiedForSendingStatus": bool(value)}


class FakeSession:
    def client(self, service_name: str, **kwargs: Any) -> FakeSESClient:
        RECORDS["client"] = {"service": service_name, **kwargs}
        return FakeSESClient()


@pytest.fixture(autouse=True)
def fake_aws(monkeypatch: pytest.MonkeyPatch):
    RECORDS.clear()
    RECORDS["account"] = {
        "SendingEnabled": True,
        "SendQuota": {
            "Max24HourSend": 200.0,
            "MaxSendRate": 1.0,
            "SentLast24Hours": 3.0,
        },
    }
    RECORDS["identities"] = {"hello@example.com": True}
    monkeypatch.setattr(ses_module.aioboto3, "Session", FakeSession)
    yield
    RECORDS.clear()


def make_adapter(
    *,
    settings: dict[str, Any] | None = None,
    credentials: dict[str, Any] | None = None,
) -> SesEmailAdapter:
    config = EmailSenderConfig(
        provider=EmailProvider.SES,
        from_name="Bolo",
        from_email="hello@example.com",
        settings={"region": "eu-west-1", **(settings or {})},
    )
    return SesEmailAdapter(
        config,
        credentials
        if credentials is not None
        else {
            "access_key_id": "AKIA_TEST_KEY",
            "secret_access_key": "test-secret",
        },
    )


def outbound(**overrides: Any) -> OutboundEmail:
    values: dict[str, Any] = {
        "to_email": "customer@example.net",
        "to_name": "Customer",
        "subject": "Your receipt",
        "html_body": "<p>Thanks!</p>",
        "text_body": "Thanks!",
    }
    values.update(overrides)
    return OutboundEmail(**values)


async def test_send_uses_ses_v2_with_explicit_credentials_and_no_retries():
    adapter = make_adapter()

    result = await adapter.send(outbound())

    assert result.provider_message_id == "ses-message-1"
    client = RECORDS["client"]
    assert client["service"] == "sesv2"
    assert client["region_name"] == "eu-west-1"
    assert client["aws_access_key_id"] == "AKIA_TEST_KEY"
    assert client["aws_secret_access_key"] == "test-secret"
    assert "aws_session_token" not in client
    assert client["config"].retries.get("total_max_attempts") == 1


async def test_send_passes_a_session_token_when_supplied():
    adapter = make_adapter(
        credentials={
            "access_key_id": "AKIA_TEST_KEY",
            "secret_access_key": "test-secret",
            "session_token": "temporary-token",
        }
    )

    await adapter.send(outbound())

    assert RECORDS["client"]["aws_session_token"] == "temporary-token"


async def test_send_transmits_exact_raw_mime_headers():
    adapter = make_adapter()

    await adapter.send(
        outbound(
            headers={
                "List-Unsubscribe": "<https://example.com/u/1>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            }
        )
    )

    request = RECORDS["send_email"]
    raw = request["Content"]["Raw"]["Data"]
    assert isinstance(raw, bytes)
    assert b"\r\n" in raw
    text = raw.decode("utf-8", "replace")
    assert "Subject: Your receipt" in text
    assert "List-Unsubscribe: <https://example.com/u/1>" in text
    assert "List-Unsubscribe-Post: List-Unsubscribe=One-Click" in text
    assert "Message-ID: <" in text
    assert "Bolo <hello@example.com>" in text
    assert request["FromEmailAddress"] == "hello@example.com"
    assert request["Destination"] == {"ToAddresses": ["customer@example.net"]}
    assert "multipart/alternative" in text


async def test_send_omits_the_configuration_set_unless_configured():
    adapter = make_adapter()
    await adapter.send(outbound())
    assert "ConfigurationSetName" not in RECORDS["send_email"]

    RECORDS.clear()
    configured = make_adapter(settings={"configuration_set": "bolo-transactional"})
    await configured.send(outbound())
    assert RECORDS["send_email"]["ConfigurationSetName"] == "bolo-transactional"


async def test_html_only_body_is_sent_as_html():
    adapter = make_adapter()

    await adapter.send(outbound(text_body=None))

    text = RECORDS["send_email"]["Content"]["Raw"]["Data"].decode("utf-8", "replace")
    assert "Content-Type: text/html" in text
    assert "multipart/alternative" not in text


async def test_header_injection_is_a_configuration_error():
    adapter = make_adapter()

    with pytest.raises(EmailConfigurationError, match="Header List-Unsubscribe"):
        await adapter.send(outbound(headers={"List-Unsubscribe": "a\r\nBcc: x@y.z"}))


async def test_bcc_headers_are_reserved_and_dropped():
    adapter = make_adapter()

    await adapter.send(
        outbound(
            headers={"Bcc": "hidden@example.net", "Resent-Bcc": "hidden@example.net"}
        )
    )

    text = RECORDS["send_email"]["Content"]["Raw"]["Data"].decode("utf-8", "replace")
    assert "hidden@example.net" not in text
    assert "Bcc:" not in text


async def test_missing_message_id_is_retryable():
    adapter = make_adapter()
    RECORDS["send_response"] = {}

    with pytest.raises(EmailDeliveryError, match="no message id"):
        await adapter.send(outbound())


@pytest.mark.parametrize(
    "code",
    [
        "ThrottlingException",
        "TooManyRequestsException",
        "LimitExceededException",
        "SendingPausedException",
        "ConfigurationSetSendingPausedException",
        "ServiceUnavailable",
        "RequestTimeout",
    ],
)
async def test_throttling_and_quota_errors_are_retryable(code: str):
    adapter = make_adapter()
    RECORDS["send_error"] = client_error(code)

    with pytest.raises(EmailDeliveryError, match=code):
        await adapter.send(outbound())


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("UnrecognizedClientException", 403),
        ("InvalidClientTokenId", 403),
        ("SignatureDoesNotMatch", 403),
        ("AccessDeniedException", 403),
        ("MessageRejected", 400),
        ("ConfigurationSetDoesNotExist", 400),
        ("ValidationException", 400),
    ],
)
async def test_bad_credentials_and_rejections_are_configuration_errors(
    code: str, status: int
):
    adapter = make_adapter()
    RECORDS["send_error"] = client_error(code, status=status)

    with pytest.raises(EmailConfigurationError, match=code):
        await adapter.send(outbound())


async def test_unknown_status_classes_are_mapped_by_http_status():
    adapter = make_adapter()
    RECORDS["send_error"] = client_error("SomethingNew", status=400)
    with pytest.raises(EmailConfigurationError):
        await adapter.send(outbound())

    RECORDS["send_error"] = client_error("SomethingNew", status=503)
    with pytest.raises(EmailDeliveryError):
        await adapter.send(outbound())


async def test_missing_credentials_from_botocore_are_a_configuration_error():
    adapter = make_adapter()
    RECORDS["send_error"] = NoCredentialsError()

    with pytest.raises(EmailConfigurationError, match="missing or incomplete"):
        await adapter.send(outbound())


async def test_connection_failures_are_retryable():
    adapter = make_adapter()
    RECORDS["send_error"] = EndpointConnectionError(
        endpoint_url="https://email.eu-west-1.amazonaws.com"
    )

    with pytest.raises(EmailDeliveryError, match="SES connection failed"):
        await adapter.send(outbound())


async def test_parameter_validation_is_a_configuration_error():
    adapter = make_adapter()
    RECORDS["send_error"] = ParamValidationError(
        report="Invalid type for parameter Destination"
    )

    with pytest.raises(EmailConfigurationError, match="parameters are invalid"):
        await adapter.send(outbound())


async def test_verify_detects_sandbox_mode_from_the_account_quota():
    adapter = make_adapter()

    await adapter.verify()

    assert adapter.sandbox_mode is True
    assert adapter.send_quota == {
        "max_24_hour_send": 200.0,
        "max_send_rate": 1.0,
        "sent_last_24_hours": 3.0,
    }
    assert SES_SANDBOX_DAILY_LIMIT == 200.0
    assert RECORDS["identity_lookups"] == ["hello@example.com"]


async def test_verify_reports_production_access_above_the_sandbox_quota():
    adapter = make_adapter()
    RECORDS["account"]["SendQuota"]["Max24HourSend"] = 50_000.0
    RECORDS["account"]["SendQuota"]["MaxSendRate"] = 14.0

    await adapter.verify()

    assert adapter.sandbox_mode is False


async def test_verify_fails_when_account_sending_is_disabled():
    adapter = make_adapter()
    RECORDS["account"]["SendingEnabled"] = False

    with pytest.raises(EmailConfigurationError, match="sending disabled"):
        await adapter.verify()


async def test_verify_falls_back_from_address_to_domain_identity():
    adapter = make_adapter()
    RECORDS["identities"] = {"example.com": True}

    await adapter.verify()

    assert RECORDS["identity_lookups"] == ["hello@example.com", "example.com"]
    assert adapter.sandbox_mode is True


async def test_verify_fails_when_the_identity_is_not_verified():
    adapter = make_adapter()
    RECORDS["identities"] = {"hello@example.com": False, "example.com": False}

    with pytest.raises(EmailConfigurationError, match="not verified"):
        await adapter.verify()


async def test_verify_maps_account_errors():
    adapter = make_adapter()
    RECORDS["account_error"] = client_error("ThrottlingException")
    with pytest.raises(EmailDeliveryError):
        await adapter.verify()

    RECORDS["account_error"] = client_error("AccessDeniedException", status=403)
    with pytest.raises(EmailConfigurationError):
        await adapter.verify()


async def test_saas_egress_policy_rejection_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = make_adapter()
    calls: list[tuple[str, dict[str, Any]]] = []

    def reject(host: str, **kwargs: Any) -> None:
        calls.append((host, kwargs))
        raise ValueError(
            "SES endpoint must resolve to a public IP address when OUTBOUND_NETWORK_POLICY=strict"
        )

    monkeypatch.setattr(ses_module, "resolve_user_configured_service_host", reject)

    with pytest.raises(EmailConfigurationError, match="public IP address"):
        await adapter.send(outbound())
    assert calls[0][0] == "email.eu-west-1.amazonaws.com"
    assert calls[0][1]["port"] == 443


async def test_saas_egress_policy_dns_failure_is_retryable(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = make_adapter()

    def unresolved(host: str, **kwargs: Any) -> None:
        raise ServiceHostResolutionError("Could not resolve service URL hostname")

    monkeypatch.setattr(ses_module, "resolve_user_configured_service_host", unresolved)

    with pytest.raises(EmailDeliveryError, match="could not be resolved"):
        await adapter.send(outbound())


def test_china_regions_use_the_china_endpoint_suffix():
    assert (
        make_adapter(settings={"region": "cn-north-1"}).endpoint_host
        == "email.cn-north-1.amazonaws.com.cn"
    )


@pytest.mark.parametrize(
    "settings",
    [
        {"region": ""},
        {"region": "eu-west-1.example.com"},
        {"region": "eu-west-1:8080"},
        {"configuration_set": "bad set!"},
        {"configuration_set": "x" * 65},
    ],
)
def test_invalid_settings_are_configuration_errors(settings: dict[str, Any]):
    with pytest.raises(EmailConfigurationError):
        make_adapter(settings=settings)


@pytest.mark.parametrize(
    "credentials",
    [
        {},
        {"access_key_id": "AKIA_TEST_KEY"},
        {"secret_access_key": "test-secret"},
    ],
)
def test_missing_credentials_are_configuration_errors(credentials: dict[str, Any]):
    with pytest.raises(EmailConfigurationError, match="access_key_id"):
        make_adapter(credentials=credentials)


@pytest.mark.asyncio
async def test_domain_verification_reports_dkim_tokens():
    RECORDS["identities"]["example.com"] = {
        "VerifiedForSendingStatus": True,
        "VerificationStatus": "Success",
        "DkimAttributes": {"Tokens": ["token-a", "token-b"]},
        "MailFromAttributes": {"MailFromDomainStatus": "Success"},
    }
    adapter = make_adapter()

    result = await adapter.get_domain_verification("example.com")

    assert result["verified"] is True
    assert result["verification_status"] == "Success"
    assert result["details"] == {
        "verification_status": "Success",
        "dkim_tokens": ["token-a", "token-b"],
        "mail_from_status": "Success",
    }


@pytest.mark.asyncio
async def test_missing_domain_identity_is_reported_not_raised():
    adapter = make_adapter()

    result = await adapter.get_domain_verification("new.example.com")

    assert result == {
        "verified": False,
        "verification_status": "NotFound",
        "details": {"verification_status": "NotFound", "dkim_tokens": []},
    }


@pytest.mark.asyncio
async def test_domain_verification_errors_use_the_shared_mapping():
    RECORDS["identities"]["example.com"] = client_error("AccessDenied", status=403)
    adapter = make_adapter()

    with pytest.raises(EmailConfigurationError, match="AccessDenied"):
        await adapter.get_domain_verification("example.com")
