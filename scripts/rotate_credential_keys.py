"""Re-encrypt stored credentials with the primary encryption key.

Usage (from the repo root, with api/.env sourced):

    python -m scripts.rotate_credential_keys --dry-run
    python -m scripts.rotate_credential_keys

During rotation, set BOLO_CREDENTIAL_ENCRYPTION_KEYS to a comma-separated list
with the new key first and the old key(s) after it. The script decrypts every
row with any configured key and rewrites it with the first key.
"""

from __future__ import annotations

import argparse
import asyncio

from loguru import logger
from sqlalchemy import select

from api.db import db_client
from api.db.models import ExternalCredentialModel
from api.services.credentials.cipher import (
    CredentialEncryptionError,
    get_credential_cipher,
)
from api.services.credentials.envelope import is_envelope


async def rotate_credentials(
    *,
    dry_run: bool = False,
    batch_size: int = 200,
) -> dict[str, int]:
    cipher = get_credential_cipher()
    totals = {"total": 0, "rotated": 0, "skipped": 0}
    last_id = 0

    while True:
        async with db_client.async_session() as session:
            result = await session.execute(
                select(ExternalCredentialModel)
                .where(ExternalCredentialModel.id > last_id)
                .order_by(ExternalCredentialModel.id)
                .limit(batch_size)
            )
            credentials = list(result.scalars().all())
            if not credentials:
                break

            for credential in credentials:
                last_id = credential.id
                totals["total"] += 1

                current = credential.credential_data
                if (
                    is_envelope(current)
                    and current.get("key_id") == cipher.primary_key_id
                ):
                    totals["skipped"] += 1
                    continue

                try:
                    plaintext = cipher.decrypt_data(current, allow_plaintext=True)
                except CredentialEncryptionError as exc:
                    # Fail closed: an unknown shape may hold a secret we cannot
                    # read, so never re-encrypt it as if it were plaintext.
                    raise CredentialEncryptionError(
                        f"Credential row {credential.id} cannot be rotated: {exc}"
                    ) from exc
                if not dry_run:
                    credential.credential_data = cipher.encrypt_data(plaintext)
                totals["rotated"] += 1

            if not dry_run:
                await session.commit()

    logger.info(
        "Credential rotation{}: total={}, rotated={}, already_on_primary={}",
        " (dry run)" if dry_run else "",
        totals["total"],
        totals["rotated"],
        totals["skipped"],
    )
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing to the database",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Rows loaded per transaction (default: 200)",
    )
    args = parser.parse_args()
    asyncio.run(rotate_credentials(dry_run=args.dry_run, batch_size=args.batch_size))


if __name__ == "__main__":
    main()
