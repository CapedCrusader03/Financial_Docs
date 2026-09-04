"""Query router and deterministic SQL templates for financial facts."""
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from app.db import connection, vector_literal
from app.llm import LLMService
from app.schemas import Answer, Route


def _company_filings(company: str) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id::text, fiscal_period, filed_date::text, filing_type
               FROM filings WHERE upper(ticker)=upper(%s) OR company ILIKE %s
               ORDER BY filed_date DESC""", (company, f"%{company}%"),
        )
        return [
            {"id": str(row[0]), "fiscal_period": row[1], "filed_date": row[2], "filing_type": row[3]}
            for row in cur.fetchall()
        ]


def resolve_filing_periods(company: str, references: list[str]) -> list[dict[str, Any]]:
    """Resolve human period references to stored filing IDs before retrieval or ranking."""
    filings = _company_filings(company)
    if not filings:
        raise LookupError(f"No ingested filings found for {company}.")
    if not references or any("latest" in ref.lower() for ref in references):
        return [filings[0]]
    selected: list[dict[str, Any]] = []
    for reference in references:
        ref = reference.lower()
        if "prior" in ref or "previous" in ref:
            if len(filings) > 1:
                selected.append(filings[1])
        else:
            selected.extend(item for item in filings if reference in item["fiscal_period"])
    return selected or [filings[0]]


def _taxonomy(company: str) -> list[str]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT fa.concept FROM facts fa JOIN filings f ON f.id=fa.filing_id
               WHERE upper(f.ticker)=upper(%s) OR f.company ILIKE %s ORDER BY fa.concept""",
            (company, f"%{company}%"),
        )
        return [row[0] for row in cur.fetchall()]


def resolve_concept(term: str, company: str, llm: LLMService) -> str:
    key = term.strip().lower()
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT xbrl_concept FROM concept_mappings WHERE natural_language_term=%s", (key,))
        row = cur.fetchone()
        if row:
            return row[0]
    concept = llm.map_concept(key, _taxonomy(company))
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO concept_mappings(natural_language_term,xbrl_concept,cached_at) VALUES (%s,%s,CURRENT_TIMESTAMP)
               ON CONFLICT (natural_language_term) DO UPDATE SET xbrl_concept=EXCLUDED.xbrl_concept,cached_at=EXCLUDED.cached_at""",
            (key, concept),
        )
    return concept


# Every numerical aggregation is expressed below as a closed, parameterized SQL template.
AGGREGATE_EXPRESSIONS = {
    "value": "jsonb_agg(jsonb_build_object('filing_period',filing_period,'fact_period',fact_period,'value',value,'unit',unit,'source',source) ORDER BY filed_date DESC)",
    "sum": "jsonb_build_array(jsonb_build_object('value', SUM(value), 'unit', MIN(unit), 'operation', 'sum'))",
    "average": "jsonb_build_array(jsonb_build_object('value', AVG(value), 'unit', MIN(unit), 'operation', 'average'))",
    "minimum": "jsonb_build_array(jsonb_build_object('value', MIN(value), 'unit', MIN(unit), 'operation', 'minimum'))",
    "maximum": "jsonb_build_array(jsonb_build_object('value', MAX(value), 'unit', MIN(unit), 'operation', 'maximum'))",
    "change": "jsonb_build_array(jsonb_build_object('value', MAX(value) - MIN(value), 'unit', MIN(unit), 'operation', 'change'))",
    "percent_change": "jsonb_build_array(jsonb_build_object('value', CASE WHEN MIN(value)=0 THEN NULL ELSE ((MAX(value)-MIN(value))/ABS(MIN(value)))*100 END, 'unit', 'percent', 'operation', 'percent_change'))",
}


def structured_query(filing_ids: list[str], concept: str, aggregation: str) -> list[dict[str, Any]]:
    if aggregation not in AGGREGATE_EXPRESSIONS:
        raise ValueError(f"Unsupported aggregate template: {aggregation}")
    # A filing's report date resolves the comparison. The window chooses the shortest
    # current-period duration, preventing a YTD or non-GAAP comparable from winning by similarity.
    expression = AGGREGATE_EXPRESSIONS[aggregation]
    sql = f"""
    WITH ranked AS (
      SELECT f.fiscal_period AS filing_period, f.filed_date, fa.period AS fact_period, fa.value, fa.unit, fa.source,
             row_number() OVER (
               PARTITION BY f.id, fa.concept, fa.unit
               ORDER BY CASE WHEN coalesce(fa.context->>'frame','') ~ '^CY[0-9]{{4}}Q[1-4]$' THEN 0 ELSE 1 END,
                        CASE WHEN position('/' in fa.period)>0 THEN
                          split_part(fa.period,'/',2)::date - split_part(fa.period,'/',1)::date ELSE 9999 END,
                        fa.id
             ) AS choice
      FROM facts fa JOIN filings f ON f.id=fa.filing_id
      WHERE fa.filing_id = ANY(%s::uuid[]) AND fa.concept=%s
        AND fa.source='xbrl' AND (fa.period=f.fiscal_period OR fa.period LIKE '%%/' || f.fiscal_period)
    ), selected AS (SELECT * FROM ranked WHERE choice=1)
    SELECT {expression} AS result FROM selected
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, (filing_ids, concept))
        result = cur.fetchone()[0]
    return result or []


def narrative_search(question: str, filings: list[dict[str, Any]], section_hint: str | None, llm: LLMService) -> list[dict[str, str]]:
    embedding = llm.embed([question])[0]
    ids = [item["id"] for item in filings]
    section_filter = f"%{section_hint}%" if section_hint else "%"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT nc.section, nc.text, f.fiscal_period, 1 - (nc.embedding <=> %s::vector) AS semantic_score
               FROM narrative_chunks nc JOIN filings f ON f.id=nc.filing_id
               WHERE nc.filing_id=ANY(%s::uuid[]) AND nc.section ILIKE %s
               ORDER BY nc.embedding <=> %s::vector LIMIT 8""",
            (vector_literal(embedding), ids, section_filter, vector_literal(embedding)),
        )
        candidates = [
            {"section": row[0], "text": row[1], "fiscal_period": row[2], "semantic_score": row[3]}
            for row in cur.fetchall()
        ]
    order = llm.judge_rerank(question, candidates)
    return [candidates[index] for index in order[:4]]


def answer_question(company: str, question: str) -> Answer:
    llm = LLMService()
    route: Route = llm.classify(question)  # Classification occurs before any database access.
    filings = resolve_filing_periods(company, route.periods)
    structured: list[dict[str, Any]] = []
    if route.intent in {"aggregate", "hybrid"}:
        if not route.concept:
            raise ValueError("Router did not identify an XBRL concept for a structured question.")
        concept = resolve_concept(route.concept, company, llm)
        structured = structured_query([item["id"] for item in filings], concept, route.aggregation or "value")
        if not structured:
            raise LookupError(f"No structured fact matched concept {concept} in the resolved filing period.")
    narrative: list[dict[str, str]] = []
    if route.intent in {"narrative", "hybrid"}:
        # For hybrid requests the same resolved filing period from SQL scopes the explanatory search.
        narrative = narrative_search(question, filings, route.section_hint, llm)
    if route.intent == "aggregate":
        # This is presentation only; the number is exactly the SQL result, not model arithmetic.
        answer = str(structured)
    else:
        answer = llm.synthesize(question, structured, narrative)
    return Answer(answer=answer, intent=route.intent, structured_evidence=structured, narrative_evidence=narrative)
