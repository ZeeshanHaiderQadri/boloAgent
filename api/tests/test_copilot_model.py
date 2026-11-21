"""Unit tests for the BYOK copilot chat model."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from langchain_core.messages import HumanMessage

from api.services.configuration.registry import ServiceProviders
from api.services.copilot.model import (
    CopilotChatModel,
    CopilotConfigurationError,
    CopilotUpstreamError,
)


class _Response:
    def __init__(self, data, status_code: int = 200):
        self._data = data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "upstream",
                request=httpx.Request("POST", "https://provider.test"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._data


def _reply(content: str = "Hello", tool_calls=None):
    message = {"content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message, "finish_reason": "stop"}], "model": "m"}


def _resolve(llm):
    return SimpleNamespace(effective=SimpleNamespace(llm=llm))


async def _ask(llm, data):
    model = CopilotChatModel(organization_id=3, user_id="u1")
    with (
        patch(
            "api.services.copilot.model.get_resolved_ai_model_configuration",
            new=AsyncMock(return_value=_resolve(llm)),
        ),
        patch.object(
            httpx.AsyncClient, "post", new=AsyncMock(return_value=_Response(data))
        ) as post,
    ):
        reply = await model.ainvoke([HumanMessage(content="hi")])
    return reply, post


@pytest.mark.asyncio
async def test_openai_request_shape_includes_tools_and_byok_key():
    llm = SimpleNamespace(
        provider=ServiceProviders.OPENAI,
        model="gpt-4.1",
        api_key="sk-user-key",
        temperature=0.2,
        base_url="https://api.openai.com/v1",
    )
    reply, post = await _ask(llm, _reply())

    url = post.await_args.args[0]
    kwargs = post.await_args.kwargs
    assert url == "https://api.openai.com/v1/chat/completions"
    assert kwargs["headers"] == {"Authorization": "Bearer sk-user-key"}
    assert kwargs["json"]["model"] == "gpt-4.1"
    assert kwargs["json"]["temperature"] == 0.2
    assert "tools" not in kwargs["json"]
    assert reply.content == "Hello"


@pytest.mark.asyncio
async def test_tool_calls_are_parsed_into_langchain_calls():
    llm = SimpleNamespace(
        provider=ServiceProviders.OPENROUTER,
        model="openai/gpt-4.1",
        api_key="or-key",
        temperature=None,
        base_url="https://openrouter.ai/api/v1",
    )
    data = _reply(
        content="",
        tool_calls=[
            {
                "id": "call_1",
                "function": {
                    "name": "search_leads",
                    "arguments": '{"limit": 3, "crm_status": "new"}',
                },
            }
        ],
    )
    reply, post = await _ask(llm, data)

    assert post.await_args.args[0] == "https://openrouter.ai/api/v1/chat/completions"
    assert "temperature" not in post.await_args.kwargs["json"]
    assert len(reply.tool_calls) == 1
    call = reply.tool_calls[0]
    assert call["name"] == "search_leads"
    assert call["args"] == {"limit": 3, "crm_status": "new"}
    assert call["id"] == "call_1"


@pytest.mark.asyncio
async def test_reasoning_models_do_not_receive_temperature():
    llm = SimpleNamespace(
        provider=ServiceProviders.OPENAI,
        model="gpt-5-mini",
        api_key="sk-user-key",
        temperature=0.1,
        base_url=None,
    )
    _, post = await _ask(llm, _reply())
    assert "temperature" not in post.await_args.kwargs["json"]


@pytest.mark.asyncio
async def test_google_uses_openai_compatible_endpoint():
    llm = SimpleNamespace(
        provider=ServiceProviders.GOOGLE,
        model="gemini-3.5-flash",
        api_key="google-key",
        temperature=None,
    )
    _, post = await _ask(llm, _reply())
    assert post.await_args.args[0] == (
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    )
    assert post.await_args.kwargs["json"]["model"] == "gemini-3.5-flash"


@pytest.mark.asyncio
async def test_azure_uses_deployment_url_and_api_key_header():
    llm = SimpleNamespace(
        provider=ServiceProviders.AZURE,
        model="my-deployment",
        api_key="azure-key",
        temperature=None,
        endpoint="https://res.openai.azure.com/",
    )
    _, post = await _ask(llm, _reply())

    assert post.await_args.args[0] == (
        "https://res.openai.azure.com/openai/deployments/my-deployment/"
        "chat/completions?api-version=2024-10-21"
    )
    assert post.await_args.kwargs["headers"] == {"api-key": "azure-key"}
    assert "model" not in post.await_args.kwargs["json"]


@pytest.mark.asyncio
async def test_bolo_hosted_inference_is_refused():
    llm = SimpleNamespace(provider=ServiceProviders.BOLO, model="default")
    with pytest.raises(CopilotConfigurationError, match="your own LLM key"):
        await _ask(llm, _reply())


@pytest.mark.asyncio
async def test_missing_key_is_a_configuration_error():
    llm = SimpleNamespace(
        provider=ServiceProviders.OPENAI,
        model="gpt-4.1",
        api_key="",
        temperature=None,
        base_url=None,
    )
    with pytest.raises(CopilotConfigurationError, match="API key"):
        await _ask(llm, _reply())


@pytest.mark.asyncio
async def test_upstream_status_is_sanitized():
    llm = SimpleNamespace(
        provider=ServiceProviders.OPENAI,
        model="gpt-4.1",
        api_key="sk-user-key",
        temperature=None,
        base_url=None,
    )
    model = CopilotChatModel(organization_id=3, user_id="u1")
    with (
        patch(
            "api.services.copilot.model.get_resolved_ai_model_configuration",
            new=AsyncMock(return_value=_resolve(llm)),
        ),
        patch.object(
            httpx.AsyncClient,
            "post",
            new=AsyncMock(return_value=_Response({}, status_code=429)),
        ),
    ):
        with pytest.raises(CopilotUpstreamError) as excinfo:
            await model.ainvoke([HumanMessage(content="hi")])
    assert excinfo.value.status_code == 429
    assert "sk-user-key" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_malformed_tool_arguments_stop_the_turn():
    llm = SimpleNamespace(
        provider=ServiceProviders.OPENAI,
        model="gpt-4.1",
        api_key="sk-user-key",
        temperature=None,
        base_url=None,
    )
    data = _reply(
        tool_calls=[
            {
                "id": "call_1",
                "function": {"name": "search_leads", "arguments": "{not json"},
            }
        ]
    )
    with pytest.raises(CopilotUpstreamError, match="malformed tool arguments"):
        await _ask(llm, data)
