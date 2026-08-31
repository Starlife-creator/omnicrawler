"""SQLite 数据库模式定义。"""

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    project_name TEXT NOT NULL,
    config_path TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    summary_json TEXT
);

CREATE TABLE IF NOT EXISTS run_state_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    reason TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stage_checkpoints (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    stage TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id, stage, idempotency_key)
);

CREATE TABLE IF NOT EXISTS export_commits (
    idempotency_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    exporter TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS frontier (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    method TEXT NOT NULL,
    headers_json TEXT NOT NULL,
    body BLOB,
    kind TEXT NOT NULL,
    render INTEGER NOT NULL DEFAULT 0,
    priority REAL NOT NULL DEFAULT 0,
    depth INTEGER NOT NULL DEFAULT 0,
    parent_url TEXT,
    meta_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    request_fingerprint TEXT NOT NULL,
    url TEXT NOT NULL,
    final_url TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    content_type TEXT,
    size_bytes INTEGER NOT NULL,
    content_sha256 TEXT NOT NULL,
    raw_path TEXT,
    etag TEXT,
    last_modified TEXT,
    changed INTEGER NOT NULL,
    elapsed_seconds REAL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS content_versions (
    url TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    seen_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(url, content_sha256)
);

CREATE TABLE IF NOT EXISTS records (
    record_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    request_fingerprint TEXT NOT NULL,
    source_url TEXT NOT NULL,
    record_type TEXT NOT NULL,
    data_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS record_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    source_url TEXT NOT NULL,
    record_type TEXT NOT NULL,
    identity TEXT NOT NULL,
    semantic_sha256 TEXT NOT NULL,
    data_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    seen_count INTEGER NOT NULL DEFAULT 1,
    UNIQUE(source_url, record_type, identity, semantic_sha256)
);

CREATE TABLE IF NOT EXISTS semantic_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    source_url TEXT NOT NULL,
    record_type TEXT NOT NULL,
    identity TEXT NOT NULL,
    change_type TEXT NOT NULL,
    similarity REAL NOT NULL,
    added_json TEXT NOT NULL,
    removed_json TEXT NOT NULL,
    modified_json TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS record_edits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- S2.5.42：ON DELETE CASCADE——records REPLACE 删除旧行时编辑历史级联清理
    record_id TEXT NOT NULL REFERENCES records(record_id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    old_value_json TEXT,
    new_value_json TEXT,
    actor TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quality_stats (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    field_name TEXT NOT NULL,
    total INTEGER NOT NULL DEFAULT 0,
    present INTEGER NOT NULL DEFAULT 0,
    valid INTEGER NOT NULL DEFAULT 0,
    anomalies INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id, field_name)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    request_fingerprint TEXT NOT NULL,
    source_url TEXT NOT NULL,
    local_path TEXT NOT NULL,
    content_type TEXT,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source_url, sha256)
);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    request_fingerprint TEXT,
    url TEXT,
    stage TEXT NOT NULL,
    error_type TEXT NOT NULL,
    message TEXT NOT NULL,
    retryable INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plugin_state (
    project_scope TEXT NOT NULL,
    plugin_id TEXT NOT NULL,
    author_fingerprint TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    state_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_scope, plugin_id, author_fingerprint, schema_version, state_key)
);

CREATE INDEX IF NOT EXISTS idx_frontier_status ON frontier(status, priority, depth);
CREATE INDEX IF NOT EXISTS idx_responses_run ON responses(run_id);
CREATE INDEX IF NOT EXISTS idx_records_run ON records(run_id);
CREATE INDEX IF NOT EXISTS idx_record_versions_lookup
    ON record_versions(source_url, record_type, identity, id);
CREATE INDEX IF NOT EXISTS idx_semantic_changes_run ON semantic_changes(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_record_edits_record ON record_edits(record_id, created_at);
CREATE INDEX IF NOT EXISTS idx_quality_stats_run ON quality_stats(run_id);
CREATE INDEX IF NOT EXISTS idx_audit_events_run ON audit_events(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_errors_run ON errors(run_id);
CREATE INDEX IF NOT EXISTS idx_run_state_events_run ON run_state_events(run_id, id);
CREATE INDEX IF NOT EXISTS idx_stage_checkpoints_run ON stage_checkpoints(run_id, stage);
CREATE INDEX IF NOT EXISTS idx_plugin_state_namespace
    ON plugin_state(project_scope, plugin_id, author_fingerprint, schema_version);
"""
