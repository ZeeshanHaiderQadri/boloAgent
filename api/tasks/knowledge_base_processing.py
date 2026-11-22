"""ARQ background task for processing knowledge base documents.

Document conversion and chunking live in the Model Proxy Service (MPS);
this task downloads the file from S3, calls MPS, then handles the embedding
and DB writes locally.
"""

import csv
import io
import os
import tempfile

from loguru import logger

from api.db import db_client
from api.db.models import KnowledgeBaseChunkModel
from api.services.gen_ai import build_embedding_service
from api.services.mps_service_key_client import mps_service_key_client
from api.services.storage import storage_fs

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
EMBEDDING_BATCH_SIZE = 64

# Spreadsheets are converted locally instead of going through MPS.
SPREADSHEET_EXTENSIONS = {".xlsx", ".csv"}
SPREADSHEET_ROWS_PER_CHUNK = 40
MAX_SPREADSHEET_ROWS = 5_000
MAX_SPREADSHEET_COLUMNS = 50


def _is_spreadsheet(filename: str) -> bool:
    return os.path.splitext(filename)[1].lower() in SPREADSHEET_EXTENSIONS


def _clean_spreadsheet_row(raw_row) -> list[str]:
    cells = ["" if value is None else str(value).strip() for value in raw_row]
    cells = cells[:MAX_SPREADSHEET_COLUMNS]
    while cells and not cells[-1]:
        cells.pop()
    return cells


def _read_xlsx_sheets(path: str) -> list[tuple[str, list[list[str]]]]:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    remaining = MAX_SPREADSHEET_ROWS
    sheets: list[tuple[str, list[list[str]]]] = []
    try:
        for worksheet in workbook.worksheets:
            rows: list[list[str]] = []
            for raw_row in worksheet.iter_rows(values_only=True):
                cleaned = _clean_spreadsheet_row(raw_row)
                if not cleaned:
                    continue
                rows.append(cleaned)
                remaining -= 1
                if remaining <= 0:
                    break
            if rows:
                sheets.append((worksheet.title or "Sheet", rows))
            if remaining <= 0:
                break
    finally:
        workbook.close()
    return sheets


def _read_csv_rows(path: str) -> list[list[str]]:
    with open(path, "rb") as handle:
        payload = handle.read()
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        # CSVs saved by desktop Excel are often cp1252, not UTF-8.
        text = payload.decode("cp1252", errors="replace")

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    rows: list[list[str]] = []
    for raw_row in csv.reader(io.StringIO(text), dialect):
        cleaned = _clean_spreadsheet_row(raw_row)
        if cleaned:
            rows.append(cleaned)
        if len(rows) >= MAX_SPREADSHEET_ROWS:
            break
    return rows


def _read_spreadsheet_sheets(
    path: str, extension: str, display_name: str
) -> list[tuple[str, list[list[str]]]]:
    if extension == ".xlsx":
        sheets = _read_xlsx_sheets(path)
    else:
        rows = _read_csv_rows(path)
        sheet_name = os.path.splitext(display_name)[0]
        sheets = [(sheet_name, rows)] if rows else []
    if not sheets:
        raise ValueError("The spreadsheet has no readable rows.")
    return sheets


def _markdown_table(rows: list[list[str]]) -> str:
    width = max(len(row) for row in rows)

    def render(cells: list[str]) -> str:
        padded = cells + [""] * (width - len(cells))
        escaped = [cell.replace("|", "\\|").replace("\n", " ") for cell in padded]
        return "| " + " | ".join(escaped) + " |"

    header, *body = rows
    lines = [render(header), "| " + " | ".join(["---"] * width) + " |"]
    lines.extend(render(row) for row in body)
    return "\n".join(lines)


def _spreadsheet_markdown(
    display_name: str, sheets: list[tuple[str, list[list[str]]]]
) -> str:
    blocks = [f"# {display_name}"]
    for name, rows in sheets:
        blocks.append(f"## Sheet: {name}\n\n{_markdown_table(rows)}")
    return "\n\n".join(blocks)


def _spreadsheet_metadata(
    sheets: list[tuple[str, list[list[str]]]],
) -> dict:
    return {
        "conversion": "local_spreadsheet",
        "sheets": [name for name, _ in sheets],
        "rows": sum(len(rows) for _, rows in sheets),
    }


def _spreadsheet_chunks(
    display_name: str, sheets: list[tuple[str, list[list[str]]]]
) -> list[dict]:
    chunks: list[dict] = []
    for name, rows in sheets:
        header, body = rows[0], rows[1:]
        for start in range(0, len(body), SPREADSHEET_ROWS_PER_CHUNK):
            group = body[start : start + SPREADSHEET_ROWS_PER_CHUNK]
            row_start = start + 2
            row_end = start + 1 + len(group)
            text = (
                f"{display_name} — sheet {name}, rows {row_start}-{row_end}\n\n"
                + _markdown_table([header, *group])
            )
            chunks.append(
                {
                    "chunk_text": text,
                    "chunk_index": len(chunks),
                    "chunk_metadata": {
                        "source": "spreadsheet",
                        "sheet": name,
                        "row_start": row_start,
                        "row_end": row_end,
                    },
                    "token_count": max(len(text) // 4, 1),
                }
            )
    if not chunks:
        raise ValueError("The spreadsheet has no data rows below its header to index.")
    return chunks


async def _embed_texts_in_batches(
    embedding_service,
    texts: list[str],
    batch_size: int = EMBEDDING_BATCH_SIZE,
) -> list[list[float]]:
    """Generate embeddings in bounded batches for provider/MPS stability."""
    embeddings: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        logger.info(
            f"Generating embedding batch {start // batch_size + 1} ({len(batch)} texts)"
        )
        embeddings.extend(await embedding_service.embed_texts(batch))
    return embeddings


async def process_knowledge_base_document(
    ctx,
    document_id: int,
    s3_key: str,
    organization_id: int,
    created_by_provider_id: str,
    max_tokens: int = 128,
    retrieval_mode: str = "chunked",
):
    """Process a knowledge base document via MPS: download, call MPS, embed, store.

    Args:
        ctx: ARQ context
        document_id: Database ID of the document
        s3_key: S3 key where the file is stored
        organization_id: Organization ID
        created_by_provider_id: Uploading user's provider ID (for OSS-mode auth to MPS)
        max_tokens: Maximum number of tokens per chunk (default: 128)
        retrieval_mode: "chunked" for vector search or "full_document" for full text
    """
    logger.info(
        f"Processing knowledge base document: document_id={document_id}, "
        f"s3_key={s3_key}, org={organization_id}, mode={retrieval_mode}"
    )

    temp_file_path = None

    try:
        await db_client.update_document_status(document_id, "processing")

        filename = s3_key.split("/")[-1]
        file_extension = os.path.splitext(filename)[1] or ".bin"

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=file_extension)
        temp_file_path = temp_file.name
        temp_file.close()

        logger.info(f"Downloading file from S3: {s3_key}")
        download_success = await storage_fs.adownload_file(s3_key, temp_file_path)
        if not download_success:
            raise Exception(f"Failed to download file from S3: {s3_key}")
        if not os.path.exists(temp_file_path):
            raise FileNotFoundError(f"Downloaded file not found: {temp_file_path}")

        file_size = os.path.getsize(temp_file_path)
        logger.info(f"Downloaded file size: {file_size} bytes")

        if file_size > MAX_FILE_SIZE_BYTES:
            error_message = (
                f"File size ({file_size / (1024 * 1024):.1f}MB) exceeds the "
                f"maximum allowed size of {MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB."
            )
            logger.warning(f"Document {document_id}: {error_message}")
            await db_client.update_document_status(
                document_id, "failed", error_message=error_message
            )
            return

        file_hash = db_client.compute_file_hash(temp_file_path)
        mime_type = db_client.get_mime_type(temp_file_path)

        document = await db_client.get_document_by_id(document_id)
        if not document:
            raise Exception(f"Document {document_id} not found")

        # Reject duplicates (same hash already ingested for this org).
        existing_doc = await db_client.get_document_by_hash(file_hash, organization_id)
        if existing_doc and existing_doc.id != document_id:
            error_message = (
                f"This content is identical to '{existing_doc.filename}'. "
                f"Keep only one copy of it in the knowledge base."
            )
            logger.warning(
                f"Duplicate document detected: {document_id} is duplicate of "
                f"{existing_doc.id} ({existing_doc.filename})"
            )
            await db_client.update_document_metadata(
                document_id,
                file_size_bytes=file_size,
                file_hash=file_hash,
                mime_type=mime_type,
            )
            await db_client.update_document_status(
                document_id,
                "failed",
                error_message=error_message,
                docling_metadata={
                    "duplicate_of": existing_doc.document_uuid,
                    "duplicate_filename": existing_doc.filename,
                },
            )
            return

        await db_client.update_document_metadata(
            document_id,
            file_size_bytes=file_size,
            file_hash=file_hash,
            mime_type=mime_type,
        )

        embeddings_provider = None
        embeddings_api_key = None
        embeddings_model = None
        embeddings_base_url = None
        embeddings_endpoint = None
        embeddings_api_version = None
        if retrieval_mode == "chunked":
            from api.services.configuration.ai_model_configuration import (
                apply_managed_embeddings_base_url,
                get_resolved_ai_model_configuration,
            )

            resolved_config = await get_resolved_ai_model_configuration(
                organization_id=document.organization_id,
            )
            effective_config = resolved_config.effective
            if effective_config.embeddings:
                embeddings_provider = getattr(
                    effective_config.embeddings, "provider", None
                )
                embeddings_api_key = effective_config.embeddings.api_key
                embeddings_model = effective_config.embeddings.model
                embeddings_base_url = apply_managed_embeddings_base_url(
                    provider=embeddings_provider,
                    base_url=getattr(effective_config.embeddings, "base_url", None),
                )
                embeddings_endpoint = getattr(
                    effective_config.embeddings, "endpoint", None
                )
                embeddings_api_version = getattr(
                    effective_config.embeddings, "api_version", None
                )
                logger.info(
                    f"Using user embeddings config: provider={embeddings_provider}, "
                    f"model={embeddings_model}"
                )

        is_spreadsheet = _is_spreadsheet(filename)
        sheets = (
            _read_spreadsheet_sheets(temp_file_path, file_extension.lower(), filename)
            if is_spreadsheet
            else []
        )

        mps_response: dict = {}
        if is_spreadsheet:
            logger.info(
                f"Converting spreadsheet locally: {len(sheets)} sheet(s), "
                f"mode={retrieval_mode}"
            )
            docling_metadata = _spreadsheet_metadata(sheets)
        else:
            logger.info(
                f"Delegating document processing to MPS (mode={retrieval_mode})"
            )
            mps_response = await mps_service_key_client.process_document(
                file_path=temp_file_path,
                filename=filename,
                content_type=mime_type or "application/octet-stream",
                retrieval_mode=retrieval_mode,
                max_tokens=max_tokens,
                organization_id=document.organization_id,
                created_by=created_by_provider_id,
            )
            docling_metadata = mps_response.get("docling_metadata", {})

        if retrieval_mode == "full_document":
            full_text = (
                _spreadsheet_markdown(filename, sheets)
                if is_spreadsheet
                else mps_response.get("full_text") or ""
            )
            await db_client.update_document_full_text(document_id, full_text)
            await db_client.update_document_status(
                document_id,
                "completed",
                total_chunks=0,
                docling_metadata=docling_metadata,
            )
            logger.info(
                f"Successfully processed full_document {document_id}. "
                f"Text length: {len(full_text)} chars"
            )
            return

        if not embeddings_api_key:
            error_message = (
                "API key not configured. Please set your API key in "
                "Model Configurations > Embedding to process documents."
            )
            logger.warning(f"Document {document_id}: {error_message}")
            await db_client.update_document_status(
                document_id, "failed", error_message=error_message
            )
            return

        # Ingestion runs outside any workflow run, so resolve the MPS correlation
        # id here.
        embedding_service = await build_embedding_service(
            db_client=db_client,
            provider=embeddings_provider,
            api_key=embeddings_api_key,
            model=embeddings_model,
            base_url=embeddings_base_url,
            endpoint=embeddings_endpoint,
            api_version=embeddings_api_version,
            resolve_correlation=True,
        )

        if is_spreadsheet:
            mps_chunks = _spreadsheet_chunks(filename, sheets)
        else:
            mps_chunks = mps_response.get("chunks", [])
        if not mps_chunks:
            logger.warning(f"Document {document_id}: MPS returned zero chunks")

        chunk_records = []
        chunk_texts = []
        for chunk in mps_chunks:
            contextualized = chunk.get("contextualized_text") or chunk["chunk_text"]
            chunk_records.append(
                KnowledgeBaseChunkModel(
                    document_id=document_id,
                    organization_id=organization_id,
                    chunk_text=chunk["chunk_text"],
                    contextualized_text=contextualized,
                    chunk_index=chunk["chunk_index"],
                    chunk_metadata=chunk.get("chunk_metadata") or {},
                    embedding_model=embedding_service.get_model_id(),
                    embedding_dimension=embedding_service.get_embedding_dimension(),
                    token_count=chunk.get("token_count", 0),
                )
            )
            chunk_texts.append(contextualized)

        logger.info(
            f"Generating embeddings for {len(chunk_texts)} chunks "
            f"using {embedding_service.get_model_id()}"
        )
        embeddings = await _embed_texts_in_batches(embedding_service, chunk_texts)
        if len(embeddings) != len(chunk_records):
            raise ValueError(
                "Embedding count mismatch: "
                f"expected {len(chunk_records)}, got {len(embeddings)}"
            )
        for chunk_record, embedding in zip(chunk_records, embeddings):
            chunk_record.embedding = embedding

        logger.info("Storing chunks in database")
        await db_client.replace_chunks_for_document(
            document_id=document_id,
            organization_id=organization_id,
            chunks=chunk_records,
        )

        await db_client.update_document_status(
            document_id,
            "completed",
            total_chunks=len(chunk_records),
            docling_metadata=docling_metadata,
        )

        logger.info(
            f"Successfully processed knowledge base document {document_id}. "
            f"Total chunks: {len(chunk_records)}"
        )

    except Exception as e:
        logger.exception(
            "Error processing knowledge base document {}: {}", document_id, e
        )
        await db_client.update_document_status(
            document_id, "failed", error_message=str(e)
        )
        raise

    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.debug(f"Cleaned up temp file: {temp_file_path}")
            except Exception as e:
                logger.warning(f"Failed to clean up temp file {temp_file_path}: {e}")
