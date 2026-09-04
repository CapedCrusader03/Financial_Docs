# Filing Q&A

An evidence-first Q&A service for SEC filings. It deliberately uses two stores in one PostgreSQL + pgvector database:

- XBRL and PDF table cells become rows in `facts` and every numeric operation is executed by a parameterized SQL template.
- Item-based narrative sections become embeddings in `narrative_chunks` and are retrieved only after company, filing, period, and optional section filters are applied.

It does **not** ask a model to calculate from retrieved prose. For narrative retrieval, the semantic shortlist is always passed through an LLM-as-a-judge reranker before synthesis.

## Quick start

```powershell
Copy-Item .env.example .env
docker compose up -d db
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Set an SEC-compliant `SEC_USER_AGENT` and `GEMINI_API_KEY` in `.env`. Then ingest Nvidia's most recent 10-Q:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/ingest/sec -ContentType application/json -Body '{"ticker":"NVDA","filing_type":"10-Q"}'
Invoke-RestMethod -Method Post http://127.0.0.1:8000/ask -ContentType application/json -Body '{"company":"NVDA","question":"What was revenue in the latest quarter?"}'
```

## Browser testing UI

With Uvicorn running, open [http://127.0.0.1:8000/](http://127.0.0.1:8000/). The dashboard can ingest a latest SEC filing, upload a PDF-only filing along with its required metadata, submit a question, and show the resulting structured SQL evidence and judge-ranked narrative excerpts. Uploaded source PDFs are kept locally under `data/uploads/` and are git-ignored.

Run seeded, independently verifiable evals after ingestion:

```powershell
python -m app.seed_evals --ticker NVDA
python -m app.evals --ticker NVDA
```

The API uses Gemini for routing, embeddings, concept resolution, synthesis, and the required Gemini judge reranker. Numeric facts are nevertheless calculated only in PostgreSQL.

Use a Gemini API key from Google AI Studio (not an OpenAI key):

```env
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.5-flash
EMBEDDING_MODEL=gemini-embedding-2
```

The vector column remains 1,536 dimensions. If you previously ingested data with OpenAI embeddings, clear and re-ingest `narrative_chunks` after this migration; embeddings from different providers must not share a similarity index.

## Execution traces

Every `/ask` response includes a `trace_id`. The app stores the route classification, period and concept decisions, executed parameterized SQL templates and safe parameters, vector-search filter, Gemini judge order, and status in `query_traces` / `query_trace_events`. Retrieve a trace at `GET /traces/{trace_id}`, or expand **Execution trace** beneath an answer in the browser UI. API keys and embedding values are never recorded.

## Boundaries and source policy

`/ingest/sec` uses EDGAR's submissions endpoint to select the filing, the filing's primary HTML document for narrative sections, and the company-facts endpoint for XBRL facts. PDF ingestion is available only through `/ingest/pdf`; it uses Docling and never a plain-text/PyPDF table extractor. The Docker database is the single system of record; there is no separate generic vector database to drift from the facts store.

For production, place SEC response caching, migrations, auth, and a job queue in front of the ingestion endpoint. Keep the supplied eval suite in CI and watch numeric and narrative pass rates independently.
