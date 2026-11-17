"""Unit tests for the credential encryption envelope."""

import json

import pytest
from cryptography.fernet import Fernet

from api.services.credentials.cipher import CredentialCipher, CredentialEncryptionError
from api.services.credentials.envelope import (
    EnvelopeError,
    build_envelope,
    decrypt_envelope,
    is_envelope,
    key_id,
    parse_key_material,
)


def _key() -> str:
    return Fernet.generate_key().decode()


def test_round_trip_preserves_payload_and_envelope_shape():
    key = _key()
    cipher = CredentialCipher([key])
    payload = {"token": "secret-token", "nested": {"user": "alice"}}

    envelope = cipher.encrypt_data(payload)

    assert is_envelope(envelope)
    assert envelope["v"] == 1
    assert envelope["alg"] == "fernet"
    assert envelope["key_id"] == key_id(key)
    assert "secret-token" not in json.dumps(envelope)
    assert cipher.decrypt_data(envelope) == payload


def test_empty_payload_round_trips():
    cipher = CredentialCipher([_key()])
    assert cipher.decrypt_data(cipher.encrypt_data({})) == {}


def test_plaintext_is_accepted_by_default_and_rejected_in_require_mode():
    key = _key()
    plaintext = {"token": "legacy"}

    assert CredentialCipher([key]).decrypt_data(plaintext) == plaintext

    cipher = CredentialCipher([key], require_encrypted=True)
    with pytest.raises(CredentialEncryptionError, match="Plaintext credential"):
        cipher.decrypt_data(plaintext)

    # Rotation/migration can explicitly opt into plaintext during dual-read.
    assert cipher.decrypt_data(plaintext, allow_plaintext=True) == plaintext


def test_multiple_keys_allow_rotation():
    old_key = _key()
    new_key = _key()
    old_envelope = build_envelope({"token": "old"}, [old_key])

    rotating = CredentialCipher([new_key, old_key])
    assert rotating.decrypt_data(old_envelope) == {"token": "old"}

    new_envelope = rotating.encrypt_data({"token": "new"})
    assert new_envelope["key_id"] == key_id(new_key)
    assert CredentialCipher([new_key]).decrypt_data(new_envelope) == {"token": "new"}


def test_wrong_key_fails_without_leaking_material():
    envelope = build_envelope({"token": "very-secret-value"}, [_key()])
    wrong_cipher = CredentialCipher([_key()])

    with pytest.raises(CredentialEncryptionError) as exc_info:
        wrong_cipher.decrypt_data(envelope)

    message = str(exc_info.value)
    assert "very-secret-value" not in message
    assert envelope["ciphertext"] not in message


def test_invalid_key_is_rejected_without_echoing_it():
    bad_key = "not-a-fernet-key"
    with pytest.raises(CredentialEncryptionError) as exc_info:
        CredentialCipher([bad_key])

    assert bad_key not in str(exc_info.value)


def test_parse_key_material_trims_deduplicates_and_keeps_order():
    first = _key()
    second = _key()
    assert parse_key_material(f" {first} , {second}, {first} ") == [first, second]
    assert parse_key_material("") == []
    assert parse_key_material(None) == []


def test_envelope_rejects_unknown_version_and_algorithm():
    key = _key()
    envelope = build_envelope({"token": "x"}, [key])

    with pytest.raises(EnvelopeError, match="version"):
        decrypt_envelope({**envelope, "v": 99}, [key])
    with pytest.raises(EnvelopeError, match="algorithm"):
        decrypt_envelope({**envelope, "alg": "rot13"}, [key])
