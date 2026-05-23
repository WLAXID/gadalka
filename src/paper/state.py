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
from loguru import logger


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
    status TEXT NOT NULL DEFAULT 'pending',
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

-- Траектория mid/bid/ask для pending-ставок: позволяет анализировать
-- drawdown и оптимальный exit пост-фактум.

-- Снимок orderbook в момент входа: даёт реальный bid/ask/depth для
-- верификации cost-model из бэктеста.
CREATE TABLE IF NOT EXISTS paper_trade_snapshots (
    trade_id BIGINT PRIMARY KEY,
    ts BIGINT NOT NULL,
    bid DOUBLE,
    ask DOUBLE,
    mid DOUBLE,
    spread_pct DOUBLE,
    bid_depth_5 DOUBLE,
    ask_depth_5 DOUBLE,
    raw_book TEXT
);

CREATE SEQUENCE IF NOT EXISTS paper_trades_seq START 1;
CREATE SEQUENCE IF NOT EXISTS paper_events_seq START 1;
CREATE SEQUENCE IF NOT EXISTS paper_trace_seq START 1;
CREATE SEQUENCE IF NOT EXISTS paper_scan_dump_seq START 1;

-- Траектория mid/bid/ask для pending-ставок.
-- id через DEFAULT nextval — батчевый executemany без round-trip за id.
CREATE TABLE IF NOT EXISTS paper_price_trace (
    id BIGINT PRIMARY KEY DEFAULT nextval('paper_trace_seq'),
    trade_id BIGINT NOT NULL,
    ts BIGINT NOT NULL,
    mid DOUBLE,
    bid DOUBLE,
    ask DOUBLE,
    spread_pct DOUBLE
);

-- Полный дамп каждого скана (in_range, near_below, near_above, candidate):
-- буфер для пост-фактум бэктеста на расширенных диапазонах.
CREATE TABLE IF NOT EXISTS paper_scan_dump (
    id BIGINT PRIMARY KEY DEFAULT nextval('paper_scan_dump_seq'),
    scan_ts BIGINT NOT NULL,
    bucket TEXT NOT NULL,
    condition_id TEXT,
    token_id TEXT,
    event_id TEXT,
    slug TEXT,
    price_yes DOUBLE,
    volume DOUBLE,
    end_date_iso TEXT
);

CREATE INDEX IF NOT EXISTS paper_trace_trade_idx
    ON paper_price_trace (trade_id, ts);
CREATE INDEX IF NOT EXISTS paper_scan_dump_ts_idx
    ON paper_scan_dump (scan_ts);
CREATE INDEX IF NOT EXISTS paper_events_ts_idx
    ON paper_events (ts);
"""

# Миграции: ALTER TABLE для существующих БД, чтобы добавить новые колонки
# без полной пересборки. DuckDB 1.0+ поддерживает ADD COLUMN IF NOT EXISTS.
MIGRATIONS = [
    "ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS event_id TEXT",
]


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
    event_id: str | None = None


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
            for migration in MIGRATIONS:
                self._conn.execute(migration)

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
        event_id: str | None = None,
    ) -> int | None:
        """Вставка идемпотентная: если ставка для (condition_id, token_id) есть — None.

        Проверка + insert под одним lock, чтобы между ними не вклинился
        другой writer (UNIQUE-constraint всё равно защитит, но IntegrityError
        дороже).
        """
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM paper_trades WHERE condition_id = ? AND token_id = ?",
                [condition_id, token_id],
            ).fetchone()
            if exists is not None:
                return None
            trade_id = self._conn.execute(
                "SELECT nextval('paper_trades_seq')"
            ).fetchone()[0]
            self._conn.execute(
                """
                INSERT INTO paper_trades (
                    trade_id, condition_id, token_id, market_slug, market_question,
                    entry_ts, entry_price, buy_cost, stake, strategy,
                    end_date_iso, volume, status, event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
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
                    event_id,
                ],
            )
            return int(trade_id)

    def has_trade_for_event(self, event_id: str) -> bool:
        """True если уже взяли ставку в любом рынке этого event.

        Защита от корреляции: на одном выборе/событии у Polymarket часто
        несколько рынков (Yes/No варианты исхода), и они движутся
        синхронно — нарушение независимости sample.
        """
        if not event_id:
            return False
        with self._lock:
            r = self._conn.execute(
                "SELECT 1 FROM paper_trades WHERE event_id = ? LIMIT 1",
                [event_id],
            ).fetchone()
            return r is not None

    def existing_trade_keys(self) -> tuple[set[tuple[str, str]], set[str]]:
        """Все (condition_id, token_id) и все event_id уже взятых ставок.

        Возвращаем оба множества одним запросом, чтобы signal-loop не
        делал N+1 проверок на каждый рынок из топ-100.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT condition_id, token_id, event_id FROM paper_trades"
            ).fetchall()
        ck = {(r[0], r[1]) for r in rows}
        ek = {r[2] for r in rows if r[2]}
        return ck, ek

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
        """Сводка: общий P&L, win rate, win/loss, открытые/резолвнутые.

        Различает transient ошибки (сеть/429 — компоненты etl/resolve/trace/
        signal) и fatal (всё остальное — schema, parsing, DB). Это даёт
        сигнал «инфраструктура шумит» vs «у нас реальный баг».
        """
        now = int(time.time())
        day_ago = now - 86400
        transient_components = ("etl", "resolve", "trace", "signal", "scan")
        comp_placeholders = ",".join(["?"] * len(transient_components))
        with self._lock:
            row = self._conn.execute(
                f"""
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
                     FROM paper_trades WHERE status = 'pending')                   AS pending_cost,
                  (SELECT COUNT(*) FROM paper_events
                     WHERE level = 'ERROR' AND ts >= ?)                            AS error_count_24h,
                  (SELECT COUNT(*) FROM paper_events
                     WHERE level = 'ERROR' AND ts >= ?
                       AND component IN ({comp_placeholders}))                     AS transient_errors_24h,
                  (SELECT COUNT(*) FROM paper_price_trace WHERE ts >= ?)           AS trace_points_24h
                """,
                [day_ago, day_ago, *transient_components, day_ago],
            ).fetchone()
        keys = [
            "pending", "resolved", "cancelled",
            "total_pnl", "wins", "losses",
            "invested", "pending_cost",
            "error_count_24h", "transient_errors_24h", "trace_points_24h",
        ]
        s = dict(zip(keys, row))
        s["fatal_errors_24h"] = s["error_count_24h"] - s["transient_errors_24h"]
        n_resolved = s["wins"] + s["losses"]
        s["win_rate"] = (s["wins"] / n_resolved) if n_resolved > 0 else None
        s["ev_per_dollar"] = (s["total_pnl"] / s["invested"]) if s["invested"] > 0 else None
        return s

    def db_integrity_check(self) -> bool:
        """Быстрая проверка что схема + ключевые таблицы доступны.

        Используется healthcheck'ом и recovery-логикой при cold start.
        """
        with self._lock:
            try:
                self._conn.execute(
                    "SELECT COUNT(*) FROM paper_trades LIMIT 1"
                ).fetchone()
                self._conn.execute(
                    "SELECT COUNT(*) FROM paper_resolutions LIMIT 1"
                ).fetchone()
                return True
            except Exception:
                return False

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

    # ---------- Dump ----------

    def make_dump(self, output_path: Path | str) -> Path:
        """Сделать консистентный снимок paper.duckdb в output_path.

        Используем DuckDB ATTACH + COPY FROM DATABASE, потому что
        shutil.copy живого файла с writelock падает на Windows
        (PermissionError). DuckDB сам пишет новый файл атомарно.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # ATTACH откажется писать в существующий файл
        if output_path.exists():
            try:
                output_path.unlink()
            except OSError as e:
                logger.warning("[dump] не смог удалить старый файл: {}", e)
        dest = output_path.as_posix().replace("'", "''")
        with self._lock:
            self._conn.execute("CHECKPOINT")
            # Имя текущей БД в DuckDB = имя файла без расширения, а не "main"
            src_db = self._conn.execute("SELECT current_database()").fetchone()[0]
            src_q = src_db.replace('"', '""')
            self._conn.execute(f"ATTACH '{dest}' AS dump_db")
            try:
                self._conn.execute(
                    f'COPY FROM DATABASE "{src_q}" TO dump_db'
                )
            finally:
                self._conn.execute("DETACH dump_db")
        return output_path

    def export_csv_bundle(self, output_dir: Path | str) -> Path:
        """Экспорт всех таблиц в CSV-папку. Возвращает путь к директории."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        tables = (
            "paper_trades", "paper_resolutions",
            "paper_events", "paper_settings",
            "paper_price_trace", "paper_trade_snapshots",
            "paper_scan_dump",
        )
        with self._lock:
            self._conn.execute("CHECKPOINT")
            for tbl in tables:
                out = output_dir / f"{tbl}.csv"
                # Имя таблицы — литерал из whitelist, инъекция невозможна.
                self._conn.execute(
                    f"COPY {tbl} TO '{out.as_posix()}' (HEADER, DELIMITER ',')"
                )
        return output_dir

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

    # ---------- Trace / Snapshots / Scan dump ----------

    def insert_trace_point(
        self,
        *,
        trade_id: int,
        mid: float | None,
        bid: float | None,
        ask: float | None,
    ) -> None:
        """Точка траектории mid/bid/ask для pending-ставки."""
        self.insert_trace_points_batch([{
            "trade_id": trade_id, "mid": mid, "bid": bid, "ask": ask,
        }])

    def insert_trace_points_batch(self, points: list[dict]) -> int:
        """Батчевый INSERT траекторных точек — один lock-acquire на весь батч.

        points — список dict с ключами trade_id, mid, bid, ask.
        spread_pct рассчитывается тут.
        """
        if not points:
            return 0
        now = int(time.time())
        payload = []
        for p in points:
            mid = p.get("mid")
            bid = p.get("bid")
            ask = p.get("ask")
            spread_pct: float | None = None
            if bid is not None and ask is not None and mid and mid > 0:
                spread_pct = (ask - bid) / mid
            payload.append(
                [int(p["trade_id"]), now, mid, bid, ask, spread_pct]
            )
        with self._lock:
            # id через DEFAULT nextval — позволяет executemany без round-trip
            self._conn.executemany(
                """
                INSERT INTO paper_price_trace
                  (trade_id, ts, mid, bid, ask, spread_pct)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
        return len(payload)

    def insert_trade_snapshot(
        self,
        *,
        trade_id: int,
        bid: float | None,
        ask: float | None,
        mid: float | None,
        bid_depth_5: float | None,
        ask_depth_5: float | None,
        raw_book: str | None,
    ) -> None:
        """Сохранить orderbook-снимок при entry. Идемпотентно (PK = trade_id)."""
        spread_pct: float | None = None
        if bid is not None and ask is not None and mid and mid > 0:
            spread_pct = (ask - bid) / mid
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO paper_trade_snapshots
                  (trade_id, ts, bid, ask, mid, spread_pct,
                   bid_depth_5, ask_depth_5, raw_book)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [int(trade_id), int(time.time()), bid, ask, mid, spread_pct,
                 bid_depth_5, ask_depth_5, raw_book],
            )

    def insert_scan_dump(self, scan_ts: int, rows: list[dict]) -> int:
        """Один пакетный INSERT всех bucket-записей одного скана.

        rows — список dict'ов с ключами: bucket, condition_id, token_id,
        event_id, slug, price_yes, volume, end_date_iso.
        Возвращает число вставленных строк.

        id заполняется DEFAULT nextval('paper_scan_dump_seq') — поэтому
        executemany делается одним батчем без round-trip за id.
        """
        if not rows:
            return 0
        payload = [
            [
                int(scan_ts),
                r["bucket"],
                r.get("condition_id"),
                r.get("token_id"),
                r.get("event_id"),
                r.get("slug"),
                r.get("price_yes"),
                r.get("volume"),
                r.get("end_date_iso"),
            ]
            for r in rows
        ]
        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO paper_scan_dump
                  (scan_ts, bucket, condition_id, token_id, event_id,
                   slug, price_yes, volume, end_date_iso)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
        return len(rows)

    def cleanup_scan_dump(self, older_than_s: int) -> int:
        """Удалить scan_dump-записи старше cutoff. Возвращает кол-во удалённых."""
        cutoff = int(time.time()) - int(older_than_s)
        with self._lock:
            before = self._conn.execute(
                "SELECT COUNT(*) FROM paper_scan_dump WHERE scan_ts < ?",
                [cutoff],
            ).fetchone()[0]
            self._conn.execute(
                "DELETE FROM paper_scan_dump WHERE scan_ts < ?", [cutoff]
            )
        return int(before)

    def stuck_pending(self, after_endDate_s: int = 7 * 86400) -> list[dict]:
        """Pending-ставки старше N секунд после end_date_iso.

        Возвращает trade'ы, у которых рынок должен был резолвиться
        давно, но всё ещё pending — повод проверить руками.

        Двойной TRY_CAST обязателен: внутренний CAST(text AS TIMESTAMP)
        бросает ConversionException на невалидном формате, и обёртка
        снаружи это НЕ ловит (исключение поднимается до окружающего
        TRY_CAST). Внутренний TRY_CAST возвращает NULL и весь
        предикат становится NULL → строка отсеивается.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT trade_id, condition_id, market_slug, market_question,
                       entry_ts, end_date_iso
                FROM paper_trades
                WHERE status = 'pending'
                  AND end_date_iso IS NOT NULL
                  AND TRY_CAST(epoch(TRY_CAST(end_date_iso AS TIMESTAMP))
                               AS BIGINT) < (epoch(NOW()) - ?)
                ORDER BY end_date_iso
                """,
                [int(after_endDate_s)],
            ).fetchall()
            cols = [d[0] for d in self._conn.description]
        return [dict(zip(cols, r)) for r in rows]
