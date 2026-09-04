from contextlib import contextmanager
from typing import Iterator

import psycopg
from pgvector.psycopg import register_vector

from app.config import settings


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings().database_url) as conn:
        register_vector(conn)
        yield conn


def vector_literal(values: list[float]) -> str:
    """pgvector's text representation; values themselves remain bound parameters."""
    return "[" + ",".join(str(float(v)) for v in values) + "]"
