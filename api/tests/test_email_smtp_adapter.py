"""Unit tests for the SMTP adapter. No network access; smtplib is faked."""

import smtplib

import pytest

import api.utils.url_security as url_security
from api.enums import EmailProvider
from api.services.email import smtp as smtp_module
from api.services.email.adapter import (
    EmailConfigurationError,
    EmailDeliveryError,
    EmailSenderConfig,
    OutboundEmail,
)
from api.services.email.smtp import HOSTINGER_SMTP_PRESET, SmtpEmailAdapter

RECORDS: dict[str, object] = {}


class FakeSMTPBase:
    use_ssl = False

    def __init__(self, host, port, timeout=None, context=None):
        RECORDS["opened"] = {
            "host": host,
            "port": port,
            "timeout": timeout,
            "ssl": self.use_ssl,
        }
        RECORDS["starttls"] = 0
        if RECORDS.get("connect_fail"):
            raise OSError("connection refused")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def ehlo(self):
        pass

    def starttls(self, context=None):
        if RECORDS.get("starttls_unsupported"):
            raise smtplib.SMTPNotSupportedError("STARTTLS not supported")
        RECORDS["starttls"] = int(RECORDS["starttls"]) + 1

    def login(self, username, password):
        if RECORDS.get("auth_fail"):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    def noop(self):
        RECORDS["noop"] = int(RECORDS.get("noop", 0)) + 1

    def send_message(self, msg, from_addr=None, to_addrs=None):
        RECORDS["sent"] = (msg, from_addr, to_addrs)
        return {}

    def quit(self):
        pass


class FakeSMTP(FakeSMTPBase):
    use_ssl = False


class FakeSMTPSSL(FakeSMTPBase):
    use_ssl = True


@pytest.fixture(autouse=True)
def fake_smtplib(monkeypatch):
    RECORDS.clear()
    monkeypatch.setattr(smtp_module.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtp_module.smtplib, "SMTP_SSL", FakeSMTPSSL)
    yield RECORDS


def _config(**settings):
    return EmailSenderConfig(
        provider=EmailProvider.SMTP,
        from_name="Bolo",
        from_email="hello@example.test",
        reply_to="support@example.test",
        settings=settings,
    )


def _adapter(settings=None, credentials=None):
    return SmtpEmailAdapter(
        _config(**(settings or {"preset": "hostinger"})),
        credentials or {"username": "user@example.test", "password": "secret"},
    )


def _message(**overrides):
    values = {
        "to_email": "lead@example.test",
        "to_name": "Lead",
        "subject": "Hello",
        "html_body": "<p>Hi</p>",
        "text_body": "Hi",
        "headers": {"List-Unsubscribe": "<https://boloagent.com/u/abc>"},
    }
    values.update(overrides)
    return OutboundEmail(**values)


def test_hostinger_preset_resolves_host_port_and_implicit_tls():
    adapter = _adapter()

    assert adapter.settings == HOSTINGER_SMTP_PRESET
    assert adapter.settings["host"] == "smtp.hostinger.com"
    assert adapter.settings["port"] == 465
    assert adapter.settings["security"] == "ssl"


def test_explicit_settings_override_the_preset():
    adapter = _adapter(
        {
            "preset": "hostinger",
            "host": "mail.example.test",
            "port": 587,
            "security": "starttls",
        }
    )

    assert adapter.settings["host"] == "mail.example.test"
    assert adapter.settings["port"] == 587
    assert adapter.settings["security"] == "starttls"


@pytest.mark.asyncio
async def test_send_delivers_one_message_and_returns_its_message_id(fake_smtplib):
    result = await _adapter().send(_message())

    assert result.provider_message_id.startswith("<")
    assert result.provider_message_id.endswith("@example.test>")

    opened = fake_smtplib["opened"]
    assert opened["ssl"] is True
    assert opened["host"] == "smtp.hostinger.com"
    assert opened["port"] == 465

    msg, from_addr, to_addrs = fake_smtplib["sent"]
    assert from_addr == "hello@example.test"
    assert to_addrs == ["lead@example.test"]
    assert msg["From"] == "Bolo <hello@example.test>"
    assert msg["To"] == "Lead <lead@example.test>"
    assert msg["Subject"] == "Hello"
    assert msg["Reply-To"] == "support@example.test"
    assert msg["List-Unsubscribe"] == "<https://boloagent.com/u/abc>"
    assert msg["Message-ID"] == result.provider_message_id

    content_types = [part.get_content_type() for part in msg.iter_parts()]
    assert content_types == ["text/plain", "text/html"]


@pytest.mark.asyncio
async def test_html_only_message_stays_single_part(fake_smtplib):
    await _adapter().send(_message(text_body=None))

    msg = fake_smtplib["sent"][0]
    assert msg.get_content_type() == "text/html"
    assert not msg.is_multipart()


@pytest.mark.asyncio
async def test_verify_authenticates_without_sending(fake_smtplib):
    await _adapter().verify()

    assert fake_smtplib["noop"] == 1
    assert "sent" not in fake_smtplib


@pytest.mark.asyncio
async def test_authentication_failure_is_a_configuration_error(fake_smtplib):
    fake_smtplib["auth_fail"] = True

    with pytest.raises(EmailConfigurationError) as exc_info:
        await _adapter().send(_message())

    assert "secret" not in str(exc_info.value)
    assert "password" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_connection_failure_is_a_delivery_error(fake_smtplib):
    fake_smtplib["connect_fail"] = True

    with pytest.raises(EmailDeliveryError):
        await _adapter().send(_message())


def test_missing_credentials_are_rejected_before_connecting():
    with pytest.raises(EmailConfigurationError, match="username and password"):
        SmtpEmailAdapter(
            _config(preset="hostinger"),
            {"username": "user@example.test", "password": ""},
        )


def test_missing_host_is_rejected():
    with pytest.raises(EmailConfigurationError, match="host"):
        _adapter({"security": "ssl"})


@pytest.mark.asyncio
async def test_starttls_security_uses_plain_smtp_and_upgrades():
    adapter = _adapter({"host": "mail.example.test", "port": 587})

    await adapter.send(_message())

    assert RECORDS["opened"]["ssl"] is False
    assert RECORDS["opened"]["port"] == 587
    assert RECORDS["starttls"] == 1


@pytest.mark.asyncio
async def test_header_injection_is_rejected(fake_smtplib):
    with pytest.raises(EmailConfigurationError, match="line breaks"):
        await _adapter().send(_message(headers={"X-Test": "a\r\nBcc: x@y.test"}))

    assert "sent" not in fake_smtplib


@pytest.mark.asyncio
async def test_reserved_headers_cannot_be_overridden(fake_smtplib):
    await _adapter().send(
        _message(headers={"From": "attacker@example.test", "X-Trace": "ok"})
    )

    msg = fake_smtplib["sent"][0]
    assert msg["From"] == "Bolo <hello@example.test>"
    assert msg["X-Trace"] == "ok"


def test_timeout_is_configurable():
    adapter = _adapter({"preset": "hostinger", "timeout_seconds": 5})
    assert adapter._timeout == 5.0


def test_non_numeric_timeout_is_a_configuration_error():
    with pytest.raises(EmailConfigurationError, match="timeout_seconds"):
        _adapter({"preset": "hostinger", "timeout_seconds": "soon"})


def test_zero_port_is_rejected_instead_of_defaulting():
    with pytest.raises(EmailConfigurationError, match="between 1 and 65535"):
        _adapter({"host": "mail.example.test", "port": 0})


def test_saas_mode_rejects_a_loopback_smtp_host(monkeypatch):
    monkeypatch.setattr(url_security, "DEPLOYMENT_MODE", "saas")

    with pytest.raises(EmailConfigurationError, match="public IP"):
        _adapter({"host": "127.0.0.1", "port": 25})


def test_saas_mode_rejects_localhost(monkeypatch):
    monkeypatch.setattr(url_security, "DEPLOYMENT_MODE", "saas")

    with pytest.raises(EmailConfigurationError, match="localhost"):
        _adapter({"host": "localhost", "port": 25})


def test_saas_mode_rejects_nonstandard_smtp_ports(monkeypatch):
    monkeypatch.setattr(url_security, "DEPLOYMENT_MODE", "saas")

    with pytest.raises(EmailConfigurationError, match="must be one of"):
        _adapter({"host": "mail.example.test", "port": 2526})


def test_oss_mode_allows_a_private_relay_on_a_custom_port():
    adapter = _adapter({"host": "127.0.0.1", "port": 1025, "security": "starttls"})

    assert adapter.settings["host"] == "127.0.0.1"
    assert adapter.settings["port"] == 1025


@pytest.mark.asyncio
async def test_egress_check_runs_again_at_send_time(monkeypatch):
    adapter = _adapter({"host": "127.0.0.1", "port": 2525, "security": "starttls"})
    monkeypatch.setattr(url_security, "DEPLOYMENT_MODE", "saas")

    with pytest.raises(EmailConfigurationError, match="public IP"):
        await adapter.send(_message())


@pytest.mark.asyncio
async def test_missing_starttls_is_a_configuration_error(fake_smtplib):
    fake_smtplib["starttls_unsupported"] = True

    with pytest.raises(EmailConfigurationError, match="security mode"):
        await _adapter({"host": "mail.example.test", "port": 587}).send(_message())

    assert "sent" not in fake_smtplib


@pytest.mark.asyncio
async def test_caller_cannot_supply_the_message_id(fake_smtplib):
    await _adapter().send(_message(headers={"Message-ID": "<forged@attacker.test>"}))

    msg = fake_smtplib["sent"][0]
    assert msg["Message-ID"] != "<forged@attacker.test>"
    assert msg["Message-ID"].endswith("@example.test>")
