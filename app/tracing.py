"""Persistent, privacy-conscious execution traces for filing Q&A requests."""
import json
import logging
from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Jsonb

from app.db import connection

logger = logging.getLogger(__name__)

TRACE_DDL = """
CREATE TABLE IF NOT EXISTS query_traces (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company TEXT NOT NULL,
  question TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('running', 'complete', 'failed')),
  intent TEXT,
  error TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS query_trace_events (
  id BIGSERIAL PRIMARY KEY,
  trace_id UUID NOT NULL REFERENCES query_traces(id) ON DELETE CASCADE,
  sequence INTEGER NOT NULL,
  stage TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (trace_id, sequence)
);
CREATE INDEX IF NOT EXISTS query_trace_events_trace_idx ON query_trace_events(trace_id, sequence);
"""


def _json_safe(value: Any) -> Any:
    """Keep trace writes robust without ever recording API keys or raw embeddings."""
    return json.loads(json.dumps(value, default=str))


@dataclass
class QueryTrace:
    id: str
    sequence: int = 0

    @classmethod
    def start(cls, company: str, question: str, route: dict[str, Any]) -> "QueryTrace":
        with connection() as conn, conn.cursor() as cur:
            # Also makes traces available for installations created before this feature.
            cur.execute(TRACE_DDL)
            cur.execute(
                "INSERT INTO query_traces(company,question,status,intent) VALUES (%s,%s,'running',%s) RETURNING id::text",
                (company, question, route.get("intent")),
            )
            trace = cls(id=cur.fetchone()[0])
        trace.event("route_classification", route)
        return trace

    def event(self, stage: str, payload: dict[str, Any]) -> None:
        self.sequence += 1
        safe_payload = _json_safe(payload)
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO query_trace_events(trace_id,sequence,stage,payload) VALUES (%s::uuid,%s,%s,%s)",
                (self.id, self.sequence, stage, Jsonb(safe_payload)),
            )
        logger.info("query_trace=%s stage=%s payload=%s", self.id, stage, safe_payload)

    def complete(self) -> None:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE query_traces SET status='complete',completed_at=CURRENT_TIMESTAMP WHERE id=%s::uuid", (self.id,))

    def fail(self, exc: Exception) -> None:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE query_traces SET status='failed',error=%s,completed_at=CURRENT_TIMESTAMP WHERE id=%s::uuid", (str(exc)[:1000], self.id))


def get_trace(trace_id: str) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id::text,company,question,status,intent,error,created_at,completed_at FROM query_traces WHERE id=%s::uuid", (trace_id,))
        trace = cur.fetchone()
        if not trace:
            return None
        cur.execute("SELECT sequence,stage,payload,created_at FROM query_trace_events WHERE trace_id=%s::uuid ORDER BY sequence", (trace_id,))
        events = cur.fetchall()
    return {
        "id": trace[0], "company": trace[1], "question": trace[2], "status": trace[3], "intent": trace[4], "error": trace[5],
        "created_at": str(trace[6]), "completed_at": str(trace[7]) if trace[7] else None,
        "events": [{"sequence": row[0], "stage": row[1], "payload": row[2], "created_at": str(row[3])} for row in events],
    }
