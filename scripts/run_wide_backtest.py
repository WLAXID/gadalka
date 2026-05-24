"""Широкий бэктест — 4 параллельных среза на одном датасете.

Цели (см. plans/phase-2-wide-backtest.md):
- A: horizon sweep — на каком T-X edge ещё жив
- B: volume × horizon heatmap — нужен ли min_volume фильтр
- C: live-style simulation — реальная дневная концентрация ставок
- D: price band grid — sweet spot для [low, high]

Запуск::

    python scripts/run_wide_backtest.py

Все результаты в data/wide_backtest/*.parquet.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from loguru import logger  # noqa: E402

from src.backtest.costs import CostModel  # noqa: E402
from src.backtest.metrics import compute_metrics  # noqa: E402


# ============================================================
# Параметры срезов
# ============================================================

# Срез A — horizon sweep (часы до резолва)
HORIZONS_HOURS_A = [1, 3, 6, 12, 24, 48, 72, 120, 168, 336, 720, 1440, 2160]
# = T-1h, 3h, 6h, 12h, 24h, 2d, 3d, 5d, 7d, 14d, 30d, 60d, 90d

# Срез B — volume buckets × подмножество horizons
VOLUME_BUCKETS = [
    ("a_<1k",     0,        1_000),
    ("b_1k-10k",  1_000,    10_000),
    ("c_10k-100k", 10_000,  100_000),
    ("d_100k-1M", 100_000,  1_000_000),
    ("e_>1M",     1_000_000, float("inf")),
]
HORIZONS_HOURS_B = [3, 24, 72, 168]  # 3h, 24h, 3d, 7d

# Срез C — live-style sim
ENTRY_HORIZON_DAYS_C = [1, 3, 7, 14]
MIN_VOLUME_C = [0, 1_000, 10_000]

# Срез D — price band grid (после выбора best horizon/volume из A+B)
LOW_GRID = [0.45, 0.50, 0.55, 0.60, 0.65, 0.70]
HIGH_GRID = [0.80, 0.85, 0.90, 0.95]

OUT_DIR = ROOT / "data" / "wide_backtest"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# Подключение и универсальная сборка датасета
# ============================================================

def connect() -> duckdb.DuckDBPyConnection:
    """DuckDB с зарегистрированными view-ами на parquet.

    Объединяем оба источника:
    - prices_history/    — плотный (часовой) на last 30 days
    - prices_history_full/ — разрежённый (daily) на полную историю до 18 мес
    UNION даёт DISTINCT автоматически — дубли по (token_id, t) схлопываются.
    """
    con = duckdb.connect()
    con.execute(
        f"CREATE VIEW markets AS SELECT * FROM "
        f"read_parquet('{ROOT}/data/raw/markets_*.parquet', union_by_name=true)"
    )
    full_dir = ROOT / "data" / "raw" / "prices_history_full"
    if full_dir.exists() and any(full_dir.glob("*.parquet")):
        con.execute(
            f"""CREATE VIEW prices_history AS
            SELECT condition_id, token_id, outcome, t, p FROM
              read_parquet('{ROOT}/data/raw/prices_history/*.parquet',
                           union_by_name=true)
            UNION
            SELECT condition_id, token_id, outcome, t, p FROM
              read_parquet('{ROOT}/data/raw/prices_history_full/*.parquet',
                           union_by_name=true)
            """
        )
    else:
        con.execute(
            f"CREATE VIEW prices_history AS SELECT * FROM "
            f"read_parquet('{ROOT}/data/raw/prices_history/*.parquet', "
            f"union_by_name=true)"
        )
    return con


def build_market_close(con: duckdb.DuckDBPyConnection) -> None:
    """Базовый view: один рынок = одна строка с close_ts и volume/liquidity."""
    con.execute(
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


def build_dataset_for_horizons(
    con: duckdb.DuckDBPyConnection,
    horizons_hours: list[int],
) -> pd.DataFrame:
    """Один датасет с колонками price_yes_h{X} для каждого horizon из списка.

    ASOF JOIN: для каждого рынка берём цену в момент close_ts - X*3600.
    """
    build_market_close(con)

    # Per-horizon view
    for h in horizons_hours:
        secs = h * 3600
        con.execute(
            f"""
            CREATE OR REPLACE TEMP VIEW price_h{h} AS
            SELECT
              mc.condition_id,
              p.p AS price_yes_h{h}
            FROM market_close mc
            ASOF JOIN prices_history p
              ON p.token_id = mc.token_id_yes
              AND p.t <= mc.close_ts - {secs}
            """
        )

    select_cols = [
        "mc.condition_id",
        "mc.token_id_yes",
        "mc.resolved_yes",
        "mc.volume",
        "mc.liquidity",
        "mc.open_ts",
        "mc.close_ts",
        "(mc.close_ts - mc.open_ts) / 86400.0 AS lifetime_days",
        "mc.n_history_points",
    ]
    join_clauses = []
    for h in horizons_hours:
        select_cols.append(f"ph{h}.price_yes_h{h}")
        join_clauses.append(
            f"LEFT JOIN price_h{h} ph{h} ON ph{h}.condition_id = mc.condition_id"
        )

    q = f"""
        SELECT {', '.join(select_cols)}
        FROM market_close mc
        {' '.join(join_clauses)}
        ORDER BY mc.close_ts
    """
    return con.execute(q).df()


# ============================================================
# Общий движок: применить (low, high, horizon, min_vol, max_vol) к датасету
# ============================================================

def run_one_config(
    df: pd.DataFrame,
    *,
    low: float,
    high: float,
    horizon_h: int,
    min_volume: float | None = None,
    max_volume: float | None = None,
    cost: CostModel,
) -> tuple[pd.DataFrame, dict]:
    """Вернуть (trades_df, metrics) для одной конфигурации."""
    price_col = f"price_yes_h{horizon_h}"
    if price_col not in df.columns:
        raise KeyError(f"Колонка {price_col} не в датасете")

    mask = (
        df[price_col].notna()
        & (df[price_col] >= low)
        & (df[price_col] < high)
    )
    if min_volume is not None:
        mask &= df["volume"] >= min_volume
    if max_volume is not None:
        mask &= df["volume"] < max_volume

    selected = df[mask].copy()
    if len(selected) == 0:
        return pd.DataFrame(), {"n_trades": 0, "ev_per_bet": None,
                                "ev_per_dollar": None, "sharpe": None,
                                "win_rate": None, "total_pnl": 0.0,
                                "max_dd": None, "max_dd_pct": None,
                                "avg_buy_cost": None}

    selected["entry_price"] = selected[price_col]
    selected["buy_cost"] = selected["entry_price"].apply(cost.effective_buy_price)
    selected["payout"] = selected["resolved_yes"].astype(int).astype(float)
    selected["pnl"] = selected.apply(
        lambda r: cost.realize_pnl(r["buy_cost"], r["payout"]), axis=1
    )
    metrics = compute_metrics(selected)
    return selected, metrics


# ============================================================
# Срез A — Horizon sweep
# ============================================================

def slice_A(df: pd.DataFrame, cost: CostModel) -> pd.DataFrame:
    rows = []
    for h in HORIZONS_HOURS_A:
        col = f"price_yes_h{h}"
        if col not in df.columns:
            continue
        _, m = run_one_config(df, low=0.50, high=0.85, horizon_h=h, cost=cost)
        m["horizon_h"] = h
        m["horizon_label"] = _hrs_label(h)
        m["coverage"] = float(df[col].notna().mean())
        rows.append(m)
    return pd.DataFrame(rows)


def _hrs_label(h: int) -> str:
    if h < 24:
        return f"T-{h}h"
    d = h / 24
    if d.is_integer():
        return f"T-{int(d)}d"
    return f"T-{d:.1f}d"


# ============================================================
# Срез B — Volume × Horizon
# ============================================================

def slice_B(df: pd.DataFrame, cost: CostModel) -> pd.DataFrame:
    rows = []
    for h in HORIZONS_HOURS_B:
        for bucket_name, vmin, vmax in VOLUME_BUCKETS:
            _, m = run_one_config(
                df, low=0.50, high=0.85, horizon_h=h,
                min_volume=vmin, max_volume=vmax, cost=cost,
            )
            m["horizon_h"] = h
            m["horizon_label"] = _hrs_label(h)
            m["volume_bucket"] = bucket_name
            m["v_min"] = vmin
            m["v_max"] = vmax if vmax != float("inf") else None
            rows.append(m)
    return pd.DataFrame(rows)


# ============================================================
# Срез C — Live-style simulation
# ============================================================

def slice_C(con: duckdb.DuckDBPyConnection, cost: CostModel) -> pd.DataFrame:
    """Day-by-day симуляция как у paper-trader.

    Для каждой пары (entry_horizon_days, min_volume) собираем все
    рынки которые в какой-то момент были в окне T-X и в полосе [0.50, 0.85],
    регистрируем entry на самую раннюю точку входа.
    """
    summary_rows = []
    for ehd in ENTRY_HORIZON_DAYS_C:
        for min_vol in MIN_VOLUME_C:
            window_secs = ehd * 86400
            q = f"""
                WITH first_entry AS (
                  SELECT
                    mc.condition_id,
                    mc.token_id_yes,
                    mc.resolved_yes,
                    mc.volume,
                    mc.close_ts,
                    MIN(p.t) AS entry_ts,
                    ARG_MIN(p.p, p.t) AS entry_price
                  FROM market_close mc
                  JOIN prices_history p
                    ON p.token_id = mc.token_id_yes
                    AND p.t BETWEEN mc.close_ts - {window_secs} AND mc.close_ts
                    AND p.p >= 0.50 AND p.p < 0.85
                  WHERE mc.volume >= {min_vol}
                  GROUP BY 1, 2, 3, 4, 5
                )
                SELECT
                  condition_id,
                  resolved_yes,
                  volume,
                  close_ts,
                  entry_ts,
                  entry_price,
                  DATE_TRUNC('day', to_timestamp(entry_ts)) AS entry_day
                FROM first_entry
                ORDER BY entry_ts
            """
            trades = con.execute(q).df()
            if len(trades) == 0:
                summary_rows.append({
                    "entry_horizon_days": ehd,
                    "min_volume": min_vol,
                    "n_trades": 0,
                })
                continue

            trades["buy_cost"] = trades["entry_price"].apply(cost.effective_buy_price)
            trades["payout"] = trades["resolved_yes"].astype(int).astype(float)
            trades["pnl"] = trades.apply(
                lambda r: cost.realize_pnl(r["buy_cost"], r["payout"]), axis=1
            )
            m = compute_metrics(trades)

            # Дневная концентрация
            per_day = trades.groupby("entry_day").size()
            m["entry_horizon_days"] = ehd
            m["min_volume"] = min_vol
            m["n_days"] = int(per_day.shape[0])
            m["trades_per_day_median"] = float(per_day.median())
            m["trades_per_day_p95"] = float(per_day.quantile(0.95))
            m["trades_per_day_max"] = int(per_day.max())
            summary_rows.append(m)

            # Сохраним timeline для лучшей конфы для notebook
            if ehd == 7 and min_vol == 0:
                trades.to_parquet(
                    OUT_DIR / "C_live_sim_h7d_v0_trades.parquet", index=False
                )

    return pd.DataFrame(summary_rows)


# ============================================================
# Срез D — Price band grid
# ============================================================

def slice_D(df: pd.DataFrame, cost: CostModel, *, horizon_h: int,
            min_volume: float | None) -> pd.DataFrame:
    """Grid по [low, high] при фиксированных best horizon/volume."""
    rows = []
    for low in LOW_GRID:
        for high in HIGH_GRID:
            if high <= low:
                continue
            _, m = run_one_config(
                df, low=low, high=high, horizon_h=horizon_h,
                min_volume=min_volume, cost=cost,
            )
            m["low"] = low
            m["high"] = high
            m["horizon_h"] = horizon_h
            m["min_volume"] = min_volume
            rows.append(m)
    return pd.DataFrame(rows)


# ============================================================
# Main
# ============================================================

def main() -> None:
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)

    logger.info("=== Widely backtest start ===")
    logger.info(f"Output: {OUT_DIR}")

    con = connect()

    # ---- Базовый датасет на все horizons из A (хватит и для B/D)
    all_horizons = sorted(set(HORIZONS_HOURS_A + HORIZONS_HOURS_B))
    logger.info(f"Build dataset for {len(all_horizons)} horizons...")
    df = build_dataset_for_horizons(con, all_horizons)
    logger.info(f"Dataset: {len(df)} markets, "
                f"period {pd.to_datetime(df['close_ts'].min(), unit='s').date()} → "
                f"{pd.to_datetime(df['close_ts'].max(), unit='s').date()}")

    cost = CostModel.realistic()

    # ====== Срез A ======
    logger.info("Slice A — horizon sweep")
    a = slice_A(df, cost)
    a.to_parquet(OUT_DIR / "A_horizon_sweep.parquet", index=False)
    print("\n=== Slice A: Horizon sweep (H1 [0.50,0.85], realistic costs) ===")
    print(a[["horizon_label", "horizon_h", "coverage", "n_trades", "win_rate",
             "ev_per_bet", "ev_per_dollar", "sharpe", "total_pnl"]].to_string(index=False))

    # ====== Срез B ======
    logger.info("Slice B — volume × horizon")
    b = slice_B(df, cost)
    b.to_parquet(OUT_DIR / "B_volume_horizon.parquet", index=False)
    print("\n=== Slice B: Volume × Horizon ===")
    print(b[["horizon_label", "volume_bucket", "n_trades", "win_rate",
             "ev_per_bet", "ev_per_dollar", "sharpe"]].to_string(index=False))

    # ====== Срез C ======
    logger.info("Slice C — live-style simulation")
    c = slice_C(con, cost)
    c.to_parquet(OUT_DIR / "C_live_sim.parquet", index=False)
    print("\n=== Slice C: Live-style simulation ===")
    show_c = c[["entry_horizon_days", "min_volume", "n_trades", "n_days",
                "trades_per_day_median", "trades_per_day_p95", "trades_per_day_max",
                "ev_per_bet", "ev_per_dollar", "sharpe", "total_pnl"]]
    print(show_c.to_string(index=False))

    # ====== Срез D ======
    # Выбираем best horizon + min_volume из A+B
    a_valid = a.dropna(subset=["ev_per_dollar"])
    if len(a_valid) > 0:
        best_h_row = a_valid.loc[a_valid["ev_per_dollar"].idxmax()]
        best_h = int(best_h_row["horizon_h"])
    else:
        best_h = 24
    # min_volume — берём ту что даёт max ev_per_dollar на best_h
    b_at_best = b[(b["horizon_h"] == best_h)].dropna(subset=["ev_per_dollar"])
    if len(b_at_best) > 0:
        best_bucket = b_at_best.loc[b_at_best["ev_per_dollar"].idxmax()]
        best_min_v = float(best_bucket["v_min"])
    else:
        best_min_v = 0.0
    logger.info(f"Slice D — grid at horizon={best_h}h, min_volume={best_min_v}")
    d = slice_D(df, cost, horizon_h=best_h, min_volume=best_min_v)
    d.to_parquet(OUT_DIR / "D_price_grid.parquet", index=False)
    print(f"\n=== Slice D: Price band grid (horizon={_hrs_label(best_h)}, "
          f"min_volume=${best_min_v:g}) ===")
    print(d[["low", "high", "n_trades", "win_rate", "ev_per_bet",
             "ev_per_dollar", "sharpe", "total_pnl"]].to_string(index=False))

    # ====== Summary JSON ======
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "n_markets": int(len(df)),
        "period_start": pd.to_datetime(df["close_ts"].min(), unit="s").isoformat(),
        "period_end": pd.to_datetime(df["close_ts"].max(), unit="s").isoformat(),
        "best_horizon_from_A": best_h,
        "best_horizon_label": _hrs_label(best_h),
        "best_min_volume_from_B": best_min_v,
    }
    import json
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    logger.info(f"💾 Saved summary: {OUT_DIR / 'summary.json'}")
    logger.info("=== Done ===")


if __name__ == "__main__":
    main()
