"""Copy the consent warning from the backend into the UI's shared JSON file.

``EMAIL_CONSENT_WARNING`` in ``api/constants.py`` is the single source of truth.
The UI imports the generated JSON so the text an operator sees always matches the
text stored in the audit trail. Load ``api/.env`` first so the constants import
sees the same settings as the backend, then run:

    set -a && source api/.env && set +a
    python scripts/sync_email_consent_warning.py
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from api.constants import EMAIL_CONSENT_WARNING

TARGET = REPO_ROOT / "ui" / "src" / "constants" / "emailConsent.json"


def main() -> None:
    payload = {"warning": EMAIL_CONSENT_WARNING}
    TARGET.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {TARGET.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
