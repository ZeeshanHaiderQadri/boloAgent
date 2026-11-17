"""Encrypt existing external credential data at rest.

Revision ID: d7c4e1a96b20
Revises: 8f2c1a7d4e90

The application writes encrypted envelopes from this release onward. This
migration backfills rows created by older releases. It is idempotent: rows that
already contain an envelope are left untouched.

Keys are loaded and every existing envelope's key id is validated before the
"nothing to encrypt" case returns, so a missing or wrong key fails the upgrade
even when the backfill already ran.

The migration requires BOLO_CREDENTIAL_ENCRYPTION_KEYS. Downgrade intentionally
does not decrypt secrets; roll back the application code instead and keep the
encrypted envelopes.
"""

from __future__ import annotations

import json
import logging
import os

import sqlalchemy as sa
from alembic import op

from api.services.credentials.backfill import plan_backfill
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
    logger.warning(
        "Credential encryption backfill is not reversed. Restore from backup if "
        "you need the previous plaintext representation."
    )
