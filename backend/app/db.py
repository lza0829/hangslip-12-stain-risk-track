from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  rack_count INTEGER NOT NULL DEFAULT 2,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS hang_tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_no TEXT NOT NULL UNIQUE,
  store_code TEXT NOT NULL,
  pickup_code TEXT NOT NULL,
  customer_name TEXT NOT NULL,
  status TEXT NOT NULL,
  opened_at TEXT NOT NULL,
  due_date TEXT NOT NULL,
  closed_at TEXT,
  note TEXT NOT NULL DEFAULT '',
  risk_state TEXT NOT NULL DEFAULT '无'
);
CREATE TABLE IF NOT EXISTS ticket_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_no TEXT NOT NULL,
  garment TEXT NOT NULL,
  qty INTEGER NOT NULL,
  remark TEXT NOT NULL DEFAULT '',
  risk_flag INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS risk_confirmations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_no TEXT NOT NULL,
  operator TEXT NOT NULL,
  confirmed_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS process_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_no TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT NOT NULL,
  operator TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS pickups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_no TEXT NOT NULL UNIQUE,
  pickup_code TEXT NOT NULL,
  picked_at TEXT NOT NULL,
  operator TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quarantines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_no TEXT NOT NULL,
  kind TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS day_gates (
  day TEXT PRIMARY KEY,
  sealed INTEGER NOT NULL DEFAULT 0,
  sealed_at TEXT,
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watermarks (
  key TEXT PRIMARY KEY,
  value INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
"""

STATUSES = ("已收", "水洗", "烘干", "熨烫", "待取", "已取", "超期隔离", "丢失隔离")
FORWARD = {
    "已收": ("水洗", "烘干", "熨烫", "待取"),
    "水洗": ("烘干", "熨烫", "待取"),
    "烘干": ("熨烫", "待取"),
    "熨烫": ("待取",),
    "待取": (),
}

# 染色风险口径（全局唯一）：强制分轨。
# 含风险件的票不得与普通件混票推进到待取；纯风险票未清险不得进待取。
RISK_NONE = "无"
RISK_PENDING = "未清"
RISK_CLEARED = "已清"
RISK_POLICY = "强制分轨：含染色风险件不与普通件同票进待取，风险侧清险确认后方可待取"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def transaction():
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        # 老库迁移：补风险列（新库由 SCHEMA 自带，幂等）
        _ensure_column(conn, "hang_tickets", "risk_state", "risk_state TEXT NOT NULL DEFAULT '无'")
        _ensure_column(conn, "ticket_items", "risk_flag", "risk_flag INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES ('风险口径', ?)",
            (RISK_POLICY,),
        )
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)
