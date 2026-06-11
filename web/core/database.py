"""SQLite database layer with thread-safe operations."""
import sqlite3
import threading
import os
import pathlib
import time
from contextlib import contextmanager
from typing import Optional, Iterator

DB_PATH = pathlib.Path(os.environ.get("HNG_DB_PATH", "/var/lib/host-nginx-manager/state.db"))
_db_lock = threading.RLock()

SCHEMA_SQL = """
-- 会话表
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    ip TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    last_active INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- 登录限流表
CREATE TABLE IF NOT EXISTS login_attempts (
    ip TEXT PRIMARY KEY,
    count INTEGER DEFAULT 0,
    locked_until INTEGER DEFAULT 0,
    last_attempt INTEGER NOT NULL
);

-- API 限流表
CREATE TABLE IF NOT EXISTS api_rate_limits (
    ip TEXT PRIMARY KEY,
    count INTEGER DEFAULT 0,
    reset_time INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_rate_reset ON api_rate_limits(reset_time);

-- 审计日志表
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    ip TEXT NOT NULL,
    session_token TEXT,
    action TEXT NOT NULL,
    resource TEXT,
    details TEXT,
    result TEXT NOT NULL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_logs(resource);

-- 配置表
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""

@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    """线程安全的数据库连接上下文管理器."""
    with _db_lock:
        conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

def init_database() -> None:
    """初始化数据库schema."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with get_db() as db:
        db.executescript(SCHEMA_SQL)
        db.execute(
            "INSERT OR IGNORE INTO config (key, value, updated_at) VALUES (?, ?, ?)",
            ("schema_version", "1", int(time.time()))
        )

def clean_expired_data() -> None:
    """清理过期数据（会话、限流记录）."""
    now = int(time.time())
    with get_db() as db:
        db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        db.execute("DELETE FROM api_rate_limits WHERE reset_time < ?", (now,))
        db.execute("DELETE FROM login_attempts WHERE locked_until < ? AND locked_until > 0", (now,))

def get_config(key: str, default: str = "") -> str:
    """获取配置值."""
    with get_db() as db:
        row = db.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

def set_config(key: str, value: str) -> None:
    """设置配置值."""
    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, int(time.time()))
        )
