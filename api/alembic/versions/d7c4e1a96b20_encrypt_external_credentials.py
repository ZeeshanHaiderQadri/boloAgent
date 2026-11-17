"""Encrypt existing external credential data at rest.

Revision ID: d7c4e1a96b20
Revises: 8f2c1a7d4e90

The application writes encrypted envelopes from this release onward. This
migration backfills rows created by older releases. It is idempotent: rows that
already contain an envelope are left untouched.

Keys are loaded and every existing envelope's key id is validated before the
"nothing to encrypt" case returns, so a missing or wrong key fails the upgrade
even when the backfill already ran.

Downgrade decrypts the rows back to plaintext, which is what a pre-encryption
application expects. It validates every row first and writes nothing if any
envelope cannot be decrypted with the configured keys; in that case restore the
database backup instead.
"""

from __future__ import annotations

import json
import logging
import os

import sqlalchemy as sa
from alembic import op

from api.services.credentials.backfill import plan_backfill, plan_downgrade
from api.services.credentials.envelope import (
    EnvelopeError,
    build_envelope,
    parse_key_material,
)

revision = "d7c4e1a96b20"
down_revision = "8f2c1a7d4e90"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


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

    try:
        keys = _load_keys()
        pending, encrypted = plan_backfill(
            [(row.id, row.credential_data) for row in rows], keys
        )
    except EnvelopeError as exc:
        raise RuntimeError(f"Credential encryption backfill failed: {exc}") from exc

    if not pending:
        logger.info(
            "Credential encryption backfill: %d row(s), 0 plaintext rows, "
            "nothing to encrypt",
            len(rows),
        )
        return

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
        encrypted,
    )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, credential_data FROM external_credentials")
    ).fetchall()

    try:
        keys = _load_keys()
        updates = plan_downgrade(
            [(row.id, row.credential_data) for row in rows], keys
        )
    except EnvelopeError as exc:
        raise RuntimeError(
            f"Credential encryption downgrade failed: {exc}. Restore the "
            "pre-upgrade database backup if the key is unavailable."
        ) from exc

    if not updates:
        logger.info(
            "Credential encryption downgrade: %d row(s), no envelopes to decrypt",
            len(rows),
        )
        return

    for row_id, data in updates:
        bind.execute(
            sa.text(
                "UPDATE external_credentials "
                "SET credential_data = CAST(:payload AS JSON) "
                "WHERE id = :id"
            ),
            {"payload": json.dumps(data), "id": row_id},
        )

    logger.info(
        "Credential encryption downgrade: decrypted %d row(s), left %d plaintext "
        "row(s) untouched",
        len(updates),
        len(rows) - len(updates),
    )
