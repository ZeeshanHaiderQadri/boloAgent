"""Installers and images must provision the credential encryption key.

The API refuses to start without ``BOLO_CREDENTIAL_ENCRYPTION_KEYS`` outside the
test environment, so every install path (local, remote, Windows, devcontainer,
compose) has to create or pass it through. These tests keep the paths in sync.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from api.services.credentials.envelope import (
    build_envelope,
    decrypt_envelope,
    parse_key_material,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_key_generator_produces_a_usable_fernet_key():
    completed = subprocess.run(
        [
            "bash",
            "-c",
            "source scripts/lib/setup_common.sh && bolo_generate_credential_key",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    key = completed.stdout.strip()

    assert key
    assert parse_key_material([key]) == [key]
    assert decrypt_envelope(build_envelope({"token": "x"}, [key]), [key]) == {
        "token": "x"
    }


def test_local_setup_generates_and_writes_the_key():
    script = _read("scripts/setup_local.sh")

    assert "bolo_generate_credential_key" in script
    assert "BOLO_CREDENTIAL_ENCRYPTION_KEYS=$BOLO_CREDENTIAL_ENCRYPTION_KEYS" in script


def test_remote_setup_generates_and_writes_the_key():
    script = _read("scripts/setup_remote.sh")

    assert "bolo_generate_credential_key" in script
    assert "BOLO_CREDENTIAL_ENCRYPTION_KEYS=$BOLO_CREDENTIAL_ENCRYPTION_KEYS" in script


def test_windows_setup_generates_and_writes_the_key():
    script = _read("scripts/setup_local.ps1")

    assert "function New-FernetKey" in script
    assert "$credentialEncryptionKeys = New-FernetKey" in script
    assert '"BOLO_CREDENTIAL_ENCRYPTION_KEYS=$credentialEncryptionKeys"' in script


def test_devcontainer_generates_the_key_on_create():
    script = _read(".devcontainer/scripts/post-create.sh")

    assert "BOLO_CREDENTIAL_ENCRYPTION_KEYS" in script
    assert "openssl rand -base64 32" in script


def test_compose_requires_and_passes_the_key_to_the_api():
    compose = _read("docker-compose.yaml")

    assert (
        'BOLO_CREDENTIAL_ENCRYPTION_KEYS: "${BOLO_CREDENTIAL_ENCRYPTION_KEYS:?'
    ) in compose


def test_upgrade_docs_require_the_key_before_updating_and_a_separate_backup():
    update_docs = _read("docs/deployment/update.mdx")

    assert "BOLO_CREDENTIAL_ENCRYPTION_KEYS" in update_docs
    assert "before you update" in update_docs
    assert "separately from the database" in update_docs


def test_cutover_docs_require_a_drained_old_version_and_a_rotation_sweep():
    update_docs = _read("docs/deployment/update.mdx")
    env_docs = _read("docs/developer/environment-variables.mdx")

    for docs in (update_docs, env_docs):
        assert "BOLO_CREDENTIAL_ENCRYPTION_REQUIRE=true" in docs
        assert "drained" in docs
        assert "rotate_credential_keys" in docs
        # The REQUIRE flip has to come after the sweep, never before it.
        assert docs.index("rotate_credential_keys") < docs.index(
            "BOLO_CREDENTIAL_ENCRYPTION_REQUIRE=true"
        )
