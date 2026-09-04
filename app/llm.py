"""Structured LLM calls. Models classify and judge evidence; SQL performs all arithmetic."""
import json
from typing import Any

from openai import OpenAI

from app.config import settings
from app.schemas import Route


class LLMService:
    def __init__(self) -> None:
        if not settings().openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required: routing and the mandatory LLM judge reranker cannot be skipped.")
        self.client = OpenAI(api_key=settings().openai_api_key)

    def _json(self, name: str, schema: dict[str, Any], instructions: str, input_text: str) -> dict[str, Any]:
        response = self.client.responses.create(
            model=settings().llm_model,
            instructions=instructions,
            input=input_text,
            text={"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
        )
        return json.loads(response.output_text)

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(model=settings().embedding_model, input=texts)
        return [row.embedding for row in response.data]

    def classify(self, question: str) -> Route:
        schema = {
            "type": "object", "additionalProperties": False,
            "properties": {
                "intent": {"type": "string", "enum": ["aggregate", "narrative", "hybrid"]},
                "concept": {"type": ["string", "null"]},
                "periods": {"type": "array", "items": {"type": "string"}},
                "aggregation": {"type": ["string", "null"], "enum": ["value", "sum", "average", "minimum", "maximum", "change", "percent_change", None]},
                "section_hint": {"type": ["string", "null"]},
            }, "required": ["intent", "concept", "periods", "aggregation", "section_hint"],
        }
        data = self._json(
            "filing_question_route", schema,
            "Classify the financial-filing question before any database access. Use aggregate for a numeric value or arithmetic; narrative for qualitative filing content; hybrid for a numeric fact plus explanation. Do not calculate. Return plain concept words and period references.",
            question,
        )
        return Route.model_validate(data)

    def map_concept(self, term: str, taxonomy: list[str]) -> str:
        if not taxonomy:
            raise LookupError("No XBRL taxonomy is available for this company yet.")
        schema = {
            "type": "object", "additionalProperties": False,
            "properties": {"concept": {"type": "string", "enum": taxonomy}}, "required": ["concept"],
        }
        return self._json(
            "xbrl_concept_mapping", schema,
            "Map the business term to exactly one supplied XBRL concept. Do not invent a tag.",
            f"Term: {term}\nAllowed XBRL concepts: {json.dumps(taxonomy)}",
        )["concept"]

    def judge_rerank(self, question: str, candidates: list[dict[str, str]]) -> list[int]:
        """Required second-stage reranker; semantic distance may only produce a shortlist."""
        if not candidates:
            return []
        schema = {
            "type": "object", "additionalProperties": False,
            "properties": {"ordered_ids": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": len(candidates) - 1}}},
            "required": ["ordered_ids"],
        }
        payload = [{"id": i, "section": item["section"], "text": item["text"][:3500]} for i, item in enumerate(candidates)]
        result = self._json(
            "filing_evidence_judge", schema,
            "You are an evidence judge. Rank only the supplied filing excerpts by direct support for the question. Prefer the precisely relevant Item section, faithful content, and the resolved period. Return every id exactly once; do not answer the question.",
            f"Question: {question}\nCandidates: {json.dumps(payload)}",
        )["ordered_ids"]
        return result if sorted(result) == list(range(len(candidates))) else list(range(len(candidates)))

    def synthesize(self, question: str, structured: list[dict[str, Any]], excerpts: list[dict[str, str]]) -> str:
        return self.client.responses.create(
            model=settings().llm_model,
            instructions="Answer only from supplied filing evidence. Structured values came from deterministic SQL: reproduce them verbatim and never calculate, total, average, or alter a value. Cite section names in prose when using narrative evidence. If evidence is insufficient, say so.",
            input=json.dumps({"question": question, "structured_sql_evidence": structured, "narrative_evidence": excerpts}),
        ).output_text
