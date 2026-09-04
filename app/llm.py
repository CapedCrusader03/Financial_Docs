"""Gemini calls for routing, concept matching, evidence judging, and embeddings.

Gemini is never asked to compute an aggregate: numerical operations remain closed SQL templates.
"""
import json
from typing import Any, Literal

from google import genai
from google.genai import types
from sentence_transformers import SentenceTransformer

from app.config import settings
from app.schemas import Route


class GeminiService:
    def __init__(self) -> None:
        if not settings().gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is required for routing, embeddings, and the mandatory Gemini judge reranker.")
        self.client = genai.Client(api_key=settings().gemini_api_key)
        self._embedder: SentenceTransformer | None = None

    @property
    def embedder(self) -> SentenceTransformer:
        if self._embedder is None:
            self._embedder = SentenceTransformer(settings().embedding_model)
        return self._embedder

    def _json(self, schema: dict[str, Any], instructions: str, input_text: str) -> dict[str, Any]:
        response = self.client.models.generate_content(
            model=settings().gemini_model,
            contents=input_text,
            config=types.GenerateContentConfig(
                system_instruction=instructions,
                response_mime_type="application/json",
                response_json_schema=schema,
            ),
        )
        if not response.text:
            raise RuntimeError("Gemini returned no text for a structured request.")
        return json.loads(response.text)

    def embed(self, texts: list[str], purpose: Literal["document", "query"] = "document") -> list[list[float]]:
        """Create local 384-dimensional vectors without API calls or rate limits."""
        if not texts:
            return []
        embeddings = self.embedder.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
        return embeddings.tolist()

    def classify(self, question: str) -> Route:
        schema = {
            "type": "object", "additionalProperties": False,
            "properties": {
                "intent": {"type": "string", "enum": ["aggregate", "narrative", "hybrid"]},
                "concept": {"type": ["string", "null"]},
                "periods": {"type": "array", "items": {"type": "string"}},
                "aggregation": {"type": ["string", "null"]},
                "section_hint": {"type": ["string", "null"]},
            }, "required": ["intent", "concept", "periods", "aggregation", "section_hint"],
        }
        data = self._json(
            schema,
            "Classify the financial-filing question before any database access. Use aggregate for a numeric value or arithmetic; narrative for qualitative filing content; hybrid for a numeric fact plus explanation. Do not calculate. Return plain concept words and period references.",
            question,
        )
        return Route.model_validate(data)

    def map_concept(self, term: str, taxonomy: list[str]) -> str:
        if not taxonomy:
            raise LookupError("No XBRL taxonomy is available for this company yet.")
        schema = {"type": "object", "additionalProperties": False, "properties": {"concept": {"type": "string", "enum": taxonomy}}, "required": ["concept"]}
        return self._json(schema, "Map the business term to exactly one supplied XBRL concept. Do not invent a tag.", f"Term: {term}\nAllowed XBRL concepts: {json.dumps(taxonomy)}")["concept"]

    def judge_rerank(self, question: str, candidates: list[dict[str, str]]) -> list[int]:
        """Required Gemini LLM-as-a-judge stage after the filtered vector shortlist."""
        if not candidates:
            return []
        schema = {"type": "object", "additionalProperties": False, "properties": {"ordered_ids": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": len(candidates) - 1}}}, "required": ["ordered_ids"]}
        payload = [{"id": i, "section": item["section"], "text": item["text"][:3500]} for i, item in enumerate(candidates)]
        result = self._json(
            schema,
            "You are an evidence judge. Rank only the supplied filing excerpts by direct support for the question. Prefer the precisely relevant Item section, faithful content, and the resolved period. Return every id exactly once; do not answer the question.",
            f"Question: {question}\nCandidates: {json.dumps(payload)}",
        )["ordered_ids"]
        return result if sorted(result) == list(range(len(candidates))) else list(range(len(candidates)))

    def synthesize(self, question: str, structured: list[dict[str, Any]], excerpts: list[dict[str, str]]) -> str:
        response = self.client.models.generate_content(
            model=settings().gemini_model,
            contents=json.dumps({"question": question, "structured_sql_evidence": structured, "narrative_evidence": excerpts}),
            config=types.GenerateContentConfig(system_instruction="Answer only from supplied filing evidence. Structured values came from deterministic SQL: reproduce them verbatim and never calculate, total, average, or alter a value. Cite section names in prose when using narrative evidence. If evidence is insufficient, say so."),
        )
        if not response.text:
            raise RuntimeError("Gemini returned no answer text.")
        return response.text
