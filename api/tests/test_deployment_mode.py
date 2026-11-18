"""DEPLOYMENT_MODE selects the egress policy, so production must choose it.

The default ("oss") keeps user-configured outbound destinations unrestricted.
A hosted deployment that inherits that default would let a tenant point a model
service or SMTP server at a private address, so ``api/constants.py`` refuses to
load with ``ENVIRONMENT=production`` unless the mode is stated explicitly.
"""

import importlib.util
import itertools
from pathlib import Path

import pytest

CONSTANTS_PATH = Path(__file__).resolve().parents[1] / "constants.py"
_module_ids = itertools.count()


def _load_constants(
    monkeypatch: pytest.MonkeyPatch,
    *,
    environment: str | None,
    deployment_mode: str | None,
):
    """Execute ``api/constants.py`` fresh so its module-level env reads run again."""
    if environment is None:
        monkeypatch.delenv("ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("ENVIRONMENT", environment)
    if deployment_mode is None:
        monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
    else:
        monkeypatch.setenv("DEPLOYMENT_MODE", deployment_mode)

    spec = importlib.util.spec_from_file_location(
        f"_constants_probe_{next(_module_ids)}", CONSTANTS_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_requires_an_explicit_deployment_mode(
    monkeypatch: pytest.MonkeyPatch,
):
    with pytest.raises(RuntimeError, match="DEPLOYMENT_MODE must be set explicitly"):
        _load_constants(monkeypatch, environment="production", deployment_mode=None)


def test_production_rejects_an_empty_deployment_mode(
    monkeypatch: pytest.MonkeyPatch,
):
    # An empty value in .env is a missing value, not an implicit "oss".
    with pytest.raises(RuntimeError, match="DEPLOYMENT_MODE must be set explicitly"):
        _load_constants(monkeypatch, environment="production", deployment_mode="")


def test_production_accepts_explicit_oss(monkeypatch: pytest.MonkeyPatch):
    module = _load_constants(
        monkeypatch, environment="production", deployment_mode="oss"
    )
    assert module.DEPLOYMENT_MODE == "oss"


def test_production_accepts_explicit_saas(monkeypatch: pytest.MonkeyPatch):
    module = _load_constants(
        monkeypatch, environment="production", deployment_mode=" SaaS "
    )
    assert module.DEPLOYMENT_MODE == "saas"


@pytest.mark.parametrize("environment", [None, "local", "test"])
def test_non_production_environments_keep_the_oss_default(
    monkeypatch: pytest.MonkeyPatch, environment: str | None
):
    module = _load_constants(monkeypatch, environment=environment, deployment_mode=None)
    assert module.DEPLOYMENT_MODE == "oss"


def test_unknown_deployment_mode_is_rejected(monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(RuntimeError, match="DEPLOYMENT_MODE must be 'oss' or 'saas'"):
        _load_constants(monkeypatch, environment="local", deployment_mode="staging")
