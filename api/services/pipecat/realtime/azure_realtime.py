"""Azure authentication and transport with Bolo's OpenAI realtime behavior."""

from api.services.pipecat.realtime.openai_realtime import BoloOpenAIRealtimeLLMService
from pipecat.services.azure.realtime.llm import AzureRealtimeLLMService


class BoloAzureRealtimeLLMService(
    BoloOpenAIRealtimeLLMService, AzureRealtimeLLMService
):
    """Share OpenAI conversation handling while retaining Azure's constructor."""
