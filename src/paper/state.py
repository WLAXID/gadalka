"""Персистентное состояние paper trader'а в DuckDB.

Таблицы:
- paper_trades       — открытые виртуальные ставки (идемпотент по condition_id+token_id)
- paper_resolutions  — закрытые ставки с финальным P&L
- paper_events       — журнал событий (info/warning/error) для /health
- paper_settings     — kv для рантайма (paused, last_etl_ts, ...)
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id BIGINT PRIMARY KEY,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    market_slug TEXT,
    market_question TEXT,
    entry_ts BIGINT NOT NULL,
    entry_price DOUBLE NOT NULL,
    buy_cost DOUBLE NOT NULL,
    stake DOUBLE NOT NULL,
    strategy TEXT NOT NULL,
    end_date_iso TEXT,
    volume DOUBLE,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending / resolved / cancelled
    UNIQUE (condition_id, token_id)
);

CREATE TABLE IF NOT EXISTS paper_resolutions (
    trade_id BIGINT PRIMARY KEY,
    resolve_ts BIGINT NOT NULL,
    payout DOUBLE NOT NULL,
    pnl DOUBLE NOT NULL,
    resolved_yes BOOLEAN,
    final_price_yes DOUBLE
);

CREATE TABLE IF NOT EXISTS paper_events (
    id BIGINT PRIMARY KEY,
    ts BIGINT NOT NULL,
    level TEXT NOT NULL,
    component TEXT NOT NULL,
    message TEXT NOT NULL,
    payload TEXT
);

CREATE TABLE IF NOT EXISTS paper_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_ts BIGINT NOT NULL
);

CREATE SEQUENCE IF NOT EXISTS paper_trades_seq START 1;
CREATE SEQUENCE IF NOT EXISTS paper_events_seq START 1;
"""


@dataclass
class Trade:
    trade_id: int
    condition_id: str
    token_id: str
    market_slug: str | None
    market_question: str | None
    entry_ts: int
    entry_price: float
    buy_cost: float
    stake: float
    strategy: str
    end_date_iso: str | None
    volume: float | None
    status: str


@dataclass
class Resolution:
    trade_id: int
    resolve_ts: int
    payout: float
    pnl: float
    resolved_yes: bool | None
    final_price_yes: float | None


class PaperState:
    """Обёртка над DuckDB-файлом со state'ом paper trader'а.

    Thread-safe: внутри Lock на все операции записи.
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(self.db_path))
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            for stmt in SCHEMA.strip().split(";"):
                if stmt.strip():
                    self._conn.execute(stmt)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- Settings ----------

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO paper_settings (key, value, updated_ts)
                VALUES (?, ?, ?)
                """,
                [key, value, int(time.time())],
            )

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT value FROM paper_settings WHERE key = ?", [key]
            ).fetchone()
            return r[0] if r else default

    def is_paused(self) -> bool:
        return self.get_setting("paused", "0") == "1"

    def set_paused(self, paused: bool) -> None:
        self.set_setting("paused", "1" if paused else "0")

    # ---------- Trades ----------

    def has_trade_for(self, condition_id: str, token_id: str) -> bool:
        with self._lock:
            r = self._conn.execute(
                "SELECT 1 FROM paper_trades WHERE condition_id = ? AND token_id = ?",
                [condition_id, token_id],
            ).fetchone()
            return r is not None

    def insert_trade(
        self,
        *,
        condition_id: str,
        token_id: str,
        market_slug: str | None,
        market_question: str | None,
        entry_price: float,
        buy_cost: float,
        stake: float,
        strategy: str,
        end_date_iso: str | None,
        volume: float | None,
    ) -> int | None:
        """Вставка идемпотентная: если ставка для (condition_id, token_id) есть — None."""
        if self.has_trade_for(condition_id, token_id):
            return None
        with self._lock:
            trade_id = self._conn.execute(
                "SELECT nextval('paper_trades_seq')"
            ).fetchone()[0]
            self._conn.execute(
                """
                INSERT INTO paper_trades (
                    trade_id, condition_id, token_id, market_slug, market_question,
                    entry_ts, entry_price, buy_cost, stake, strategy,
                    end_date_iso, volume, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                [
                    trade_id,
                    condition_id,
                    token_id,
                    market_slug,
                    market_question,
                    int(time.time()),
                    entry_price,
                    buy_cost,
                    stake,
                    strategy,
                    end_date_iso,
                    volume,
                ],
            )
            return int(trade_id)

    def pending_trades(self) -> list[Trade]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM paper_trades WHERE status = 'pending' ORDER BY entry_ts"
            ).fetchall()
            cols = [d[0] for d in self._conn.description]
        return [Trade(**dict(zip(cols, r))) for r in rows]

    def resolve_trade(
        self,
        trade_id: int,
        *,
        resolved_yes: bool | None,
        final_price_yes: float | None,
        payout: float,
        pnl: float,
        cancelled: bool = False,
    ) -> None:
        with self._lock:
            new_status = "cancelled" if cancelled else "resolved"
            self._conn.execute(
                "UPDATE paper_trades SET status = ? WHERE trade_id = ?",
                [new_status, trade_id],
            )
            self._conn.execute(
                """
                INSERT OR REPLACE INTO paper_resolutions
                  (trade_id, resolve_ts, payout, pnl, resolved_yes, final_price_yes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    trade_id,
                    int(time.time()),
                    payout,
                    pnl,
                    resolved_yes,
                    final_price_yes,
                ],
            )

    # ---------- Events ----------

    def log_event(
        self,
        level: str,
        component: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            event_id = self._conn.execute(
                "SELECT nextval('paper_events_seq')"
            ).fetchone()[0]
            self._conn.execute(
                """
                INSERT INTO paper_events (id, ts, level, component, message, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    int(event_id),
                    int(time.time()),
                    level.upper(),
                    component,
                    message,
                    json.dumps(payload, ensure_ascii=False) if payload else None,
                ],
            )

    def last_events(self, limit: int = 20, level: str | None = None) -> list[dict]:
        with self._lock:
            if level:
                q = (
                    "SELECT * FROM paper_events WHERE level = ? "
                    "ORDER BY ts DESC LIMIT ?"
                )
                rows = self._conn.execute(q, [level.upper(), limit]).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM paper_events ORDER BY ts DESC LIMIT ?", [limit]
                ).fetchall()
            cols = [d[0] for d in self._conn.description]
        return [dict(zip(cols, r)) for r in rows]

    # ---------- Stats ----------

    def summary_stats(self) -> dict[str, Any]:
        """Сводка: общий P&L, win rate, win/loss, открытые/резолвнутые."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM paper_trades WHERE status = 'pending')   AS pending,
                  (SELECT COUNT(*) FROM paper_trades WHERE status = 'resolved')  AS resolved,
                  (SELECT COUNT(*) FROM paper_trades WHERE status = 'cancelled') AS cancelled,
                  (SELECT COALESCE(SUM(pnl), 0)   FROM paper_resolutions)        AS total_pnl,
                  (SELECT COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), 0)
                                                   FROM paper_resolutions)        AS wins,
                  (SELECT COALESCE(SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END), 0)
                                                   FROM paper_resolutions)        AS losses,
                  (SELECT COALESCE(SUM(buy_cost), 0)
                     FROM paper_trades WHERE status = 'resolved')                  AS invested,
                  (SELECT COALESCE(SUM(buy_cost), 0)
                     FROM paper_trades WHERE status = 'pending')                   AS pending_cost
                """
            ).fetchone()
        keys = [
            "pending", "resolved", "cancelled",
            "total_pnl", "wins", "losses",
            "invested", "pending_cost",
        ]
        s = dict(zip(keys, row))
        n_resolved = s["wins"] + s["losses"]
        s["win_rate"] = (s["wins"] / n_resolved) if n_resolved > 0 else None
        s["ev_per_dollar"] = (s["total_pnl"] / s["invested"]) if s["invested"] > 0 else None
        return s

    def recent_resolutions(self, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                  t.trade_id, t.market_question, t.market_slug,
                  t.entry_price, t.buy_cost, t.stake,
                  r.resolve_ts, r.payout, r.pnl, r.resolved_yes
                FROM paper_resolutions r
                JOIN paper_trades t USING (trade_id)
                ORDER BY r.resolve_ts DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
            cols = [d[0] for d in self._conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def pending_summary(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT trade_id, market_question, market_slug, entry_price,
                       buy_cost, entry_ts, end_date_iso, volume
                FROM paper_trades
                WHERE status = 'pending'
                ORDER BY end_date_iso NULLS LAST, entry_ts DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
            cols = [d[0] for d in self._conn.description]
        return [dict(zip(cols, r)) for r in rows]
