"""Pure helpers for the credential encryption envelope.

This module deliberately has no application imports so Alembic migrations and
operational scripts can use the exact same format as the API.

Envelope shape::

    {
        "v": 1,
        "alg": "fernet",
        "key_id": "a1b2c3d4",
        "ciphertext": "gAAAA..."
    }
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

ENVELOPE_VERSION = 1
ALGORITHM = "fernet"


class EnvelopeError(ValueError):
    """Raised when credential data cannot be encrypted or decrypted safely."""


def parse_key_material(raw_keys: str | Sequence[str] | None) -> list[str]:
    """Normalize comma-separated or sequence key material into Fernet keys.

    The first key is the primary (encrypting) key. All keys are tried during
    decryption so a rotation can append a new key before old rows are rewritten.
    Invalid keys are rejected without echoing their value into the error.
    """
    if raw_keys is None:
        return []
    if isinstance(raw_keys, str):
        candidates = raw_keys.split(",")
    else:
        candidates = list(raw_keys)

    keys: list[str] = []
    for candidate in candidates:
        key = str(candidate).strip()
        if not key:
            continue
        try:
            Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise EnvelopeError(
                "BOLO_CREDENTIAL_ENCRYPTION_KEYS contains an invalid Fernet key"
            ) from exc
        if key not in keys:
            keys.append(key)
    return keys


def key_id(key: str) -> str:
    """Return a short, non-secret identifier for a key."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def is_envelope(value: Any) -> bool:
    """Return True when value already has the encrypted envelope shape."""
    return (
        isinstance(value, Mapping)
        and value.get("alg") == ALGORITHM
        and isinstance(value.get("ciphertext"), str)
        and bool(value.get("ciphertext"))
    )


def build_envelope(data: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    """Encrypt a JSON-compatible mapping into the storage envelope."""
    if not keys:
        raise EnvelopeError("No credential encryption key is configured")

    payload = json.dumps(
        dict(data),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    token = Fernet(keys[0].encode("ascii")).encrypt(payload).decode("ascii")
    return {
        "v": ENVELOPE_VERSION,
        "alg": ALGORITHM,
        "key_id": key_id(keys[0]),
        "ciphertext": token,
    }


def decrypt_envelope(
    envelope: Mapping[str, Any], keys: Sequence[str]
) -> dict[str, Any]:
    """Decrypt an envelope, trying every configured key."""
    if not keys:
        raise EnvelopeError("No credential encryption key is configured")
    if envelope.get("v") != ENVELOPE_VERSION:
        raise EnvelopeError("Unsupported credential encryption envelope version")
    if envelope.get("alg") != ALGORITHM:
        raise EnvelopeError("Unsupported credential encryption algorithm")

    ciphertext = envelope.get("ciphertext")
    if not isinstance(ciphertext, str) or not ciphertext:
        raise EnvelopeError("Credential encryption envelope has no ciphertext")

    decryptor = MultiFernet([Fernet(key.encode("ascii")) for key in keys])
    try:
        raw = decryptor.decrypt(ciphertext.encode("ascii"))
    except (InvalidToken, UnicodeEncodeError) as exc:
        raise EnvelopeError(
            "Unable to decrypt credential data with the configured keys"
        ) from exc

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvelopeError("Decrypted credential data is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise EnvelopeError("Decrypted credential data must be a JSON object")
    return parsed
