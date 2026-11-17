"""Bolo subclass of pipecat's Gemini Live Vertex AI LLM service.

Diamond inheritance: combines the Bolo engine-integration overrides from
:class:`BoloGeminiLiveLLMService` with the Vertex-specific tweaks from
upstream's :class:`GeminiLiveVertexLLMService` (no history config,
``NON_BLOCKING`` tools disabled, service-account credentials).

MRO::

    BoloGeminiLiveVertexLLMService
      -> BoloGeminiLiveLLMService
      -> RealtimeConversationMixin
      -> GeminiLiveVertexLLMService
      -> GeminiLiveLLMService
      -> LLMService
      -> ...
"""

from google.genai.types import LiveConnectConfig

from api.services.pipecat.google_credentials import (
    get_google_credentials,
    prepare_google_credentials,
)
from api.services.pipecat.realtime.gemini_live import BoloGeminiLiveLLMService
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService
from pipecat.services.google.gemini_live.vertex.llm import (
    GeminiLiveVertexLLMService,
)


class BoloGeminiLiveVertexLLMService(
    BoloGeminiLiveLLMService,
    GeminiLiveVertexLLMService,
):
    """Vertex AI variant of Gemini Live with Bolo integration quirks."""

    _get_credentials = staticmethod(get_google_credentials)

    async def _connection_task_handler(self, config: LiveConnectConfig):
        # Live's SDK refreshes synchronously inside connect(), including on
        # reconnection. Prepare authentication off-loop before entering it.
        try:
            await prepare_google_credentials(self._credentials)
        except Exception as error:  # noqa: BLE001 - use the provider's failure policy
            if not self._disconnecting and await self._handle_connection_error(error):
                await self._reconnect()
            return
        await super()._connection_task_handler(config)


# Guard against MRO regressions: a future refactor that flips inheritance
# order or breaks the diamond would silently bypass the Bolo overrides.
_mro = BoloGeminiLiveVertexLLMService.__mro__
assert _mro[1] is BoloGeminiLiveLLMService, (
    f"Expected BoloGeminiLiveLLMService at MRO[1], got {_mro[1]}"
)
assert _mro.index(GeminiLiveVertexLLMService) < _mro.index(GeminiLiveLLMService), (
    "Vertex overrides must precede the base Gemini implementation"
)
del _mro
