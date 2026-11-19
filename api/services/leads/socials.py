"""Discover a business's own social profiles from its scraped website.

Google Maps does not expose social accounts, so the only first-party source is
the business website itself. We fetch the site (and one contact page when the
homepage has no profiles) and keep only links whose host and path identify a
real profile on Instagram, X, TikTok or YouTube. Nothing is invented: a lead
without a discoverable profile simply has no icon.

Everything here treats website URLs as untrusted input. Fetches are restricted
to public http(s) hosts on standard ports, redirects are re-validated hop by
hop, and responses are capped so a hostile page cannot exhaust the worker.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
from loguru import logger

from api.utils.url_security import (
    ServiceHostResolutionError,
    UnsafeOutboundURLError,
    validate_public_http_url,
)

SOCIAL_PLATFORMS = ("instagram", "x", "tiktok", "youtube")

MAX_PAGE_BYTES = 512 * 1024
REQUEST_TIMEOUT = httpx.Timeout(8.0, connect=5.0, read=8.0)
MAX_REDIRECTS = 3

# Matches absolute and protocol-relative URLs anywhere in the document. Social
# links appear in anchors, JSON-LD ``sameAs`` arrays and occasionally inline
# scripts, so scanning the whole payload catches all three.
_URL_RE = re.compile(r"""(?:https?:)?//[^\s"'<>()\\]+""", re.IGNORECASE)
_ANCHOR_RE = re.compile(
    r"""<a\b[^>]*\bhref\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL
)

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

# Words that say nothing about which business owns a handle. A template
# footer often links the platform vendor's own account ("squarespace",
# "facebook"); requiring the handle to relate to the site or brand name keeps
# those out without needing a hand-maintained denylist.
_GENERIC_BRAND_WORDS = {
    "about",
    "and",
    "austin",
    "bar",
    "cafe",
    "coffee",
    "company",
    "group",
    "inc",
    "llc",
    "ltd",
    "official",
    "restaurant",
    "shop",
    "store",
    "the",
}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        follow_redirects=False,
        trust_env=False,
        headers={
            "User-Agent": "BoloAgentLinkChecker/1.0 (+https://boloagent.com)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )


def _normalize_website(website: str | None) -> str | None:
    text = (website or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = f"https://{text.lstrip('/')}"
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return text


def _host_matches(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")


def _clean_candidate(value: str) -> str:
    return value.rstrip(".,;:)]}'\">")


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
    normalized = _normalize_website(url)
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
        # Opaque channel IDs are only ever linked by the owner; custom and
        # legacy paths carry a name that can be checked like a handle.
        if segments[0].lower() == "channel":
            return None
        return segments[1] if len(segments) > 1 else None
    return None


def _is_business_profile(
    platform: str,
    canonical: str,
    *,
    base_url: str,
    business_name: str | None,
) -> bool:
    handle = _profile_handle(platform, canonical)
    if handle is None:
        return platform == "youtube"  # channel IDs are owner-only
    handle_key = _normalize_key(handle)
    if len(handle_key) < 4:
        return False

    host = (urlparse(base_url).hostname or "").lower()
    domain_label = host.removeprefix("www.").split(".")[0]
    domain_key = _normalize_key(domain_label)
    if len(domain_key) >= 4 and (domain_key in handle_key or handle_key in domain_key):
        return True

    for word in re.split(r"[^A-Za-z0-9]+", business_name or ""):
        key = _normalize_key(word)
        if len(key) >= 4 and key not in _GENERIC_BRAND_WORDS and key in handle_key:
            return True
    return False


def extract_social_links(
    html: str, base_url: str, *, business_name: str | None = None
) -> dict[str, str]:
    """Pull profile links out of a page, one canonical URL per platform."""
    if not html:
        return {}
    # JSON-LD escapes slashes; the URL scan below expects plain URLs.
    text = html.replace("\\/", "/")
    links: dict[str, str] = {}
    for match in _URL_RE.finditer(text):
        candidate = _clean_candidate(match.group(0))
        if candidate.startswith("//"):
            candidate = f"{urlparse(base_url).scheme}:{candidate}"
        try:
            parsed = urlparse(urljoin(base_url, candidate))
        except ValueError:
            continue
        if parsed.scheme not in {"http", "https"}:
            continue
        target = _social_target(parsed)
        if target is None:
            continue
        platform, canonical = target
        if platform in links:
            continue
        if not _is_business_profile(
            platform, canonical, base_url=base_url, business_name=business_name
        ):
            continue
        links[platform] = canonical
        if len(links) == len(SOCIAL_PLATFORMS):
            break
    return {
        platform: links[platform] for platform in SOCIAL_PLATFORMS if platform in links
    }


def _find_contact_url(html: str, base_url: str) -> str | None:
    base_host = (urlparse(base_url).hostname or "").lower().removeprefix("www.")
    for match in _ANCHOR_RE.finditer(html):
        href = match.group(2).strip()
        if not href or href.startswith(("mailto:", "tel:", "#", "javascript:")):
            continue
        candidate = _clean_candidate(urljoin(base_url, href))
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        if parsed.hostname.lower().removeprefix("www.") != base_host:
            continue
        path = parsed.path.lower()
        if any(keyword in path for keyword in _CONTACT_KEYWORDS):
            return candidate
    return None


async def _fetch_html(client: httpx.AsyncClient, url: str) -> str | None:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        validate_public_http_url(current)
        async with client.stream("GET", current) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    return None
                current = urljoin(current, location)
                continue
            if response.status_code >= 400:
                return None
            content_type = response.headers.get("content-type", "")
            if content_type and "html" not in content_type.lower():
                return None
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_PAGE_BYTES:
                    chunks.append(chunk[: MAX_PAGE_BYTES - (size - len(chunk))])
                    break
                chunks.append(chunk)
            encoding = response.encoding or "utf-8"
            return b"".join(chunks).decode(encoding, errors="replace")
    return None


async def discover_social_links(
    website: str | None, *, business_name: str | None = None
) -> dict[str, str]:
    """Best-effort scan of a business website for its social profiles."""
    direct = social_link_from_url(website)
    if direct:
        return direct

    url = _normalize_website(website)
    if not url:
        return {}

    try:
        async with _client() as client:
            html = await _fetch_html(client, url)
            links = (
                extract_social_links(html, url, business_name=business_name)
                if html
                else {}
            )
            if not links and html:
                contact_url = _find_contact_url(html, url)
                if contact_url:
                    contact_html = await _fetch_html(client, contact_url)
                    if contact_html:
                        links = extract_social_links(
                            contact_html,
                            contact_url,
                            business_name=business_name,
                        )
            return links
    except (UnsafeOutboundURLError, ServiceHostResolutionError) as exc:
        logger.info("Skipping social scan for {}: {}", url, exc)
        return {}
    except httpx.HTTPError as exc:
        logger.info("Social scan failed for {}: {}", url, exc)
        return {}
