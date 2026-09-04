"""PDF-only path using Docling's layout/table model. Do not substitute a text extractor."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from app.db import connection
from app.ingestion import cross_check_xbrl_pdf
from app.llm import LLMService

SCALE = re.compile(r"(?:\$|US\$)?\s*\(?\s*in\s+(thousands|millions|billions)\s*(?:of\s+dollars)?\s*\)?", re.I)
FOOTNOTE = re.compile(r"(?<=\d)[*†‡a-zA-Z]+$")
NUMBER = re.compile(r"^\(?\$?\s*([\d,]+(?:\.\d+)?)\)?$")
MULTIPLIER = {"thousands": 1_000, "millions": 1_000_000, "billions": 1_000_000_000}


def _unit_from_caption(caption: str) -> tuple[str, int]:
    match = SCALE.search(caption)
    if not match:
        return "USD", 1
    scale = match.group(1).lower()
    return f"USD ({scale})", MULTIPLIER[scale]


def _parse_cell(value: object, multiplier: int) -> Decimal | None:
    text = FOOTNOTE.sub("", str(value).strip()).replace("—", "").replace("–", "")
    match = NUMBER.match(text)
    if not match:
        return None
    try:
        parsed = Decimal(match.group(1).replace(",", "")) * multiplier
        return -parsed if text.startswith("(") else parsed
    except InvalidOperation:
        return None


def _markdown(frame: Any) -> str:
    headers = [str(item) for item in frame.columns]
    rows = [[str(cell) for cell in row] for row in frame.astype(str).values.tolist()]
    return "| " + " | ".join(headers) + " |\n| " + " | ".join(["---"] * len(headers)) + " |\n" + "\n".join(
        "| " + " | ".join(row) + " |" for row in rows
    )


def _pdf_internal_consistency(filing_id: str) -> int:
    """Use available Q1–Q4/annual labels as a PDF-only sanity signal, never as a correction."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT concept, unit, period, value FROM facts
               WHERE filing_id=%s AND source='pdf_table' ORDER BY concept, unit, period""", (filing_id,)
        )
        groups: dict[tuple[str, str], list[tuple[str, Decimal]]] = {}
        for concept, unit, period, value in cur.fetchall():
            groups.setdefault((concept, unit), []).append((period.lower(), value))
        flags: list[tuple[str, str, str]] = []
        for (concept, unit), entries in groups.items():
            quarters = [value for label, value in entries if re.search(r"\bq[1-4]\b", label)]
            annual = next((value for label, value in entries if "annual" in label or re.search(r"\bfy\s*20", label)), None)
            if len(quarters) == 4 and annual is not None and sum(quarters) != annual:
                flags.append((concept, "PDF quarterly values do not sum to reported annual value", unit))
        cur.executemany(
            "INSERT INTO validation_flags(filing_id,concept,period,flag_type,details) VALUES (%s,%s,'pdf_only','pdf_internal_consistency',%s)",
            [(filing_id, concept, f"{detail}; unit={unit}") for concept, detail, unit in flags],
        )
        return len(flags)


def ingest_pdf(path: str, company: str, ticker: str, filing_type: str, fiscal_period: str, filed_date: str, source_url: str) -> dict[str, Any]:
    """Extract table grids, captions, labels, and cells. Docling is mandatory for this path."""
    document_path = Path(path)
    if not document_path.is_file():
        raise ValueError(f"PDF does not exist: {path}")
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise RuntimeError("Install the docling extra to enable PDF-only ingestion.") from exc

    result = DocumentConverter().convert(document_path)
    doc = result.document
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO filings(company,ticker,filing_type,fiscal_period,filed_date,source_url)
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
            (company, ticker.upper(), filing_type, fiscal_period, date.fromisoformat(filed_date), source_url),
        )
        filing_id = str(cur.fetchone()[0])

    rows: list[tuple[Any, ...]] = []
    table_chunks: list[tuple[str, str]] = []
    # Docling yields table objects produced by its layout and table-structure models.
    for ordinal, table in enumerate(doc.tables):
        frame = table.export_to_dataframe()
        caption = " ".join(str(x) for x in getattr(table, "captions", []) or [])
        unit, multiplier = _unit_from_caption(caption)
        markdown = _markdown(frame)
        table_chunks.append((f"PDF table {ordinal + 1}: {caption or 'uncaptioned'}", f"{caption}\n\n{markdown}"))
        columns = [str(col) for col in frame.columns]
        for _, record in frame.iterrows():
            label = str(record.iloc[0]).strip()
            for col_index, column in enumerate(columns[1:], start=1):
                parsed = _parse_cell(record.iloc[col_index], multiplier)
                if parsed is not None:
                    # The row and column labels and the nearby scale caption are retained in context.
                    rows.append((filing_id, label, column, parsed, unit, "pdf_table", 0.72,
                                 Jsonb({"table_caption": caption, "row_label": label, "column_label": column, "scale_caption": caption})))
    with connection() as conn, conn.cursor() as cur:
        if rows:
            cur.executemany("INSERT INTO facts(filing_id,concept,period,value,unit,source,confidence,context) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", rows)
        if table_chunks:
            vectors = LLMService().embed([text for _, text in table_chunks])
            cur.executemany("INSERT INTO narrative_chunks(filing_id,section,text,embedding,content_kind) VALUES (%s,%s,%s,%s,'table_markdown')",
                            [(filing_id, section, text, vector) for (section, text), vector in zip(table_chunks, vectors, strict=True)])
    xbrl_pdf_flags = cross_check_xbrl_pdf(filing_id)
    internal_flags = _pdf_internal_consistency(filing_id)
    return {"filing_id": filing_id, "pdf_facts": len(rows), "table_markdown_chunks": len(table_chunks),
            "validation_flags": xbrl_pdf_flags + internal_flags}
