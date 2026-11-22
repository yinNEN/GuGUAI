from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import settings

logger = logging.getLogger("deepseek.db")

SCHEMA = """
PRAGMA auto_vacuum=INCREMENTAL;
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    username             TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash        TEXT NOT NULL,
    role                 TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin','user')),
    is_active            INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL,
    last_login_at        TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    ip         TEXT,
    user_agent TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS conversations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title         TEXT NOT NULL DEFAULT '新会话',
    model         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    fingerprint     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_messages_fp ON messages(conversation_id, role, fingerprint);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_snapshots (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_id  TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    data       TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, client_id)
);
CREATE INDEX IF NOT EXISTS idx_session_snapshots_user ON session_snapshots(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS stored_files (
    id          TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    bucket      TEXT NOT NULL CHECK (bucket IN ('buffer','text')),
    name        TEXT NOT NULL,
    size        INTEGER NOT NULL,
    mime        TEXT NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL,
    last_used   INTEGER NOT NULL,
    deepseek_id TEXT,
    purpose     TEXT,
    path        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stored_files_user ON stored_files(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_stored_files_bucket ON stored_files(bucket, last_used);

CREATE TABLE IF NOT EXISTS session_files (
    user_id   INTEGER NOT NULL,
    client_id TEXT NOT NULL,
    file_id   TEXT NOT NULL,
    PRIMARY KEY (user_id, client_id, file_id)
);
CREATE INDEX IF NOT EXISTS idx_session_files_file ON session_files(user_id, file_id);
CREATE INDEX IF NOT EXISTS idx_session_files_client ON session_files(user_id, client_id);

CREATE TABLE IF NOT EXISTS audit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    action     TEXT NOT NULL,
    detail     TEXT,
    ip         TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-20000")
    conn.execute("PRAGMA journal_size_limit=67108864")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_incremental_auto_vacuum() -> None:
    """Enable incremental auto-vacuum for old databases.

    SQLite only changes auto_vacuum mode after a VACUUM. This runs once for
    existing deployments; new databases already get the pragma in SCHEMA.
    """
    conn = connect()
    try:
        mode = int(conn.execute("PRAGMA auto_vacuum").fetchone()[0])
        if mode != 2:
            conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
            logger.info("converting database to incremental auto-vacuum (one-time VACUUM)")
            conn.execute("VACUUM")
        _vacuum_free_pages(conn)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _vacuum_free_pages(conn: sqlite3.Connection) -> None:
    # SQLite's no-argument incremental_vacuum frees one page per call on some
    # versions; loop in batches until the freelist is drained.
    for _ in range(10000):
        free = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        if free <= 0:
            return
        conn.execute(f"PRAGMA incremental_vacuum({min(free, 1000)})")


def reclaim_space() -> None:
    """Return freed SQLite pages to the filesystem and truncate the WAL."""
    try:
        with db() as conn:
            _vacuum_free_pages(conn)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        logger.warning("database space reclamation failed", exc_info=True)


def init_db() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA)
        migrate(conn)
    _ensure_incremental_auto_vacuum()


def migrate(conn: sqlite3.Connection) -> None:
    """兼容已部署的旧数据库：补充同步所需的字段与索引。"""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(conversations)")}
    if "client_id" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN client_id TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_client "
        "ON conversations(user_id, client_id) WHERE client_id IS NOT NULL"
    )
