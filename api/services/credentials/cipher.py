"""Application-level encryption for stored third-party credentials.

The cipher is intentionally thin: envelope parsing/formatting lives in
``envelope.py`` so migrations and operational scripts can share it without
importing the API.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

from api.constants import ENVIRONMENT
from api.services.credentials.envelope import (
    EnvelopeError,
    build_envelope,
    decrypt_envelope,
    is_envelope,
    key_id,
    looks_like_envelope,
    parse_key_material,
)

KEY_ENV_VAR = "BOLO_CREDENTIAL_ENCRYPTION_KEYS"
REQUIRE_ENCRYPTED_ENV_VAR = "BOLO_CREDENTIAL_ENCRYPTION_REQUIRE"


class CredentialEncryptionError(RuntimeError):
    """Raised when credential encryption is misconfigured or fails."""


def _env_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


class CredentialCipher:
    """Encrypt and decrypt credential payloads with a multi-key Fernet setup."""

    def __init__(
        self,
        keys: Sequence[str],
        *,
        require_encrypted: bool = False,
    ) -> None:
        try:
            self._keys = parse_key_material(keys)
        except EnvelopeError as exc:
            raise CredentialEncryptionError(str(exc)) from exc
        if not self._keys:
            raise CredentialEncryptionError(
                f"{KEY_ENV_VAR} is required to store credentials"
            )
        self._require_encrypted = require_encrypted

    @classmethod
    def from_env(cls) -> CredentialCipher:
        return cls(
            parse_key_material(os.getenv(KEY_ENV_VAR)),
            require_encrypted=_env_truthy(os.getenv(REQUIRE_ENCRYPTED_ENV_VAR)),
        )

    @property
    def primary_key_id(self) -> str:
        return key_id(self._keys[0])

    def encrypt_data(self, data: Mapping[str, Any] | None) -> dict[str, Any]:
        """Encrypt a credential payload for storage."""
        try:
            return build_envelope(dict(data or {}), self._keys)
        except EnvelopeError as exc:
            raise CredentialEncryptionError(str(exc)) from exc

    def decrypt_data(
        self,
        data: Mapping[str, Any] | None,
        *,
        allow_plaintext: bool | None = None,
    ) -> dict[str, Any]:
        """Decrypt stored credential data, optionally accepting legacy plaintext.

        ``allow_plaintext`` defaults to the inverse of the configured
        ``BOLO_CREDENTIAL_ENCRYPTION_REQUIRE`` flag. Rotation and migrations
        pass ``True`` explicitly while the deployment is still in dual-read
        mode.
        """
        if data is None:
            return {}
        if not isinstance(data, Mapping):
            raise CredentialEncryptionError("Credential data must be a JSON object")

        if is_envelope(data):
            try:
                return decrypt_envelope(data, self._keys)
            except EnvelopeError as exc:
                raise CredentialEncryptionError(str(exc)) from exc

        if looks_like_envelope(data):
            raise CredentialEncryptionError(
                "Credential data looks like an encryption envelope but is not a "
                "valid v1 fernet envelope; refusing to treat it as plaintext"
            )

        allow = (
            not self._require_encrypted if allow_plaintext is None else allow_plaintext
        )
        if not allow:
            raise CredentialEncryptionError(
                "Plaintext credential data is not allowed; run the credential "
                "encryption backfill first"
            )
        return dict(data)


_cipher: CredentialCipher | None = None


def get_credential_cipher() -> CredentialCipher:
    """Return the process-wide cipher, constructing it on first use."""
    global _cipher
    if _cipher is None:
        try:
            _cipher = CredentialCipher.from_env()
        except EnvelopeError as exc:
            raise CredentialEncryptionError(str(exc)) from exc
    return _cipher


def reset_credential_cipher_cache() -> None:
    """Clear the cached cipher. Intended for tests and key rotation tooling."""
    global _cipher
    _cipher = None


def require_credential_encryption_configured() -> None:
    """Fail fast at service startup when no encryption key is configured."""
    if ENVIRONMENT == "test":
        return
    get_credential_cipher()


def encrypt_credential_data(data: Mapping[str, Any] | None) -> dict[str, Any]:
    return get_credential_cipher().encrypt_data(data)


def decrypt_credential_data(data: Mapping[str, Any] | None) -> dict[str, Any]:
    return get_credential_cipher().decrypt_data(data)
