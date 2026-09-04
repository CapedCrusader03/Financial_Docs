import logging
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg import OperationalError

from app.ingestion import ingest_sec
from app.pdf_fallback import ingest_pdf
from app.query import answer_question
from app.schemas import Answer, AskRequest, PDFIngestRequest, SECIngestRequest

app = FastAPI(title="Financial Filings Q&A", version="0.1.0")
logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"
UPLOAD_DIR = Path("data/uploads")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _raise_service_error(exc: Exception) -> None:
    logger.exception("Filing QA request failed")
    if isinstance(exc, OperationalError):
        raise HTTPException(
            status_code=503,
            detail="Database connection failed. Check DATABASE_URL and ensure the Postgres container is running.",
        ) from exc
    if isinstance(exc, (ValueError, RuntimeError, LookupError)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=502, detail="The upstream filing or AI service failed. See the server log for details.") from exc


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ingest/sec")
def ingest_from_sec(request: SECIngestRequest) -> dict:
    try:
        return ingest_sec(request.ticker, request.filing_type, request.accession_number)
    except Exception as exc:
        _raise_service_error(exc)


@app.post("/ingest/pdf")
def ingest_from_pdf(request: PDFIngestRequest) -> dict:
    try:
        return ingest_pdf(**request.model_dump())
    except Exception as exc:
        _raise_service_error(exc)


@app.post("/ingest/pdf/upload")
def upload_and_ingest_pdf(
    file: UploadFile = File(...),
    company: str = Form(...),
    ticker: str = Form(...),
    filing_type: str = Form(...),
    fiscal_period: str = Form(...),
    filed_date: str = Form(...),
) -> dict:
    if filing_type not in {"10-K", "10-Q"}:
        raise HTTPException(status_code=422, detail="filing_type must be 10-K or 10-Q")
    if not file.filename or Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=422, detail="Upload a PDF file.")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid4()}-{Path(file.filename).name}"
    stored_path = UPLOAD_DIR / stored_name
    try:
        with stored_path.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        return ingest_pdf(
            path=str(stored_path.resolve()), company=company, ticker=ticker,
            filing_type=filing_type, fiscal_period=fiscal_period, filed_date=filed_date,
            source_url=f"uploaded://{stored_name}",
        )
    except Exception as exc:
        _raise_service_error(exc)
    finally:
        file.file.close()


@app.post("/ask", response_model=Answer)
def ask(request: AskRequest) -> Answer:
    try:
        return answer_question(request.company, request.question)
    except Exception as exc:
        _raise_service_error(exc)
