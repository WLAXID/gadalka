"""Построение train-ready датасета для бэктеста.

Одна строка = один резолвнутый рынок с фичами:
- price_yes_t1h / t6h / t24h / t7d (asof join к prices_history)
- volume, liquidity, lifetime_days
- close_ts (используется для time-based train/test split)
- resolved_yes (target)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from loguru import logger

from src.storage.duckdb_loader import GadalkaDB

ROOT = Path(__file__).resolve().parent.parent.parent


def build_backtest_dataset(
    *,
    require_t24h: bool = True,
    min_volume: float | None = None,
) -> pd.DataFrame:
    """Собрать analysis-ready DataFrame через DuckDB.

    Возвращает колонки:
      condition_id, token_id_yes, resolved_yes (bool),
      volume, liquidity, open_ts, close_ts, lifetime_days,
      price_yes_t1h, price_yes_t6h, price_yes_t24h, price_yes_t7d,
      n_history_points
    """
    with GadalkaDB(ROOT / "data" / "processed" / "gadalka.duckdb") as db:
        db.register_parquet_views(root=ROOT)

        # 1) market_close — закрытые с историей
        db.sql(
            """
            CREATE OR REPLACE TEMP VIEW market_close AS
            SELECT
              m.conditionId AS condition_id,
              m.token_id_yes,
              CAST(m.resolved_yes AS BOOL) AS resolved_yes,
              CAST(m.volumeNum AS DOUBLE)  AS volume,
              CAST(m.liquidity AS DOUBLE)  AS liquidity,
              CAST(MIN(p.t) AS BIGINT)     AS open_ts,
              CAST(MAX(p.t) AS BIGINT)     AS close_ts,
              COUNT(p.t)                   AS n_history_points
            FROM markets m
            JOIN prices_history p ON p.condition_id = m.conditionId
            WHERE m.token_id_yes IS NOT NULL
              AND m.resolved_yes IS NOT NULL
            GROUP BY 1, 2, 3, 4, 5
            """
        )

        # 2) Цены на разных горизонтах через ASOF JOIN
        for label, secs in [("t1h", 3600), ("t6h", 6 * 3600),
                             ("t24h", 86400), ("t7d", 7 * 86400)]:
            db.sql(
                f"""
                CREATE OR REPLACE TEMP VIEW price_{label} AS
                SELECT
                  mc.condition_id,
                  p.p AS price_yes_{label}
                FROM market_close mc
                ASOF JOIN prices_history p
                  ON p.token_id = mc.token_id_yes
                  AND p.t <= mc.close_ts - {secs}
                """
            )

        # 3) Сборка
        df = db.df(
            """
            SELECT
              mc.condition_id,
              mc.token_id_yes,
              mc.resolved_yes,
              mc.volume,
              mc.liquidity,
              mc.open_ts,
              mc.close_ts,
              (mc.close_ts - mc.open_ts) / 86400.0 AS lifetime_days,
              mc.n_history_points,
              p1.price_yes_t1h,
              p6.price_yes_t6h,
              p24.price_yes_t24h,
              p7d.price_yes_t7d
            FROM market_close mc
            LEFT JOIN price_t1h  p1  ON p1.condition_id  = mc.condition_id
            LEFT JOIN price_t6h  p6  ON p6.condition_id  = mc.condition_id
            LEFT JOIN price_t24h p24 ON p24.condition_id = mc.condition_id
            LEFT JOIN price_t7d  p7d ON p7d.condition_id = mc.condition_id
            ORDER BY mc.close_ts
            """
        )

    if require_t24h:
        before = len(df)
        df = df[df["price_yes_t24h"].notna()].copy()
        logger.info(
            "Фильтр require_t24h: {b} → {a} рынков",
            b=before, a=len(df),
        )

    if min_volume is not None:
        before = len(df)
        df = df[df["volume"] >= min_volume].copy()
        logger.info(
            "Фильтр volume >= {v}: {b} → {a}", v=min_volume, b=before, a=len(df),
        )

    return df.reset_index(drop=True)


def time_train_test_split(
    df: pd.DataFrame,
    train_frac: float = 0.7,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Разбить датасет по времени (oldest train_frac, newest 1-train_frac)."""
    df_sorted = df.sort_values("close_ts").reset_index(drop=True)
    n_train = int(len(df_sorted) * train_frac)
    train = df_sorted.iloc[:n_train].copy()
    test = df_sorted.iloc[n_train:].copy()
    logger.info(
        "Time split: train={t} (close_ts до {tt}), test={te} (close_ts от {tf})",
        t=len(train),
        te=len(test),
        tt=pd.to_datetime(train["close_ts"].max(), unit="s") if len(train) else None,
        tf=pd.to_datetime(test["close_ts"].min(), unit="s") if len(test) else None,
    )
    return train, test
