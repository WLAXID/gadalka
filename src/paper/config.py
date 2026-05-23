"""Конфиг paper trader'а — читается из .env через python-dotenv."""

from __future__ import annotations

import os
from dataclasses import dataclass
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

    # --- Costs (для расчёта paper-PnL) ---
    fee_rate: float = 0.02
    spread_pct: float = 0.015
    slippage_pct: float = 0.005

    # --- Limits ---
    stake_amount: float = 1.0  # фиксированный размер ставки в paper $
    min_market_volume: float = 100.0
    max_pending_markets: int = 5000  # safety cap

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
            daily_report_time=_env("PAPER_DAILY_REPORT_TIME", "23:59"),
            strategy_low=_env_float("PAPER_STRATEGY_LOW", 0.50),
            strategy_high=_env_float("PAPER_STRATEGY_HIGH", 0.85),
        )
