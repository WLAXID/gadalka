"""Глубокий skeptical анализ H1 — ОДИН процесс + ThreadPool.

Архитектура (после инцидента с RAM 99% на ProcessPoolExecutor):
- Один Python процесс, один DuckDB connection
- Один DataFrame trades загружен в память (shared)
- ThreadPoolExecutor запускает checks параллельно над shared DataFrame
- Параллель работает: GIL отпускается на numpy/pandas/duckdb
- RAM-overhead минимальный (DataFrame копируется только если check его модифицирует)

Запуск::

    python scripts/deep_analysis.py
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.backtest.costs import CostModel  # noqa: E402


def setup_db(con: duckdb.DuckDBPyConnection) -> None:
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
    con.execute(
        f"CREATE VIEW markets AS SELECT * FROM "
        f"read_parquet('{ROOT}/data/raw/markets_*.parquet', union_by_name=true)"
    )


def load_trades(con: duckdb.DuckDBPyConnection,
                horizon_hours: int = 24, min_volume: float = 10_000.0,
                low: float = 0.50, high: float = 0.85) -> pd.DataFrame:
    secs = horizon_hours * 3600
    q = f"""
    WITH mc AS (
      SELECT m.conditionId AS condition_id, m.token_id_yes,
             m.question, m.slug,
             CAST(m.resolved_yes AS BOOL) AS resolved_yes,
             CAST(m.volumeNum AS DOUBLE)  AS volume,
             CAST(MAX(p.t) AS BIGINT)     AS close_ts
      FROM markets m
      JOIN prices_history p ON p.condition_id = m.conditionId
      WHERE m.token_id_yes IS NOT NULL AND m.resolved_yes IS NOT NULL
      GROUP BY 1,2,3,4,5,6
    ),
    ep AS (
      SELECT mc.*, p.p AS entry_price
      FROM mc
      ASOF JOIN prices_history p
        ON p.token_id = mc.token_id_yes
        AND p.t <= mc.close_ts - {secs}
    ),
    fp AS (
      SELECT mc.condition_id, p.p AS final_price
      FROM mc
      ASOF JOIN prices_history p
        ON p.token_id = mc.token_id_yes
        AND p.t <= mc.close_ts
    )
    SELECT ep.condition_id, ep.question, ep.slug,
           ep.resolved_yes, ep.volume, ep.close_ts,
           ep.entry_price, fp.final_price
    FROM ep LEFT JOIN fp USING (condition_id)
    WHERE ep.entry_price IS NOT NULL
      AND ep.entry_price >= {low}
      AND ep.entry_price < {high}
      AND ep.volume >= {min_volume}
    ORDER BY ep.close_ts
    """
    df = con.execute(q).df()
    cost = CostModel.realistic()
    df["buy_cost"] = df["entry_price"].apply(cost.effective_buy_price)
    df["payout"] = df["resolved_yes"].astype(int).astype(float)
    df["pnl"] = df.apply(
        lambda r: cost.realize_pnl(r["buy_cost"], r["payout"]), axis=1)
    df["close_date"] = pd.to_datetime(df["close_ts"], unit="s").dt.date
    df["close_week"] = pd.to_datetime(df["close_ts"], unit="s").dt.to_period("W")
    return df


# ============================================================
# Checks — каждая возвращает строку, чтобы потом печатать в порядке
# ============================================================

def check_A_walkforward(df: pd.DataFrame) -> str:
    out = ["=== A. Walk-forward sliding windows (8 окон, overlap 50%) ==="]
    if len(df) < 60:
        out.append("Недостаточно ставок")
        return "\n".join(out)
    df = df.sort_values("close_ts").reset_index(drop=True)
    n = len(df)
    window = max(1, n // 8)
    step = max(1, window // 2)
    rows = []
    for i, start in enumerate(range(0, n - window + 1, step)):
        end = start + window
        seg = df.iloc[start:end]
        ev_d = seg["pnl"].sum() / seg["buy_cost"].sum()
        win = seg["resolved_yes"].mean()
        ps = pd.to_datetime(seg["close_ts"].min(), unit="s").date()
        pe = pd.to_datetime(seg["close_ts"].max(), unit="s").date()
        rows.append({"i": i, "n": len(seg), "period": f"{ps}..{pe}",
                    "ev_$": round(ev_d, 4), "win": round(win, 3)})
    rep = pd.DataFrame(rows)
    out.append(rep.to_string(index=False))
    pos_pct = (rep["ev_$"] > 0).mean() * 100
    out.append(f"\n% окон с EV/$ > 0: {pos_pct:.1f}%")
    out.append(f"Spread EV: [{rep['ev_$'].min():.4f}, {rep['ev_$'].max():.4f}]")
    if rep["ev_$"].max() - rep["ev_$"].min() > 0.5:
        out.append("⚠ Spread > 50pp — edge нестабилен во времени")
    return "\n".join(out)


def check_B_weekly_pnl(df: pd.DataFrame) -> str:
    out = ["=== B. Per-week PnL distribution ==="]
    if len(df) == 0:
        out.append("Нет данных")
        return "\n".join(out)
    weekly = df.groupby("close_week").agg(
        n=("pnl", "size"), pnl=("pnl", "sum"), invested=("buy_cost", "sum"),
    )
    weekly["ev_$"] = weekly["pnl"] / weekly["invested"]
    out.append(weekly.tail(20).to_string())
    win_weeks = (weekly["pnl"] > 0).mean() * 100
    out.append(f"\nВсего недель: {len(weekly)}")
    out.append(f"% прибыльных: {win_weeks:.1f}%")
    out.append(f"Mean PnL/week: ${weekly['pnl'].mean():+.2f}")
    out.append(f"Median PnL/week: ${weekly['pnl'].median():+.2f}")
    out.append(f"Worst week: ${weekly['pnl'].min():+.2f}")
    return "\n".join(out)


def check_C_top_bottom(df: pd.DataFrame) -> str:
    out = ["=== C. Top/bottom 10 trades ==="]
    if len(df) < 20:
        out.append("Недостаточно")
        return "\n".join(out)
    df_s = df.copy()
    df_s["q"] = df_s["question"].str[:55]
    cols = ["q", "entry_price", "final_price", "resolved_yes", "pnl", "volume"]
    out.append("--- Top-10 winners ---")
    out.append(df_s.nlargest(10, "pnl")[cols].to_string(index=False))
    out.append("\n--- Bottom-10 losers ---")
    out.append(df_s.nsmallest(10, "pnl")[cols].to_string(index=False))
    return "\n".join(out)


def check_D_wilson_ci(df: pd.DataFrame) -> str:
    out = ["=== D. Wilson 95% CI ==="]
    if len(df) == 0:
        out.append("Нет данных")
        return "\n".join(out)
    n = len(df)
    k = int(df["resolved_yes"].sum())
    p = k / n
    z = 1.96
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    lo, hi = center - half, center + half
    avg_buy = df["buy_cost"].mean()
    out.append(f"n={n}, wins={k}, win-rate={p:.4f}")
    out.append(f"95% CI: [{lo:.4f}, {hi:.4f}]")
    out.append(f"Avg buy_cost ≈ break-even: {avg_buy:.4f}")
    margin_pp = (p - avg_buy) * 100
    out.append(f"Margin к break-even: {margin_pp:+.2f}pp")
    if lo > avg_buy:
        out.append("✅ CI lower bound > break-even — edge статистически значим")
    else:
        out.append("⚠ CI lower bound НЕ выше break-even — edge может быть шумом")
    return "\n".join(out)


def check_E_one_threshold(con_factory, low: float) -> dict:
    """ОДИН порог low — отдельная task для ThreadPool."""
    con = con_factory()
    df_x = load_trades(con, low=low)
    con.close()
    if len(df_x) < 20:
        return {"low": low, "n": len(df_x), "ev_$": None, "win": None}
    ev = df_x["pnl"].sum() / df_x["buy_cost"].sum()
    return {"low": low, "n": len(df_x),
            "ev_$": round(ev, 4),
            "win": round(df_x["resolved_yes"].mean(), 3)}


def aggregate_E(rows: list[dict]) -> str:
    out = ["=== E. Sensitivity к threshold low ==="]
    rows_sorted = sorted(rows, key=lambda r: r["low"])
    out.append(pd.DataFrame(rows_sorted).to_string(index=False))
    out.append("Если EV резко меняется при +/-2pp вокруг 0.50 — overfit.")
    return "\n".join(out)


def check_G_price_drift(df: pd.DataFrame) -> str:
    out = ["=== G. Entry → Final price drift ==="]
    if len(df) == 0:
        out.append("Нет данных")
        return "\n".join(out)
    df = df.dropna(subset=["final_price"]).copy()
    df["drift"] = df["final_price"] - df["entry_price"]
    win = df[df["resolved_yes"] == True]
    loss = df[df["resolved_yes"] == False]
    out.append(f"n trades с final_price: {len(df)}")
    out.append(f"Mean drift: {df['drift'].mean():+.4f}")
    out.append(f"  winners drift: {win['drift'].mean():+.4f}")
    out.append(f"  losers drift:  {loss['drift'].mean():+.4f}")
    out.append("\nDistribution drift:")
    out.append(df["drift"].describe().to_string())
    return "\n".join(out)


def check_H_drawdown(df: pd.DataFrame) -> str:
    out = ["=== H. Drawdown ==="]
    if len(df) == 0:
        out.append("Нет данных")
        return "\n".join(out)
    df = df.sort_values("close_ts").reset_index(drop=True).copy()
    df["cum_pnl"] = df["pnl"].cumsum()
    df["running_max"] = df["cum_pnl"].cummax()
    df["dd"] = df["cum_pnl"] - df["running_max"]
    out.append(f"n trades: {len(df)}")
    out.append(f"Total cum PnL: ${df['cum_pnl'].iloc[-1]:+.2f}")
    out.append(f"Max drawdown: ${df['dd'].min():+.2f}")
    pct = df["dd"].min() / df["buy_cost"].sum() * 100
    out.append(f"Max DD как %% от total invested: {pct:+.2f}%%")
    daily = df.groupby("close_date")["pnl"].sum()
    out.append("\nDaily PnL describe:")
    out.append(daily.describe().to_string())
    return "\n".join(out)


# ============================================================
# Main
# ============================================================

def main() -> None:
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.max_rows", 50)
    pd.set_option("display.float_format", lambda x: f"{x:.4f}")

    t0 = time.time()
    print("=" * 70, flush=True)
    print("  Deep skeptical analysis — single process + ThreadPool", flush=True)
    print("=" * 70, flush=True)

    main_con = duckdb.connect()
    setup_db(main_con)

    # Factory для thread-specific cursors (для check_E)
    def con_factory():
        c = duckdb.connect()
        setup_db(c)
        return c

    print("[load] Загружаю trades...", flush=True)
    df = load_trades(main_con)
    ram_mb = df.memory_usage(deep=True).sum() / 1024 / 1024
    print(f"[load] DataFrame: {len(df):,} trades, {ram_mb:.1f} MB", flush=True)

    # Все checks плоско. df-only — на shared DataFrame, E_low_X — на свой
    # connection. max_workers=3: пик ~9 GB (3 параллельных DuckDB UNION),
    # free сейчас 23 GB → запас 14 GB на систему (>10%).
    df_only_checks = [
        ("A", check_A_walkforward),
        ("B", check_B_weekly_pnl),
        ("C", check_C_top_bottom),
        ("D", check_D_wilson_ci),
        ("G", check_G_price_drift),
        ("H", check_H_drawdown),
    ]
    E_LOWS = [0.46, 0.48, 0.50, 0.51, 0.52, 0.54, 0.55, 0.60, 0.65]

    total = len(df_only_checks) + len(E_LOWS)
    print(f"[run] {total} tasks (6 df-only + 9 E-subtasks), 3 workers...",
          flush=True)

    results: dict[str, str] = {}
    e_rows: list[dict] = []

    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {}
        for name, fn in df_only_checks:
            futs[ex.submit(fn, df)] = ("df", name)
        for low in E_LOWS:
            futs[ex.submit(check_E_one_threshold, con_factory, low)] = (
                "e", f"E[low={low}]")

        for fut in as_completed(futs):
            kind, name = futs[fut]
            try:
                r = fut.result()
                if kind == "df":
                    results[name] = r
                else:
                    e_rows.append(r)
                print(f"  [done] {name}", flush=True)
            except Exception as e:
                msg = f"=== {name} — ERROR ===\n{type(e).__name__}: {e}"
                if kind == "df":
                    results[name] = msg
                print(f"  [err]  {name}: {e}", flush=True)

    results["E"] = aggregate_E(e_rows)

    print(f"\n{'='*70}\n  RESULTS\n{'='*70}", flush=True)
    for name in ["A", "B", "C", "D", "E", "G", "H"]:
        print()
        print(results.get(name, f"=== {name} — пусто ==="), flush=True)

    main_con.close()
    print(f"\n{'='*70}\n  Done за {time.time()-t0:.1f}s\n{'='*70}", flush=True)


if __name__ == "__main__":
    main()
