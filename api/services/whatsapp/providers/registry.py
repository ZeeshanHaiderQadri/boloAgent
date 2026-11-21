"""Name-to-provider registry for WhatsApp messaging.

Cloud API registers itself on import. A BSP implementation registers the same
way and becomes selectable without touching the send path. Stored
configuration rows name their messaging provider, which keeps the choice a
property of the workspace rather than of Bolo.
"""

from __future__ import annotations

from typing import Any, Callable

from api.services.whatsapp.providers.base import WhatsAppProvider

ProviderFactory = Callable[[dict[str, Any]], WhatsAppProvider]

_FACTORIES: dict[str, ProviderFactory] = {}


def register(name: str, factory: ProviderFactory) -> None:
    _FACTORIES[name] = factory


def available_providers() -> tuple[str, ...]:
    return tuple(sorted(_FACTORIES))


def build_provider(name: str, config: dict[str, Any]) -> WhatsAppProvider:
    factory = _FACTORIES.get(name)
    if factory is None:
        raise ValueError(
            f"Unknown WhatsApp messaging provider {name!r}. "
            f"Available: {', '.join(available_providers()) or 'none'}."
        )
    return factory(config)
