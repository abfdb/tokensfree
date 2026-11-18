CREATE TABLE accounts (
    id             INTEGER PRIMARY KEY,
    provider       TEXT NOT NULL,
    alias          TEXT NOT NULL,
    credential_ref TEXT NOT NULL,
    fingerprint    TEXT NOT NULL,
    tier           TEXT NOT NULL DEFAULT 'real_declared',
    status         TEXT NOT NULL DEFAULT 'pending',
    created_at     REAL NOT NULL,
    last_used_at   REAL,
    cooldown_until REAL,
    UNIQUE(provider, alias)
);

CREATE TABLE provider_health (
    provider      TEXT PRIMARY KEY,
    fail_streak   INTEGER NOT NULL DEFAULT 0,
    window_start  REAL,
    window_fails  INTEGER NOT NULL DEFAULT 0,
    circuit_state TEXT NOT NULL DEFAULT 'closed',
    open_until    REAL
);

CREATE TABLE quota_ledger (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    provider    TEXT NOT NULL,
    request_id  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    delta_units REAL NOT NULL,
    unit        TEXT NOT NULL,
    accuracy    TEXT NOT NULL,
    detail      TEXT,
    created_at  REAL NOT NULL
);

CREATE INDEX idx_ledger_account_time ON quota_ledger(account_id, created_at);

CREATE TABLE idempotency_keys (
    key           TEXT PRIMARY KEY,
    request_hash  TEXT NOT NULL,
    status        TEXT NOT NULL,
    response_json TEXT,
    created_at    REAL NOT NULL,
    expires_at    REAL NOT NULL
);

CREATE INDEX idx_idem_expiry ON idempotency_keys(expires_at);

CREATE TABLE events (
    id         INTEGER PRIMARY KEY,
    request_id TEXT NOT NULL,
    at         REAL NOT NULL,
    level      TEXT NOT NULL,
    provider   TEXT,
    account_id INTEGER,
    outcome    TEXT,
    latency_ms INTEGER,
    bytes_in   INTEGER,
    bytes_out  INTEGER,
    detail     TEXT
);

CREATE INDEX idx_events_at ON events(at);
