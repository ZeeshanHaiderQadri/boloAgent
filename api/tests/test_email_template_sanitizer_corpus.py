"""Regression corpus for the email sanitizer.

The payloads come from the two independent reviews of the template editor and
from public XSS cheat sheets. Each case asserts that the cleaned output carries
no active content. The assertions deliberately avoid pinning an exact
normalization: URL quoting, attribute order and whitespace may change as long
as the result stays inert.
"""

import time
import unicodedata
from html.parser import HTMLParser

import pytest

from api.services.email.templates import sanitize_html

# Tags that must never appear in a stored template body.
_ACTIVE_TAGS = {
    "script",
    "style",
    "iframe",
    "object",
    "embed",
    "form",
    "input",
    "button",
    "svg",
    "math",
    "meta",
    "base",
    "link",
    "textarea",
}

_DANGEROUS_SCHEMES = ("javascript:", "vbscript:", "data:", "file:", "blob:")


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attributes: list[tuple[str, str, str | None]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag.lower())
        self.attributes.extend(
            (tag.lower(), name.lower(), value) for name, value in attrs
        )

    handle_startendtag = handle_starttag


def _canonical_url(value: str) -> str:
    """Approximate how a browser strips invisible characters before a scheme."""
    kept = []
    for character in value:
        if ord(character) <= 0x20:
            continue
        if unicodedata.category(character) in {"Cc", "Cf", "Zs", "Zl", "Zp"}:
            continue
        kept.append(character)
    return "".join(kept).lower()


def _assert_inert(cleaned: str) -> None:
    collector = _Collector()
    collector.feed(cleaned)

    assert _ACTIVE_TAGS.isdisjoint(collector.tags), collector.tags
    for tag, name, _value in collector.attributes:
        assert not name.startswith("on"), (tag, name)
        assert name not in {"formaction", "srcdoc", "xlink:href", "background"}
    for tag, name, value in collector.attributes:
        if name in {"href", "src"} and value is not None:
            canonical = _canonical_url(value)
            assert not canonical.startswith(_DANGEROUS_SCHEMES), (tag, name, value)
            assert "javascript:" not in canonical


_URL_PAYLOADS = [
    '<img src="javascript:alert(1)">',
    '<img src="JaVaScRiPt:alert(1)">',
    '<a href="jav&#x09;ascript:alert(1)">x</a>',
    '<a href="jav&#x0A;ascript:alert(1)">x</a>',
    '<a href="jav&#x0D;ascript:alert(1)">x</a>',
    '<a href="&#106;&#97;&#118;&#97;&#115;&#99;&#114;&#105;&#112;&#116;&#58;alert(1)">x</a>',
    '<a href="  javascript:alert(1)">x</a>',
    '<a href="\u200bjavascript:alert(1)">x</a>',
    '<a href="java\u200bscript:alert(1)">x</a>',
    '<a href="java\tscript:alert(1)">x</a>',
    '<a href="java\nscript:alert(1)">x</a>',
    '<a href="vbscript:msgbox(1)">x</a>',
    '<img src="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">',
    '<img src="data:image/svg+xml,<svg onload=alert(1)>">',
    '<a href="blob:https://evil.example/x">x</a>',
    '<a href="file:///etc/passwd">x</a>',
]

_EVENT_HANDLER_PAYLOADS = [
    "<p onclick=alert(1)>x</p>",
    "<p ONCLICK=alert(1)>x</p>",
    '<p onclick="alert(1)" onmouseover="alert(2)" onfocus="alert(3)">x</p>',
    '<img src="https://apex-software-demo.com/logo.png" onerror="alert(1)">',
    '<a href="https://boloagent.com" onclick="alert(1)">x</a>',
    '<svg onload="alert(1)"></svg>',
    '<body onload="alert(1)">x</body>',
    '<details open ontoggle="alert(1)">x</details>',
]

_CSS_PAYLOADS = [
    '<div style="background:url(javascript:alert(1))">x</div>',
    '<div style="background-image:url(https://tracker.example/pixel.gif)">x</div>',
    '<div style="width:expression(alert(1))">x</div>',
    '<div style="color:expression(alert(1))">x</div>',
    '<div style="font-family:url(javascript:alert(1))">x</div>',
    '<div style="behavior:url(x.htc)">x</div>',
    '<div style="-moz-binding:url(https://evil.example/x.xml#xss)">x</div>',
    '<div style="background:url(&quot;javascript:alert(1)&quot;)">x</div>',
    '<div style="color:red;background:url(https://tracker.example/x)">x</div>',
    "<div style='font-family:\" onmouseover=\"alert(1)'>x</div>",
    '<div style="color: rgb(0 0 0 / 5%); --x: url(javascript:alert(1))">x</div>',
]

_STRUCTURE_PAYLOADS = [
    "<script>alert(1)</script>after",
    "<SCRIPT>alert(1)</SCRIPT>",
    "<script>alert(1)",
    "<scr<script>ipt>alert(1)</script>",
    "<style>@import url(javascript:alert(1));</style>x",
    '<iframe src="javascript:alert(1)"></iframe>x',
    '<iframe srcdoc="<script>alert(1)</script>"></iframe>x',
    '<object data="javascript:alert(1)"><embed src="javascript:alert(1)"></object>x',
    '<form action="https://evil.example"><input name="email">'
    '<button formaction="javascript:alert(1)">Go</button></form>x',
    '<svg><script>alert(1)</script><a xlink:href="javascript:alert(1)">x</a></svg>x',
    '<math><mi xlink:href="javascript:alert(1)">x</mi></math>x',
    '<meta http-equiv="refresh" content="0;url=javascript:alert(1)">x',
    '<base href="javascript:alert(1)//">x',
    '<link rel="stylesheet" href="javascript:alert(1)">x',
    "<textarea><script>alert(1)</script></textarea>x",
    "<!-- <script>alert(1)</script> --><p>ok</p>",
    "<p>unclosed <b>bold</p>",
    '<div><span><a href="javascript:alert(1)">x</a></div>',
]


@pytest.mark.parametrize("payload", _URL_PAYLOADS)
def test_dangerous_urls_never_survive(payload):
    _assert_inert(sanitize_html(payload))


@pytest.mark.parametrize("payload", _EVENT_HANDLER_PAYLOADS)
def test_event_handlers_never_survive(payload):
    _assert_inert(sanitize_html(payload))


@pytest.mark.parametrize("payload", _CSS_PAYLOADS)
def test_css_payloads_never_survive(payload):
    _assert_inert(sanitize_html(payload))


@pytest.mark.parametrize("payload", _STRUCTURE_PAYLOADS)
def test_structural_payloads_never_survive(payload):
    _assert_inert(sanitize_html(payload))


def test_safe_newsletter_markup_and_unsubscribe_link_survive():
    source = (
        '<table role="presentation" width="100%" style="background-color:#ffffff">'
        '<tr><td style="padding:16px;color:#1f7a6b">'
        '<a href="https://boloagent.com/pricing">Pricing</a>'
        '<a href="mailto:support@apex-software-demo.com">Email us</a>'
        '<img src="https://apex-software-demo.com/logo.png" alt="Logo">'
        '<a href="{{unsubscribe_link}}">Unsubscribe</a>'
        "</td></tr></table>"
    )
    cleaned = sanitize_html(source)

    assert 'href="https://boloagent.com/pricing"' in cleaned
    assert 'href="mailto:support@apex-software-demo.com"' in cleaned
    assert 'src="https://apex-software-demo.com/logo.png"' in cleaned
    assert 'href="{{unsubscribe_link}}"' in cleaned
    assert 'style="padding:16px;color:#1f7a6b"' in cleaned
    _assert_inert(cleaned)


def test_every_corpus_payload_is_idempotent():
    for payload in (
        _URL_PAYLOADS + _EVENT_HANDLER_PAYLOADS + _CSS_PAYLOADS + _STRUCTURE_PAYLOADS
    ):
        once = sanitize_html(payload)
        assert sanitize_html(once) == once


def test_deeply_nested_markup_stays_linear():
    payload = "<div>" * 2_000 + "x" + "</div>" * 2_000
    started = time.perf_counter()
    cleaned = sanitize_html(payload)
    elapsed = time.perf_counter() - started

    assert "x" in cleaned
    assert elapsed < 2.0
