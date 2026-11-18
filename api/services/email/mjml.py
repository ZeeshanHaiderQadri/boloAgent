"""Compile a small, documented MJML subset to table-based HTML.

Bolo does not ship the Node.js MJML compiler, and no Python package provides a
current, maintained implementation with wheels for every platform we support.
Instead this module compiles a deliberately small subset of MJML on the server
and rejects anything outside it with a clear error, so an author never believes
a template rendered correctly when it did not.

Supported tags:

``mjml``, ``mj-head``, ``mj-title``, ``mj-preview``, ``mj-body``,
``mj-wrapper``, ``mj-section``, ``mj-column``, ``mj-text``, ``mj-button``,
``mj-image``, ``mj-divider``, ``mj-spacer``.

Supported attributes are listed per tag in ``_ALLOWED_ATTRIBUTES``. Unknown
tags and attributes are errors. The result is a fragment; the caller sanitizes
it before storing or serving it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

# Text content inside mj-text is arbitrary HTML. These are the tags that never
# take a closing tag, so the tree builder does not expect one.
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

_PRESENTATION_ATTRIBUTES = {
    "padding",
    "padding-top",
    "padding-right",
    "padding-bottom",
    "padding-left",
    "background-color",
    "border",
    "border-radius",
    "width",
    "align",
    "css-class",
}

_ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "mjml": set(),
    "mj-head": set(),
    "mj-title": set(),
    "mj-preview": set(),
    "mj-body": {"background-color", "width"},
    "mj-wrapper": _PRESENTATION_ATTRIBUTES,
    "mj-section": _PRESENTATION_ATTRIBUTES,
    "mj-column": _PRESENTATION_ATTRIBUTES,
    "mj-text": _PRESENTATION_ATTRIBUTES
    | {
        "color",
        "font-family",
        "font-size",
        "font-weight",
        "line-height",
        "letter-spacing",
        "text-align",
    },
    "mj-button": {
        "href",
        "background-color",
        "color",
        "font-family",
        "font-size",
        "font-weight",
        "border",
        "border-radius",
        "padding",
        "inner-padding",
        "align",
        "width",
        "css-class",
    },
    "mj-image": {
        "src",
        "alt",
        "title",
        "href",
        "width",
        "align",
        "padding",
        "border",
        "border-radius",
        "css-class",
    },
    "mj-divider": {
        "border-color",
        "border-style",
        "border-width",
        "width",
        "padding",
        "align",
        "css-class",
    },
    "mj-spacer": {"height", "padding", "css-class"},
}

_LEAF_TAGS = {"mj-text", "mj-button", "mj-image", "mj-divider", "mj-spacer"}

_COLOR_PATTERN = re.compile(r"(#[0-9A-Fa-f]{3,8}|[A-Za-z]{1,32})")
_LENGTH_PATTERN = re.compile(r"\d{1,4}(\.\d{1,2})?(px|%)?")
_PADDING_TOKEN = re.compile(r"\d{1,3}(px|%)")
_ALIGNMENTS = {"left", "center", "right"}
_BORDER_STYLES = {"solid", "dashed", "dotted", "double", "none"}

_DEFAULT_FONT = "Arial, Helvetica, sans-serif"


class MjmlError(ValueError):
    """The source is not valid, or uses unsupported MJML."""


@dataclass
class _Element:
    tag: str
    attrs: dict[str, str]
    children: list["_Element | str"] = field(default_factory=list)


class _TreeParser(HTMLParser):
    """Build a tag tree without treating MJML as HTML that needs repairing."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Element("#document", {})
        self.stack: list[_Element] = [self.root]
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        node = _Element(
            tag.lower(), {key.lower(): value for key, value in attrs if value}
        )
        self.stack[-1].children.append(node)
        if node.tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs) -> None:
        node = _Element(
            tag.lower(), {key.lower(): value for key, value in attrs if value}
        )
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _VOID_TAGS:
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                # Leave implicitly closed children in the tree; HTML permits
                # unclosed <p> and similar inside text content.
                del self.stack[index:]
                return
        self.errors.append(f"unexpected closing tag </{tag}>")

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)

    def handle_comment(self, data: str) -> None:
        # Comments never reach the output; dropping them here also means a
        # commented-out tag cannot confuse the structure checks.
        return

    def handle_decl(self, decl: str) -> None:
        return

    def handle_pi(self, data: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        return


def _elements(node: _Element) -> list[_Element]:
    return [child for child in node.children if isinstance(child, _Element)]


def _text(node: _Element) -> str:
    return "".join(child for child in node.children if isinstance(child, str))


def _require_no_text(node: _Element) -> None:
    if _text(node).strip():
        raise MjmlError(
            f"Text directly inside <{node.tag}> is not supported; wrap it in "
            "an mj-text, mj-button or other content tag."
        )


def _validate_attributes(node: _Element) -> None:
    allowed = _ALLOWED_ATTRIBUTES.get(node.tag)
    if allowed is None:
        raise MjmlError(
            f"<{node.tag}> is not supported by Bolo's MJML compiler. Supported "
            f"tags: {', '.join(sorted(_ALLOWED_ATTRIBUTES))}."
        )
    unknown = sorted(set(node.attrs) - allowed)
    if unknown:
        raise MjmlError(
            f"Unsupported attribute(s) on <{node.tag}>: {', '.join(unknown)}."
        )


def _validate_tree(node: _Element) -> None:
    # Structural checks already constrained which MJML tags may appear where.
    # Arbitrary HTML inside mj-text is not MJML and is left for the sanitizer.
    if node.tag == "mjml" or node.tag.startswith("mj-"):
        _validate_attributes(node)
    for child in _elements(node):
        _validate_tree(child)


def _check_structure(root: _Element) -> tuple[_Element | None, _Element]:
    roots = _elements(root)
    if len(roots) != 1 or roots[0].tag != "mjml":
        raise MjmlError("The source must contain exactly one <mjml> root element.")
    mjml = roots[0]
    heads = [child for child in _elements(mjml) if child.tag == "mj-head"]
    bodies = [child for child in _elements(mjml) if child.tag == "mj-body"]
    if len(bodies) != 1:
        raise MjmlError("The source must contain exactly one <mj-body> element.")
    if len(heads) > 1:
        raise MjmlError("The source must contain at most one <mj-head> element.")
    for child in _elements(mjml):
        if child.tag not in {"mj-head", "mj-body"}:
            raise MjmlError(
                f"<{child.tag}> is only allowed inside <mj-body> or <mj-head>."
            )

    head = heads[0] if heads else None
    if head is not None:
        _require_no_text(head)
        for child in _elements(head):
            if child.tag not in {"mj-title", "mj-preview"}:
                raise MjmlError(
                    f"<{child.tag}> is not supported inside <mj-head>; only "
                    "<mj-title> and <mj-preview> are available."
                )

    body = bodies[0]
    _require_no_text(body)
    _validate_body_children(body)
    return head, body


def _validate_body_children(body: _Element) -> None:
    for child in _elements(body):
        if child.tag not in {"mj-section", "mj-wrapper"}:
            raise MjmlError(
                f"<{child.tag}> is not allowed directly inside <mj-body>; use "
                "<mj-section> and <mj-column>."
            )
        if child.tag == "mj-wrapper":
            _validate_wrapper(child)
        else:
            _validate_section(child)


def _validate_wrapper(wrapper: _Element) -> None:
    _require_no_text(wrapper)
    for child in _elements(wrapper):
        if child.tag != "mj-section":
            raise MjmlError(
                f"<{child.tag}> is not allowed inside <mj-wrapper>; only "
                "<mj-section> is supported."
            )
        _validate_section(child)


def _validate_section(section: _Element) -> None:
    _require_no_text(section)
    for child in _elements(section):
        if child.tag != "mj-column":
            raise MjmlError(
                f"<{child.tag}> is not allowed inside <mj-section>; only "
                "<mj-column> is supported."
            )
        _validate_column(child)


def _validate_column(column: _Element) -> None:
    _require_no_text(column)
    for child in _elements(column):
        if child.tag not in _LEAF_TAGS:
            raise MjmlError(
                f"<{child.tag}> is not allowed inside <mj-column>. Supported "
                f"content tags: {', '.join(sorted(_LEAF_TAGS))}."
            )
        for nested in _elements(child):
            if nested.tag.startswith("mj-"):
                raise MjmlError(
                    f"<{nested.tag}> cannot be nested inside <{child.tag}>."
                )


def _css_value(value: str, *, attribute: str) -> str:
    """Reject values that could escape their property or run code."""
    cleaned = value.strip()
    lowered = cleaned.lower()
    if (
        not cleaned
        or len(cleaned) > 200
        or any(character in cleaned for character in "<>{};")
        or any(character in lowered for character in ("expression", "javascript:"))
        or "vbscript:" in lowered
        or "url(" in lowered
        or "\n" in cleaned
        or "\r" in cleaned
    ):
        raise MjmlError(f"Unsupported value for {attribute}: {value!r}.")
    return escape(cleaned, quote=True)


def _color(value: str, *, attribute: str) -> str:
    cleaned = value.strip()
    if not _COLOR_PATTERN.fullmatch(cleaned):
        raise MjmlError(f"{attribute} must be a hex or named color.")
    return cleaned


def _length(value: str, *, attribute: str) -> str:
    cleaned = value.strip()
    if not _LENGTH_PATTERN.fullmatch(cleaned):
        raise MjmlError(f"{attribute} must be a number with px or %.")
    return cleaned


def _padding(value: str, *, attribute: str) -> str:
    tokens = value.split()
    if not 1 <= len(tokens) <= 4 or not all(
        _PADDING_TOKEN.fullmatch(token) for token in tokens
    ):
        raise MjmlError(
            f"{attribute} must contain one to four px or % values, "
            "for example 10px or 10px 20px."
        )
    return " ".join(tokens)


def _alignment(value: str, *, attribute: str) -> str:
    cleaned = value.strip().lower()
    if cleaned not in _ALIGNMENTS:
        raise MjmlError(f"{attribute} must be left, center or right.")
    return cleaned


def _border_style(value: str) -> str:
    cleaned = value.strip().lower()
    if cleaned not in _BORDER_STYLES:
        raise MjmlError(
            f"border-style must be one of {', '.join(sorted(_BORDER_STYLES))}."
        )
    return cleaned


def _link(value: str, *, attribute: str, allow_mailto: bool) -> str:
    cleaned = value.strip()
    if len(cleaned) > 2048:
        raise MjmlError(f"{attribute} is too long.")
    parts = urlsplit(cleaned)
    schemes = {"http", "https"} | ({"mailto"} if allow_mailto else set())
    if parts.scheme.lower() not in schemes:
        raise MjmlError(f"{attribute} must start with http:// or https://.")
    if parts.scheme.lower() != "mailto" and not parts.netloc:
        raise MjmlError(f"{attribute} must include a host name.")
    return escape(cleaned, quote=True)


def _width_attribute(value: str) -> str | None:
    match = re.fullmatch(r"(\d{1,4})(?:px)?", value.strip())
    return match.group(1) if match else None


def _style(node: _Element, properties: list[tuple[str, str]]) -> str:
    parts: list[str] = []
    for attribute, prop in properties:
        raw = node.attrs.get(attribute)
        if raw is None:
            continue
        if prop in {"padding", "inner-padding"}:
            rendered = _padding(raw, attribute=attribute)
        elif prop in {"background-color", "color", "border-color"}:
            rendered = _color(raw, attribute=attribute)
        elif prop in {"width", "font-size", "line-height", "letter-spacing", "height"}:
            rendered = _length(raw, attribute=attribute)
        elif prop == "text-align" or prop == "align":
            rendered = _alignment(raw, attribute=attribute)
        elif prop == "border-style":
            rendered = _border_style(raw)
        else:
            rendered = _css_value(raw, attribute=attribute)
        target = "padding" if prop == "inner-padding" else prop
        parts.append(f"{target}:{rendered}")
    return ";".join(parts) + (";" if parts else "")


def _class_attribute(node: _Element) -> str:
    css_class = node.attrs.get("css-class", "").strip()
    if not css_class:
        return ""
    return f' class="{escape(css_class, quote=True)}"'


def _serialize(node: _Element | str) -> str:
    if isinstance(node, str):
        return node
    attrs = "".join(
        f' {key}="{escape(value, quote=True)}"' for key, value in node.attrs.items()
    )
    if node.tag in _VOID_TAGS:
        return f"<{node.tag}{attrs}>"
    inner = "".join(_serialize(child) for child in node.children)
    return f"<{node.tag}{attrs}>{inner}</{node.tag}>"


def _render_text(node: _Element) -> str:
    style = _style(
        node,
        [
            ("color", "color"),
            ("font-family", "font-family"),
            ("font-size", "font-size"),
            ("font-weight", "font-weight"),
            ("line-height", "line-height"),
            ("letter-spacing", "letter-spacing"),
            ("text-align", "text-align"),
            ("padding", "padding"),
        ],
    )
    defaults = (
        f"color:#333333;font-family:{_DEFAULT_FONT};font-size:14px;line-height:1.5;"
    )
    inner = "".join(_serialize(child) for child in node.children)
    return f'<div{_class_attribute(node)} style="{defaults}{style}">{inner}</div>'


def _render_button(node: _Element) -> str:
    inner = "".join(_serialize(child) for child in node.children).strip()
    if not inner:
        raise MjmlError("mj-button needs visible text.")
    background = _color(
        node.attrs.get("background-color", "#1f7a6b"),
        attribute="background-color",
    )
    color = _color(node.attrs.get("color", "#ffffff"), attribute="color")
    font_size = _length(node.attrs.get("font-size", "14px"), attribute="font-size")
    radius = _length(node.attrs.get("border-radius", "4px"), attribute="border-radius")
    padding = _padding(
        node.attrs.get("inner-padding", "12px 24px"), attribute="inner-padding"
    )
    align = _alignment(node.attrs.get("align", "left"), attribute="align")
    outer_padding = (
        _padding(node.attrs["padding"], attribute="padding")
        if "padding" in node.attrs
        else "0"
    )
    width = (
        _length(node.attrs["width"], attribute="width") if "width" in node.attrs else ""
    )
    border = (
        _css_value(node.attrs["border"], attribute="border")
        if "border" in node.attrs
        else ""
    )
    link_style = (
        f"display:inline-block;background-color:{background};color:{color};"
        f"font-family:{_DEFAULT_FONT};font-size:{font_size};"
        f"text-decoration:none;border-radius:{radius};padding:{padding};"
    )
    if border:
        link_style += f"border:{border};"
    if "href" in node.attrs:
        href = _link(node.attrs["href"], attribute="href", allow_mailto=True)
        action = f'<a href="{href}" style="{link_style}">{inner}</a>'
    else:
        action = f'<span style="{link_style}">{inner}</span>'
    table_width = f"width:{width};" if width else ""
    return (
        f'<div{_class_attribute(node)} style="text-align:{align};padding:{outer_padding};">'
        f'<table role="presentation" border="0" cellpadding="0" cellspacing="0" '
        f'style="display:inline-table;{table_width}"><tr><td>{action}</td></tr></table>'
        f"</div>"
    )


def _render_image(node: _Element) -> str:
    if "src" not in node.attrs:
        raise MjmlError("mj-image needs a src attribute.")
    src = _link(node.attrs["src"], attribute="src", allow_mailto=False)
    alt = escape(node.attrs.get("alt", ""), quote=True)
    align = _alignment(node.attrs.get("align", "center"), attribute="align")
    padding = (
        _padding(node.attrs["padding"], attribute="padding")
        if "padding" in node.attrs
        else "0"
    )
    radius = (
        _length(node.attrs["border-radius"], attribute="border-radius")
        if "border-radius" in node.attrs
        else ""
    )
    width = _width_attribute(node.attrs.get("width", ""))
    width_style = f"width:{width}px;" if width else "max-width:100%;"
    width_attr = f' width="{width}"' if width else ""
    title = (
        f' title="{escape(node.attrs["title"], quote=True)}"'
        if "title" in node.attrs
        else ""
    )
    radius_style = f"border-radius:{radius};" if radius else ""
    image = (
        f'<img src="{src}" alt="{alt}"{title}{width_attr} '
        f'style="display:inline-block;border:0;{width_style}{radius_style}height:auto;" />'
    )
    if "href" in node.attrs:
        href = _link(node.attrs["href"], attribute="href", allow_mailto=True)
        image = f'<a href="{href}" target="_blank">{image}</a>'
    return (
        f'<div{_class_attribute(node)} style="text-align:{align};padding:{padding};">'
        f"{image}</div>"
    )


def _render_divider(node: _Element) -> str:
    color = _color(node.attrs.get("border-color", "#dddddd"), attribute="border-color")
    style = _border_style(node.attrs.get("border-style", "solid"))
    width = _length(node.attrs.get("border-width", "1px"), attribute="border-width")
    padding = (
        _padding(node.attrs["padding"], attribute="padding")
        if "padding" in node.attrs
        else "10px 0"
    )
    return (
        f'<div{_class_attribute(node)} style="padding:{padding};">'
        f'<hr style="border:0;border-top:{width} {style} {color};margin:0;" /></div>'
    )


def _render_spacer(node: _Element) -> str:
    height = _length(node.attrs.get("height", "20px"), attribute="height")
    padding = (
        _padding(node.attrs["padding"], attribute="padding")
        if "padding" in node.attrs
        else ""
    )
    return (
        f'<div{_class_attribute(node)} style="height:{height};line-height:{height};'
        f'font-size:0;padding:{padding or "0"};">&nbsp;</div>'
    )


def _render_column(node: _Element) -> str:
    style = _style(
        node,
        [
            ("padding", "padding"),
            ("background-color", "background-color"),
            ("border", "border"),
            ("border-radius", "border-radius"),
        ],
    )
    width = node.attrs.get("width")
    width_style = f"width:{_length(width, attribute='width')};" if width else ""
    inner = "".join(_render_leaf(child) for child in _elements(node))
    return (
        f'<td valign="top"{_class_attribute(node)} style="{width_style}{style}">'
        f"{inner}</td>"
    )


def _render_section(node: _Element) -> str:
    style = _style(
        node,
        [
            ("background-color", "background-color"),
            ("border", "border"),
            ("border-radius", "border-radius"),
        ],
    )
    padding = (
        _padding(node.attrs["padding"], attribute="padding")
        if "padding" in node.attrs
        else "0"
    )
    columns = "".join(_render_column(child) for child in _elements(node))
    return (
        f'<table role="presentation" width="100%" border="0" cellpadding="0" '
        f'cellspacing="0"{_class_attribute(node)} style="{style}"><tr>'
        f'<td style="padding:{padding};"><table role="presentation" width="100%" '
        f'border="0" cellpadding="0" cellspacing="0"><tr>{columns}</tr></table>'
        f"</td></tr></table>"
    )


def _render_wrapper(node: _Element) -> str:
    style = _style(
        node,
        [
            ("background-color", "background-color"),
            ("border", "border"),
            ("border-radius", "border-radius"),
        ],
    )
    padding = (
        _padding(node.attrs["padding"], attribute="padding")
        if "padding" in node.attrs
        else "0"
    )
    sections = "".join(_render_section(child) for child in _elements(node))
    return (
        f'<table role="presentation" width="100%" border="0" cellpadding="0" '
        f'cellspacing="0"{_class_attribute(node)} style="{style}"><tr>'
        f'<td style="padding:{padding};">{sections}</td></tr></table>'
    )


def _render_leaf(node: _Element) -> str:
    renderers = {
        "mj-text": _render_text,
        "mj-button": _render_button,
        "mj-image": _render_image,
        "mj-divider": _render_divider,
        "mj-spacer": _render_spacer,
    }
    return renderers[node.tag](node)


def compile_mjml(source: str) -> str:
    """Compile the supported MJML subset to an HTML fragment.

    Raises :class:`MjmlError` for invalid or unsupported source.
    """
    parser = _TreeParser()
    parser.feed(source)
    parser.close()
    if parser.errors:
        raise MjmlError("; ".join(parser.errors))
    unclosed = [
        f"<{node.tag}>"
        for node in parser.stack[1:]
        if node.tag == "mjml" or node.tag.startswith("mj-")
    ]
    if unclosed:
        raise MjmlError("Unclosed tag(s): " + ", ".join(unclosed))

    head, body = _check_structure(parser.root)
    _validate_tree(body)
    if head is not None:
        _validate_tree(head)

    width = _length(body.attrs.get("width", "600px"), attribute="width")
    background = _color(
        body.attrs.get("background-color", "#ffffff"),
        attribute="background-color",
    )
    inner = "".join(
        _render_wrapper(child) if child.tag == "mj-wrapper" else _render_section(child)
        for child in _elements(body)
    )
    return (
        f'<div style="margin:0;padding:0;background-color:{background};">'
        f'<table role="presentation" width="100%" border="0" cellpadding="0" '
        f'cellspacing="0" style="background-color:{background};"><tr>'
        f'<td align="center" style="padding:0;">'
        f'<table role="presentation" border="0" cellpadding="0" cellspacing="0" '
        f'style="width:{width};max-width:100%;"><tr><td>{inner}</td></tr></table>'
        f"</td></tr></table></div>"
    )
