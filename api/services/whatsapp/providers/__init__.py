"""Pluggable WhatsApp messaging providers.

Importing this package registers the built-in Cloud API implementation. Add a
BSP module that calls ``register`` and it becomes selectable by name.
"""

from api.services.whatsapp.providers import cloud_api  # noqa: F401
from api.services.whatsapp.providers.base import (  # noqa: F401
    WhatsAppProviderError,
    WhatsAppSendReceipt,
    WhatsAppSendRequest,
    WhatsAppTemplate,
)
from api.services.whatsapp.providers.registry import (  # noqa: F401
    available_providers,
    build_provider,
    register,
)
