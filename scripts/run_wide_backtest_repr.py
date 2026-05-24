"""Wide backtest на РЕПРЕЗЕНТАТИВНОМ датасете (sharded markets + new prices).

Использует:
- data/raw/markets_processed_2026-05-24.parquet (54k markets, 95% past-endDate)
- data/raw/prices_history_repr/ (новый prices pull со start_ts для всех)

Это даёт честную картину БЕЗ selection bias. Сравниваем с backtest
на старой выборке (10k markets, 100% future-endDate).

Запуск::

    python scripts/run_wide_backtest_repr.py
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.backtest.costs import CostModel  # noqa: E402


SPREAD = 0.015
SLIP = 0.005
FEE = 0.02


def setup_db(con: duckdb.DuckDBPyConnection) -> None:
    """Только новый sharded markets + repr prices."""
    con.execute(
        f"CREATE VIEW markets AS SELECT * FROM "
        f"read_parquet('{ROOT}/data/raw/markets_processed_2026-05-24.parquet')"
    )
    con.execute(
        f"CREATE VIEW prices_history AS SELECT condition_id, token_id, outcome, t, p FROM "
        f"read_parquet('{ROOT}/data/raw/prices_history_repr/*.parquet', "
        f"union_by_name=true)"
    )


def load_trades(con: duckdb.DuckDBPyConnection,
                horizon_hours: int = 24, min_volume: float = 10_000.0,
                low: float = 0.50, high: float = 0.85) -> pd.DataFrame:
    secs = horizon_hours * 3600
    q = f"""
    WITH mc AS (
      SELECT m.conditionId AS condition_id, m.token_id_yes,
             m.question, m.slug, m.endDate,
             CAST(m.resolved_yes AS BOOL) AS resolved_yes,
             CAST(m.volumeNum AS DOUBLE)  AS volume,
             CAST(MAX(p.t) AS BIGINT)     AS close_ts
      FROM markets m
      JOIN prices_history p ON p.condition_id = m.conditionId
      WHERE m.token_id_yes IS NOT NULL AND m.resolved_yes IS NOT NULL
      GROUP BY 1,2,3,4,5,6,7
    ),
    ep AS (
      SELECT mc.*, p.p AS entry_price
      FROM mc
      ASOF JOIN prices_history p
        ON p.token_id = mc.token_id_yes
        AND p.t <= mc.close_ts - {secs}
    )
    SELECT condition_id, question, slug, endDate, resolved_yes, volume,
           close_ts, entry_price
    FROM ep
    WHERE entry_price IS NOT NULL
      AND entry_price >= {low}
      AND entry_price < {high}
      AND volume >= {min_volume}
    ORDER BY close_ts
    """
    df = con.execute(q).df()
    cost = CostModel.realistic()
    df["buy_cost"] = df["entry_price"].apply(cost.effective_buy_price)
    df["payout"] = df["resolved_yes"].astype(int).astype(float)
    df["pnl"] = df.apply(lambda r: cost.realize_pnl(r["buy_cost"], r["payout"]), axis=1)
    df["close_date"] = pd.to_datetime(df["close_ts"], unit="s").dt.date
    return df


def pnl_yes(entry: np.ndarray, resolved: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    buy = entry * (1 + SPREAD/2 + SLIP)
    payout = resolved.astype(float)
    profit = payout - buy
    fees = np.where(profit > 0, profit * FEE, 0.0)
    return profit - fees, buy


def pnl_no(entry: np.ndarray, resolved: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    buy = (1 - entry) * (1 + SPREAD/2 + SLIP)
    payout = (~resolved).astype(float)
    profit = payout - buy
    fees = np.where(profit > 0, profit * FEE, 0.0)
    return profit - fees, buy


def stats(pnls: np.ndarray, buys: np.ndarray, resolved: np.ndarray) -> dict:
    if len(pnls) == 0:
        return {"n": 0}
    return {
        "n": len(pnls),
        "win_rate": float((pnls > 0).mean()),
        "ev_dollar": float(pnls.sum() / buys.sum()),
        "total_pnl": float(pnls.sum()),
    }


# ============================================================
# Checks
# ============================================================

def check_main_strategies(con: duckdb.DuckDBPyConnection) -> str:
    out = ["=== Main: H1 / H1_INVERSE / H_low / H_all на REPR выборке ==="]
    rows = []

    # Load full trade pool без price filter
    con.execute("DROP VIEW IF EXISTS mc_all")
    con.execute("""
    CREATE TEMP VIEW mc_all AS
    SELECT m.conditionId AS condition_id, m.token_id_yes,
           CAST(m.resolved_yes AS BOOL) AS resolved_yes,
           CAST(m.volumeNum AS DOUBLE) AS volume,
           CAST(MAX(p.t) AS BIGINT) AS close_ts
    FROM markets m
    JOIN prices_history p ON p.condition_id = m.conditionId
    WHERE m.token_id_yes IS NOT NULL AND m.resolved_yes IS NOT NULL
    GROUP BY 1,2,3,4
    """)
    full = con.execute(f"""
    SELECT mc_all.*, p.p AS entry_price
    FROM mc_all ASOF JOIN prices_history p
      ON p.token_id = mc_all.token_id_yes
      AND p.t <= mc_all.close_ts - 86400
    WHERE p.p IS NOT NULL AND mc_all.volume >= 10000
    """).df()
    out.append(f"Total trade pool: {len(full):,} (T-24h, volume>=10k)")

    for label, lo, hi, side in [
        ("H1 [0.50, 0.85] BUY YES",        0.50, 0.85, "yes"),
        ("H1 INVERSE [0.50, 0.85] BUY NO", 0.50, 0.85, "no"),
        ("H_low [0.15, 0.50] BUY NO",       0.15, 0.50, "no"),
        ("H_low_invert [0.15, 0.50] BUY YES", 0.15, 0.50, "yes"),
        ("H_extreme_fav [0.85, 0.99] BUY YES", 0.85, 0.99, "yes"),
        ("H_extreme_dog [0.01, 0.15] BUY YES", 0.01, 0.15, "yes"),
        ("H_all BUY YES (no filter)", 0.0, 1.0, "yes"),
        ("H_all BUY NO (no filter)",  0.0, 1.0, "no"),
    ]:
        sub = full[(full["entry_price"] >= lo) & (full["entry_price"] < hi)]
        if len(sub) == 0:
            rows.append({"strategy": label, "n": 0})
            continue
        e = sub["entry_price"].to_numpy()
        r = sub["resolved_yes"].to_numpy(dtype=bool)
        if side == "yes":
            p, b = pnl_yes(e, r)
        else:
            p, b = pnl_no(e, r)
        s = stats(p, b, r)
        rows.append({"strategy": label, "n": s["n"],
                    "win_rate": round(s["win_rate"], 3),
                    "ev_$": round(s["ev_dollar"], 4),
                    "total_pnl": round(s["total_pnl"], 2)})

    out.append(pd.DataFrame(rows).to_string(index=False))
    return "\n".join(out)


def check_wilson_ci(df: pd.DataFrame) -> str:
    out = ["=== Wilson 95% CI на H1 REPR ==="]
    if len(df) == 0:
        out.append("Нет данных"); return "\n".join(out)
    n = len(df)
    k = int(df["resolved_yes"].sum())
    p = k / n
    z = 1.96
    denom = 1 + z**2/n
    center = (p + z**2/(2*n)) / denom
    half = z * np.sqrt(p*(1-p)/n + z**2/(4*n**2)) / denom
    lo, hi = center - half, center + half
    avg_buy = df["buy_cost"].mean()
    out.append(f"n={n}, wins={k}, win-rate={p:.4f}")
    out.append(f"95% CI: [{lo:.4f}, {hi:.4f}]")
    out.append(f"Break-even (avg buy_cost): {avg_buy:.4f}")
    margin = (p - avg_buy) * 100
    out.append(f"Margin к break-even: {margin:+.2f}pp")
    if lo > avg_buy:
        out.append("✅ CI lower bound > BE — edge статистически значим")
    else:
        out.append("⚠ CI lower bound НЕ выше BE — edge может быть шумом")
    return "\n".join(out)


def check_endate_split(df: pd.DataFrame) -> str:
    out = ["=== Past-endDate vs Future-endDate (snapshot 2026-05-24) ==="]
    if len(df) == 0:
        out.append("Нет данных"); return "\n".join(out)
    now_ts = 1716539400  # 2026-05-24 08:30 UTC
    df = df.copy()
    df["bucket"] = df["close_ts"].apply(lambda t: "past" if t < now_ts else "future")
    rows = []
    for bucket in ["past", "future"]:
        sub = df[df["bucket"] == bucket]
        if len(sub) == 0:
            rows.append({"bucket": bucket, "n": 0}); continue
        rows.append({
            "bucket": bucket,
            "n": len(sub),
            "win_rate": round(sub["resolved_yes"].mean(), 3),
            "ev_$": round(sub["pnl"].sum() / sub["buy_cost"].sum(), 4),
        })
    out.append(pd.DataFrame(rows).to_string(index=False))
    out.append("""
ВАЖНО:
- Старая выборка была 100% future-endDate (только досрочные UMA)
- Новая включает обе категории
- Если past-endDate даёт МЕНЬШИЙ EV — selection bias подтверждён
""")
    return "\n".join(out)


def check_by_year(df: pd.DataFrame) -> str:
    out = ["=== Per-year breakdown ==="]
    if len(df) == 0:
        out.append("Нет данных"); return "\n".join(out)
    df = df.copy()
    df["year"] = pd.to_datetime(df["close_ts"], unit="s").dt.year
    rows = df.groupby("year").apply(lambda g: pd.Series({
        "n": len(g),
        "win_rate": round(g["resolved_yes"].mean(), 3),
        "ev_$": round(g["pnl"].sum() / g["buy_cost"].sum(), 4),
        "total_pnl": round(g["pnl"].sum(), 2),
    }))
    out.append(rows.to_string())
    return "\n".join(out)


def main() -> None:
    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda x: f"{x:.4f}")

    t0 = time.time()
    print("=" * 70, flush=True)
    print("  Wide backtest на РЕПРЕЗЕНТАТИВНОЙ выборке (sharded markets)", flush=True)
    print("=" * 70, flush=True)
    con = duckdb.connect()
    setup_db(con)

    n_markets = con.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
    n_prices = con.execute("SELECT COUNT(*) FROM prices_history").fetchone()[0]
    print(f"[load] markets: {n_markets:,}  prices points: {n_prices:,}", flush=True)

    print("[load] Загружаю H1 trades...", flush=True)
    df_h1 = load_trades(con)
    print(f"[load] H1 [0.50, 0.85] @ T-24h: {len(df_h1):,} trades", flush=True)

    # Параллельные checks
    checks = [
        ("main",   lambda: check_main_strategies(con)),
        ("wilson", lambda: check_wilson_ci(df_h1)),
        ("split",  lambda: check_endate_split(df_h1)),
        ("year",   lambda: check_by_year(df_h1)),
    ]
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=2) as ex:  # DuckDB shared, осторожно
        futs = {ex.submit(fn): name for name, fn in checks}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                results[name] = fut.result()
                print(f"  [done] {name}", flush=True)
            except Exception as e:
                results[name] = f"=== {name} ERROR === {e}"
                print(f"  [err]  {name}: {e}", flush=True)

    print(f"\n{'='*70}\n  RESULTS\n{'='*70}\n", flush=True)
    for name in ["main", "wilson", "split", "year"]:
        print(results.get(name, ""), flush=True)
        print()

    con.close()
    print(f"Done за {time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
