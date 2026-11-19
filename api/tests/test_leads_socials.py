"""Social-profile discovery: canonical links only, and never private hosts."""

import pytest

from api.services.leads.socials import (
    discover_social_links,
    extract_social_links,
    social_link_from_url,
)
from api.utils.url_security import UnsafeOutboundURLError, validate_public_http_url


def test_extracts_canonical_profiles_from_attributes_and_json_ld():
    html = """
    <html><body>
      <a href="https://www.instagram.com/apex.labs/?igsh=abc">Instagram</a>
      <a href="//x.com/apexlabs/status/123">X</a>
      <a href="https://www.tiktok.com/@apex.labs/video/456">TikTok</a>
      <script type="application/ld+json">
        {"@type": "Organization", "sameAs": ["https:\\/\\/www.youtube.com\\/@apexlabs"]}
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


def test_youtube_channel_and_custom_urls_are_kept():
    html = """
    <a href="https://youtube.com/channel/UCabcdefghijklmnopqrstuv">Channel</a>
    <a href="https://youtube.com/c/ApexLabs">Custom</a>
    """

    links = extract_social_links(html, "https://apex-labs.com")

    assert (
        links["youtube"] == "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv"
    )


def test_ignores_template_vendor_accounts_in_the_footer():
    # Squarespace pages link the vendor's own accounts next to the brand's.
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


def test_website_that_is_a_profile_binds_directly():
    assert social_link_from_url("instagram.com/apex.labs") == {
        "instagram": "https://www.instagram.com/apex.labs/"
    }
    assert social_link_from_url("https://www.facebook.com/apexlabs") == {}
    assert social_link_from_url(None) == {}


@pytest.mark.asyncio
async def test_discover_does_not_fetch_a_private_or_local_website(monkeypatch):
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
        "api.services.leads.socials._client", lambda: client, raising=True
    )

    assert await discover_social_links("http://127.0.0.1/admin") == {}
    assert await discover_social_links("http://localhost") == {}
    assert client.used is False


def test_public_url_validator_blocks_local_targets():
    for url in (
        "http://127.0.0.1/",
        "http://localhost/",
        "http://10.0.0.5/",
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
        "http://example.com:22/",
        "https://user:pass@example.com/",
    ):
        with pytest.raises(UnsafeOutboundURLError):
            validate_public_http_url(url)

    assert (
        validate_public_http_url("https://93.184.216.34/contact")
        == "https://93.184.216.34/contact"
    )
