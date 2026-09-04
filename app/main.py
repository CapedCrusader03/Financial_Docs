from fastapi import FastAPI, HTTPException

from app.ingestion import ingest_sec
from app.pdf_fallback import ingest_pdf
from app.query import answer_question
from app.schemas import Answer, AskRequest, PDFIngestRequest, SECIngestRequest

app = FastAPI(title="Financial Filings Q&A", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ingest/sec")
def ingest_from_sec(request: SECIngestRequest) -> dict:
    try:
        return ingest_sec(request.ticker, request.filing_type, request.accession_number)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/ingest/pdf")
def ingest_from_pdf(request: PDFIngestRequest) -> dict:
    try:
        return ingest_pdf(**request.model_dump())
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/ask", response_model=Answer)
def ask(request: AskRequest) -> Answer:
    try:
        return answer_question(request.company, request.question)
    except (ValueError, RuntimeError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
