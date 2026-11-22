import pytest

from api.tasks.knowledge_base_processing import (
    _embed_texts_in_batches,
    _read_spreadsheet_sheets,
    _spreadsheet_chunks,
    _spreadsheet_markdown,
    _spreadsheet_metadata,
)


class FakeEmbeddingService:
    def __init__(self):
        self.calls = []

    async def embed_texts(self, texts):
        self.calls.append(list(texts))
        return [[float(len(text))] for text in texts]


@pytest.mark.asyncio
async def test_embed_texts_in_batches_preserves_order():
    service = FakeEmbeddingService()

    embeddings = await _embed_texts_in_batches(
        service,
        ["a", "bb", "ccc", "dddd", "eeeee"],
        batch_size=2,
    )

    assert service.calls == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]
    assert embeddings == [[1.0], [2.0], [3.0], [4.0], [5.0]]


def test_csv_is_read_with_bom_and_semicolon_delimiter(tmp_path):
    path = tmp_path / "leads.csv"
    path.write_text(
        "Business;Phone\nAcme;+1 512 555 0100\nGlobex | LLC;\n",
        encoding="utf-8-sig",
    )

    sheets = _read_spreadsheet_sheets(str(path), ".csv", "leads.csv")

    assert sheets == [
        (
            "leads",
            [
                ["Business", "Phone"],
                ["Acme", "+1 512 555 0100"],
                ["Globex | LLC"],
            ],
        )
    ]


def test_xlsx_reads_every_non_empty_sheet(tmp_path):
    from openpyxl import Workbook

    workbook = Workbook()
    first = workbook.active
    first.title = "Leads"
    first.append(["Business", "Phone"])
    first.append(["Acme", "+15125550100"])
    second = workbook.create_sheet("Notes")
    second.append(["Owner", None, "Tier"])
    second.append(["Dana", None, "Gold"])
    workbook.save(tmp_path / "leads.xlsx")

    sheets = _read_spreadsheet_sheets(
        str(tmp_path / "leads.xlsx"), ".xlsx", "leads.xlsx"
    )

    assert [name for name, _ in sheets] == ["Leads", "Notes"]
    assert sheets[0][1] == [["Business", "Phone"], ["Acme", "+15125550100"]]
    assert sheets[1][1] == [["Owner", "", "Tier"], ["Dana", "", "Gold"]]


def test_spreadsheet_markdown_escapes_pipes_and_names_sheets(tmp_path):
    sheets = [("Leads", [["Business", "Phone"], ["Acme | LLC", "+15125550100"]])]

    markdown = _spreadsheet_markdown("leads.xlsx", sheets)

    assert markdown.startswith("# leads.xlsx")
    assert "## Sheet: Leads" in markdown
    assert "| Business | Phone |" in markdown
    assert "Acme \\| LLC" in markdown
    assert _spreadsheet_metadata(sheets) == {
        "conversion": "local_spreadsheet",
        "sheets": ["Leads"],
        "rows": 2,
    }


def test_spreadsheet_chunks_repeat_the_header_and_keep_row_ranges():
    rows = [["Business", "Phone"]] + [
        [f"Lead {index}", f"+1512555{index:04d}"] for index in range(45)
    ]

    chunks = _spreadsheet_chunks("leads.csv", [("leads", rows)])

    assert len(chunks) == 2
    assert [chunk["chunk_index"] for chunk in chunks] == [0, 1]
    assert "rows 2-41" in chunks[0]["chunk_text"]
    assert "rows 42-46" in chunks[1]["chunk_text"]
    assert chunks[0]["chunk_text"].count("| Business | Phone |") == 1
    assert chunks[1]["chunk_text"].count("| Business | Phone |") == 1
    assert chunks[1]["chunk_metadata"]["row_start"] == 42
    assert chunks[1]["chunk_metadata"]["row_end"] == 46


def test_header_only_spreadsheet_cannot_be_chunked():
    with pytest.raises(ValueError, match="no data rows"):
        _spreadsheet_chunks("leads.csv", [("leads", [["Business", "Phone"]])])
