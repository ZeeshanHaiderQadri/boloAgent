"""Consent writes and their audit rows must share one transaction."""

from types import SimpleNamespace

import pytest

from api.constants import EMAIL_CONSENT_WARNING
from api.db.lead_client import LeadClient


class _Result:
    def scalar_one_or_none(self):
        return SimpleNamespace(id=1)


class _FakeSession:
    def __init__(self, *, fail_activity: bool = False):
        self.operations: list[str] = []
        self.fail_activity = fail_activity

    async def execute(self, _statement):
        self.operations.append("update")
        return _Result()

    def add_all(self, _items):
        self.operations.append("activity")
        if self.fail_activity:
            raise RuntimeError("audit insert failed")

    async def commit(self):
        self.operations.append("commit")

    async def refresh(self, _row):
        pass


class _FakeContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False


def _client(session):
    client = object.__new__(LeadClient)
    client.async_session = lambda: _FakeContext(session)
    return client


def _activity():
    return {
        "organization_id": 1,
        "lead_id": 1,
        "activity_type": "email_consent",
        "title": "Email consent: Unknown → Opted in",
        "extra_metadata": {"warning": EMAIL_CONSENT_WARNING},
    }


@pytest.mark.asyncio
async def test_consent_change_and_activity_use_one_commit():
    session = _FakeSession()

    await _client(session).update_lead(
        1,
        1,
        email_consent="opted_in",
        activities=[_activity()],
    )

    assert session.operations == ["update", "activity", "commit"]


@pytest.mark.asyncio
async def test_consent_change_is_not_committed_without_the_activity():
    session = _FakeSession(fail_activity=True)

    with pytest.raises(RuntimeError, match="audit insert failed"):
        await _client(session).update_lead(
            1,
            1,
            email_consent="opted_in",
            activities=[_activity()],
        )

    assert "commit" not in session.operations
