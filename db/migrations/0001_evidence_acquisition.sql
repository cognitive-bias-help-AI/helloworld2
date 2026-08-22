CREATE TABLE acquisition_queries (
    query_id varchar(26) PRIMARY KEY,
    run_id text NOT NULL,
    scope text NOT NULL CHECK (scope IN ('claim', 'stock')),
    claim_id varchar(26),
    intent text NOT NULL CHECK (intent IN ('verify', 'counter', 'context')),
    provider text NOT NULL CHECK (provider IN ('dart', 'naver', 'kiwoom')),
    endpoint text NOT NULL CHECK (length(endpoint) > 0),
    params jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    CHECK (
        (scope = 'claim' AND claim_id IS NOT NULL)
        OR (scope = 'stock' AND claim_id IS NULL)
    ),
    UNIQUE (query_id, run_id, provider, endpoint)
);

CREATE INDEX acquisition_queries_claim_id_idx
    ON acquisition_queries (claim_id);

CREATE TABLE provider_calls (
    provider_request_id varchar(26) PRIMARY KEY,
    run_id text NOT NULL,
    provider text NOT NULL CHECK (provider IN ('dart', 'naver', 'kiwoom')),
    endpoint text NOT NULL CHECK (length(endpoint) > 0),
    query_id varchar(26) NOT NULL,
    http_status smallint CHECK (http_status BETWEEN 100 AND 599),
    latency_ms bigint NOT NULL CHECK (latency_ms >= 0),
    cache_hit boolean NOT NULL,
    reason_code text CHECK (reason_code IN (
        'input_insufficient', 'out_of_scope', 'stock_unresolved', 'pii_detected',
        'illegal_request', 'self_harm_signal', 'prompt_injection', 'rate_limit',
        'upstream_5xx', 'upstream_timeout', 'auth_failed', 'ip_mismatch',
        'no_result', 'coverage_truncated', 'stale_data', 'schema_invalid',
        'evidence_insufficient', 'forbidden_expression', 'span_mismatch',
        'budget_exceeded', 'timeout_machine', 'timeout_hitl', 'stale_snapshot',
        'conflict_unresolved', 'idempotent_replay', 'context_overflow',
        'contract_violation'
    )),
    idempotency_key char(64) NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (provider_request_id, run_id),
    FOREIGN KEY (query_id, run_id, provider, endpoint)
        REFERENCES acquisition_queries (query_id, run_id, provider, endpoint)
);

CREATE INDEX provider_calls_query_id_idx ON provider_calls (query_id);
CREATE INDEX provider_calls_idempotency_key_idx ON provider_calls (idempotency_key);

CREATE TABLE evidence (
    evidence_id varchar(26) PRIMARY KEY,
    run_id text NOT NULL,
    source_type text NOT NULL CHECK (source_type IN ('dart', 'news', 'quote')),
    source_ref text NOT NULL CHECK (length(source_ref) > 0),
    source_url text,
    publisher text,
    published_at timestamptz,
    fetched_at timestamptz NOT NULL,
    raw_span text NOT NULL CHECK (length(raw_span) BETWEEN 1 AND 500),
    span_scope text NOT NULL CHECK (
        span_scope IN ('headline_snippet', 'full_text', 'structured_field')
    ),
    content_sha256 char(64) NOT NULL,
    normalized_value jsonb,
    provider_request_id varchar(26) NOT NULL,
    as_of timestamptz NOT NULL,
    UNIQUE (run_id, content_sha256),
    FOREIGN KEY (provider_request_id, run_id)
        REFERENCES provider_calls (provider_request_id, run_id)
);

CREATE INDEX evidence_provider_request_id_idx ON evidence (provider_request_id);

CREATE TABLE evidence_query_links (
    evidence_id varchar(26) NOT NULL REFERENCES evidence (evidence_id),
    query_id varchar(26) NOT NULL REFERENCES acquisition_queries (query_id),
    PRIMARY KEY (evidence_id, query_id)
);
