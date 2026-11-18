"""Only an anchor href in sanitized HTML sets the unsubscribe flag."""

from api.services.email.templates import (
    has_unsubscribe_placeholder,
    sanitize_html,
)

UNSUBSCRIBE = '<p><a href="{{unsubscribe_link}}">Unsubscribe</a></p>'


def test_unsubscribe_placeholder_survives_sanitizing():
    cleaned = sanitize_html(UNSUBSCRIBE)
    assert 'href="{{unsubscribe_link}}"' in cleaned
    assert has_unsubscribe_placeholder(cleaned)


def test_unsubscribe_placeholder_detection_requires_an_anchor_href():
    assert has_unsubscribe_placeholder(UNSUBSCRIBE)
    assert has_unsubscribe_placeholder(
        '<a href="{{ unsubscribe_link }}">Unsubscribe</a>'
    )
    assert not has_unsubscribe_placeholder("<p>No link</p>")
    # A mention in text, a comment, a title attribute or a script does not count.
    assert not has_unsubscribe_placeholder("<p>{{unsubscribe_link}}</p>")
    assert not has_unsubscribe_placeholder("<!-- {{unsubscribe_link}} -->")
    assert not has_unsubscribe_placeholder(
        '<p title="{{unsubscribe_link}}">No link</p>'
    )
    assert not has_unsubscribe_placeholder(
        "<script>var x = '{{unsubscribe_link}}';</script>"
    )
