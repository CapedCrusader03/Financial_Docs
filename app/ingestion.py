"""EDGAR-first ingestion. XBRL is normalized directly; narrative is Item-aware."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from psycopg.types.json import Jsonb

from app.db import connection
from app.llm import LLMService
from app.narrative import NarrativePart, extract_sections, header_aware_chunks
from app.sec import EdgarClient, FilingRef


def _xbrl_period(unit: dict[str, Any]) -> str:
    end = unit.get("end", "")
    start = unit.get("start")
    return f"{start}/{end}" if start else end


def _insert_filing(ref: FilingRef) -> str:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO filings(company,ticker,filing_type,fiscal_period,filed_date,source_url)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (ticker,filing_type,fiscal_period,filed_date)
               DO UPDATE SET source_url=EXCLUDED.source_url
               RETURNING id""",
            (ref.company, ref.ticker, ref.form, ref.report_date, ref.filed_date, ref.html_url),
        )
        return str(cur.fetchone()[0])


def _store_xbrl_facts(filing_id: str, accession: str, payload: dict[str, Any]) -> int:
    accession = accession.replace("-", "")
    rows: list[tuple[Any, ...]] = []
    for namespace, concepts in payload.get("facts", {}).items():
        for concept, body in concepts.items():
            for unit_name, facts in body.get("units", {}).items():
                for fact in facts:
                    if fact.get("accn") != accession or fact.get("form") not in {"10-K", "10-Q"}:
                        continue
                    try:
                        value = Decimal(str(fact["val"]))
                    except (InvalidOperation, KeyError):
                        continue
                    context = {key: fact[key] for key in ("fy", "fp", "frame", "filed", "form") if key in fact}
                    context["namespace"] = namespace
                    rows.append((filing_id, concept, _xbrl_period(fact), value, unit_name, "xbrl", 1.0, Jsonb(context)))
    if not rows:
        return 0
    with connection() as conn, conn.cursor() as cur:
        # Re-ingestion is idempotent at the filing level.
        cur.execute("DELETE FROM facts WHERE filing_id=%s AND source='xbrl'", (filing_id,))
        cur.executemany(
            """INSERT INTO facts(filing_id,concept,period,value,unit,source,confidence,context)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", rows,
        )
    return len(rows)


def _store_narrative(filing_id: str, html: str, llm: LLMService) -> int:
    chunks = [chunk for section in extract_sections(html) for chunk in header_aware_chunks(section)]
    if not chunks:
        return 0
    vectors = llm.embed([chunk.text for chunk in chunks])
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM narrative_chunks WHERE filing_id=%s AND content_kind='narrative'", (filing_id,))
        cur.executemany(
            "INSERT INTO narrative_chunks(filing_id,section,text,embedding,content_kind) VALUES (%s,%s,%s,%s,'narrative')",
            [(filing_id, chunk.section, chunk.text, vector) for chunk, vector in zip(chunks, vectors, strict=True)],
        )
    return len(chunks)


def cross_check_xbrl_pdf(filing_id: str) -> int:
    """Flag mismatched table cells without treating lower-confidence PDF values as truth."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM validation_flags WHERE filing_id=%s", (filing_id,))
        cur.execute(
            """SELECT x.concept, x.period, x.value, p.value
               FROM facts x JOIN facts p ON p.filing_id=x.filing_id AND p.concept=x.concept AND p.period=x.period AND p.unit=x.unit
               WHERE x.filing_id=%s AND x.source='xbrl' AND p.source='pdf_table' AND x.value <> p.value""",
            (filing_id,),
        )
        mismatches = cur.fetchall()
        cur.executemany(
            "INSERT INTO validation_flags(filing_id,concept,period,flag_type,details) VALUES (%s,%s,%s,'xbrl_pdf_mismatch',%s)",
            [(filing_id, row[0], row[1], f"XBRL={row[2]}; PDF={row[3]}") for row in mismatches],
        )
        return len(mismatches)


def ingest_sec(ticker: str, filing_type: str, accession_number: str | None = None) -> dict[str, Any]:
    client = EdgarClient()
    ref = client.select_filing(ticker, filing_type, accession_number)
    filing_id = _insert_filing(ref)
    facts = _store_xbrl_facts(filing_id, ref.accession, client.company_facts(ref.cik))
    chunks = _store_narrative(filing_id, client.fetch_primary_html(ref), LLMService())
    flags = cross_check_xbrl_pdf(filing_id)
    return {"filing_id": filing_id, "ticker": ref.ticker, "filing_type": ref.form, "period": ref.report_date,
            "source_url": ref.html_url, "xbrl_facts": facts, "narrative_chunks": chunks, "validation_flags": flags}
