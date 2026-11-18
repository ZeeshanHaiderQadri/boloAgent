"""Every writer of ``lead.email`` must consider the consent attached to it.

A static guard is enough here: it lists the persistence entry points and fails
when a module writes an email field through one of them without using the shared
``email_address_changed`` helper that decides whether consent must be reset.
"""

from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent

# Modules allowed to persist a lead email today. Both must keep using the
# helper; anything new that persists an email shows up as an offender too.
KNOWN_WRITERS = {
    "api/db/lead_client.py",
    "api/routes/leads.py",
}

# Calls that can write a LeadModel row. A module containing one of these and an
# ``"email"`` field is treated as an email writer.
WRITE_MARKERS = (
    "pg_insert(LeadModel)",
    "update(LeadModel)",
    "db_client.update_lead(",
    "bulk_update_leads(",
)

SKIP_PARTS = {"tests", ".venv", "__pycache__", "migrations"}


def _python_sources():
    for path in API_ROOT.rglob("*.py"):
        if SKIP_PARTS.intersection(path.parts):
            continue
        yield path


def test_lead_email_writers_use_the_consent_helper():
    detected: set[str] = set()
    missing_helper: list[str] = []

    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        if '"email"' not in text:
            continue
        if not any(marker in text for marker in WRITE_MARKERS):
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        detected.add(relative)
        if "email_address_changed(" not in text:
            missing_helper.append(relative)

    assert KNOWN_WRITERS <= detected, (
        "The guard no longer finds a known lead-email writer; update the "
        f"markers or the paths. Missing: {sorted(KNOWN_WRITERS - detected)}"
    )
    assert missing_helper == [], (
        f"These modules write lead.email without the consent helper: {missing_helper}"
    )
