"""Validation rules for the credential encryption backfill planning."""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from api.services.credentials.backfill import plan_backfill, plan_downgrade
from api.services.credentials.envelope import EnvelopeError, build_envelope


def _key() -> str:
    return Fernet.generate_key().decode()


def test_missing_key_fails_even_when_every_row_is_encrypted():
    keys = [_key()]
    rows = [(1, build_envelope({"token": "x"}, keys))]

    with pytest.raises(EnvelopeError, match="No credential encryption key"):
        plan_backfill(rows, [])


def test_missing_key_fails_on_an_empty_table():
    with pytest.raises(EnvelopeError, match="No credential encryption key"):
        plan_backfill([], [])


def test_unknown_key_fails_even_when_every_row_is_encrypted():
    old_keys = [_key()]
    rows = [(7, build_envelope({"token": "x"}, old_keys))]

    with pytest.raises(EnvelopeError, match="row 7"):
        plan_backfill(rows, [_key()])


def test_plaintext_rows_are_pending_and_existing_envelopes_are_counted():
    keys = [_key()]
    rows = [
        (1, {"token": "plain"}),
        (2, build_envelope({"token": "encrypted"}, keys)),
        (3, json.dumps({"token": "json-string"})),
    ]

    pending, encrypted = plan_backfill(rows, keys)

    assert pending == [
        (1, {"token": "plain"}),
        (3, {"token": "json-string"}),
    ]
    assert encrypted == 1


def test_malformed_envelope_rows_abort_the_plan():
    keys = [_key()]
    rows = [(9, {"v": 1, "ciphertext": ""})]

    with pytest.raises(EnvelopeError, match="row 9"):
        plan_backfill(rows, keys)


def test_downgrade_decrypts_envelopes_and_leaves_plaintext_rows():
    keys = [_key()]
    rows = [
        (1, build_envelope({"token": "encrypted"}, keys)),
        (2, {"token": "already-plain"}),
    ]

    assert plan_downgrade(rows, keys) == [(1, {"token": "encrypted"})]


def test_downgrade_requires_a_key():
    with pytest.raises(EnvelopeError, match="No credential encryption key"):
        plan_downgrade([(1, build_envelope({"token": "x"}, [_key()]))], [])


def test_downgrade_aborts_on_an_unknown_key():
    rows = [(4, build_envelope({"token": "x"}, [_key()]))]

    with pytest.raises(EnvelopeError, match="row 4"):
        plan_downgrade(rows, [_key()])


