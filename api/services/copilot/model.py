"""BYOK chat model for the copilot.

Bolo hosts no inference for the copilot. Every turn resolves the requesting
organization's own LLM connection and calls that provider directly, so usage
is billed to the user's key and never leaves their chosen vendor.

Supported: OpenAI, OpenRouter, Groq, Atlas Cloud, Hopper, Google AI Studio and
Azure OpenAI. ``bolo`` is rejected on purpose — it is Bolo-hosted inference.
"""

from __future__ import annotations

import json
import random
import re
from uuid import uuid4

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, convert_to_openai_messages
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from loguru import logger

from api.services.configuration.ai_model_configuration import (
    get_resolved_ai_model_configuration,
)
from api.services.configuration.registry import ServiceProviders

DEFAULT_BASE_URLS = {
    ServiceProviders.OPENAI.value: "https://api.openai.com/v1",
    ServiceProviders.OPENROUTER.value: "https://openrouter.ai/api/v1",
    ServiceProviders.GROQ.value: "https://api.groq.com/openai/v1",
    ServiceProviders.GOOGLE.value: (
        "https://generativelanguage.googleapis.com/v1beta/openai"
    ),
}
# Azure's API version is a routing detail, not a user setting, so keep one
# pinned value that supports tool calling on current deployments.
AZURE_API_VERSION = "2024-10-21"
# Reasoning models reject a non-default temperature on some providers.
_REASONING_MODEL = re.compile(r"^(o\d|gpt-5)", re.IGNORECASE)


class CopilotConfigurationError(Exception):
    """The organization has no usable BYOK chat model."""


class CopilotUpstreamError(Exception):
    """The user's provider failed; the message is safe to show."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _error_detail(response: httpx.Response) -> str | None:
    """The provider's own explanation for a refusal, if it sent one."""
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    error = data.get("error")
    candidates = [data.get("detail"), data.get("message")]
    if isinstance(error, dict):
        candidates.append(error.get("message"))
    elif isinstance(error, str):
        candidates.append(error)
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:200]
    return None


def _provider(llm) -> str:
    return str(getattr(llm.provider, "value", llm.provider))


def _pick_key(llm) -> str:
    key = getattr(llm, "api_key", None)
    if isinstance(key, list):
        key = random.choice(key) if key else ""
    return (key or "").strip()


def _endpoint(llm) -> tuple[str, dict[str, str], bool]:
    """Return (url, auth headers, include_model_in_body) for a connection."""
    provider = _provider(llm)
    key = _pick_key(llm)
    if not key:
        raise CopilotConfigurationError(
            "Add an API key for your LLM provider in Model connections."
        )

    if provider == ServiceProviders.AZURE.value:
        endpoint = (getattr(llm, "endpoint", "") or "").rstrip("/")
        if not endpoint:
            raise CopilotConfigurationError(
                "Add your Azure OpenAI endpoint in Model connections."
            )
        url = (
            f"{endpoint}/openai/deployments/{llm.model}/chat/completions"
            f"?api-version={AZURE_API_VERSION}"
        )
        return url, {"api-key": key}, False

    if provider == ServiceProviders.GOOGLE_VERTEX.value:
        raise CopilotConfigurationError(
            "Vertex AI connections are not supported by the copilot yet. "
            "Use Google AI Studio, OpenAI, OpenRouter, Groq or Azure."
        )

    base_url = (getattr(llm, "base_url", None) or "").rstrip("/")
    base_url = base_url or DEFAULT_BASE_URLS.get(provider, "")
    if not base_url:
        raise CopilotConfigurationError(
            f"No chat endpoint is configured for {provider}. "
            "Add one in Model connections."
        )
    return f"{base_url}/chat/completions", {"Authorization": f"Bearer {key}"}, True


class CopilotChatModel(BaseChatModel):
    """One LangChain chat model bound to an organization's own LLM key."""

    organization_id: int
    user_id: str

    @property
    def _llm_type(self) -> str:
        return "bolo_copilot_byok"

    def bind_tools(self, tools, **kwargs):
        return self.bind(
            tools=[convert_to_openai_tool(tool) for tool in tools], **kwargs
        )

    def _generate(self, *args, **kwargs):
        raise NotImplementedError("The copilot uses async model calls")

    async def describe(self) -> str:
        """Provider and model label for the system prompt (never the key)."""
        resolved = await get_resolved_ai_model_configuration(
            organization_id=self.organization_id
        )
        llm = resolved.effective.llm
        if llm is None:
            return "not configured"
        return f"{_provider(llm)} / {llm.model}"

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        resolved = await get_resolved_ai_model_configuration(
            organization_id=self.organization_id
        )
        llm = resolved.effective.llm
        if llm is None:
            raise CopilotConfigurationError(
                "The copilot needs your own LLM. Add a provider key in Model "
                "connections (OpenAI, Google, Groq, OpenRouter, Azure, Atlas "
                "Cloud or Hopper)."
            )
        if _provider(llm) == ServiceProviders.BOLO.value:
            raise CopilotConfigurationError(
                "The copilot runs on your own LLM key, not Bolo-hosted "
                "inference. Add a provider connection in Model connections."
            )

        url, headers, include_model = _endpoint(llm)
        body: dict = {"messages": convert_to_openai_messages(messages)}
        if include_model:
            body["model"] = llm.model
        temperature = getattr(llm, "temperature", None)
        if temperature is not None and not _REASONING_MODEL.match(llm.model or ""):
            body["temperature"] = temperature
        if stop:
            body["stop"] = stop
        if kwargs.get("tools"):
            body["tools"] = kwargs["tools"]
            body["tool_choice"] = kwargs.get("tool_choice") or "auto"

        try:
            # A local model can spend over a minute evaluating the system
            # prompt and tool schemas before it answers.
            async with httpx.AsyncClient(timeout=150) as client:
                response = await client.post(url, json=body, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            detail = _error_detail(exc.response)
            logger.warning(
                "Copilot LLM upstream failed provider={} status={} organization_id={}",
                _provider(llm),
                status,
                self.organization_id,
            )
            message = f"Your LLM provider returned an error ({status})."
            if detail:
                message = f"Your LLM provider rejected the request ({status}): {detail}"
            raise CopilotUpstreamError(message, status_code=status) from exc
        except httpx.TimeoutException as exc:
            logger.warning(
                "Copilot LLM upstream timed out provider={} organization_id={}",
                _provider(llm),
                self.organization_id,
            )
            raise CopilotUpstreamError(
                "Your LLM provider did not answer in time. A local model can "
                "be slow on a long conversation; try again, or start a new "
                "conversation."
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "Copilot LLM upstream unreachable provider={} organization_id={}",
                _provider(llm),
                self.organization_id,
            )
            raise CopilotUpstreamError(
                "Could not reach your LLM provider. Check your network and "
                "the connection in Model connections.",
            ) from exc

        try:
            choice = data["choices"][0]
            raw = choice.get("message") or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise CopilotUpstreamError(
                "Your LLM provider returned an unexpected response."
            ) from exc

        tool_calls = []
        for call in raw.get("tool_calls") or []:
            function = call.get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments else {}
                except ValueError as exc:
                    # No side effect has happened yet; stop the turn rather
                    # than guess at arguments.
                    raise CopilotUpstreamError(
                        "Your LLM returned malformed tool arguments. Try again."
                    ) from exc
            if not isinstance(arguments, dict):
                raise CopilotUpstreamError(
                    "Your LLM returned malformed tool arguments. Try again."
                )
            tool_calls.append(
                {
                    "name": function.get("name") or "",
                    "args": arguments,
                    "id": call.get("id") or f"call_{uuid4().hex[:12]}",
                }
            )

        message = AIMessage(
            content=raw.get("content") or "",
            tool_calls=tool_calls,
            response_metadata={
                "finish_reason": choice.get("finish_reason"),
                "provider": _provider(llm),
                "model": data.get("model") or llm.model,
            },
        )
        return ChatResult(generations=[ChatGeneration(message=message)])
