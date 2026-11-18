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
REPO_ROOT = Path(__file__).resolve().parents[2]
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


def test_hostinger_compose_requires_deployment_mode():
    text = (REPO_ROOT / "deploy/hostinger/docker-compose.yaml").read_text()
    assert '"${DEPLOYMENT_MODE:?' in text


def test_main_compose_passes_deployment_mode_through_without_a_default():
    text = (REPO_ROOT / "docker-compose.yaml").read_text()
    # Both the init renderer and the API must see the same value: empty when
    # unset, so the API guard decides instead of Compose picking for it.
    assert text.count('DEPLOYMENT_MODE: "${DEPLOYMENT_MODE:-}"') == 2
    assert "DEPLOYMENT_MODE:-oss" not in text


def test_helm_chart_has_no_default_and_fails_to_render_without_the_value():
    values = (REPO_ROOT / "deploy/helm/bolo/values.yaml").read_text()
    template = (REPO_ROOT / "deploy/helm/bolo/templates/configmap.yaml").read_text()
    assert "\n  deploymentMode: " not in values
    assert "required" in template
    assert "config.deploymentMode" in template
