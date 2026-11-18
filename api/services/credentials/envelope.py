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
import re
from collections.abc import Mapping, Sequence
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

ENVELOPE_VERSION = 1
ALGORITHM = "fernet"
# Fernet tokens start with the version byte 0x80 followed by a big-endian
# timestamp that is almost always 0x00-padded, so they encode to "gAAAA...".
FERNET_TOKEN_PREFIX = "gAAAA"
_KEY_ID_PATTERN = re.compile(r"[0-9a-f]{8}")


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


def looks_like_envelope(value: Any) -> bool:
    """Return True when value has the distinctive shape of our envelope.

    Credential data is free-form user JSON, so generic field names such as
    ``v``, ``alg`` or ``ciphertext`` cannot on their own mark a row as an
    envelope. Only signals that are specific to this format do:

    * ``alg`` is exactly ``fernet``
    * ``ciphertext`` starts with the Fernet token prefix ``gAAAA``
    * ``key_id`` is eight lowercase hex characters (our truncated SHA-256 id)

    A row that matches one of them but is not a valid v1 envelope must fail
    closed rather than be served or re-encrypted as plaintext. That keeps a
    truncated real envelope (for example one missing its ``ciphertext``) from
    being treated as a credential.

    Remaining edge cases, all deliberately resolved toward failing closed:

    * A plaintext credential that happens to carry an eight-hex ``key_id``, or
      a ``ciphertext`` beginning ``gAAAA``, is indistinguishable from a
      truncated envelope and is rejected. Rename the field to proceed.
    * An envelope truncated so far that it lost ``alg``, ``ciphertext`` and
      ``key_id`` (for example ``{"v": 1}``) is treated as plaintext. The
      ciphertext is gone by then, so there is nothing left to decrypt.
    """
    if not isinstance(value, Mapping):
        return False
    if value.get("alg") == ALGORITHM:
        return True
    ciphertext = value.get("ciphertext")
    if isinstance(ciphertext, str) and ciphertext.startswith(FERNET_TOKEN_PREFIX):
        return True
    key = value.get("key_id")
    return isinstance(key, str) and _KEY_ID_PATTERN.fullmatch(key) is not None


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
