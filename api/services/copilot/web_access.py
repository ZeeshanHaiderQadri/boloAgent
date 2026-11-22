"""Organization-level web access for the Bolo Agent.

Connectors live under one ``organization_configurations`` row. Provider API
keys are stored as Fernet envelopes and are never returned to the UI.
Keyless providers (Exa) are available without a key and are enabled by
default; everything else stays off until the owner turns it on.

Fetches treat URLs as untrusted input: only http(s) on standard ports, DNS is
resolved once and the checked address is the one dialed, redirects are
re-validated, and the response size is capped.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpcore
import httpx
from loguru import logger

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.services.credentials.cipher import (
    CredentialEncryptionError,
    decrypt_credential_data,
    encrypt_credential_data,
)
from api.utils.url_security import (
    PinnedTarget,
    UnsafeOutboundURLError,
    resolve_public_http_url,
)

CONFIG_KEY = OrganizationConfigurationKey.COPILOT_WEB_ACCESS.value

DEFAULT_RESULTS = 5
MAX_RESULTS = 10
MAX_FETCH_CHARACTERS = 8000
REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=8.0, read=15.0, write=10.0, pool=5.0)
MAX_PAGE_BYTES = 1024 * 1024
MAX_REDIRECTS = 3
USER_AGENT = "BoloAgentWebAccess/1.0 (+https://boloagent.com)"

EXA_MCP_URL = "https://mcp.exa.ai/mcp"
EXA_MCP_TIMEOUT = 25.0
EXA_DEFAULT_OBJECTIVE = (
    "Find the most relevant, current public web pages for the user's question."
)

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class WebAccessError(ValueError):
    """Web access is unavailable or a provider rejected the request."""


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    label: str
    keyless: bool
    search: bool
    fetch: bool
    docs_url: str


PROVIDERS: dict[str, ProviderSpec] = {
    spec.name: spec
    for spec in (
        ProviderSpec(
            name="exa",
            label="Exa",
            keyless=True,
            search=True,
            fetch=True,
            docs_url="https://exa.ai/docs/get-started/exa-mcp",
        ),
        ProviderSpec(
            name="tavily",
            label="Tavily",
            keyless=False,
            search=True,
            fetch=False,
            docs_url="https://docs.tavily.com",
        ),
        ProviderSpec(
            name="firecrawl",
            label="Firecrawl",
            keyless=False,
            search=True,
            fetch=True,
            docs_url="https://docs.firecrawl.dev",
        ),
        ProviderSpec(
            name="serper",
            label="Serper",
            keyless=False,
            search=True,
            fetch=False,
            docs_url="https://serper.dev",
        ),
        ProviderSpec(
            name="serpapi",
            label="SerpAPI",
            keyless=False,
            search=True,
            fetch=False,
            docs_url="https://serpapi.com",
        ),
    )
}

_PROVIDER_ORDER = tuple(PROVIDERS)


@dataclass(frozen=True)
class _ProviderState:
    spec: ProviderSpec
    enabled: bool
    api_key: str | None


async def _stored_providers(organization_id: int) -> dict:
    row = await db_client.get_configuration(organization_id, CONFIG_KEY)
    value = row.value if row is not None else None
    if not isinstance(value, dict):
        return {}
    providers = value.get("providers")
    return providers if isinstance(providers, dict) else {}


def _api_key(credentials: object) -> str | None:
    if not isinstance(credentials, dict) or not credentials:
        return None
    try:
        data = decrypt_credential_data(credentials)
    except CredentialEncryptionError:
        logger.warning("A stored web access credential could not be decrypted")
        return None
    key = data.get("api_key")
    if isinstance(key, str) and key.strip():
        return key.strip()
    return None


def _state(providers: dict, name: str) -> _ProviderState:
    spec = PROVIDERS[name]
    entry = providers.get(name)
    entry = entry if isinstance(entry, dict) else {}
    enabled = entry.get("enabled")
    if enabled is None:
        enabled = spec.keyless
    return _ProviderState(
        spec=spec,
        enabled=bool(enabled),
        api_key=_api_key(entry.get("credentials")),
    )


def _states(providers: dict) -> list[_ProviderState]:
    return [_state(providers, name) for name in _PROVIDER_ORDER]


def _usable(state: _ProviderState) -> bool:
    return state.enabled and (state.spec.keyless or state.api_key is not None)


async def public_settings(organization_id: int) -> dict:
    states = _states(await _stored_providers(organization_id))
    return {
        "providers": [
            {
                "name": state.spec.name,
                "label": state.spec.label,
                "keyless": state.spec.keyless,
                "enabled": state.enabled,
                "configured": state.spec.keyless or state.api_key is not None,
                "supports_search": state.spec.search,
                "supports_fetch": state.spec.fetch,
                "docs_url": state.spec.docs_url,
            }
            for state in states
        ],
        "any_enabled": any(_usable(state) for state in states),
    }


async def enabled_provider_labels(organization_id: int) -> list[str]:
    """Labels of providers the agent may use this turn; empty means off."""
    return [
        state.spec.label
        for state in _states(await _stored_providers(organization_id))
        if _usable(state)
    ]


async def update_settings(organization_id: int, updates: list[dict]) -> dict:
    providers = dict(await _stored_providers(organization_id))
    for update in updates:
        name = update.get("name")
        if name not in PROVIDERS:
            raise WebAccessError(f"Unknown web access provider: {name!r}")
        entry = dict(providers.get(name) or {})
        if update.get("enabled") is not None:
            entry["enabled"] = bool(update["enabled"])
        if update.get("clear_key"):
            entry.pop("credentials", None)
        api_key = update.get("api_key")
        if isinstance(api_key, str) and api_key.strip():
            entry["credentials"] = encrypt_credential_data({"api_key": api_key.strip()})
        providers[name] = entry
    await db_client.upsert_configuration(
        organization_id, CONFIG_KEY, {"providers": providers}
    )
    return await public_settings(organization_id)


async def search(
    organization_id: int, query: str, max_results: int = DEFAULT_RESULTS
) -> dict:
    states = _states(await _stored_providers(organization_id))
    attempts: list[str] = []
    for state in states:
        if not _usable(state) or not state.spec.search:
            continue
        limit = max(1, min(int(max_results), MAX_RESULTS))
        try:
            results = await _search_with(state, query.strip(), limit)
        except WebAccessError as exc:
            attempts.append(f"{state.spec.label}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 — one provider must not break the rest
            logger.warning(f"Web search via {state.spec.name} failed: {exc!r}")
            attempts.append(f"{state.spec.label} failed")
            continue
        if results:
            return {
                "provider": state.spec.label,
                "query": query.strip(),
                "results": results,
            }
        attempts.append(f"{state.spec.label} returned no results")
    if attempts:
        raise WebAccessError(
            "Web search did not return results. " + "; ".join(attempts)
        )
    raise WebAccessError(
        "Web access is off. Turn on a provider under Settings -> Web access."
    )


async def fetch(organization_id: int, url: str) -> dict:
    states = _states(await _stored_providers(organization_id))
    usable = [state for state in states if _usable(state)]
    if not usable:
        raise WebAccessError(
            "Web access is off. Turn on a provider under Settings -> Web access."
        )

    _ensure_http_url(url)
    attempts: list[str] = []
    for state in usable:
        if not state.spec.fetch:
            continue
        try:
            if state.spec.name == "firecrawl":
                content = await _firecrawl_fetch(state.api_key or "", url)
            else:
                content = await _exa_fetch(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Web fetch via {state.spec.name} failed: {exc!r}")
            attempts.append(state.spec.label)
            continue
        if content.strip():
            return _page_payload(state.spec.label, url, content)

    try:
        content = await _plain_fetch(url)
    except WebAccessError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Direct web fetch failed for {url}: {exc!r}")
        raise WebAccessError(
            "That page could not be read. Check the URL and try again."
        ) from exc
    provider = "Direct fetch" if not attempts else "Direct fetch (fallback)"
    return _page_payload(provider, url, content)


def _page_payload(provider: str, url: str, content: str) -> dict:
    text = content.strip()
    truncated = len(text) > MAX_FETCH_CHARACTERS
    return {
        "provider": provider,
        "url": url,
        "content": text[:MAX_FETCH_CHARACTERS],
        "truncated": truncated,
    }


def _ensure_http_url(url: str) -> None:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WebAccessError("Only http and https URLs can be read.")


async def _search_with(state: _ProviderState, query: str, limit: int) -> list[dict]:
    if state.spec.name == "exa":
        return await _exa_search(query, limit)
    if state.spec.name == "tavily":
        return await _tavily_search(state.api_key or "", query, limit)
    if state.spec.name == "firecrawl":
        return await _firecrawl_search(state.api_key or "", query, limit)
    if state.spec.name == "serper":
        return await _serper_search(state.api_key or "", query, limit)
    return await _serpapi_search(state.api_key or "", query, limit)


async def _exa_search(query: str, limit: int) -> list[dict]:
    text = await _exa_mcp_call(
        "web_search_exa",
        {
            "query": query,
            "numResults": limit,
            "objective": EXA_DEFAULT_OBJECTIVE,
        },
    )
    results = _parse_exa_results(text)
    if results:
        return results[:limit]
    raise WebAccessError("Exa returned no readable results")


async def _exa_fetch(url: str) -> str:
    text = await _exa_mcp_call(
        "web_fetch_exa",
        {"urls": [url], "maxCharacters": MAX_FETCH_CHARACTERS},
    )
    return text


async def _exa_mcp_call(tool: str, arguments: dict) -> str:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def run() -> str:
        async with streamable_http_client(EXA_MCP_URL) as streams:
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments)
                if getattr(result, "is_error", False):
                    raise WebAccessError("Exa rejected the request")
                text = ""
                for content in getattr(result, "content", []) or []:
                    part = getattr(content, "text", None)
                    if part:
                        text += part
                return text

    try:
        async with asyncio.timeout(EXA_MCP_TIMEOUT):
            return await run()
    except TimeoutError as exc:
        raise WebAccessError("Exa did not answer in time") from exc


_EXA_TITLE_RE = re.compile(r"(?m)^Title:\s*(.+)$")
_EXA_URL_RE = re.compile(r"(?m)^URL:\s*(\S+)$")


def _parse_exa_results(text: str) -> list[dict]:
    """Turn Exa's Markdown result blocks into title/url/snippet records."""
    titles = list(_EXA_TITLE_RE.finditer(text))
    results: list[dict] = []
    for index, match in enumerate(titles):
        end = titles[index + 1].start() if index + 1 < len(titles) else len(text)
        block = text[match.end() : end]
        url_match = _EXA_URL_RE.search(block)
        if url_match is None:
            continue
        snippet = block[url_match.end() :].strip()
        snippet = re.sub(r"(?m)^(Published|Author|Highlights):.*$", "", snippet)
        snippet = re.sub(r"\s+", " ", snippet).strip()
        results.append(
            {
                "title": match.group(1).strip(),
                "url": url_match.group(1).strip(),
                "snippet": snippet[:500],
            }
        )
    return results


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
        trust_env=False,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )


async def _post_json(url: str, *, headers: dict, body: dict) -> dict:
    async with _client() as client:
        response = await client.post(url, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        raise WebAccessError("The search provider returned an unexpected payload")
    return data


async def _tavily_search(api_key: str, query: str, limit: int) -> list[dict]:
    data = await _post_json(
        "https://api.tavily.com/search",
        headers={"Content-Type": "application/json"},
        body={
            "api_key": api_key,
            "query": query,
            "max_results": limit,
            "include_answer": False,
        },
    )
    return [
        {
            "title": item.get("title") or item.get("url") or "",
            "url": item.get("url") or "",
            "snippet": (item.get("content") or "")[:500],
        }
        for item in data.get("results") or []
        if isinstance(item, dict) and item.get("url")
    ]


async def _firecrawl_search(api_key: str, query: str, limit: int) -> list[dict]:
    data = await _post_json(
        "https://api.firecrawl.dev/v1/search",
        headers={"Authorization": f"Bearer {api_key}"},
        body={"query": query, "limit": limit},
    )
    return [
        {
            "title": item.get("title") or item.get("url") or "",
            "url": item.get("url") or "",
            "snippet": (item.get("description") or "")[:500],
        }
        for item in data.get("data") or []
        if isinstance(item, dict) and item.get("url")
    ]


async def _serper_search(api_key: str, query: str, limit: int) -> list[dict]:
    data = await _post_json(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": api_key},
        body={"q": query, "num": limit},
    )
    return [
        {
            "title": item.get("title") or "",
            "url": item.get("link") or "",
            "snippet": (item.get("snippet") or "")[:500],
        }
        for item in data.get("organic") or []
        if isinstance(item, dict) and item.get("link")
    ]


async def _serpapi_search(api_key: str, query: str, limit: int) -> list[dict]:
    async with _client() as client:
        response = await client.get(
            "https://serpapi.com/search.json",
            params={"q": query, "num": limit, "api_key": api_key},
        )
        response.raise_for_status()
        data = response.json()
    return [
        {
            "title": item.get("title") or "",
            "url": item.get("link") or "",
            "snippet": (item.get("snippet") or "")[:500],
        }
        for item in data.get("organic_results") or []
        if isinstance(item, dict) and item.get("link")
    ]


async def _firecrawl_fetch(api_key: str, url: str) -> str:
    data = await _post_json(
        "https://api.firecrawl.dev/v1/scrape",
        headers={"Authorization": f"Bearer {api_key}"},
        body={"url": url, "formats": ["markdown"], "onlyMainContent": True},
    )
    payload = data.get("data")
    if isinstance(payload, dict):
        return str(payload.get("markdown") or payload.get("content") or "")
    return ""


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Dial the address the egress check already validated."""

    def __init__(self, target: PinnedTarget) -> None:
        self._host = target.host.lower()
        self._ip = target.ip
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        target = self._ip if host.lower() == self._host else host
        return await self._inner.connect_tcp(
            target,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options=None
    ):
        return await self._inner.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options
        )

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _PinnedTransport(httpx.AsyncHTTPTransport):
    def __init__(self, target: PinnedTarget) -> None:
        super().__init__(
            verify=True,
            trust_env=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        )
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=True, trust_env=False),
            max_connections=1,
            max_keepalive_connections=0,
            network_backend=_PinnedNetworkBackend(target),
        )


def _pinned_client(target: PinnedTarget) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=_PinnedTransport(target),
        timeout=REQUEST_TIMEOUT,
        follow_redirects=False,
        trust_env=False,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
        },
    )


async def _plain_fetch(url: str) -> str:
    current = url.strip()
    for _ in range(MAX_REDIRECTS + 1):
        try:
            target = resolve_public_http_url(current)
        except UnsafeOutboundURLError as exc:
            raise WebAccessError(str(exc)) from exc
        async with _pinned_client(target) as client:
            async with client.stream("GET", current) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise WebAccessError("That page redirected without a target")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    remaining = MAX_PAGE_BYTES - len(body)
                    if remaining <= 0:
                        break
                    body.extend(chunk[:remaining])
                charset = response.charset_encoding or "utf-8"
                html = bytes(body).decode(charset, errors="replace")
                return _html_to_text(html)
    raise WebAccessError("That page redirected too many times")


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "template"}
    _BREAK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skipped = 0
        self._parts: list[str] = []
        self.title: str = ""

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._SKIP:
            self._skipped += 1
        elif tag in self._BREAK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skipped:
            self._skipped -= 1
        elif tag in self._BREAK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skipped:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if text:
            self._parts.append(text)


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — malformed markup still yields text
        pass
    lines = [
        re.sub(r"[ \t]+", " ", line).strip()
        for line in "".join(parser._parts).splitlines()
    ]
    text = "\n".join(line for line in lines if line)
    return text[: MAX_FETCH_CHARACTERS * 2]
