CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS filings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company TEXT NOT NULL,
  ticker TEXT NOT NULL,
  filing_type TEXT NOT NULL CHECK (filing_type IN ('10-K', '10-Q')),
  fiscal_period TEXT NOT NULL,
  filed_date DATE NOT NULL,
  source_url TEXT NOT NULL,
  UNIQUE (ticker, filing_type, fiscal_period, filed_date)
);

CREATE TABLE IF NOT EXISTS facts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  filing_id UUID NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
  concept TEXT NOT NULL,
  period TEXT NOT NULL,
  value NUMERIC NOT NULL,
  unit TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('xbrl', 'pdf_table')),
  confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  context JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS facts_lookup_idx ON facts(filing_id, concept, period, unit, source);

CREATE TABLE IF NOT EXISTS concept_mappings (
  natural_language_term TEXT PRIMARY KEY,
  xbrl_concept TEXT NOT NULL,
  cached_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS narrative_chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  filing_id UUID NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
  section TEXT NOT NULL,
  text TEXT NOT NULL,
  embedding VECTOR(384) NOT NULL,
  content_kind TEXT NOT NULL DEFAULT 'narrative' CHECK (content_kind IN ('narrative', 'table_markdown'))
);
CREATE INDEX IF NOT EXISTS narrative_chunks_filing_section_idx ON narrative_chunks(filing_id, section);
CREATE INDEX IF NOT EXISTS narrative_chunks_embedding_idx ON narrative_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS validation_flags (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  filing_id UUID NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
  concept TEXT NOT NULL,
  period TEXT NOT NULL,
  flag_type TEXT NOT NULL,
  details TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS eval_cases (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  question TEXT NOT NULL,
  expected_answer TEXT NOT NULL,
  category TEXT NOT NULL CHECK (category IN ('numeric', 'narrative')),
  ticker TEXT NOT NULL,
  last_run_result TEXT,
  last_run_at TIMESTAMP
);
