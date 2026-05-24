"""Конфиг paper trader'а — читается из .env через python-dotenv."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time as dtime
from pathlib import Path

from dotenv import load_dotenv


def _env(name: str, default: str | None = None) -> str:
    val = os.getenv(name)
    if val is None or val == "":
        if default is None:
            raise RuntimeError(f"Не задана переменная окружения {name}")
        return default
    return val


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return int(val)


def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return float(val)


def _env_time(name: str, default: str) -> str:
    """Валидируем HH:MM формат — иначе явный crash, не тихий fallback.

    Тихий fallback означает: юзер задал backup_time=25:00 опечаткой → backup
    всегда в 03:00, и он этого не узнает месяц.
    """
    val = os.getenv(name) or default
    try:
        dtime.fromisoformat(val + ":00")
    except ValueError as e:
        raise RuntimeError(
            f"Некорректный {name}={val!r}, ожидаю HH:MM (24h UTC): {e}"
        )
    return val


@dataclass(frozen=True)
class PaperConfig:
    """Все параметры paper trader'а в одном месте."""

    # --- Telegram ---
    tg_bot_token: str
    tg_owner_id: int

    # --- Database ---
    db_path: Path
    log_level: str

    # --- Loops ---
    etl_interval_s: int
    resolve_interval_s: int
    daily_report_time: str  # HH:MM UTC

    # --- Strategy (H1 baseline) ---
    strategy_low: float
    strategy_high: float

    # --- Trace / heartbeat / backup ---
    trace_interval_s: int = 3600          # как часто снимаем mid для pending
    heartbeat_threshold_s: int = 3600     # >1h без УСПЕШНОГО scan → алерт
    heartbeat_throttle_s: int = 21600     # не чаще раза в 6h
    backup_time: str = "03:00"            # UTC, отдельно от daily report
    backup_dir: Path = Path("data/backups")
    backup_retention_days: int = 14
    scan_dump_retention_days: int = 45    # старше — чистим
    tg_watchdog_interval_s: int = 300     # каждые 5 мин пингуем getMe()

    # --- Costs (для расчёта paper-PnL) ---
    fee_rate: float = 0.02
    spread_pct: float = 0.015
    slippage_pct: float = 0.005

    # --- Limits / sample correctness ---
    stake_amount: float = 1.0  # фиксированный размер ставки в paper $
    min_market_volume: float = 100.0
    max_pending_markets: int = 5000  # safety cap
    # F11: Polymarket часто резолвит ДО endDate (UMA-trigger). Жёсткий
    # фильтр в 30d отсекал бы такие сигналы — берём 60d как компромисс.
    max_market_ttl_days: int = 60    # skip рынки с endDate > now+N дней
    stuck_pending_after_days: int = 7  # warning если pending после endDate+N
    pending_growth_alert: int = 200    # если pending > N → warning в daily
    scan_max_markets: int = 10000      # = hard cap Gamma (offset>10000 ошибка)

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> "PaperConfig":
        if env_file:
            load_dotenv(env_file, override=False)
        else:
            load_dotenv(override=False)

        return cls(
            tg_bot_token=_env("TG_BOT_TOKEN"),
            tg_owner_id=int(_env("TG_OWNER_ID")),
            db_path=Path(_env("PAPER_DB_PATH", "data/paper.duckdb")),
            log_level=_env("PAPER_LOG_LEVEL", "INFO"),
            etl_interval_s=_env_int("PAPER_ETL_INTERVAL_S", 900),
            resolve_interval_s=_env_int("PAPER_RESOLVE_INTERVAL_S", 3600),
            daily_report_time=_env_time("PAPER_DAILY_REPORT_TIME", "23:59"),
            strategy_low=_env_float("PAPER_STRATEGY_LOW", 0.50),
            strategy_high=_env_float("PAPER_STRATEGY_HIGH", 0.85),
            trace_interval_s=_env_int("PAPER_TRACE_INTERVAL_S", 3600),
            heartbeat_threshold_s=_env_int("PAPER_HEARTBEAT_THRESHOLD_S", 3600),
            heartbeat_throttle_s=_env_int("PAPER_HEARTBEAT_THROTTLE_S", 21600),
            backup_time=_env_time("PAPER_BACKUP_TIME", "03:00"),
            backup_dir=Path(_env("PAPER_BACKUP_DIR", "data/backups")),
            backup_retention_days=_env_int("PAPER_BACKUP_RETENTION_DAYS", 14),
            scan_dump_retention_days=_env_int("PAPER_SCAN_DUMP_RETENTION_DAYS", 45),
            tg_watchdog_interval_s=_env_int("PAPER_TG_WATCHDOG_INTERVAL_S", 300),
            max_market_ttl_days=_env_int("PAPER_MAX_MARKET_TTL_DAYS", 60),
            stuck_pending_after_days=_env_int("PAPER_STUCK_PENDING_AFTER_DAYS", 7),
            pending_growth_alert=_env_int("PAPER_PENDING_GROWTH_ALERT", 200),
            scan_max_markets=_env_int("PAPER_SCAN_MAX_MARKETS", 10000),
        )
