-- Shortfall Bridge AI knowledge store schema (PostgreSQL 16 + pgvector 0.7+)
-- Idempotent: safe to run on every startup.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS label_documents (
    set_id              TEXT PRIMARY KEY,
    spl_id              TEXT,
    version             TEXT,
    effective_time      DATE,
    title               TEXT NOT NULL,
    generic_names       TEXT[] NOT NULL DEFAULT '{}',
    brand_names         TEXT[] NOT NULL DEFAULT '{}',
    ingredients         TEXT[] NOT NULL DEFAULT '{}',
    moieties            TEXT[] NOT NULL DEFAULT '{}',
    manufacturer        TEXT,
    routes              TEXT[] NOT NULL DEFAULT '{}',
    product_type        TEXT,
    rxcuis              TEXT[] NOT NULL DEFAULT '{}',
    pharm_class_epc     TEXT[] NOT NULL DEFAULT '{}',
    pharm_class_moa     TEXT[] NOT NULL DEFAULT '{}',
    application_numbers TEXT[] NOT NULL DEFAULT '{}',
    source              TEXT NOT NULL,
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_label_moieties ON label_documents USING GIN (moieties);
CREATE INDEX IF NOT EXISTS ix_label_epc ON label_documents USING GIN (pharm_class_epc);

CREATE TABLE IF NOT EXISTS label_chunks (
    chunk_id        TEXT PRIMARY KEY,
    set_id          TEXT NOT NULL REFERENCES label_documents(set_id) ON DELETE CASCADE,
    section         TEXT NOT NULL,
    ordinal         INT NOT NULL,
    text            TEXT NOT NULL,
    text_hash       TEXT NOT NULL,
    moieties        TEXT[] NOT NULL DEFAULT '{}',
    title           TEXT NOT NULL DEFAULT '',
    effective_time  DATE,
    tsv             tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    embedding       vector(%(dim)s) NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chunk_set ON label_chunks (set_id);
CREATE INDEX IF NOT EXISTS ix_chunk_moieties ON label_chunks USING GIN (moieties);
CREATE INDEX IF NOT EXISTS ix_chunk_section ON label_chunks (section);
CREATE INDEX IF NOT EXISTS ix_chunk_tsv ON label_chunks USING GIN (tsv);
CREATE INDEX IF NOT EXISTS ix_chunk_hnsw ON label_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 128);

CREATE TABLE IF NOT EXISTS drug_shortages (
    record_id            TEXT PRIMARY KEY,
    generic_name         TEXT NOT NULL,
    ingredients          TEXT[] NOT NULL DEFAULT '{}',
    proprietary_name     TEXT,
    company_name         TEXT,
    presentation         TEXT,
    dosage_form          TEXT,
    strength             TEXT[] NOT NULL DEFAULT '{}',
    status               TEXT NOT NULL,
    availability         TEXT,
    shortage_reason      TEXT,
    related_info         TEXT,
    therapeutic_category TEXT[] NOT NULL DEFAULT '{}',
    package_ndc          TEXT,
    routes               TEXT[] NOT NULL DEFAULT '{}',
    rxcuis               TEXT[] NOT NULL DEFAULT '{}',
    pharm_class_epc      TEXT[] NOT NULL DEFAULT '{}',
    initial_posting_date DATE,
    update_date          DATE
);
CREATE INDEX IF NOT EXISTS ix_shortage_ingredients ON drug_shortages USING GIN (ingredients);
CREATE INDEX IF NOT EXISTS ix_shortage_status ON drug_shortages (status);
CREATE INDEX IF NOT EXISTS ix_shortage_name_trgm ON drug_shortages USING GIN (generic_name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS orange_book_products (
    id                  BIGSERIAL PRIMARY KEY,
    ingredient          TEXT NOT NULL,
    moieties            TEXT[] NOT NULL DEFAULT '{}',
    dosage_form         TEXT NOT NULL,
    route               TEXT NOT NULL,
    trade_name          TEXT NOT NULL,
    applicant           TEXT NOT NULL,
    applicant_full_name TEXT,
    strength            TEXT NOT NULL,
    appl_type           TEXT NOT NULL,
    appl_no             TEXT NOT NULL,
    product_no          TEXT NOT NULL,
    te_code             TEXT,
    approval_date       TEXT,
    rld                 BOOLEAN NOT NULL DEFAULT FALSE,
    rs                  BOOLEAN NOT NULL DEFAULT FALSE,
    marketing_type      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ob_moieties ON orange_book_products USING GIN (moieties);

CREATE TABLE IF NOT EXISTS ingredient_classes (
    moiety      TEXT NOT NULL,
    class_id    TEXT NOT NULL,
    class_name  TEXT NOT NULL,
    class_type  TEXT NOT NULL,
    source      TEXT NOT NULL,
    PRIMARY KEY (moiety, class_id)
);
CREATE INDEX IF NOT EXISTS ix_class_id ON ingredient_classes (class_id);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL,
    records     INT NOT NULL,
    details     JSONB NOT NULL DEFAULT '{}'::jsonb,
    finished_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS recommendation_audit (
    request_id  TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    request     JSONB NOT NULL,
    response    JSONB NOT NULL
);

-- v2: tamper evident audit chain (existing rows stay legacy and unchained)
ALTER TABLE recommendation_audit ADD COLUMN IF NOT EXISTS seq BIGSERIAL;
ALTER TABLE recommendation_audit ADD COLUMN IF NOT EXISTS prev_hash TEXT;
ALTER TABLE recommendation_audit ADD COLUMN IF NOT EXISTS record_hash TEXT;
ALTER TABLE recommendation_audit ADD COLUMN IF NOT EXISTS chain_ts TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS ix_audit_seq ON recommendation_audit (seq);

-- v2: pharmacist review of recommendations (agreement and overrides become labelled data)
CREATE TABLE IF NOT EXISTS pharmacist_reviews (
    review_id            TEXT PRIMARY KEY,
    request_id           TEXT NOT NULL,
    candidate_ingredient TEXT NOT NULL,
    candidate_tier       TEXT,
    decision             TEXT NOT NULL,
    reason               TEXT,
    chosen_alternative   TEXT,
    reviewer_role        TEXT NOT NULL DEFAULT 'pharmacist',
    rank                 INT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_reviews_request ON pharmacist_reviews (request_id);
CREATE INDEX IF NOT EXISTS ix_reviews_created ON pharmacist_reviews (created_at DESC);
