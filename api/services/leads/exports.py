"""Lead list exports: CSV, XLSX, JSON, branded PDF and PNG snapshots."""

from __future__ import annotations

import csv
import io
import json
import os
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from api.constants import LEADS_EXPORT_MAX_TABLE_ROWS

EXPORT_FORMATS = ("csv", "xlsx", "json", "pdf", "png")

MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
    "pdf": "application/pdf",
    "png": "image/png",
}

TEAL = "#1F7A6B"
TEAL_LIGHT = "#E8F4F0"
INK = "#101614"
MUTED = "#5B6B67"


def _fmt_rating(lead: Any) -> str:
    rating = getattr(lead, "rating", None)
    return f"{rating:.1f}" if rating is not None else ""


def _fmt_date(value: Any) -> str:
    if value is None:
        return ""
    try:
        return value.strftime("%Y-%m-%d")
    except AttributeError:
        return str(value)


def _fmt_tags(lead: Any) -> str:
    tags = getattr(lead, "tags", None) or []
    return ", ".join(str(tag) for tag in tags)


EXPORT_COLUMNS: list[tuple[str, Callable[[Any], str]]] = [
    ("Business", lambda lead: lead.title or ""),
    ("Category", lambda lead: lead.category or ""),
    ("Phone", lambda lead: lead.phone_e164 or lead.phone or ""),
    ("Email", lambda lead: lead.email or ""),
    ("Website", lambda lead: lead.website or ""),
    ("Address", lambda lead: lead.address or ""),
    ("City", lambda lead: lead.city or ""),
    ("State", lambda lead: lead.state or ""),
    ("Country", lambda lead: lead.country or ""),
    ("Rating", _fmt_rating),
    ("Reviews", lambda lead: str(lead.review_count or "")),
    ("Status", lambda lead: (lead.crm_status or "").replace("_", " ").title()),
    ("Tags", _fmt_tags),
    ("Added", lambda lead: _fmt_date(lead.created_at)),
]


def _rows(leads: Iterable[Any], columns=EXPORT_COLUMNS) -> list[list[str]]:
    return [[getter(lead) for _, getter in columns] for lead in leads]


def _filename(fmt: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"bolo_leads_{stamp}.{fmt}"


def build_csv(leads: list[Any], *, title: str = "", subtitle: str = "") -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([header for header, _ in EXPORT_COLUMNS])
    writer.writerows(_rows(leads))
    # UTF-8 BOM keeps Excel from mangling accented business names.
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def build_json(leads: list[Any], *, title: str = "", subtitle: str = "") -> bytes:
    payload = []
    for lead in leads:
        payload.append(
            {
                "id": lead.id,
                "title": lead.title,
                "category": lead.category,
                "address": lead.address,
                "city": lead.city,
                "state": lead.state,
                "country": lead.country,
                "postal_code": lead.postal_code,
                "phone": lead.phone,
                "phone_e164": lead.phone_e164,
                "email": lead.email,
                "website": lead.website,
                "place_id": lead.place_id,
                "rating": lead.rating,
                "review_count": lead.review_count,
                "latitude": lead.latitude,
                "longitude": lead.longitude,
                "crm_status": lead.crm_status,
                "tags": lead.tags or [],
                "owner_user_id": lead.owner_user_id,
                "notes": lead.notes,
                "source": lead.source,
                "created_at": (
                    lead.created_at.isoformat() if lead.created_at else None
                ),
            }
        )
    return json.dumps(
        {"title": title, "subtitle": subtitle, "count": len(payload), "leads": payload},
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")


def build_xlsx(leads: list[Any], *, title: str = "", subtitle: str = "") -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Leads"

    headers = [header for header, _ in EXPORT_COLUMNS]
    sheet.append(headers)
    header_fill = PatternFill("solid", fgColor="1F7A6B")
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center")

    for row in _rows(leads):
        sheet.append(row)

    widths = {
        "A": 34,
        "B": 20,
        "C": 20,
        "D": 28,
        "E": 30,
        "F": 40,
        "G": 18,
        "H": 12,
        "I": 10,
        "J": 9,
        "K": 9,
        "L": 16,
        "M": 22,
        "N": 12,
    }
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(leads) + 1}"

    # Keep long free-text cells readable without exploding row height.
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=False)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _table_leads(leads: list[Any]) -> tuple[list[Any], bool]:
    if len(leads) <= LEADS_EXPORT_MAX_TABLE_ROWS:
        return leads, False
    return leads[:LEADS_EXPORT_MAX_TABLE_ROWS], True


_PDF_COLUMNS: list[tuple[str, Callable[[Any], str]]] = [
    ("Business", lambda lead: lead.title or ""),
    ("Category", lambda lead: lead.category or ""),
    ("Phone", lambda lead: lead.phone_e164 or lead.phone or ""),
    ("Email", lambda lead: lead.email or ""),
    ("City", lambda lead: lead.city or ""),
    ("State", lambda lead: lead.state or ""),
    ("Country", lambda lead: lead.country or ""),
    ("Rating", _fmt_rating),
    ("Reviews", lambda lead: str(lead.review_count or "")),
    ("Status", lambda lead: (lead.crm_status or "").replace("_", " ").title()),
]


def build_pdf(
    leads: list[Any], *, title: str = "Lead export", subtitle: str = ""
) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    table_leads, truncated = _table_leads(leads)
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title=title,
    )
    title_style = ParagraphStyle(
        "LeadTitle",
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor(INK),
    )
    meta_style = ParagraphStyle(
        "LeadMeta",
        fontName="Helvetica",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor(MUTED),
    )
    story: list[Any] = [
        Paragraph(title, title_style),
        Spacer(1, 3 * mm),
        Paragraph(
            f"{len(leads)} lead(s)"
            + (f" · {subtitle}" if subtitle else "")
            + f" · Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
            meta_style,
        ),
        Spacer(1, 5 * mm),
    ]

    data = [[header for header, _ in _PDF_COLUMNS]]
    for lead in table_leads:
        data.append([getter(lead) for _, getter in _PDF_COLUMNS])

    table = Table(
        data,
        repeatRows=1,
        colWidths=[40 * mm, 27 * mm, 27 * mm, 33 * mm, 22 * mm, 15 * mm, 15 * mm, 13 * mm, 14 * mm, 24 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(TEAL)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F8F6")]),
                ("LINEBELOW", (0, 0), (-1, 0), 0.7, colors.HexColor(TEAL)),
                ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#D5E3DE")),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(table)
    if truncated:
        story.append(Spacer(1, 3 * mm))
        story.append(
            Paragraph(
                f"Showing the first {LEADS_EXPORT_MAX_TABLE_ROWS} rows. "
                "Download CSV or XLSX for the full list.",
                meta_style,
            )
        )
    document.build(story)
    return buffer.getvalue()


_PNG_COLUMNS: list[tuple[str, int, Callable[[Any], str]]] = [
    ("Business", 300, lambda lead: lead.title or ""),
    ("Category", 150, lambda lead: lead.category or ""),
    ("Phone", 150, lambda lead: lead.phone_e164 or lead.phone or ""),
    ("City", 130, lambda lead: lead.city or ""),
    ("Country", 80, lambda lead: lead.country or ""),
    ("Rating", 70, _fmt_rating),
    ("Reviews", 80, lambda lead: str(lead.review_count or "")),
    ("Status", 140, lambda lead: (lead.crm_status or "").replace("_", " ").title()),
]


def _load_font(size: int):
    from PIL import ImageFont

    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def build_png(
    leads: list[Any], *, title: str = "Lead export", subtitle: str = ""
) -> bytes:
    from PIL import Image, ImageDraw

    table_leads, truncated = _table_leads(leads)
    padding = 18
    row_height = 36
    header_height = 44
    title_height = 78
    footer_height = 34
    width = padding * 2 + sum(width for _, width, _ in _PNG_COLUMNS)
    height = (
        title_height
        + header_height
        + row_height * max(1, len(table_leads))
        + footer_height
        + padding
    )
    image = Image.new("RGB", (width, height), "#FFFFFF")
    draw = ImageDraw.Draw(image)

    title_font = _load_font(22)
    subtitle_font = _load_font(12)
    header_font = _load_font(13)
    cell_font = _load_font(12)

    draw.text((padding, 18), title, font=title_font, fill=INK)
    meta = f"{len(leads)} lead(s)"
    if subtitle:
        meta += f" · {subtitle}"
    meta += f" · Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}"
    draw.text((padding, 48), meta, font=subtitle_font, fill=MUTED)

    def fit(text: str, font, max_width: int) -> str:
        text = str(text or "")
        if draw.textlength(text, font=font) <= max_width:
            return text
        while text and draw.textlength(text + "…", font=font) > max_width:
            text = text[:-1]
        return text + "…"

    header_y = title_height
    draw.rectangle(
        (padding, header_y, width - padding, header_y + header_height),
        fill=TEAL,
    )
    x = padding
    for label, column_width, _ in _PNG_COLUMNS:
        draw.text(
            (x + 10, header_y + 14),
            label,
            font=header_font,
            fill="#FFFFFF",
        )
        x += column_width

    y = header_y + header_height
    for index, lead in enumerate(table_leads):
        if index % 2 == 1:
            draw.rectangle(
                (padding, y, width - padding, y + row_height),
                fill="#F3F8F6",
            )
        x = padding
        for _, column_width, getter in _PNG_COLUMNS:
            draw.text(
                (x + 10, y + 11),
                fit(getter(lead), cell_font, column_width - 20),
                font=cell_font,
                fill=INK,
            )
            x += column_width
        draw.line(
            (padding, y + row_height, width - padding, y + row_height),
            fill="#D5E3DE",
            width=1,
        )
        y += row_height

    footer = "Generated by Bolo"
    if truncated:
        footer += f" · showing first {LEADS_EXPORT_MAX_TABLE_ROWS} rows"
    draw.text((padding, height - 26), footer, font=subtitle_font, fill=MUTED)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


_BUILDERS = {
    "csv": build_csv,
    "xlsx": build_xlsx,
    "json": build_json,
    "pdf": build_pdf,
    "png": build_png,
}


def build_export(
    leads: list[Any], fmt: str, *, title: str = "", subtitle: str = ""
) -> tuple[bytes, str, str]:
    """Return (content, filename, media_type) for the requested format."""
    builder = _BUILDERS.get(fmt)
    if builder is None:
        raise ValueError(f"Unsupported export format: {fmt}")
    content = builder(leads, title=title, subtitle=subtitle)
    return content, _filename(fmt), MEDIA_TYPES[fmt]
