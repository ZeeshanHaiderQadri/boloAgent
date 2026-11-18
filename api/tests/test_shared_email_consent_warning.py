"""The UI and API must show and store exactly the same consent warning."""

import json
from pathlib import Path

from api.constants import EMAIL_CONSENT_WARNING

UI_WARNING_FILE = (
    Path(__file__).resolve().parents[2]
    / "ui"
    / "src"
    / "constants"
    / "emailConsent.json"
)


def test_ui_warning_matches_the_backend_constant():
    payload = json.loads(UI_WARNING_FILE.read_text(encoding="utf-8"))

    assert payload["warning"] == EMAIL_CONSENT_WARNING, (
        "ui/src/constants/emailConsent.json is out of date; run "
        "`python scripts/sync_email_consent_warning.py`."
    )
