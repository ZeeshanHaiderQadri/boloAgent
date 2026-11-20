"""Discover a business's own social profiles from its scraped website.

Google Maps does not expose social accounts, so the only first-party source is
the business website itself. We fetch the site (and one contact page when the
homepage has no profiles) and keep only links whose host and path identify a
real profile on Instagram, X, TikTok or YouTube. Nothing is invented: a lead
without a discoverable profile simply has no icon.

Everything here treats website URLs as untrusted input:

* DNS is resolved once per request and the checked address is the one dialed,
  so a hostname cannot be rebound to loopback between validation and connect.
* Each lead has an absolute 15 second deadline, a 512 KB total response
  budget and at most four redirects, shared across robots.txt, the homepage
  and the contact page.
* robots.txt is honoured for both pages, and a scan never fetches more than
  the homepage plus one contact page from a site.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpcore
import httpx
from loguru import logger

from api.utils.url_security import (
    PinnedTarget,
    ServiceHostResolutionError,
    UnsafeOutboundURLError,
    resolve_public_http_url,
)

SOCIAL_PLATFORMS = ("instagram", "x", "tiktok", "youtube")

SCAN_TIMEOUT_SECONDS = 15.0
MAX_PAGE_BYTES = 512 * 1024
MAX_REDIRECTS_PER_SCAN = 4
USER_AGENT = "BoloAgentLinkChecker/1.0 (+https://boloagent.com)"
REQUEST_TIMEOUT = httpx.Timeout(8.0, connect=5.0, read=8.0, write=8.0, pool=5.0)

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_SCRIPT_RE = re.compile(
    r"<script\b(?P<attrs>[^>]*)>(?P<body>.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
_ANCHOR_RE = re.compile(
    r"""<a\b[^>]*\bhref\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL
)
_LINK_TAG_RE = re.compile(r"<link\b(?P<attrs>[^>]*)>", re.IGNORECASE)
_META_TAG_RE = re.compile(r"<meta\b(?P<attrs>[^>]*)>", re.IGNORECASE)

_INSTAGRAM_HANDLE_RE = re.compile(r"[A-Za-z0-9._]{1,30}")
_X_HANDLE_RE = re.compile(r"[A-Za-z0-9_]{1,15}")
_TIKTOK_HANDLE_RE = re.compile(r"[A-Za-z0-9._]{1,24}")
_YOUTUBE_HANDLE_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
_YOUTUBE_CHANNEL_RE = re.compile(r"UC[A-Za-z0-9_-]{10,60}")

_INSTAGRAM_RESERVED = {
    "about",
    "accounts",
    "api",
    "blog",
    "business",
    "challenge",
    "developer",
    "developers",
    "direct",
    "directory",
    "emails",
    "embed",
    "explore",
    "help",
    "legal",
    "p",
    "privacy",
    "reel",
    "reels",
    "session",
    "share",
    "sharer",
    "stories",
    "terms",
    "tv",
    "web",
}

_X_RESERVED = {
    "about",
    "account",
    "compose",
    "download",
    "explore",
    "hashtag",
    "help",
    "home",
    "i",
    "intent",
    "login",
    "logout",
    "messages",
    "notifications",
    "privacy",
    "search",
    "settings",
    "share",
    "signup",
    "tos",
}

_YOUTUBE_RESERVED = {
    "about",
    "account",
    "embed",
    "feed",
    "playlist",
    "results",
    "shorts",
    "signin",
    "watch",
}

_CONTACT_KEYWORDS = ("contact", "kontakt", "contacto", "contatti", "impressum")

# Multi-label public suffixes common enough to matter for brand matching.
# This is a relevance heuristic, not a security boundary; unknown suffixes
# fall back to the final two labels.
_MULTI_LABEL_SUFFIXES = {
    "co.uk",
    "org.uk",
    "ac.uk",
    "gov.uk",
    "com.au",
    "net.au",
    "org.au",
    "co.nz",
    "net.nz",
    "org.nz",
    "co.jp",
    "ne.jp",
    "or.jp",
    "co.in",
    "net.in",
    "org.in",
    "com.br",
    "com.mx",
    "com.ar",
    "com.co",
    "com.sg",
    "com.hk",
    "com.tw",
    "com.tr",
    "co.za",
    "co.il",
    "co.kr",
    "co.id",
    "com.ph",
    "com.my",
    "co.th",
    "com.vn",
    "com.pk",
    "com.sa",
    "com.eg",
    "com.ng",
    "com.pe",
    "com.ec",
    "com.uy",
    "com.py",
    "com.bo",
    "com.do",
    "com.gt",
    "com.sv",
    "com.hn",
    "com.ni",
    "com.pa",
    "com.cr",
    "co.ke",
    "co.ug",
    "co.tz",
    "com.gh",
}

# Words that say nothing about which business owns a handle. Template
# footers link the platform vendor's own account ("squarespace", "facebook");
# category words ("pizza", "salon") are shared by thousands of unrelated
# businesses. Requiring a non-generic brand word keeps both out.
_GENERIC_BRAND_WORDS = {
    "about",
    "and",
    "auto",
    "bake",
    "bakery",
    "bar",
    "barber",
    "bbq",
    "beauty",
    "bistro",
    "brew",
    "brewery",
    "burger",
    "cafe",
    "cakes",
    "care",
    "carpet",
    "cleaners",
    "cleaning",
    "clinic",
    "coffee",
    "company",
    "contracting",
    "deli",
    "dental",
    "dentist",
    "donut",
    "donuts",
    "electric",
    "electrical",
    "food",
    "grill",
    "group",
    "gym",
    "hair",
    "health",
    "home",
    "hotel",
    "inc",
    "italian",
    "kitchen",
    "landscaping",
    "laundry",
    "law",
    "llc",
    "ltd",
    "market",
    "massage",
    "mexican",
    "nails",
    "official",
    "pasta",
    "pet",
    "pets",
    "pizza",
    "plumbing",
    "restaurant",
    "roofing",
    "salon",
    "service",
    "services",
    "shop",
    "spa",
    "store",
    "studio",
    "sushi",
    "taco",
    "tacos",
    "tattoo",
    "thai",
    "the",
    "wine",
    "yoga",
}

_ATTR_RE_CACHE: dict[str, re.Pattern[str]] = {}


class _ScanFailed(Exception):
    """Base class for a page fetch that did not produce usable HTML."""


class _ScanTransient(_ScanFailed):
    """A retryable failure: timeout, DNS outage, TLS error or 5xx."""


class _ScanTerminal(_ScanFailed):
    """A permanent outcome: bad URL, robots denial, 4xx or a redirect loop."""

    def __init__(self, reason: str, *, status: int | None = None) -> None:
        super().__init__(reason)
        self.status = status


@dataclass(frozen=True)
class SocialScan:
    """Outcome of scanning one website.

    ``checked`` means the lead should not be retried automatically.
    ``replace_links`` means the fetch actually produced an authoritative page,
    so stored links may be replaced (with an empty dict when nothing matched).
    """

    links: dict[str, str]
    checked: bool
    replace_links: bool
    error: str | None = None


@dataclass(frozen=True)
class _Page:
    text: str
    final_url: str


@dataclass
class _Budget:
    deadline: float
    redirects: int = MAX_REDIRECTS_PER_SCAN
    remaining_bytes: int = MAX_PAGE_BYTES

    def has_time(self) -> bool:
        return time.monotonic() < self.deadline


@dataclass(frozen=True)
class _RobotsRules:
    parser: RobotFileParser | None
    disallow_all: bool = False


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Dial the address the egress check already validated.

    httpcore still uses the original host for the ``Host`` header and for TLS
    SNI / certificate verification, because only the TCP destination changes.
    """

    def __init__(self, pins: dict[tuple[str, int], str], *, inner=None) -> None:
        self._pins = {(host.lower(), port): ip for (host, port), ip in pins.items()}
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        target = self._pins.get((host.lower(), port), host)
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


def _make_backend(pins: dict[tuple[str, int], str]):
    return _PinnedNetworkBackend(pins)


class _PinnedTransport(httpx.AsyncHTTPTransport):
    def __init__(self, target: PinnedTarget) -> None:
        limits = httpx.Limits(max_connections=1, max_keepalive_connections=0)
        super().__init__(verify=True, trust_env=False, limits=limits)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=True, trust_env=False),
            max_connections=1,
            max_keepalive_connections=0,
            network_backend=_make_backend({(target.host, target.port): target.ip}),
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


# Tests replace this seam to keep local servers reachable without weakening
# the production SSRF policy.
_resolve_target = resolve_public_http_url


def normalize_website(website: str | None) -> str | None:
    """Return a usable absolute http(s) URL, or None for blank/malformed input."""
    text = (website or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = f"https://{text.lstrip('/')}"
    try:
        parsed = urlparse(text)
        # Accessing ``port`` validates bracketed IPv6 and numeric ports.
        parsed.port
        host = (parsed.hostname or "").encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or any(ch.isspace() for ch in host)
    ):
        return None
    return text


def _host_matches(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")


def _registrable_domain(hostname: str) -> str:
    labels = [part for part in hostname.lower().rstrip(".").split(".") if part]
    if len(labels) <= 2:
        return ".".join(labels)
    if ".".join(labels[-2:]) in _MULTI_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _clean_candidate(value: str) -> str:
    return value.rstrip(".,;:)]}'\">")


def _attr(attrs: str, name: str) -> str | None:
    pattern = _ATTR_RE_CACHE.get(name)
    if pattern is None:
        pattern = re.compile(
            rf"""\b{re.escape(name)}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""",
            re.IGNORECASE,
        )
        _ATTR_RE_CACHE[name] = pattern
    match = pattern.search(attrs)
    if not match:
        return None
    return next((group for group in match.groups() if group is not None), None)


def _social_target(parsed) -> tuple[str, str] | None:
    """Return ``(platform, canonical_url)`` for a profile URL, else None."""
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return None

    if _host_matches(host, "instagram.com"):
        segments = [part for part in parsed.path.split("/") if part]
        if not segments:
            return None
        handle = segments[0]
        if handle.lower() in _INSTAGRAM_RESERVED or not _INSTAGRAM_HANDLE_RE.fullmatch(
            handle
        ):
            return None
        return "instagram", f"https://www.instagram.com/{handle}/"

    if _host_matches(host, "x.com") or _host_matches(host, "twitter.com"):
        segments = [part for part in parsed.path.split("/") if part]
        if not segments:
            return None
        handle = segments[0]
        if handle.lower() in _X_RESERVED or not _X_HANDLE_RE.fullmatch(handle):
            return None
        return "x", f"https://x.com/{handle}"

    if _host_matches(host, "tiktok.com"):
        segments = [part for part in parsed.path.split("/") if part]
        if not segments or not segments[0].startswith("@"):
            return None
        handle = segments[0][1:]
        if not _TIKTOK_HANDLE_RE.fullmatch(handle):
            return None
        return "tiktok", f"https://www.tiktok.com/@{handle}"

    if _host_matches(host, "youtube.com"):
        segments = [part for part in parsed.path.split("/") if part]
        if not segments:
            return None
        head = segments[0].lower()
        if head in _YOUTUBE_RESERVED:
            return None
        if head.startswith("@") and _YOUTUBE_HANDLE_RE.fullmatch(segments[0][1:]):
            return "youtube", f"https://www.youtube.com/{segments[0]}"
        if (
            head in {"channel", "c", "user"}
            and len(segments) > 1
            and _YOUTUBE_HANDLE_RE.fullmatch(segments[1])
        ):
            name = segments[1]
            if head == "channel" and not _YOUTUBE_CHANNEL_RE.fullmatch(name):
                return None
            return "youtube", f"https://www.youtube.com/{head}/{name}"
        return None

    return None


def social_link_from_url(url: str | None) -> dict[str, str]:
    """A profile link when the lead's website URL is itself a social URL."""
    normalized = normalize_website(url)
    if not normalized:
        return {}
    parsed = urlparse(normalized)
    target = _social_target(parsed)
    if target is None:
        return {}
    platform, canonical = target
    return {platform: canonical}


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _profile_handle(platform: str, canonical: str) -> str | None:
    path = urlparse(canonical).path
    segments = [part for part in path.split("/") if part]
    if not segments:
        return None
    if platform == "instagram" or platform == "x":
        return segments[0]
    if platform == "tiktok":
        return segments[0].lstrip("@")
    if platform == "youtube":
        if segments[0].startswith("@"):
            return segments[0][1:]
        # Opaque channel IDs carry no readable brand; they are only accepted
        # from owner-declared JSON-LD sameAs data.
        if segments[0].lower() == "channel":
            return None
        return segments[1] if len(segments) > 1 else None
    return None


def _brand_keys(base_url: str, business_name: str | None) -> set[str]:
    host = (urlparse(base_url).hostname or "").lower()
    # Only the registrable domain's main label. Subdomains such as shop. or
    # mail. are infrastructure, not brand names.
    main_label = _registrable_domain(host).split(".")[0]
    candidates = {_normalize_key(main_label)}
    for word in re.split(r"[^A-Za-z0-9]+", business_name or ""):
        candidates.add(_normalize_key(word))
    return {
        key for key in candidates if len(key) >= 4 and key not in _GENERIC_BRAND_WORDS
    }


def _handle_matches(handle_key: str, brand_keys: set[str]) -> bool:
    return any(
        handle_key == key or handle_key.startswith(key) or key.startswith(handle_key)
        for key in brand_keys
    )


def _is_business_profile(
    platform: str,
    canonical: str,
    *,
    base_url: str,
    business_name: str | None,
    structured: bool,
) -> bool:
    handle = _profile_handle(platform, canonical)
    if handle is None:
        # An opaque YouTube channel ID is accepted only when the site's own
        # JSON-LD declares it, never from a plain footer anchor.
        return platform == "youtube" and structured
    handle_key = _normalize_key(handle)
    if len(handle_key) < 4:
        return False
    return _handle_matches(handle_key, _brand_keys(base_url, business_name))


def _visible_markup(html: str) -> str:
    """Drop comments and non-JSON-LD scripts before scanning for links."""
    text = _COMMENT_RE.sub(" ", html)

    def replace_script(match: re.Match[str]) -> str:
        attrs = (match.group("attrs") or "").lower()
        return match.group(0) if "ld+json" in attrs else " "

    return _SCRIPT_RE.sub(replace_script, text)


def _collect_same_as(node, out: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "sameAs":
                if isinstance(value, str):
                    out.append(value)
                elif isinstance(value, list):
                    out.extend(item for item in value if isinstance(item, str))
            else:
                _collect_same_as(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_same_as(item, out)


def _json_ld_same_as(html: str) -> list[str]:
    values: list[str] = []
    for match in _SCRIPT_RE.finditer(html):
        if "ld+json" not in (match.group("attrs") or "").lower():
            continue
        try:
            data = json.loads(match.group("body"))
        except (TypeError, ValueError):
            continue
        _collect_same_as(data, values)
    return values


def _parse_candidate(value: str, base_url: str) -> tuple[str, str] | None:
    candidate = _clean_candidate(value.strip())
    if not candidate:
        return None
    if candidate.startswith("//"):
        candidate = f"{urlparse(base_url).scheme}:{candidate}"
    try:
        parsed = urlparse(urljoin(base_url, candidate))
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    return _social_target(parsed)


def _page_candidates(markup: str, base_url: str):
    for match in _ANCHOR_RE.finditer(markup):
        yield match.group(2)
    for match in _LINK_TAG_RE.finditer(markup):
        attrs = match.group("attrs")
        rel = (_attr(attrs, "rel") or "").lower().split()
        if "me" not in rel:
            continue
        href = _attr(attrs, "href")
        if href:
            yield href
    for match in _META_TAG_RE.finditer(markup):
        attrs = match.group("attrs")
        key = (_attr(attrs, "property") or _attr(attrs, "name") or "").lower()
        if key != "og:see_also":
            continue
        content = _attr(attrs, "content")
        if content:
            yield content


def extract_social_links(
    html: str, base_url: str, *, business_name: str | None = None
) -> dict[str, str]:
    """Pull owner-declared profile links out of a page, one per platform.

    Only real anchors, ``<link rel="me">`` / ``og:see_also`` metadata and
    JSON-LD ``sameAs`` entries are read. Comments and inline scripts are
    ignored, so copy-pasted snippets cannot inject a profile link.
    """
    if not html:
        return {}

    links: dict[str, str] = {}
    for raw in _json_ld_same_as(html):
        target = _parse_candidate(raw, base_url)
        if target is None:
            continue
        platform, canonical = target
        if platform in links:
            continue
        if not _is_business_profile(
            platform,
            canonical,
            base_url=base_url,
            business_name=business_name,
            structured=True,
        ):
            continue
        links[platform] = canonical

    for raw in _page_candidates(_visible_markup(html), base_url):
        target = _parse_candidate(raw, base_url)
        if target is None:
            continue
        platform, canonical = target
        if platform in links:
            continue
        if not _is_business_profile(
            platform,
            canonical,
            base_url=base_url,
            business_name=business_name,
            structured=False,
        ):
            continue
        links[platform] = canonical
        if len(links) == len(SOCIAL_PLATFORMS):
            break

    return {
        platform: links[platform] for platform in SOCIAL_PLATFORMS if platform in links
    }


def _find_contact_url(html: str, base_url: str) -> str | None:
    base_domain = _registrable_domain(
        (urlparse(base_url).hostname or "").lower().removeprefix("www.")
    )
    for match in _ANCHOR_RE.finditer(_visible_markup(html)):
        href = match.group(2).strip()
        if not href or href.startswith(("mailto:", "tel:", "#", "javascript:")):
            continue
        candidate = _clean_candidate(urljoin(base_url, href))
        try:
            parsed = urlparse(candidate)
        except ValueError:
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        host = parsed.hostname.lower().removeprefix("www.")
        if _registrable_domain(host) != base_domain:
            continue
        path = parsed.path.lower()
        if any(keyword in path for keyword in _CONTACT_KEYWORDS):
            return candidate
    return None


def _is_facebook_site(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(
        _host_matches(host, domain) for domain in ("facebook.com", "fb.com", "fb.me")
    )


async def _fetch_page(url: str, budget: _Budget, *, kind: str = "html") -> _Page:
    """Fetch one page, following redirects with a fresh pinned address each hop."""
    current = url
    while True:
        if not budget.has_time():
            raise _ScanTransient("scan deadline reached")
        try:
            target = _resolve_target(current)
        except UnsafeOutboundURLError as exc:
            raise _ScanTerminal(str(exc)) from exc
        except ServiceHostResolutionError as exc:
            raise _ScanTransient("dns resolution failed") from exc

        try:
            async with _pinned_client(target) as client:
                async with client.stream("GET", current) as response:
                    status = response.status_code
                    if status in _REDIRECT_STATUSES:
                        if budget.redirects <= 0:
                            raise _ScanTerminal("too many redirects")
                        budget.redirects -= 1
                        location = response.headers.get("location")
                        if not location:
                            raise _ScanTerminal("redirect without location")
                        current = urljoin(current, location)
                        continue
                    if status == 429 or status >= 500:
                        raise _ScanTransient(f"http {status}")
                    if status >= 400:
                        raise _ScanTerminal(f"http {status}", status=status)

                    content_type = response.headers.get("content-type", "").lower()
                    if kind == "html" and content_type and "html" not in content_type:
                        raise _ScanTerminal("response is not html")
                    if kind == "text" and content_type and "text" not in content_type:
                        raise _ScanTerminal("response is not text")

                    chunks: list[bytes] = []
                    remaining = budget.remaining_bytes
                    async for chunk in response.aiter_bytes():
                        if remaining <= 0:
                            break
                        take = chunk[:remaining]
                        chunks.append(take)
                        remaining -= len(take)
                    budget.remaining_bytes = remaining

                    try:
                        encoding = response.encoding or "utf-8"
                    except (LookupError, ValueError):
                        encoding = "utf-8"
                    return _Page(
                        b"".join(chunks).decode(encoding, errors="replace"),
                        str(response.url),
                    )
        except httpx.HTTPError as exc:
            raise _ScanTransient(type(exc).__name__) from exc


async def _load_robots(base_url: str, budget: _Budget) -> _RobotsRules:
    if not budget.has_time():
        return _RobotsRules(None)
    try:
        page = await _fetch_page(urljoin(base_url, "/robots.txt"), budget, kind="text")
    except _ScanTransient:
        # An unreachable robots file is treated as absent, which is the
        # standard crawler behaviour for a 5xx or timeout.
        return _RobotsRules(None)
    except _ScanTerminal as exc:
        return _RobotsRules(None, disallow_all=exc.status in {401, 403})
    parser = RobotFileParser()
    parser.parse(page.text.splitlines())
    return _RobotsRules(parser)


def _robots_allows(rules: _RobotsRules, url: str) -> bool:
    if rules.disallow_all:
        return False
    if rules.parser is None:
        return True
    return rules.parser.can_fetch(USER_AGENT, url)


async def _scan_site(
    url: str, business_name: str | None, budget: _Budget
) -> SocialScan:
    rules = await _load_robots(url, budget)
    if not _robots_allows(rules, url):
        logger.info("robots.txt disallows social scan for {}", url)
        return SocialScan({}, checked=True, replace_links=False)

    page = await _fetch_page(url, budget)
    links = extract_social_links(page.text, page.final_url, business_name=business_name)
    if not links:
        contact_url = _find_contact_url(page.text, page.final_url)
        if contact_url and _robots_allows(rules, contact_url) and budget.has_time():
            try:
                contact_page = await _fetch_page(contact_url, budget)
            except _ScanTerminal:
                contact_page = None
            if contact_page is not None:
                links = extract_social_links(
                    contact_page.text,
                    contact_page.final_url,
                    business_name=business_name,
                )
    return SocialScan(links, checked=True, replace_links=True)


async def discover_social_links(
    website: str | None, *, business_name: str | None = None
) -> SocialScan:
    """Best-effort scan of a business website for its social profiles.

    The whole scan, including redirects and robots.txt, is bounded by
    ``SCAN_TIMEOUT_SECONDS``. A transient failure returns
    ``checked=False`` so the caller keeps existing links and retries later.
    """
    normalized = normalize_website(website)
    if normalized is None:
        return SocialScan({}, checked=True, replace_links=False)

    direct = social_link_from_url(normalized)
    if direct:
        return SocialScan(direct, checked=True, replace_links=True)

    if _is_facebook_site(normalized):
        # Facebook pages cannot be meaningfully scraped and expose no
        # first-party profile links, so record nothing and fetch nothing.
        logger.info("Skipping social scan for Facebook website {}", normalized)
        return SocialScan({}, checked=True, replace_links=False)

    budget = _Budget(deadline=time.monotonic() + SCAN_TIMEOUT_SECONDS)
    try:
        async with asyncio.timeout(SCAN_TIMEOUT_SECONDS):
            return await _scan_site(normalized, business_name, budget)
    except TimeoutError:
        logger.info("Social scan for {} hit the time budget", normalized)
        return SocialScan({}, checked=False, replace_links=False, error="timeout")
    except _ScanTransient as exc:
        logger.info("Social scan for {} failed temporarily: {}", normalized, exc)
        return SocialScan({}, checked=False, replace_links=False, error=str(exc))
    except _ScanTerminal as exc:
        logger.info("Social scan for {} ended without data: {}", normalized, exc)
        return SocialScan({}, checked=True, replace_links=False)
