#!/usr/bin/env python3
"""Report saved leads whose email address fails the shared validator.

Read-only. Nothing is deleted or rewritten. Use the output to correct the
source rows (or leave them; the scraper will not create new invalid addresses
once this validator is in use).

Run against the database in ``DATABASE_URL`` after sourcing the environment:

    source venv/bin/activate
    set -a && source api/.env && set +a
    python -m scripts.report_invalid_lead_emails

Useful options:

    --org-id 12             only one organization
    --csv /tmp/bad.csv      also write the rows to a spreadsheet-friendly file
    --database-url ...      override DATABASE_URL

The connection is opened read-only, so this cannot modify the database even by
mistake.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from api.utils.email_validation import classify_email


def _postgres_dsn(raw: str) -> str:
    """Strip SQLAlchemy's driver suffix so asyncpg can parse the URL."""
    cleaned = raw.strip()
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://"):
        if cleaned.startswith(prefix):
            return "postgresql://" + cleaned[len(prefix) :]
    return cleaned


def _redact(dsn: str) -> str:
    parts = urlsplit(dsn)
    if not parts.password:
        return dsn
    netloc = parts.netloc.replace(f":{parts.password}@", ":***@")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


async def _report(args: argparse.Namespace) -> int:
    raw_dsn = args.database_url or os.getenv("DATABASE_URL")
    if not raw_dsn:
        print(
            "DATABASE_URL is not set. Source the backend environment first:\n"
            "  set -a && source api/.env && set +a",
            file=sys.stderr,
        )
        return 2

    dsn = _postgres_dsn(raw_dsn)
    query = """
        SELECT l.id, l.organization_id, o.provider_id AS organization_name,
               l.title, l.email
        FROM leads l
        JOIN organizations o ON o.id = l.organization_id
        WHERE l.email IS NOT NULL AND btrim(l.email) <> ''
    """
    params: list[object] = []
    if args.org_id is not None:
        query += " AND l.organization_id = $1"
        params.append(args.org_id)
    query += " ORDER BY l.organization_id, l.id"

    connection = await asyncpg.connect(
        dsn,
        server_settings={"default_transaction_read_only": "on"},
    )
    try:
        rows = await connection.fetch(query, *params)
    finally:
        await connection.close()

    reasons: Counter[str] = Counter()
    invalid: list[dict[str, str]] = []
    for row in rows:
        _normalized, reason = classify_email(row["email"])
        if reason is None:
            continue
        reasons[reason] += 1
        invalid.append(
            {
                "organization_id": str(row["organization_id"]),
                "organization_name": row["organization_name"] or "",
                "lead_id": str(row["id"]),
                "title": row["title"] or "",
                "email": row["email"] or "",
                "reason": reason,
            }
        )

    print(f"Database: {_redact(dsn)}")
    print(f"Leads with an email: {len(rows)}")
    print(f"Invalid: {len(invalid)}")
    for reason, count in reasons.most_common():
        print(f"  {reason}: {count}")
    if not invalid:
        print("No invalid lead emails found.")
        return 0

    print()
    for item in invalid:
        print(
            f"org {item['organization_id']} lead {item['lead_id']} "
            f"[{item['reason']}] {item['email']} — {item['title']}"
        )

    if args.csv:
        path = Path(args.csv).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(invalid[0]))
            writer.writeheader()
            writer.writerows(invalid)
        print(f"\nWrote {len(invalid)} rows to {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", type=int, default=None)
    parser.add_argument("--csv", type=str, default=None)
    parser.add_argument("--database-url", type=str, default=None)
    args = parser.parse_args()
    return asyncio.run(_report(args))


if __name__ == "__main__":
    raise SystemExit(main())
