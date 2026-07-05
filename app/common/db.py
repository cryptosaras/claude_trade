import json
import time

import psycopg
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from .config import db_dsn

_pool: ConnectionPool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
  symbol text NOT NULL, tf text NOT NULL, ts timestamptz NOT NULL,
  o double precision, h double precision, l double precision,
  c double precision, v double precision,
  PRIMARY KEY (symbol, tf, ts)
);
CREATE TABLE IF NOT EXISTS funding (
  symbol text NOT NULL, ts timestamptz NOT NULL, rate double precision,
  PRIMARY KEY (symbol, ts)
);
CREATE TABLE IF NOT EXISTS open_interest (
  symbol text NOT NULL, ts timestamptz NOT NULL, oi double precision,
  PRIMARY KEY (symbol, ts)
);
CREATE TABLE IF NOT EXISTS tickers (
  symbol text PRIMARY KEY, price double precision, change24h double precision,
  turnover24h double precision, funding_rate double precision, updated timestamptz
);
CREATE TABLE IF NOT EXISTS regime (
  ts timestamptz PRIMARY KEY, label text, confidence double precision, meta jsonb
);
CREATE TABLE IF NOT EXISTS strategies (
  name text PRIMARY KEY, status text, meta jsonb, updated timestamptz DEFAULT now()
);
CREATE TABLE IF NOT EXISTS positions (
  id bigserial PRIMARY KEY, strategy text, symbol text, grp text, side text,
  qty double precision, lev double precision,
  entry_price double precision, entry_ts timestamptz,
  sl double precision, tp double precision,
  status text DEFAULT 'open',
  exit_price double precision, exit_ts timestamptz, exit_reason text,
  gross_pnl double precision, fees double precision DEFAULT 0,
  funding_paid double precision DEFAULT 0, net_pnl double precision,
  regime_entry text, notional double precision, mode text DEFAULT 'live'
);
CREATE INDEX IF NOT EXISTS positions_status_idx ON positions (status, mode);
CREATE INDEX IF NOT EXISTS positions_strategy_idx ON positions (strategy, exit_ts);
CREATE TABLE IF NOT EXISTS equity (
  ts timestamptz PRIMARY KEY, equity double precision
);
CREATE TABLE IF NOT EXISTS events (
  id bigserial PRIMARY KEY, ts timestamptz DEFAULT now(), kind text,
  message text, data jsonb
);
CREATE TABLE IF NOT EXISTS backtests (
  id bigserial PRIMARY KEY, ts timestamptz DEFAULT now(), params jsonb,
  status text DEFAULT 'running', result jsonb
);
CREATE TABLE IF NOT EXISTS kv (
  key text PRIMARY KEY, value jsonb, updated timestamptz DEFAULT now()
);
"""

HYPERTABLES = """
SELECT create_hypertable('candles', 'ts', if_not_exists => TRUE, migrate_data => TRUE);
"""


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(db_dsn(), min_size=1, max_size=6, open=True)
    return _pool


def wait_for_db(timeout: int = 120) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with psycopg.connect(db_dsn(), connect_timeout=5):
                return
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2)
    raise RuntimeError(f"database not reachable: {last}")


def init_schema() -> None:
    with pool().connection() as conn:
        conn.execute(SCHEMA)
        try:
            conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
            conn.execute(HYPERTABLES)
        except Exception:  # plain postgres also works, just slower
            conn.rollback()
        conn.commit()


def q(sql: str, params=None) -> list[tuple]:
    with pool().connection() as conn:
        cur = conn.execute(sql, params)
        if cur.description is None:
            return []
        return cur.fetchall()


def qd(sql: str, params=None) -> list[dict]:
    with pool().connection() as conn:
        cur = conn.execute(sql, params)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def execute(sql: str, params=None) -> None:
    with pool().connection() as conn:
        conn.execute(sql, params)


def event(kind: str, message: str, data: dict | None = None) -> None:
    execute(
        "INSERT INTO events (kind, message, data) VALUES (%s, %s, %s)",
        (kind, message, Json(data or {})),
    )


def kv_get(key: str, default=None):
    rows = q("SELECT value FROM kv WHERE key=%s", (key,))
    return rows[0][0] if rows else default


def kv_set(key: str, value) -> None:
    execute(
        "INSERT INTO kv (key, value, updated) VALUES (%s, %s, now()) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated=now()",
        (key, Json(value)),
    )
