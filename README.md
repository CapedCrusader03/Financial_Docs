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

Set an SEC-compliant `SEC_USER_AGENT` and `OPENAI_API_KEY` in `.env`. Then ingest Nvidia's most recent 10-Q:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/ingest/sec -ContentType application/json -Body '{"ticker":"NVDA","filing_type":"10-Q"}'
Invoke-RestMethod -Method Post http://127.0.0.1:8000/ask -ContentType application/json -Body '{"company":"NVDA","question":"What was revenue in the latest quarter?"}'
```

Run seeded, independently verifiable evals after ingestion:

```powershell
python -m app.seed_evals --ticker NVDA
python -m app.evals --ticker NVDA
```

The API requires an LLM configuration for routing, concept resolution, synthesis, and the required judge reranker. Numeric facts are nevertheless calculated only in PostgreSQL.

## Boundaries and source policy

`/ingest/sec` uses EDGAR's submissions endpoint to select the filing, the filing's primary HTML document for narrative sections, and the company-facts endpoint for XBRL facts. PDF ingestion is available only through `/ingest/pdf`; it uses Docling and never a plain-text/PyPDF table extractor. The Docker database is the single system of record; there is no separate generic vector database to drift from the facts store.

For production, place SEC response caching, migrations, auth, and a job queue in front of the ingestion endpoint. Keep the supplied eval suite in CI and watch numeric and narrative pass rates independently.
