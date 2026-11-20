"""Social-profile discovery: canonical links, polite fetching and SSRF safety."""

import asyncio
import socket
import time
from urllib.parse import urlparse

import httpcore
import pytest

from api.services.leads import socials
from api.services.leads.socials import (
    SocialScan,
    discover_social_links,
    extract_social_links,
    normalize_website,
    social_link_from_url,
)
from api.utils import url_security
from api.utils.url_security import (
    PinnedTarget,
    UnsafeOutboundURLError,
    validate_public_http_url,
)


class _Site:
    """Tiny single-connection HTTP server for deterministic scan tests."""

    def __init__(self, routes, *, drip=None):
        self._routes = routes
        self._drip = drip
        self.hits: list[str] = []
        self._server: asyncio.AbstractServer | None = None
        self._connections: list[asyncio.Task] = []

    async def __aenter__(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *args):
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for task in self._connections:
            task.cancel()
        await asyncio.gather(*self._connections, return_exceptions=True)

    async def _handle(self, reader: asyncio.StreamReader, writer):
        task = asyncio.current_task()
        self._connections.append(task)
        try:
            request_line = await reader.readline()
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
            path = request_line.split(b" ")[1].decode() if request_line else "/"
            self.hits.append(path)
            if self._drip == path:
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                    b"Content-Length: 5000\r\n\r\n<html><body>"
                )
                await writer.drain()
                for _ in range(100):
                    writer.write(b"x")
                    await writer.drain()
                    await asyncio.sleep(0.2)
                return
            status, content_type, body = self._routes.get(path, (404, "text/html", b""))
            head = (
                f"HTTP/1.1 {status} X\r\n"
                f"Content-Type: {content_type}\r\n"
                f"Content-Length: {len(body)}\r\n\r\n"
            ).encode()
            writer.write(head + body)
            await writer.drain()
        except (asyncio.CancelledError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, asyncio.CancelledError):
                pass


def _pin_to_local(monkeypatch):
    def resolve(url: str) -> PinnedTarget:
        parsed = urlparse(url)
        return PinnedTarget(
            url=url,
            host=parsed.hostname or "",
            port=parsed.port or 80,
            ip="127.0.0.1",
        )

    monkeypatch.setattr(socials, "_resolve_target", resolve)


def _html(body: str, status: int = 200):
    return (status, "text/html", body.encode())


def _robots(body: str = ""):
    return (200, "text/plain", body.encode())


def test_extracts_canonical_profiles_from_anchors_meta_and_json_ld():
    html = """
    <html><head>
      <link rel="me" href="https://www.instagram.com/apex.labs/?igsh=abc">
      <meta property="og:see_also" content="https://x.com/apexlabs">
    </head><body>
      <a href="//www.tiktok.com/@apex.labs/video/456">TikTok</a>
      <script type="application/ld+json">
        {"@type": "Organization", "sameAs": ["https://www.youtube.com/@apexlabs"]}
      </script>
    </body></html>
    """

    links = extract_social_links(html, "https://apex-labs.com")

    assert links == {
        "instagram": "https://www.instagram.com/apex.labs/",
        "x": "https://x.com/apexlabs",
        "tiktok": "https://www.tiktok.com/@apex.labs",
        "youtube": "https://www.youtube.com/@apexlabs",
    }


def test_ignores_share_sheets_and_platform_reserved_paths():
    html = """
    <a href="https://www.instagram.com/p/Cabc123/">Post</a>
    <a href="https://www.instagram.com/explore/tags/coffee/">Tag</a>
    <a href="https://x.com/intent/tweet?url=https://apex-labs.com">Share</a>
    <a href="https://twitter.com/home">Home</a>
    <a href="https://www.youtube.com/watch?v=abc">Video</a>
    <a href="https://www.tiktok.com/search?q=coffee">Search</a>
    """

    assert extract_social_links(html, "https://apex-labs.com") == {}


def test_youtube_custom_urls_need_brand_evidence_and_channels_need_json_ld():
    html = """
    <a href="https://youtube.com/channel/UCabcdefghijklmnopqrstuv">Channel</a>
    <a href="https://youtube.com/c/ApexLabs">Custom</a>
    """

    links = extract_social_links(html, "https://apex-labs.com")
    assert links == {"youtube": "https://www.youtube.com/c/ApexLabs"}

    declared = """
    <script type="application/ld+json">
      {"@type": "Organization",
       "sameAs": ["https://youtube.com/channel/UCabcdefghijklmnopqrstuv"]}
    </script>
    """
    assert extract_social_links(declared, "https://apex-labs.com") == {
        "youtube": "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv"
    }


def test_ignores_template_vendor_accounts_in_the_footer():
    html = """
    <footer>
      <a href="https://www.instagram.com/squarespace/">Instagram</a>
      <a href="https://x.com/facebook">X</a>
      <a href="https://www.instagram.com/joscoffee/">Our Instagram</a>
    </footer>
    """

    links = extract_social_links(
        html, "https://www.joscoffee.com/", business_name="Jo's Coffee"
    )

    assert links == {"instagram": "https://www.instagram.com/joscoffee/"}


def test_brand_name_can_locate_a_handle_the_domain_does_not():
    html = '<a href="https://www.tiktok.com/@pawsonchiconatx">TikTok</a>'

    links = extract_social_links(
        html, "https://www.pawsonchicon.com/", business_name="Paws on Chicon"
    )

    assert links == {"tiktok": "https://www.tiktok.com/@pawsonchiconatx"}


@pytest.mark.parametrize(
    ("base_url", "business_name", "profile_url"),
    [
        (
            "https://shop.apexlabs.com/",
            None,
            "https://www.instagram.com/coffeeshopnyc/",
        ),
        (
            "https://mail.apexlabs.com/",
            None,
            "https://www.instagram.com/mailboxdeals/",
        ),
        ("https://pizza-palace.com/", "Pizza Palace", "https://x.com/pizzatruck"),
        (
            "https://studio54salon.com/",
            "Studio 54 Salon",
            "https://www.tiktok.com/@studiodog",
        ),
    ],
)
def test_rejects_subdomain_and_category_false_positives(
    base_url, business_name, profile_url
):
    html = f'<a href="{profile_url}">Our social</a>'

    assert extract_social_links(html, base_url, business_name=business_name) == {}


def test_rejects_profile_links_hidden_in_comments_or_inline_scripts():
    html = """
    <!-- <a href="https://www.instagram.com/apexlabs/">hidden</a> -->
    <script>var u = "https://www.instagram.com/apexlabs/";</script>
    """

    assert extract_social_links(html, "https://apexlabs.com") == {}


def test_youtube_channel_id_from_an_anchor_has_no_brand_evidence():
    html = '<a href="https://youtube.com/channel/UCabcdefghijklmnopqrstuv">Watch us</a>'

    assert extract_social_links(html, "https://apexlabs.com") == {}


def test_prefix_handle_matches_a_domain_label():
    html = '<a href="https://www.instagram.com/apexlabsnyc/">Instagram</a>'

    assert extract_social_links(html, "https://apexlabs.com") == {
        "instagram": "https://www.instagram.com/apexlabsnyc/"
    }


def test_extracts_facebook_and_linkedin_pages():
    html = """
    <a href="https://www.facebook.com/apexlabsnyc/?ref=footer">Facebook</a>
    <a href="https://www.linkedin.com/company/apex-labs/posts/">LinkedIn</a>
    """

    links = extract_social_links(html, "https://apex-labs.com")

    assert links == {
        "facebook": "https://www.facebook.com/apexlabsnyc",
        "linkedin": "https://www.linkedin.com/company/apex-labs/",
    }


def test_ignores_share_sheets_and_reserved_facebook_and_linkedin_paths():
    html = """
    <a href="https://www.facebook.com/sharer/sharer.php?u=https://apex-labs.com">Share</a>
    <a href="https://www.facebook.com/events/123/">Event</a>
    <a href="https://www.facebook.com/profile.php?id=123">Profile</a>
    <a href="https://www.linkedin.com/feed/">Feed</a>
    <a href="https://www.linkedin.com/shareArticle?mini=true&url=https://apex-labs.com">Share</a>
    <a href="https://www.linkedin.com/school/apexlabs/">School</a>
    """

    assert extract_social_links(html, "https://apex-labs.com") == {}


def test_facebook_and_linkedin_need_brand_evidence():
    html = """
    <a href="https://www.facebook.com/unrelatedvenues/">Facebook</a>
    <a href="https://www.linkedin.com/company/acme-holdings/">LinkedIn</a>
    <a href="https://www.facebook.com/apexlabs/">Our page</a>
    """

    links = extract_social_links(html, "https://apex-labs.com")

    assert links == {"facebook": "https://www.facebook.com/apexlabs"}


def test_website_that_is_a_profile_binds_directly():
    assert social_link_from_url("instagram.com/apex.labs") == {
        "instagram": "https://www.instagram.com/apex.labs/"
    }
    assert social_link_from_url("https://www.facebook.com/apexlabs") == {
        "facebook": "https://www.facebook.com/apexlabs"
    }
    assert social_link_from_url("https://www.linkedin.com/company/apex-labs") == {
        "linkedin": "https://www.linkedin.com/company/apex-labs/"
    }
    assert social_link_from_url("https://www.facebook.com/help/") == {}
    assert social_link_from_url(None) == {}
    assert social_link_from_url("http://[::1") == {}


def test_malformed_urls_are_rejected_without_raising():
    assert normalize_website("http://[::1") is None
    assert normalize_website("not a url") is None
    with pytest.raises(UnsafeOutboundURLError):
        validate_public_http_url("http://[::1")


@pytest.mark.asyncio
async def test_facebook_website_is_never_fetched(monkeypatch):
    def explode(_url):
        raise AssertionError("Facebook pages must not be fetched")

    monkeypatch.setattr(socials, "_resolve_target", explode)

    scan = await discover_social_links("https://www.facebook.com/apexlabs")
    # The page URL itself is the business account; nothing is fetched.
    assert scan == SocialScan(
        {"facebook": "https://www.facebook.com/apexlabs"},
        checked=True,
        replace_links=True,
    )

    shortener = await discover_social_links("https://fb.me/apexlabs")
    assert shortener == SocialScan({}, checked=True, replace_links=False)


@pytest.mark.asyncio
async def test_private_or_local_website_is_never_dialled(monkeypatch):
    class NoNetworkClient:
        used = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            self.used = True
            raise AssertionError("a private host must never be dialled")

    client = NoNetworkClient()
    monkeypatch.setattr(
        "api.services.leads.socials._pinned_client", lambda _target: client
    )

    for url in ("http://127.0.0.1/admin", "http://localhost"):
        scan = await discover_social_links(url)
        assert scan == SocialScan({}, checked=True, replace_links=False)
    assert client.used is False


@pytest.mark.asyncio
async def test_pinned_transport_never_dials_the_second_dns_answer(monkeypatch):
    """Validation pins a public IP; a later loopback answer must not be dialled.

    The fake resolver returns the public address when the URL is validated and
    loopback for any resolution after that. A transport that re-resolved the
    hostname at connect time would end up on the loopback server, so its hits
    are the proof that no rebinding happened.
    """
    sentinel = '<a href="https://www.instagram.com/loopbackpage/">x</a>'
    dialled: list[str] = []

    async with _Site({"/": _html("public page")}) as public_site:
        async with _Site({"/": _html(sentinel)}) as loopback_site:

            def resolve_public(url: str) -> PinnedTarget:
                parsed = urlparse(url)
                return PinnedTarget(
                    url=url,
                    host=parsed.hostname or "",
                    port=parsed.port or 80,
                    ip="93.184.216.34",
                )

            def fake_getaddrinfo(host, port, *args, **kwargs):
                return [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))
                ]

            class FakeInner(httpcore.AsyncNetworkBackend):
                def __init__(self):
                    self._inner = httpcore.AnyIOBackend()

                async def connect_tcp(
                    self,
                    host,
                    port,
                    timeout=None,
                    local_address=None,
                    socket_options=None,
                ):
                    dialled.append(host)
                    if host == "93.184.216.34":
                        return await self._inner.connect_tcp(
                            "127.0.0.1", public_site.port, timeout=timeout
                        )
                    # A regression: dial the hostname, which now answers with
                    # loopback.
                    socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
                    return await self._inner.connect_tcp(
                        "127.0.0.1", loopback_site.port, timeout=timeout
                    )

                async def connect_unix_socket(
                    self, path, timeout=None, socket_options=None
                ):
                    return await self._inner.connect_unix_socket(
                        path, timeout=timeout, socket_options=socket_options
                    )

                async def sleep(self, seconds):
                    await self._inner.sleep(seconds)

            def make_backend(pins):
                assert pins, "the transport must receive a pinned address"
                return socials._PinnedNetworkBackend(pins, inner=FakeInner())

            monkeypatch.setattr(socials, "_resolve_target", resolve_public)
            monkeypatch.setattr(url_security.socket, "getaddrinfo", fake_getaddrinfo)
            monkeypatch.setattr(socials, "_make_backend", make_backend)

            scan = await discover_social_links(
                f"http://rebind.test:{public_site.port}/"
            )

    assert scan.checked is True
    assert scan.links == {}
    assert dialled and set(dialled) == {"93.184.216.34"}
    assert loopback_site.hits == []


@pytest.mark.asyncio
async def test_slow_drip_site_is_cut_off_by_the_scan_deadline(monkeypatch):
    _pin_to_local(monkeypatch)
    monkeypatch.setattr(socials, "SCAN_TIMEOUT_SECONDS", 0.6)

    async with _Site({}, drip="/") as site:
        started = time.monotonic()
        scan = await asyncio.wait_for(
            discover_social_links(f"http://slow.test:{site.port}/"),
            timeout=5,
        )
        elapsed = time.monotonic() - started

    assert scan.checked is False
    assert scan.error == "timeout"
    assert elapsed < 3


@pytest.mark.asyncio
async def test_robots_disallow_skips_the_page_without_fetching_it(monkeypatch):
    _pin_to_local(monkeypatch)

    async with _Site(
        {
            "/robots.txt": _robots("User-agent: *\nDisallow: /\n"),
            "/": _html('<a href="https://instagram.com/apexlabs/">x</a>'),
        }
    ) as site:
        scan = await discover_social_links(f"http://apexlabs.test:{site.port}/")

    assert scan == SocialScan({}, checked=True, replace_links=False)
    assert site.hits == ["/robots.txt"]


@pytest.mark.asyncio
async def test_successful_scan_replaces_links_and_server_errors_are_transient(
    monkeypatch,
):
    _pin_to_local(monkeypatch)

    async with _Site(
        {
            "/robots.txt": _robots(),
            "/": _html('<a href="https://instagram.com/apexlabs/">x</a>'),
        }
    ) as site:
        scan = await discover_social_links(f"http://apexlabs.test:{site.port}/")

    assert scan.checked is True
    assert scan.replace_links is True
    assert scan.links == {"instagram": "https://www.instagram.com/apexlabs/"}

    async with _Site(
        {
            "/robots.txt": _robots(),
            "/": _html("boom", status=500),
        }
    ) as site:
        scan = await discover_social_links(f"http://broken.test:{site.port}/")

    assert scan.checked is False
    assert scan.replace_links is False


@pytest.mark.asyncio
async def test_404_is_terminal_but_does_not_replace_links(monkeypatch):
    _pin_to_local(monkeypatch)

    async with _Site(
        {
            "/robots.txt": _robots(),
            "/": _html("gone", status=404),
        }
    ) as site:
        scan = await discover_social_links(f"http://gone.test:{site.port}/")

    assert scan.checked is True
    assert scan.replace_links is False


def test_public_url_validator_blocks_local_targets():
    for url in (
        "http://127.0.0.1/",
        "http://localhost/",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
        "http://example.com:22/",
        "https://user:pass@example.com/",
        "http://[::1",
    ):
        with pytest.raises(UnsafeOutboundURLError):
            validate_public_http_url(url)

    target = validate_public_http_url("https://93.184.216.34/contact")
    assert target == "https://93.184.216.34/contact"
    resolved = url_security.resolve_public_http_url("https://93.184.216.34/contact")
    assert resolved.ip == "93.184.216.34"
    assert resolved.port == 443
