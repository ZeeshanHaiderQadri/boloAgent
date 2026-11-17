"""Encrypt existing external credential data at rest.

Revision ID: d7c4e1a96b20
Revises: 8f2c1a7d4e90

The application writes encrypted envelopes from this release onward. This
migration backfills rows created by older releases. It is idempotent: rows that
already contain an envelope are left untouched.

The migration requires BOLO_CREDENTIAL_ENCRYPTION_KEYS only when there is
plaintext data to encrypt. Downgrade intentionally does not decrypt secrets;
roll back the application code instead and keep the encrypted envelopes.
"""

from __future__ import annotations

import json
import logging
import os

import sqlalchemy as sa
from alembic import op

from api.services.credentials.envelope import (
    EnvelopeError,
    build_envelope,
    is_envelope,
    key_id,
    parse_key_material,
)

revision = "d7c4e1a96b20"
down_revision = "8f2c1a7d4e90"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def _as_dict(value):
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise EnvelopeError("credential_data must be a JSON object")
        return parsed
    raise EnvelopeError("credential_data has an unsupported type")


def _load_keys() -> list[str]:
    try:
        return parse_key_material(os.getenv("BOLO_CREDENTIAL_ENCRYPTION_KEYS"))
    except EnvelopeError as exc:
        raise RuntimeError(
            "BOLO_CREDENTIAL_ENCRYPTION_KEYS contains an invalid Fernet key"
        ) from exc


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, credential_data FROM external_credentials")
    ).fetchall()

    pending: list[tuple[int, dict]] = []
    for row in rows:
        try:
            data = _as_dict(row.credential_data)
        except (EnvelopeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Credential row {row.id} has malformed credential_data; "
                "refusing to encrypt the table"
            ) from exc
        if not is_envelope(data):
            pending.append((row.id, data))

    if not pending:
        logger.info(
            "Credential encryption backfill: %d row(s), 0 plaintext rows, "
            "nothing to encrypt",
            len(rows),
        )
        return

    keys = _load_keys()
    if not keys:
        raise RuntimeError(
            f"Credential encryption backfill found {len(pending)} plaintext "
            "row(s) but BOLO_CREDENTIAL_ENCRYPTION_KEYS is not set. Set the key "
            "before running this migration."
        )

    known_key_ids = {key_id(key) for key in keys}
    unknown_key_rows = [
        row.id
        for row in rows
        if is_envelope(_as_dict(row.credential_data))
        and _as_dict(row.credential_data).get("key_id") not in known_key_ids
    ]
    if unknown_key_rows:
        raise RuntimeError(
            "Credential encryption backfill found rows encrypted with a key "
            f"that is not configured (row ids: {unknown_key_rows[:10]}). "
            "Add the old key to BOLO_CREDENTIAL_ENCRYPTION_KEYS or restore a "
            "backup before upgrading."
        )

    for row_id, data in pending:
        envelope = build_envelope(data, keys)
        bind.execute(
            sa.text(
                "UPDATE external_credentials "
                "SET credential_data = CAST(:payload AS JSON) "
                "WHERE id = :id"
            ),
            {"payload": json.dumps(envelope), "id": row_id},
        )

    logger.info(
        "Credential encryption backfill: %d row(s), encrypted %d plaintext row(s), "
        "left %d encrypted row(s) untouched",
        len(rows),
        len(pending),
        len(rows) - len(pending),
    )


def downgrade() -> None:
    logger.warning(
        "Credential encryption backfill is not reversed. Restore from backup if "
        "you need the previous plaintext representation."
    )
