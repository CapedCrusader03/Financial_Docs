from typing import Literal

from pydantic import BaseModel, Field


class SECIngestRequest(BaseModel):
    ticker: str = Field(examples=["NVDA"])
    filing_type: Literal["10-K", "10-Q"] = "10-Q"
    accession_number: str | None = None


class PDFIngestRequest(BaseModel):
    path: str
    company: str
    ticker: str
    filing_type: Literal["10-K", "10-Q"]
    fiscal_period: str
    filed_date: str
    source_url: str


class AskRequest(BaseModel):
    company: str = Field(description="Ticker or company name, e.g. NVDA")
    question: str


class Route(BaseModel):
    intent: Literal["aggregate", "narrative", "hybrid"]
    concept: str | None = None
    periods: list[str] = Field(default_factory=list)
    aggregation: Literal["value", "sum", "average", "minimum", "maximum", "change", "percent_change"] | None = None
    section_hint: str | None = None


class Answer(BaseModel):
    answer: str
    intent: str
    structured_evidence: list[dict] = Field(default_factory=list)
    narrative_evidence: list[dict] = Field(default_factory=list)
