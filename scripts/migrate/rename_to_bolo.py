#!/usr/bin/env python3
"""One-shot migration: rename the Dograh codebase to Bolo.

Bolo is a white-label fork of Dograh (BSD-2-Clause, Zansat Technologies
Private Limited). This script rewrites every tracked text file so the product
identity, package names, env vars, module names, image names and URLs all read
"Bolo" instead of "Dograh".

Deliberate exceptions (external contracts that must keep resolving):

* ``services.dograh.com`` — the upstream **MPS** (Managed Platform Service)
  host. It is an external SaaS endpoint, not our code. Override with
  ``MPS_API_URL`` to point at your own MPS deployment.
* ``github.com/dograh-hq/pipecat.git`` — the ``pipecat`` git submodule. The
  upstream fork publishes ``pipecat.services.dograh.*`` classes, so symbols
  imported from it keep their upstream names.
* ``github.com/dograh-hq/minio`` / ``dograhai/minio`` — the community MinIO
  build that upstream MinIO stopped publishing.
* ``DOGRAH_TTFS_P`` — environment variable read by the pipecat submodule.

Usage:
    python3 scripts/migrate/rename_to_bolo.py [--dry-run]
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Files that must not be rewritten at all.
SKIP_FILES = {
    "LICENSE",  # BSD-2-Clause notice — must be retained verbatim
    "CHANGELOG.md",  # historical upstream record
    ".gitmodules",  # contains the protected pipecat submodule URL
}

# Directories excluded from rewriting (separate git repositories / build dirs).
SKIP_DIRS = {".git", "pipecat", "node_modules", ".next", "__pycache__", ".venv"}

# Strings preserved verbatim wherever they appear.
PROTECTED_GLOBAL = [
    "services.dograh.com",
    "github.com/dograh-hq/pipecat.git",
    "github.com/dograh-hq/minio",
    "dograhai/minio",
    "DOGRAH_TTFS_P",
    # The pipecat submodule publishes `pipecat.services.dograh.*`; the module
    # path itself is upstream-owned and must not be renamed.
    "pipecat.services.dograh",
]

# Ordered literal replacements. Longest / most specific first.
RULES = [
    ("ghcr.io/dograh-hq", "ghcr.io/zeeshanhaiderqadri"),
    ("dograh-hq", "ZeeshanHaiderQadri"),
    ("dograhai", "boloagent"),
    ("app.dograh.com", "app.boloagent.com"),
    ("api.dograh.com", "api.boloagent.com"),
    ("docs.dograh.com", "docs.boloagent.com"),
    ("www.dograh.com", "www.boloagent.com"),
    ("dograh.com", "boloagent.com"),
    ("DOGRAH", "BOLO"),
    ("Dograh", "Bolo"),
    ("dograh", "bolo"),
]

# Imports from the pipecat submodule keep their upstream symbol names.
PIPECAT_IMPORT_RE = re.compile(
    r"from\s+pipecat\.services\.dograh[\w.]*\s+import\s+(?:\(([^)]*)\)|([^\n]+))"
)

MARK = "\ue000"  # private-use sentinel marker (cannot occur in source files)


class Stash:
    """Holds literal -> sentinel mappings so values can be restored later."""

    def __init__(self) -> None:
        self.values: list[str] = []

    def put(self, literal: str) -> str:
        self.values.append(literal)
        return f"{MARK}{len(self.values) - 1}{MARK}"

    def restore(self, text: str) -> str:
        for index, value in enumerate(self.values):
            text = text.replace(f"{MARK}{index}{MARK}", value)
        return text


def _imported_names(match: re.Match) -> list[tuple[str, str | None]]:
    blob = match.group(1) or match.group(2) or ""
    names = []
    for part in blob.replace("\n", " ").split(","):
        part = part.strip()
        if not part:
            continue
        if " as " in part:
            original, alias = [p.strip() for p in part.split(" as ", 1)]
            names.append((original, alias))
        else:
            names.append((part, None))
    return names


def protect(text: str, stash: Stash) -> str:
    """Mask protected literals and pipecat-imported symbols with sentinels."""
    # 1) Aliases: protect the in-repo alias everywhere (it points at a pipecat
    #    symbol), so the local name never gets renamed.
    for match in PIPECAT_IMPORT_RE.finditer(text):
        for original, alias in _imported_names(match):
            if alias:
                text = re.sub(rf"\b{re.escape(alias)}\b", stash.put(alias), text)

    # 2) Direct imports: protect the upstream symbol everywhere in the file,
    #    but only inside the import statement for aliased imports so that
    #    same-named *local* classes (e.g. api...registry.DograhLLMService)
    #    are still renamed.
    for match in PIPECAT_IMPORT_RE.finditer(text):
        for original, alias in _imported_names(match):
            if alias:
                continue
            text = re.sub(rf"\b{re.escape(original)}\b", stash.put(original), text)

    # 3) Aliased imports keep the upstream name inside the import statement.
    def fix_alias(match: re.Match) -> str:
        statement = match.group(0)
        for original, alias in _imported_names(match):
            if alias:
                statement = re.sub(
                    rf"(?<![\w]){re.escape(original)}(?=\s+as\s)",
                    stash.put(original),
                    statement,
                )
        return statement

    text = PIPECAT_IMPORT_RE.sub(fix_alias, text)

    # 4) Mask the remaining global literals last, so that the pipecat import
    #    detection above still sees the upstream module path.
    for value in PROTECTED_GLOBAL:
        if value in text:
            text = text.replace(value, stash.put(value))

    return text


def rewrite(path: Path, dry_run: bool) -> tuple[bool, int]:
    rel = path.relative_to(ROOT).as_posix()
    if rel in SKIP_FILES or any(rel.startswith(d + "/") for d in SKIP_DIRS):
        return False, 0
    try:
        raw = path.read_bytes()
    except OSError:
        return False, 0
    if b"\x00" in raw[:8000]:  # binary asset
        return False, 0
    try:
        original = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False, 0

    stash = Stash()
    text = protect(original, stash)
    for old, new in RULES:
        text = text.replace(old, new)
    text = stash.restore(text)

    if text != original:
        if not dry_run:
            path.write_text(text, encoding="utf-8")
        return True, sum(original.count(rule[0]) for rule in RULES)
    return False, 0


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [ROOT / p for p in out.split("\0") if p]


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    changed = 0
    total = 0
    for path in tracked_files():
        if not path.is_file():
            continue
        did, count = rewrite(path, dry_run)
        if did:
            changed += 1
            total += count
    verb = "would rewrite" if dry_run else "rewrote"
    print(f"{verb} {changed} files ({total} tokens)")


if __name__ == "__main__":
    main()
