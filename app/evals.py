"""Separate numeric and narrative regression reporting over stored eval_cases."""
import argparse
from datetime import datetime, timezone

from app.db import connection
from app.query import answer_question


def run(ticker: str) -> dict[str, dict[str, int]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id::text, question, expected_answer, category FROM eval_cases WHERE ticker=%s", (ticker.upper(),))
        cases = cur.fetchall()
    score = {"numeric": {"passed": 0, "failed": 0}, "narrative": {"passed": 0, "failed": 0}}
    for case_id, question, expected, category in cases:
        result = answer_question(ticker, question)
        if category == "numeric":
            passed = expected in result.answer  # expected answer is a filing-verified exact value.
        else:
            # Narrative answers must have judge-ranked evidence; a production deployment can replace this
            # with a second judge prompt while retaining the stored retrieval evidence.
            passed = bool(result.narrative_evidence) and expected.lower() in " ".join(item["section"].lower() for item in result.narrative_evidence)
        score[category]["passed" if passed else "failed"] += 1
        with connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE eval_cases SET last_run_result=%s,last_run_at=%s WHERE id=%s::uuid",
                        ("pass" if passed else f"fail: {result.answer[:500]}", datetime.now(timezone.utc), case_id))
    return score


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    print(run(parser.parse_args().ticker))
