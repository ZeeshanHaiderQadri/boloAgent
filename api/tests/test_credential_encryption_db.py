"""Database and API tests for credential encryption and dual-read rollout."""

import json
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from loguru import logger
from sqlalchemy import update

from api.db.models import ExternalCredentialModel
from api.services.credentials.cipher import (
    CredentialEncryptionError,
    encrypt_credential_data,
)
from api.services.credentials.envelope import build_envelope, is_envelope
from api.utils.credential_auth import build_auth_header
from scripts.rotate_credential_keys import rotate_credentials


async def _org(db_client):
    user, _ = await db_client.get_or_create_user_by_provider_id(
        f"credential-{uuid4().hex}"
    )
    org, _ = await db_client.get_or_create_organization_by_provider_id(
        f"credential-{uuid4().hex}", user.id
    )
    return user, org


async def _insert_plaintext_credential(db_client, user, org, name, data):
    async with db_client.async_session() as session:
        credential = ExternalCredentialModel(
            organization_id=org.id,
            created_by=user.id,
            name=name,
            credential_type="bearer_token",
            credential_data=data,
        )
        session.add(credential)
        await session.flush()
        return credential


async def _stored_credential_data(db_client, credential_id):
    rows = await db_client.execute_raw_query(
        "SELECT credential_data FROM external_credentials WHERE id = :id",
        {"id": credential_id},
    )
    value = rows[0]["credential_data"]
    return json.loads(value) if isinstance(value, str) else value


@pytest.mark.asyncio
async def test_startup_probe_accepts_rows_encrypted_with_the_configured_key(
    db_session,
):
    user, org = await _org(db_session)
    await db_session.create_credential(
        organization_id=org.id,
        user_id=user.id,
        name="Readable",
        credential_type="bearer_token",
        credential_data={"token": "probe-ok"},
    )

    await db_session.verify_credential_encryption_readable()


@pytest.mark.asyncio
async def test_startup_probe_rejects_rows_encrypted_with_an_unconfigured_key(
    db_session,
):
    user, org = await _org(db_session)
    other_key = Fernet.generate_key().decode()
    async with db_session.async_session() as session:
        session.add(
            ExternalCredentialModel(
                organization_id=org.id,
                created_by=user.id,
                name="Wrong key",
                credential_type="bearer_token",
                credential_data=build_envelope({"token": "unreadable"}, [other_key]),
            )
        )
        await session.flush()

    with pytest.raises(CredentialEncryptionError):
        await db_session.verify_credential_encryption_readable()


@pytest.mark.asyncio
async def test_create_encrypts_at_rest_and_reads_decrypted(db_session):
    user, org = await _org(db_session)
    secret = "super-secret-bearer-token"

    credential = await db_session.create_credential(
        organization_id=org.id,
        user_id=user.id,
        name="Bearer",
        credential_type="bearer_token",
        credential_data={"token": secret},
    )

    assert credential.credential_data == {"token": secret}
    stored = await _stored_credential_data(db_session, credential.id)
    assert is_envelope(stored)
    assert secret not in json.dumps(stored)

    fetched = await db_session.get_credential_by_uuid(
        credential.credential_uuid, org.id
    )
    assert fetched.credential_data == {"token": secret}
    assert build_auth_header(fetched) == {"Authorization": f"Bearer {secret}"}

    listed = await db_session.get_credentials_for_organization(org.id)
    assert any(item.credential_uuid == credential.credential_uuid for item in listed)
    assert all(
        item.credential_data.get("token") != secret or item.id == credential.id
        for item in listed
    )
    assert next(
        item for item in listed if item.id == credential.id
    ).credential_data == {"token": secret}


@pytest.mark.asyncio
async def test_update_encrypts_new_secret(db_session):
    user, org = await _org(db_session)
    credential = await db_session.create_credential(
        organization_id=org.id,
        user_id=user.id,
        name="Rotating",
        credential_type="bearer_token",
        credential_data={"token": "first"},
    )

    updated = await db_session.update_credential(
        credential_uuid=credential.credential_uuid,
        organization_id=org.id,
        credential_data={"token": "second"},
    )

    assert updated.credential_data == {"token": "second"}
    stored = await _stored_credential_data(db_session, credential.id)
    assert is_envelope(stored)
    assert "second" not in json.dumps(stored)
    assert build_auth_header(updated) == {"Authorization": "Bearer second"}


@pytest.mark.asyncio
async def test_update_with_another_organization_returns_none_and_changes_nothing(
    db_session,
):
    user, org = await _org(db_session)
    _, other_org = await _org(db_session)
    credential = await db_session.create_credential(
        organization_id=org.id,
        user_id=user.id,
        name="Scoped",
        credential_type="bearer_token",
        credential_data={"token": "original"},
    )
    before = await _stored_credential_data(db_session, credential.id)

    updated = await db_session.update_credential(
        credential_uuid=credential.credential_uuid,
        organization_id=other_org.id,
        credential_data={"token": "should-not-land"},
    )

    assert updated is None
    assert await _stored_credential_data(db_session, credential.id) == before


@pytest.mark.asyncio
async def test_update_reselect_stays_organization_scoped(db_session, monkeypatch):
    """The post-update re-select must not return a row from another org.

    The pre-check normally catches this, but a row that changes hands between
    the check and the re-select would slip through an unscoped select.
    """
    user, org = await _org(db_session)
    _, other_org = await _org(db_session)
    credential = await db_session.create_credential(
        organization_id=org.id,
        user_id=user.id,
        name="Race",
        credential_type="bearer_token",
        credential_data={"token": "original"},
    )
    before = await _stored_credential_data(db_session, credential.id)

    async def fake_get(credential_uuid, organization_id, active_only=True, purpose=None):
        return credential

    monkeypatch.setattr(db_session, "get_credential_by_uuid", fake_get)

    updated = await db_session.update_credential(
        credential_uuid=credential.credential_uuid,
        organization_id=other_org.id,
        credential_data={"token": "should-not-land"},
    )

    assert updated is None
    assert await _stored_credential_data(db_session, credential.id) == before


@pytest.mark.asyncio
async def test_reads_decrypt_a_freshly_loaded_row_not_the_in_memory_copy(
    db_session, async_session
):
    """A read must decrypt the stored envelope on a real load.

    ``create_credential`` hands back an object whose ``credential_data`` was
    decrypted in place. Change the stored envelope behind that object's back,
    expire its attributes so the next access reloads from the database, and read
    through the client again: getting the new secret proves decryption happens
    on load, not by reusing the in-memory value.
    """
    user, org = await _org(db_session)
    credential = await db_session.create_credential(
        organization_id=org.id,
        user_id=user.id,
        name="Fresh load",
        credential_type="bearer_token",
        credential_data={"token": "stale-in-memory"},
    )
    credential_id = credential.id
    credential_uuid = credential.credential_uuid
    async with db_session.async_session() as session:
        await session.execute(
            update(ExternalCredentialModel)
            .where(ExternalCredentialModel.id == credential_id)
            .values(credential_data=encrypt_credential_data({"token": "fresh-from-db"}))
        )
        await session.flush()
    async_session.expire(credential)

    fetched = await db_session.get_credential_by_uuid(credential_uuid, org.id)

    assert fetched.credential_data == {"token": "fresh-from-db"}
    assert build_auth_header(fetched) == {"Authorization": "Bearer fresh-from-db"}


@pytest.mark.asyncio
async def test_legacy_plaintext_still_authenticates_without_being_rewritten(db_session):
    user, org = await _org(db_session)
    legacy_token = "legacy-plaintext-token"
    credential = await _insert_plaintext_credential(
        db_session,
        user,
        org,
        "Legacy",
        {"token": legacy_token},
    )
    credential_uuid = credential.credential_uuid
    credential_id = credential.id

    fetched = await db_session.get_credential_by_uuid(credential_uuid, org.id)

    assert fetched.credential_data == {"token": legacy_token}
    assert build_auth_header(fetched) == {"Authorization": f"Bearer {legacy_token}"}
    # Dual-read must not write plaintext back or rewrite during a read.
    still_plaintext = await _stored_credential_data(db_session, credential_id)
    assert still_plaintext == {"token": legacy_token}


@pytest.mark.asyncio
async def test_rotation_is_idempotent_and_encrypts_legacy_rows(db_session):
    user, org = await _org(db_session)
    credential = await _insert_plaintext_credential(
        db_session,
        user,
        org,
        "Rotate me",
        {"token": "rotate-me"},
    )

    dry_run = await rotate_credentials(dry_run=True)
    assert dry_run["rotated"] >= 1
    assert (await _stored_credential_data(db_session, credential.id)) == {
        "token": "rotate-me"
    }

    first = await rotate_credentials(dry_run=False)
    assert first["rotated"] >= 1
    stored = await _stored_credential_data(db_session, credential.id)
    assert is_envelope(stored)

    second = await rotate_credentials(dry_run=False)
    assert second["rotated"] == 0
    assert second["skipped"] >= 1

    fetched = await db_session.get_credential_by_uuid(
        credential.credential_uuid, org.id
    )
    assert fetched.credential_data == {"token": "rotate-me"}


@pytest.mark.asyncio
async def test_rotation_encrypts_plaintext_that_resembles_envelope_fields(db_session):
    user, org = await _org(db_session)
    lookalikes = {
        "v": {"token": "x", "v": 1},
        "alg": {"token": "x", "alg": "HS256"},
        "ciphertext": {"api_key": "k", "ciphertext": "sha256"},
        "v-and-alg": {"v": 1, "alg": "HS256"},
    }
    rows = {}
    for name, data in lookalikes.items():
        rows[name] = await _insert_plaintext_credential(
            db_session, user, org, name, data
        )

    await rotate_credentials(dry_run=False)

    for name, data in lookalikes.items():
        stored = await _stored_credential_data(db_session, rows[name].id)
        assert is_envelope(stored), name
        fetched = await db_session.get_credential_by_uuid(
            rows[name].credential_uuid, org.id
        )
        assert fetched.credential_data == data, name


@pytest.mark.asyncio
async def test_rotation_refuses_malformed_envelopes_and_leaves_them_untouched(
    db_session,
):
    user, org = await _org(db_session)
    malformed = {"v": 1, "alg": "fernet"}
    credential = await _insert_plaintext_credential(
        db_session, user, org, "Malformed", malformed
    )

    with pytest.raises(CredentialEncryptionError, match="cannot be rotated"):
        await rotate_credentials(dry_run=False)

    assert await _stored_credential_data(db_session, credential.id) == malformed


@pytest.mark.asyncio
async def test_api_never_returns_secret_and_logs_never_contain_it(
    db_session, test_client_factory
):
    user, org = await _org(db_session)
    await db_session.update_user_selected_organization(user.id, org.id)
    user.selected_organization_id = org.id
    secret = "api-secret-must-not-leak"

    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="DEBUG")
    try:
        async with test_client_factory(user) as client:
            created = await client.post(
                "/api/v1/credentials/",
                json={
                    "name": "API key",
                    "credential_type": "bearer_token",
                    "credential_data": {"token": secret},
                },
            )
            assert created.status_code == 200, created.text
            body = created.json()
            assert "credential_data" not in body
            assert secret not in created.text

            credential_uuid = body["uuid"]
            fetched = await client.get(f"/api/v1/credentials/{credential_uuid}")
            assert fetched.status_code == 200, fetched.text
            assert "credential_data" not in fetched.json()
            assert secret not in fetched.text

            listed = await client.get("/api/v1/credentials/")
            assert listed.status_code == 200, listed.text
            assert secret not in listed.text
    finally:
        logger.remove(sink_id)

    assert secret not in "\n".join(messages)
