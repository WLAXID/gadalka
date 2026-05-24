"""Stress test: симуляция «реальных» условий поверх backtest trades.

Идея: бэктест показывает EV +20%, но мы знаем что:
- Спред в реальности зависит от volume (1-5% а не fix 1.5%)
- Slippage больше 0
- Cancel rate ненулевой (UMA disputes)
- Selection bias может означать что обычные рынки имеют меньший win-rate

Накачиваем каждый trade реалистичными penalty + Monte Carlo на исход.
1000 прогонов на каждой комбинации параметров → distribution EV.

Запуск::

    python scripts/stress_test.py
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


# ============================================================
# Загрузка trades
# ============================================================

def load_raw_trades() -> pd.DataFrame:
    """Загрузка raw trades без costs — costs накладываем в симуляции."""
    con = duckdb.connect()
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
    secs = 24 * 3600
    df = con.execute(f"""
    WITH mc AS (
      SELECT m.conditionId AS condition_id, m.token_id_yes,
             CAST(m.resolved_yes AS BOOL) AS resolved_yes,
             CAST(m.volumeNum AS DOUBLE)  AS volume,
             CAST(MAX(p.t) AS BIGINT)     AS close_ts
      FROM markets m
      JOIN prices_history p ON p.condition_id = m.conditionId
      WHERE m.token_id_yes IS NOT NULL AND m.resolved_yes IS NOT NULL
      GROUP BY 1,2,3,4
    )
    SELECT mc.condition_id, mc.resolved_yes, mc.volume,
           p.p AS entry_price
    FROM mc
    ASOF JOIN prices_history p
      ON p.token_id = mc.token_id_yes
      AND p.t <= mc.close_ts - {secs}
    WHERE p.p >= 0.50 AND p.p < 0.85 AND mc.volume >= 10000
    """).df()
    con.close()
    return df


# ============================================================
# Volume-зависимый спред
# ============================================================

def spread_for_volume(v: float) -> float:
    if v < 1_000:
        return 0.05
    if v < 10_000:
        return 0.03
    if v < 100_000:
        return 0.02
    return 0.01


# ============================================================
# Одна симуляция всей выборки
# ============================================================

def simulate_one(
    df: pd.DataFrame,
    rng: np.random.Generator,
    *,
    cancel_rate: float,
    flip_rate: float,
    slippage_max: float,
    fee_rate: float = 0.02,
) -> dict:
    n = len(df)
    entries = df["entry_price"].to_numpy()
    volumes = df["volume"].to_numpy()
    outcomes = df["resolved_yes"].to_numpy(dtype=bool)

    # Random per-trade penalties
    slips = rng.uniform(0, slippage_max, size=n)
    spreads = np.array([spread_for_volume(v) for v in volumes])
    buy_cost = entries * (1 + spreads / 2 + slips)

    # Flip некоторые исходы (имитация что обычный рынок не такой чёткий)
    flip_mask = rng.random(n) < flip_rate
    outcomes_sim = np.where(flip_mask, ~outcomes, outcomes)

    # Cancel — рынок завис, refund по entry-mid (теряем spread×0.5)
    cancel_mask = rng.random(n) < cancel_rate
    payout = np.where(outcomes_sim, 1.0, 0.0)
    # Если cancel — payout ≈ buy_cost - spread/2 (refund mid, но мы платили ask)
    cancel_payout = entries  # refund по mid-price
    payout = np.where(cancel_mask, cancel_payout, payout)

    profit = payout - buy_cost
    # Fee только на положительной prtail
    fees = np.where(profit > 0, profit * fee_rate, 0.0)
    pnl = profit - fees

    total_invested = buy_cost.sum()
    total_pnl = pnl.sum()
    return {
        "n": n,
        "ev_dollar": total_pnl / total_invested,
        "total_pnl": total_pnl,
        "win_rate": (pnl > 0).mean(),
    }


# ============================================================
# Сценарий = (cancel_rate, flip_rate, slippage_max) × N симуляций
# ============================================================

def run_scenario(df: pd.DataFrame, label: str, params: dict,
                 n_sims: int = 1000) -> dict:
    seed_base = abs(hash(label)) % 2**31
    evs = np.empty(n_sims)
    for i in range(n_sims):
        rng = np.random.default_rng(seed_base + i)
        r = simulate_one(df, rng, **params)
        evs[i] = r["ev_dollar"]
    return {
        "label": label,
        "params": params,
        "n_sims": n_sims,
        "ev_mean": float(np.mean(evs)),
        "ev_median": float(np.median(evs)),
        "ev_p05": float(np.percentile(evs, 5)),
        "ev_p25": float(np.percentile(evs, 25)),
        "ev_p75": float(np.percentile(evs, 75)),
        "ev_p95": float(np.percentile(evs, 95)),
        "prob_positive": float((evs > 0).mean()),
    }


# ============================================================
# Main
# ============================================================

SCENARIOS = [
    # label, params dict
    ("optimistic — низкие penalty", {
        "cancel_rate": 0.02, "flip_rate": 0.05, "slippage_max": 0.005,
    }),
    ("realistic — base case", {
        "cancel_rate": 0.05, "flip_rate": 0.10, "slippage_max": 0.015,
    }),
    ("realistic+ — выше отказы", {
        "cancel_rate": 0.10, "flip_rate": 0.15, "slippage_max": 0.020,
    }),
    ("pessimistic — рынок не такой как выборка", {
        "cancel_rate": 0.10, "flip_rate": 0.25, "slippage_max": 0.030,
    }),
    ("worst — outlier scenario", {
        "cancel_rate": 0.15, "flip_rate": 0.40, "slippage_max": 0.040,
    }),
]


def main() -> None:
    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda x: f"{x:+.4f}")

    t0 = time.time()
    print("=" * 70, flush=True)
    print("  Stress test — Monte Carlo over our backtest trades", flush=True)
    print("=" * 70, flush=True)
    print("[load] Загружаю trades...", flush=True)
    df = load_raw_trades()
    print(f"[load] {len(df):,} trades, средний volume ${df['volume'].mean():,.0f}",
          flush=True)
    print(f"[load] Avg entry_price: {df['entry_price'].mean():.4f}", flush=True)
    print(f"[load] Empirical win-rate (без manipulations): "
          f"{df['resolved_yes'].mean():.4f}", flush=True)

    n_sims = 2000
    print(f"\n[run] {len(SCENARIOS)} сценариев × {n_sims} симуляций = "
          f"{len(SCENARIOS)*n_sims:,} прогонов  (4 worker'а параллельно)\n",
          flush=True)

    results = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(run_scenario, df, lbl, p, n_sims): lbl
                for lbl, p in SCENARIOS}
        for fut in as_completed(futs):
            try:
                r = fut.result()
                results.append(r)
                print(f"  [done] {r['label']}", flush=True)
            except Exception as e:
                print(f"  [err]  {futs[fut]}: {e}", flush=True)

    print(f"\n{'='*70}\n  RESULTS\n{'='*70}\n", flush=True)
    rep = []
    for r in sorted(results, key=lambda x: -x["ev_median"]):
        rep.append({
            "scenario": r["label"],
            "cancel": r["params"]["cancel_rate"],
            "flip": r["params"]["flip_rate"],
            "slip_max": r["params"]["slippage_max"],
            "EV_median": r["ev_median"],
            "EV_p05": r["ev_p05"],
            "EV_p95": r["ev_p95"],
            "P(EV>0)": r["prob_positive"],
        })
    print(pd.DataFrame(rep).to_string(index=False))

    print("\n" + "=" * 70)
    print("  ИНТЕРПРЕТАЦИЯ")
    print("=" * 70)
    print("""
- P(EV>0) — вероятность что в данном сценарии стратегия в плюсе.
  > 95%: статистически уверенно работает.
  60-90%: работает в большинстве случаев, но есть риск.
  < 50%: edge неустойчив, скорее всего бесполезен.

- EV_median — типичный исход. EV_p05/p95 — границы 90% CI.

- 'flip_rate' — главный честный параметр. Он отвечает на вопрос:
  «если бы наша выборка не была selection-biased, а часть наших
  "лёгких" побед оказалась реальными "трудными" с другим исходом,
  что бы было?»

Базовый case (flip 10%, cancel 5%): это что я ожидаю в live при
капитале $100-1000.

Pessimistic (flip 25%): что если selection bias жёстче и каждая
4-я "победа" в реальности другая? — это реальный риск.
""", flush=True)

    print(f"\nDone за {time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
