"""Stress test для топ-стратегий из grid search.

Каждую стратегию прогоняем через Monte Carlo с реалистичными penalty
на репрезентативной выборке.

В отличие от первого stress_test (для H1):
- flip_rate ниже (наша выборка уже репрезентативная, без 100% selection bias)
- spread зависит от volume (1-5%)
- cancel_rate реалистичный

Запуск::

    python scripts/stress_test_top_strategies.py
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


FEE = 0.02


def setup_db(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        f"CREATE VIEW markets AS SELECT * FROM "
        f"read_parquet('{ROOT}/data/raw/markets_processed_2026-05-24.parquet')"
    )
    con.execute(
        f"CREATE VIEW prices_history AS SELECT condition_id, token_id, outcome, t, p FROM "
        f"read_parquet('{ROOT}/data/raw/prices_history_repr/*.parquet', "
        f"union_by_name=true)"
    )


def load_pool(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    q = """
    WITH mc AS (
      SELECT m.conditionId AS condition_id, m.token_id_yes,
             m.question, m.endDate,
             CAST(m.resolved_yes AS BOOL) AS resolved_yes,
             CAST(m.volumeNum AS DOUBLE) AS volume,
             CAST(MAX(p.t) AS BIGINT) AS close_ts
      FROM markets m
      JOIN prices_history p ON p.condition_id = m.conditionId
      WHERE m.token_id_yes IS NOT NULL AND m.resolved_yes IS NOT NULL
      GROUP BY 1,2,3,4,5,6
    ),
    e24 AS (SELECT mc.condition_id, p.p AS p_t24h
            FROM mc ASOF JOIN prices_history p
              ON p.token_id = mc.token_id_yes AND p.t <= mc.close_ts - 86400),
    e7d AS (SELECT mc.condition_id, p.p AS p_t7d
            FROM mc ASOF JOIN prices_history p
              ON p.token_id = mc.token_id_yes AND p.t <= mc.close_ts - 604800)
    SELECT mc.condition_id, mc.question, mc.resolved_yes,
           mc.volume, mc.close_ts,
           e24.p_t24h, e7d.p_t7d
    FROM mc
    LEFT JOIN e24 USING (condition_id)
    LEFT JOIN e7d USING (condition_id)
    WHERE mc.volume >= 10000
    """
    df = con.execute(q).df()
    df["drift"] = (df["p_t24h"] - df["p_t7d"])
    return df


def categorize(q: str) -> str:
    if not isinstance(q, str):
        return "other"
    ql = q.lower()
    if any(w in ql for w in ["nba", "nfl", "fifa", "world cup", "olympics",
                              "champion", "uefa", "f1", "ufc", "wimbledon",
                              "tennis", "match", "vs.", " vs ", "premier",
                              "playoff", "finals", "tournament", "league",
                              "roland garros", "wta", "atp", "open"]):
        return "sport"
    if any(w in ql for w in ["bitcoin", "btc", "ethereum", "eth", "crypto",
                              "solana", "sol ", "fdv", "token", "memecoin",
                              "above $"]):
        return "crypto"
    if any(w in ql for w in ["trump", "biden", "election", "vote", "senate",
                              "congress", "president", "putin", "ukraine",
                              "russia", "israel", "iran", "nominee", "primary"]):
        return "politics"
    return "other"


# ============================================================
# Стратегии: каждая возвращает (df_filtered, side='yes'/'no')
# ============================================================

def strat_crypto_momentum(df):
    df = df.copy()
    df["category"] = df["question"].apply(categorize)
    sub = df[(df["category"] == "crypto") & (df["drift"] > 0.10)
             & df["p_t24h"].notna() & df["p_t7d"].notna()]
    return sub, "yes", "p_t24h"


def strat_crypto_underdog(df):
    df = df.copy()
    df["category"] = df["question"].apply(categorize)
    sub = df[(df["category"] == "crypto") & (df["p_t24h"] >= 0.15)
             & (df["p_t24h"] < 0.50)]
    return sub, "yes", "p_t24h"


def strat_longshots(df):
    sub = df[(df["p_t24h"] >= 0.01) & (df["p_t24h"] < 0.10)]
    return sub, "yes", "p_t24h"


def strat_h1_high_vol(df):
    sub = df[(df["volume"] >= 1_000_000) & (df["p_t24h"] >= 0.50)
             & (df["p_t24h"] < 0.85)]
    return sub, "yes", "p_t24h"


def strat_mean_rev_down(df):
    sub = df[(df["drift"] < -0.20) & df["p_t24h"].notna() & df["p_t7d"].notna()]
    return sub, "yes", "p_t24h"


def strat_band_20_30(df):
    sub = df[(df["p_t24h"] >= 0.20) & (df["p_t24h"] < 0.30)]
    return sub, "yes", "p_t24h"


def strat_momentum_yes(df):
    sub = df[(df["drift"] > 0.15) & (df["p_t24h"] < 0.85)
             & df["p_t7d"].notna()]
    return sub, "yes", "p_t24h"


def strat_sport_no(df):
    df = df.copy()
    df["category"] = df["question"].apply(categorize)
    sub = df[(df["category"] == "sport") & (df["p_t24h"] >= 0.50)
             & (df["p_t24h"] < 0.85)]
    return sub, "no", "p_t24h"


# ============================================================
# Monte Carlo
# ============================================================

def spread_for_volume(v: float) -> float:
    if v < 1_000:
        return 0.06
    if v < 10_000:
        return 0.04
    if v < 100_000:
        return 0.025
    if v < 1_000_000:
        return 0.015
    return 0.008


def simulate_one(entries: np.ndarray, resolved: np.ndarray,
                 volumes: np.ndarray, side: str, rng: np.random.Generator,
                 cancel_rate: float, flip_rate: float,
                 slippage_max: float) -> float:
    """Adversarial симуляция: penalty в направлении ухудшения стратегии.

    flip_rate работает АДВЕРСАРНО — только наши winners случайно становятся
    losers (не наоборот). Это правильный stress test: имитирует что наша
    выборка может содержать «случайные» победы которых в реальности не было.
    """
    n = len(entries)
    base = entries if side == "yes" else (1 - entries)
    spreads = np.array([spread_for_volume(v) for v in volumes])
    slips = rng.uniform(0, slippage_max, size=n)
    buy = base * (1 + spreads / 2 + slips)

    # is_winner от точки зрения стратегии
    if side == "yes":
        is_winner = resolved
    else:
        is_winner = ~resolved

    # Adversarial flip: только winners случайно становятся losers
    flip_mask = (rng.random(n) < flip_rate) & is_winner
    payout_winners_kept = is_winner & ~flip_mask
    payout = payout_winners_kept.astype(float)

    # Cancel: refund по mid-цене (стратегия не реализуется)
    cancel_mask = rng.random(n) < cancel_rate
    refund = base  # equivalent to "no PnL" примерно
    payout = np.where(cancel_mask, refund, payout)

    profit = payout - buy
    fees = np.where(profit > 0, profit * FEE, 0.0)
    pnl = profit - fees
    return pnl.sum() / buy.sum()


def run_strategy_mc(name: str, sub: pd.DataFrame, side: str,
                    price_col: str, n_sims: int) -> dict:
    if len(sub) < 30:
        return {"strategy": name, "n": len(sub), "raw_ev": None,
                "ev_median": None, "ev_p05": None, "P(EV>0)": None}

    entries = sub[price_col].to_numpy()
    resolved = sub["resolved_yes"].to_numpy(dtype=bool)
    volumes = sub["volume"].to_numpy()

    # Raw EV (без penalty, только volume-spread + базовый slip)
    raw_rng = np.random.default_rng(123)
    raw_ev = simulate_one(entries, resolved, volumes, side, raw_rng,
                          cancel_rate=0, flip_rate=0, slippage_max=0)

    scenarios = {
        "optimistic": (0.02, 0.02, 0.005),
        "realistic":  (0.05, 0.05, 0.010),
        "harsh":      (0.10, 0.10, 0.020),
        "worst":      (0.15, 0.20, 0.030),
    }
    results = {"strategy": name, "n": len(sub),
              "side": side, "raw_ev": round(raw_ev, 4)}
    for label, (cancel, flip, slip) in scenarios.items():
        seed_base = abs(hash(name + label)) % 2**31
        evs = np.empty(n_sims)
        for i in range(n_sims):
            rng = np.random.default_rng(seed_base + i)
            evs[i] = simulate_one(entries, resolved, volumes, side, rng,
                                  cancel, flip, slip)
        results[f"{label}_median"] = round(float(np.median(evs)), 4)
        results[f"{label}_p05"] = round(float(np.percentile(evs, 5)), 4)
        results[f"{label}_p_positive"] = round(float((evs > 0).mean()), 3)
    return results


# ============================================================
# Main
# ============================================================

STRATEGIES = [
    ("CRYPTO + drift>+10pp BUY YES",     strat_crypto_momentum),
    ("CRYPTO underdog [0.15,0.50] YES",  strat_crypto_underdog),
    ("LONGSHOTS [0.01,0.10] BUY YES",    strat_longshots),
    ("H1 + vol>$1M BUY YES",             strat_h1_high_vol),
    ("MEAN-REV: drift<-20pp BUY YES",    strat_mean_rev_down),
    ("price [0.20,0.30] BUY YES",        strat_band_20_30),
    ("MOMENTUM: drift>+15pp BUY YES",    strat_momentum_yes),
    ("SPORT NO [0.50,0.85]",             strat_sport_no),
]


def main() -> None:
    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda x: f"{x:+.4f}")

    t0 = time.time()
    print("=" * 70, flush=True)
    print("  Stress test для топ-стратегий (репрезентативная выборка)", flush=True)
    print("=" * 70, flush=True)

    con = duckdb.connect()
    setup_db(con)
    print("[load] Загружаю pool...", flush=True)
    df = load_pool(con)
    print(f"[load] {len(df):,} markets", flush=True)
    con.close()

    n_sims = 1500
    print(f"\n[run] {len(STRATEGIES)} стратегий × 4 сценария × {n_sims} MC = "
          f"{len(STRATEGIES)*4*n_sims:,} прогонов  (4 workers)\n", flush=True)

    tasks = []
    for name, fn in STRATEGIES:
        sub, side, price_col = fn(df)
        tasks.append((name, sub, side, price_col))

    results = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(run_strategy_mc, name, sub, side, price_col, n_sims):
                name for name, sub, side, price_col in tasks}
        for fut in as_completed(futs):
            try:
                r = fut.result()
                results.append(r)
                print(f"  [done] {r['strategy']}  (n={r['n']})", flush=True)
            except Exception as e:
                print(f"  [err]  {futs[fut]}: {e}", flush=True)

    print(f"\n{'='*70}\n  RESULTS\n{'='*70}\n", flush=True)
    df_res = pd.DataFrame(results)
    df_res = df_res.dropna(subset=["raw_ev"])
    df_res = df_res.sort_values("realistic_median", ascending=False)

    cols = ["strategy", "n", "side", "raw_ev",
            "optimistic_median", "realistic_median", "realistic_p05",
            "realistic_p_positive", "harsh_median", "worst_median"]
    print(df_res[cols].to_string(index=False), flush=True)

    print("\n=== ВЫЖИВАНИЕ в realistic сценарии (cancel=5%, flip=5%, slip<=1%) ===")
    print("Стратегии где P(EV>0) >= 0.95 в realistic:")
    survivors = df_res[df_res["realistic_p_positive"] >= 0.95]
    if len(survivors) > 0:
        print(survivors[["strategy", "n", "side", "realistic_median",
                         "realistic_p05", "realistic_p_positive"]]
              .to_string(index=False))
    else:
        print("⚠ Ни одна стратегия не выжила с P>=95% в realistic")
        print("Самые устойчивые (P>=80%):")
        somewhat = df_res[df_res["realistic_p_positive"] >= 0.80]
        if len(somewhat) > 0:
            print(somewhat[["strategy", "n", "side", "realistic_median",
                            "realistic_p05", "realistic_p_positive"]]
                  .to_string(index=False))

    out_path = ROOT / "data" / "wide_backtest" / "stress_test_top_strategies.parquet"
    df_res.to_parquet(out_path, index=False)
    print(f"\n💾 Saved → {out_path.name}", flush=True)
    print(f"Done за {time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
