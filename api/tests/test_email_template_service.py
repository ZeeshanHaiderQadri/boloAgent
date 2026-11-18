"""Template authoring primitives: sanitizing, merge fields, MJML, text fallback."""

import time
from html.parser import HTMLParser

import pytest

from api.services.email.mjml import MjmlError, compile_mjml
from api.services.email.templates import (
    MAX_SOURCE_BYTES,
    TemplateValidationError,
    extract_merge_fields,
    html_to_text,
    prepare_template,
    render_html,
    render_prepared,
    render_subject,
    sample_values,
    sanitize_html,
    validate_merge_fields,
)

UNSUBSCRIBE = '<p><a href="{{unsubscribe_link}}">Unsubscribe</a></p>'


def _html(body: str = "<p>Hello {{business_name}}</p>") -> str:
    return f'<div style="font-family:Arial">{body}</div>{UNSUBSCRIBE}'


def test_sanitizer_strips_active_content_and_keeps_email_markup():
    source = (
        "<script>alert(1)</script>"
        "<style>body{background:url(javascript:alert(2))}</style>"
        '<p style="color:#333;background-image:url(https://tracker.example/x);'
        'expression(alert(3))">Hello</p>'
        '<a href="javascript:alert(4)" onclick="evil()">link</a>'
        '<img src="data:image/png;base64,AAAA" onerror="evil()">'
        '<form action="https://evil.example/collect" method="post">'
        '<input name="email"><button>Send</button></form>'
        '<iframe src="https://evil.example"></iframe>'
        "<!-- comment -->"
        "<table><tr><td>kept</td></tr></table>" + UNSUBSCRIBE
    )

    cleaned = sanitize_html(source)

    assert "alert(" not in cleaned
    assert "javascript:" not in cleaned
    assert "onerror" not in cleaned
    assert "onclick" not in cleaned
    assert "<script" not in cleaned
    assert "<style" not in cleaned
    assert "<form" not in cleaned
    assert "<input" not in cleaned
    assert "<button" not in cleaned
    assert "<iframe" not in cleaned
    assert "data:image" not in cleaned
    assert "expression(" not in cleaned
    assert "background-image" not in cleaned
    assert "<!--" not in cleaned
    # Ordinary content survives (nh3 normalizes away the trailing semicolon).
    assert 'style="color:#333"' in cleaned
    assert '<p style="color:#333">Hello</p>' in cleaned
    assert "<table><tbody><tr><td>kept</td></tr></tbody></table>" in cleaned
    assert 'href="{{unsubscribe_link}}"' in cleaned


def test_sanitizer_removes_unclosed_script_even_without_a_closing_tag():
    cleaned = sanitize_html("<p>before</p><script>alert(1)")
    assert cleaned == "<p>before</p>"


class _AttributeCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.attributes: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag, attrs):
        self.attributes.extend(attrs)

    handle_startendtag = handle_starttag


def _attributes(html: str) -> dict[str, list[str | None]]:
    collector = _AttributeCollector()
    collector.feed(html)
    return {
        name: [value for other, value in collector.attributes if other == name]
        for name, _ in collector.attributes
    }


def test_style_quote_payload_never_becomes_an_event_attribute():
    cleaned = sanitize_html(
        "<div style='font-family:\" onmouseover=\"alert(1)'>x</div>"
    )
    names = _attributes(cleaned)
    assert "onmouseover" not in names
    assert "style" in names


@pytest.mark.parametrize(
    "payload",
    [
        '<a href="javascript\u200b:alert(1)">x</a>',
        "\u200bjavascript:alert(1)",
        '<a href="\u200bjavascript:alert(1)">x</a>',
        '<a href="java\tscript:alert(1)">x</a>',
        '<a href="&#106;avascript:alert(1)">x</a>',
    ],
)
def test_invisible_and_encoded_javascript_urls_lose_the_href(payload):
    cleaned = sanitize_html(payload)
    assert "href" not in _attributes(cleaned)


def test_many_repetitions_of_an_unclosed_script_are_linear_time():
    source = "<script>" * 30_000
    assert len(source.encode("utf-8")) < MAX_SOURCE_BYTES
    started = time.perf_counter()
    cleaned = sanitize_html(source)
    elapsed = time.perf_counter() - started
    assert cleaned == ""
    assert elapsed < 1.0


def test_script_tag_mentioned_as_text_removes_the_tag_and_its_content():
    # Defined behaviour: the tag and everything it swallows are removed, so the
    # visible sentence is truncated instead of leaking script source.
    assert sanitize_html("<p>Use the <script> tag</p>") == "<p>Use the </p>"


def test_sanitizing_is_idempotent():
    source = (
        '<div style="color:#333;background-image:url(https://tracker.example/x);'
        'expression(alert(1))"><a href="{{unsubscribe_link}}">Unsubscribe</a></div>'
    )
    once = sanitize_html(source)
    assert sanitize_html(once) == once


def test_merge_fields_render_sample_values_and_fall_back_when_empty():
    values = sample_values()
    assert "Acme Demo Co." in render_html("Hi {{ business_name }}", values)
    assert "Acme Demo Co." in render_subject("Hi {{business_name}}", values)
    assert render_subject("Hi {{business_name}}", {"business_name": ""}) == "Hi there"
    assert render_subject("Hi {{city}}", {"city": "   "}) == "Hi your area"
    # Unknown names are rejected instead of shipping as literal braces.
    with pytest.raises(TemplateValidationError, match="Unknown merge field"):
        validate_merge_fields("Hi {{bussiness_name}}")
    assert extract_merge_fields("{{city}}", "{{city}} and {{phone}}") == [
        "city",
        "phone",
    ]


def test_subject_merge_values_cannot_inject_newlines():
    subject = render_subject("Hi {{business_name}}", {"business_name": "A\nB"})
    assert subject == "Hi A B"


def test_prepare_template_sanitizes_compiles_and_flags_a_missing_unsubscribe():
    prepared = prepare_template(
        subject="Hello {{business_name}}",
        source_format="html",
        source=_html("<p onclick='x()'>Hi</p>"),
    )
    assert prepared.has_unsubscribe_placeholder is True
    assert prepared.warnings == []
    assert "onclick" not in prepared.html_body
    assert "Hi" in prepared.text_body

    no_link = prepare_template(
        subject="Hello", source_format="html", source="<p>Hello</p>"
    )
    assert no_link.has_unsubscribe_placeholder is False
    assert "{{unsubscribe_link}}" in no_link.warnings[0]


def test_prepare_template_rejects_unknown_fields_and_empty_output():
    with pytest.raises(TemplateValidationError, match="Unknown merge field"):
        prepare_template(
            subject="Hi",
            source_format="html",
            source="<p>{{first_nmae}}</p>",
        )
    with pytest.raises(TemplateValidationError, match="no visible content"):
        prepare_template(
            subject="Hi",
            source_format="html",
            source="<script>alert(1)</script>",
        )


def test_source_size_limit_is_enforced():
    oversized = "a" * (MAX_SOURCE_BYTES + 1)
    with pytest.raises(TemplateValidationError, match="limit"):
        prepare_template(subject="Hi", source_format="html", source=oversized)


def test_html_to_text_keeps_links_and_drops_script_text():
    text = html_to_text(
        "<p>Hi <strong>there</strong></p><script>alert(1)</script>"
        '<a href="https://boloagent.com/x">Open</a><ul><li>One</li><li>Two</li></ul>'
    )
    assert "Hi there" in text
    assert "alert" not in text
    assert "Open (https://boloagent.com/x)" in text
    assert "- One" in text
    assert "- Two" in text


def test_mjml_subset_compiles_to_tables_and_keeps_merge_fields():
    source = """
    <mjml>
      <mj-head><mj-title>Hello</mj-title></mj-head>
      <mj-body background-color="#f4f4f4" width="600px">
        <mj-section padding="20px" background-color="#ffffff">
          <mj-column width="100%">
            <mj-text font-size="18px" color="#1f7a6b">Hi {{business_name}}</mj-text>
            <mj-button href="https://apex-software-demo.com">Book now</mj-button>
            <mj-image src="https://apex-software-demo.com/logo.png" alt="Logo" />
            <mj-divider /><mj-spacer height="12px" />
            <mj-text><a href="{{unsubscribe_link}}">Unsubscribe</a></mj-text>
          </mj-column>
        </mj-section>
      </mj-body>
    </mjml>
    """
    compiled = compile_mjml(source)
    assert "<table" in compiled
    assert "{{business_name}}" in compiled
    assert 'href="https://apex-software-demo.com"' in compiled
    assert "{{unsubscribe_link}}" in compiled
    assert "mj-" not in compiled

    prepared = prepare_template(
        subject="Hi {{business_name}}", source_format="mjml", source=source
    )
    assert prepared.has_unsubscribe_placeholder is True
    assert "Hi {{business_name}}" in prepared.html_body


def test_mjml_rejects_unsupported_tags_attributes_and_unbalanced_input():
    with pytest.raises(MjmlError, match="mj-hero"):
        compile_mjml("<mjml><mj-body><mj-hero /></mj-body></mjml>")
    with pytest.raises(MjmlError, match="Unsupported attribute"):
        compile_mjml(
            "<mjml><mj-body><mj-section><mj-column>"
            '<mj-text magic="yes">Hi</mj-text>'
            "</mj-column></mj-section></mj-body></mjml>"
        )
    with pytest.raises(MjmlError, match="<mj-body>"):
        compile_mjml("<mjml><mj-body><mj-section>")


def test_preview_uses_sample_values_never_lead_data():
    prepared = prepare_template(
        subject="Hi {{business_name}}",
        source_format="html",
        source=_html("<p>Call {{phone}}</p>"),
    )
    rendered = render_prepared(prepared)
    assert "Acme Demo Co." in rendered.subject
    assert "+1 555 0100" in rendered.text
    assert "https://boloagent.com/unsubscribe/preview-sample" in rendered.html
