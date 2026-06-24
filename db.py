
import sqlite3
from contextlib import contextmanager

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id              TEXT PRIMARY KEY,
    total_amount         REAL    NOT NULL DEFAULT 0,   -- true lifetime total, never capped
    transaction_count    INTEGER NOT NULL DEFAULT 0,
    score_amount_total   REAL    NOT NULL DEFAULT 0,   -- sum of per-tx CAPPED amounts, used only for ranking
    active_days_count    INTEGER NOT NULL DEFAULT 0,   -- distinct UTC calendar days with >=1 transaction
    first_transaction_at TEXT,
    last_transaction_at  TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT    NOT NULL,
    amount          REAL    NOT NULL,
    description     TEXT,
    idempotency_key TEXT    NOT NULL UNIQUE,
    created_at      TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_transactions_user_created
    ON transactions (user_id, created_at);

-- One row per (user, calendar day) that had at least one transaction.
-- Used to cheaply maintain users.active_days_count incrementally.
CREATE TABLE IF NOT EXISTS user_active_days (
    user_id     TEXT NOT NULL,
    active_date TEXT NOT NULL,
    PRIMARY KEY (user_id, active_date)
);

-- Idempotency cache: the source of truth for "have we already processed
-- this exact request". Storing the full response lets a retried request
-- get back byte-for-byte the same answer without recomputation.
CREATE TABLE IF NOT EXISTS idempotency_records (
    idempotency_key TEXT PRIMARY KEY,
    request_hash    TEXT    NOT NULL,
    response_json   TEXT    NOT NULL,
    status_code     INTEGER NOT NULL,
    created_at      TEXT    NOT NULL
);
"""


def get_raw_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_connection():
    conn = get_raw_connection()
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with get_connection() as conn:
        conn.executescript(SCHEMA)
