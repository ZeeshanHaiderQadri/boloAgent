"""Rendered merge values cannot introduce live markup or a dangerous href."""

from html.parser import HTMLParser

from api.services.email.templates import render_html


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


def test_rendered_lead_values_cannot_restore_a_dangerous_href():
    rendered = render_html(
        '<p><a href="{{website}}">Visit</a></p>',
        {"website": "javascript:alert(1)"},
    )
    assert "href" not in _attributes(rendered)


def test_rendered_lead_values_are_escaped_not_live_markup():
    rendered = render_html(
        "<p>{{website}}</p>",
        {"website": '<img src=x onerror="alert(1)">'},
    )
    assert "<img" not in rendered
    assert "onerror" not in _attributes(rendered)
    assert "&lt;img" in rendered
