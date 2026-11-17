"""Pure planning helpers for the credential encryption migration.

Kept free of application imports so the Alembic revision and unit tests can use
it without booting the API. Validating the whole table before returning is
deliberate: a missing key or an envelope encrypted with an unconfigured key must
fail the migration even when there is nothing left to encrypt.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from api.services.credentials.envelope import (
    EnvelopeError,
    has_envelope_markers,
    is_envelope,
    key_id,
)


def as_credential_dict(value: Any) -> dict[str, Any]:
    """Normalize a stored credential_data value into a dict.

    Rows read through a raw connection can surface a JSON column as a string or
    an already-decoded mapping. Anything else is malformed and aborts the
    migration rather than being encrypted as if it were a credential.
    """
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise EnvelopeError("credential_data is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise EnvelopeError("credential_data must be a JSON object")
    return dict(value)


def plan_backfill(
    rows: Sequence[tuple[int, Any]],
    keys: Sequence[str],
) -> tuple[list[tuple[int, dict[str, Any]]], int]:
    """Return the rows that still need encryption and the already-encrypted count.

    Every existing envelope is validated against the configured keys before the
    caller decides whether there is work to do, so a deployment that forgot a
    key cannot "succeed" merely because the backfill already ran.
    """
    if not keys:
        raise EnvelopeError(
            "No credential encryption key is configured; set "
            "BOLO_CREDENTIAL_ENCRYPTION_KEYS before running the migration"
        )

    known_key_ids = {key_id(key) for key in keys}
    pending: list[tuple[int, dict[str, Any]]] = []

    for row_id, raw in rows:
        data = as_credential_dict(raw)
        if is_envelope(data):
            if data.get("key_id") not in known_key_ids:
                raise EnvelopeError(
                    f"Credential row {row_id} is encrypted with a key that is "
                    "not configured; add the old key to "
                    "BOLO_CREDENTIAL_ENCRYPTION_KEYS before migrating"
                )
            continue
        if has_envelope_markers(data):
            raise EnvelopeError(
                f"Credential row {row_id} has encryption envelope fields but is "
                "not a valid v1 fernet envelope"
            )
        pending.append((row_id, data))

    return pending, len(rows) - len(pending)
