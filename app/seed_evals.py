"""Seed a small filing-grounded regression set after the first SEC ingestion."""
import argparse

from app.db import connection


def seed(ticker: str) -> int:
    ticker = ticker.upper()
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT f.fiscal_period, fa.value, fa.unit, f.source_url
               FROM facts fa JOIN filings f ON f.id=fa.filing_id
               WHERE f.ticker=%s AND fa.source='xbrl' AND fa.concept='Revenues'
                 AND coalesce(fa.context->>'frame','') ~ '^CY[0-9]{4}Q[1-4]$'
               ORDER BY f.filed_date DESC LIMIT 1""", (ticker,)
        )
        revenue = cur.fetchone()
        cur.execute(
            """SELECT f.fiscal_period, nc.section, f.source_url
               FROM narrative_chunks nc JOIN filings f ON f.id=nc.filing_id
               WHERE f.ticker=%s AND nc.section ILIKE 'Item 1A%%'
               ORDER BY f.filed_date DESC LIMIT 1""", (ticker,)
        )
        risk = cur.fetchone()
        if not revenue or not risk:
            raise LookupError("Ingest a filing containing quarterly Revenues and Item 1A before seeding evals.")
        cases = [
            (f"What was {ticker}'s revenue in its latest reported quarter?", str(revenue[1]), "numeric"),
            (f"What risks does {ticker} disclose in its latest filing?", risk[1], "narrative"),
        ]
        inserted = 0
        for question, expected, category in cases:
            cur.execute("SELECT 1 FROM eval_cases WHERE ticker=%s AND question=%s", (ticker, question))
            if cur.fetchone():
                continue
            cur.execute("INSERT INTO eval_cases(question,expected_answer,category,ticker) VALUES (%s,%s,%s,%s)",
                        (question, expected, category, ticker))
            inserted += 1
        return inserted


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    print(f"seeded {seed(parser.parse_args().ticker)} eval case(s)")
