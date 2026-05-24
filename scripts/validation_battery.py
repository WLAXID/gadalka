"""Battery валидационных проверок H1 стратегии — параллель, single process.

Проверки:
1. Inverse strategy — если работает наш edge, inverse должен давать -EV
2. Multiple strategies на одной выборке — H1 vs random vs extreme vs etc
3. Permutation test — shuffle outcomes 1000 раз, смотрим distribution
5. Early exit — что если выйти через 1ч/6ч/12ч до резолва
8. Bayesian posterior — формальный prior, distribution истинного EV

Запуск::

    python scripts/validation_battery.py
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


SPREAD = 0.015
SLIP = 0.005
FEE = 0.02


def effective_buy(p: np.ndarray) -> np.ndarray:
    return p * (1 + SPREAD / 2 + SLIP)


def pnl_from(entry: np.ndarray, resolved: np.ndarray,
             *, side: str = "yes") -> np.ndarray:
    """Считает PnL.
    side='yes': покупаем YES, payout=1 если resolved_yes, иначе 0
    side='no':  покупаем NO, payout=1 если NOT resolved_yes, иначе 0
                buy_cost = (1-entry) * (1 + spread/2 + slip)
    """
    if side == "yes":
        buy = effective_buy(entry)
        payout = resolved.astype(float)
    else:
        buy = effective_buy(1 - entry)
        payout = (~resolved).astype(float)
    profit = payout - buy
    fees = np.where(profit > 0, profit * FEE, 0.0)
    return profit - fees, buy


def stats_of(pnls: np.ndarray, buys: np.ndarray) -> dict:
    if len(pnls) == 0:
        return {"n": 0}
    return {
        "n": len(pnls),
        "win_rate": float((pnls > 0).mean()),
        "ev_per_bet": float(pnls.mean()),
        "ev_dollar": float(pnls.sum() / buys.sum()),
        "total_pnl": float(pnls.sum()),
        "sharpe_trade": float(pnls.mean() / pnls.std() * np.sqrt(len(pnls)))
                       if pnls.std() > 1e-9 else None,
    }


# ============================================================
# Загрузка trades с разными horizons (для early exit)
# ============================================================

def load_trades_multi_horizon(con: duckdb.DuckDBPyConnection,
                              min_volume: float = 10_000.0) -> pd.DataFrame:
    """Один query, все entry/exit horizons сразу."""
    # Entry на T-24h, exit на T-X (для early exit analysis)
    q = """
    WITH mc AS (
      SELECT m.conditionId AS condition_id, m.token_id_yes,
             m.question, m.slug,
             CAST(m.resolved_yes AS BOOL) AS resolved_yes,
             CAST(m.volumeNum AS DOUBLE) AS volume,
             CAST(MAX(p.t) AS BIGINT) AS close_ts
      FROM markets m
      JOIN prices_history p ON p.condition_id = m.conditionId
      WHERE m.token_id_yes IS NOT NULL AND m.resolved_yes IS NOT NULL
      GROUP BY 1,2,3,4,5,6
    ),
    entry AS (
      SELECT mc.*, p.p AS entry_price
      FROM mc ASOF JOIN prices_history p
        ON p.token_id = mc.token_id_yes
        AND p.t <= mc.close_ts - 86400
    ),
    e_1h  AS (SELECT mc.condition_id, p.p AS exit_1h
              FROM mc ASOF JOIN prices_history p
                ON p.token_id = mc.token_id_yes
                AND p.t <= mc.close_ts - 3600),
    e_6h  AS (SELECT mc.condition_id, p.p AS exit_6h
              FROM mc ASOF JOIN prices_history p
                ON p.token_id = mc.token_id_yes
                AND p.t <= mc.close_ts - 21600),
    e_12h AS (SELECT mc.condition_id, p.p AS exit_12h
              FROM mc ASOF JOIN prices_history p
                ON p.token_id = mc.token_id_yes
                AND p.t <= mc.close_ts - 43200),
    fp AS (SELECT mc.condition_id, p.p AS final_price
           FROM mc ASOF JOIN prices_history p
             ON p.token_id = mc.token_id_yes
             AND p.t <= mc.close_ts)
    SELECT entry.condition_id, entry.question, entry.resolved_yes,
           entry.volume, entry.close_ts, entry.entry_price,
           e_1h.exit_1h, e_6h.exit_6h, e_12h.exit_12h, fp.final_price
    FROM entry
    LEFT JOIN e_1h  USING (condition_id)
    LEFT JOIN e_6h  USING (condition_id)
    LEFT JOIN e_12h USING (condition_id)
    LEFT JOIN fp    USING (condition_id)
    WHERE entry.entry_price IS NOT NULL
      AND entry.volume >= """ + str(min_volume)
    return con.execute(q).df()


# ============================================================
# Check 1 + 2: Multiple strategies на одной выборке
# ============================================================

def check_multi_strategies(df_all: pd.DataFrame) -> str:
    out = ["=== 1+2. Multiple strategies на одной выборке ==="]
    strategies = [
        ("H1 [0.50, 0.85] BUY YES",  0.50, 0.85, "yes"),
        ("H_low [0.15, 0.50] BUY NO", 0.15, 0.50, "no"),
        ("H_low_invert [0.15,0.50] BUY YES (inverse of H_low)", 0.15, 0.50, "yes"),
        ("H1 INVERSE [0.50, 0.85] BUY NO (inverse of H1)", 0.50, 0.85, "no"),
        ("H_extreme_fav [0.85, 0.99] BUY YES", 0.85, 0.99, "yes"),
        ("H_extreme_dog [0.01, 0.15] BUY YES (longshot)", 0.01, 0.15, "yes"),
        ("H_all [0.00, 1.00] BUY YES (no filter)", 0.0, 1.0, "yes"),
        ("H_all [0.00, 1.00] BUY NO (no filter)", 0.0, 1.0, "no"),
    ]
    rows = []
    for label, lo, hi, side in strategies:
        mask = (df_all["entry_price"].notna()
                & (df_all["entry_price"] >= lo)
                & (df_all["entry_price"] < hi))
        sub = df_all[mask]
        if len(sub) == 0:
            rows.append({"strategy": label, "n": 0})
            continue
        entry = sub["entry_price"].to_numpy()
        resolved = sub["resolved_yes"].to_numpy(dtype=bool)
        pnls, buys = pnl_from(entry, resolved, side=side)
        s = stats_of(pnls, buys)
        rows.append({"strategy": label, "n": s["n"],
                    "win_rate": round(s["win_rate"], 3),
                    "ev_$": round(s["ev_dollar"], 4),
                    "total_pnl": round(s["total_pnl"], 2)})
    rep = pd.DataFrame(rows)
    out.append(rep.to_string(index=False))
    out.append("""
Интерпретация:
- Если только H1 в плюсе, остальные ≈ 0 или минус → edge реальный
- Если H1 INVERSE даёт ~+EV — выборка скомпрометирована (selection bias)
- Если H_all в плюсе — наш фильтр [0.50, 0.85] не главный driver edge
""")
    return "\n".join(out)


# ============================================================
# Check 3: Permutation test
# ============================================================

def check_permutation(df_h1: pd.DataFrame, n_perms: int = 5000) -> str:
    out = ["=== 3. Permutation test (5000 random shuffles) ==="]
    if len(df_h1) == 0:
        out.append("Нет данных")
        return "\n".join(out)
    entry = df_h1["entry_price"].to_numpy()
    resolved = df_h1["resolved_yes"].to_numpy(dtype=bool)
    pnls, buys = pnl_from(entry, resolved, side="yes")
    real_ev = pnls.sum() / buys.sum()

    rng = np.random.default_rng(42)
    null_evs = np.empty(n_perms)
    for i in range(n_perms):
        shuffled = rng.permutation(resolved)
        p_shuf, b_shuf = pnl_from(entry, shuffled, side="yes")
        null_evs[i] = p_shuf.sum() / b_shuf.sum()

    p_value = (null_evs >= real_ev).mean()
    out.append(f"Real EV/$:           {real_ev:+.4f}")
    out.append(f"Null mean:           {null_evs.mean():+.4f}")
    out.append(f"Null std:            {null_evs.std():.4f}")
    out.append(f"Null p05 / p95:      [{np.percentile(null_evs, 5):+.4f}, "
              f"{np.percentile(null_evs, 95):+.4f}]")
    out.append(f"P-value (real >= null): {p_value:.4f}")
    if p_value < 0.01:
        out.append("✅ p<0.01 — наш EV статистически значимо отличается от шума")
    elif p_value < 0.05:
        out.append("✅ p<0.05 — наш EV значимо, но slabo")
    else:
        out.append("⚠ p>=0.05 — наш EV может быть случайностью")
    return "\n".join(out)


# ============================================================
# Check 5: Early exit analysis
# ============================================================

def check_early_exit(df_h1: pd.DataFrame) -> str:
    out = ["=== 5. Early exit analysis ==="]
    out.append("Что если выйти из позиции на T-12h / T-6h / T-1h вместо ждать резолв?")
    if len(df_h1) == 0:
        out.append("Нет данных")
        return "\n".join(out)
    entry = df_h1["entry_price"].to_numpy()
    buy = effective_buy(entry)

    rows = []
    # Hold to resolution
    resolved = df_h1["resolved_yes"].to_numpy(dtype=bool)
    payout_hold = resolved.astype(float)
    profit_hold = payout_hold - buy
    fees_hold = np.where(profit_hold > 0, profit_hold * FEE, 0.0)
    pnl_hold = profit_hold - fees_hold
    rows.append({"exit_strategy": "hold to resolution",
                "n": len(pnl_hold),
                "ev_$": round(pnl_hold.sum() / buy.sum(), 4),
                "win_rate": round((pnl_hold > 0).mean(), 3)})

    for col, label in [("exit_12h", "exit at T-12h"),
                       ("exit_6h", "exit at T-6h"),
                       ("exit_1h", "exit at T-1h")]:
        sub = df_h1.dropna(subset=[col]).copy()
        if len(sub) == 0:
            rows.append({"exit_strategy": label, "n": 0})
            continue
        e = sub["entry_price"].to_numpy()
        b = effective_buy(e)
        exit_price = sub[col].to_numpy()
        # Selling YES по mid - spread/2 - slip
        sell = exit_price * (1 - SPREAD / 2 - SLIP)
        profit = sell - b
        fees = np.where(profit > 0, profit * FEE, 0.0)
        pnl = profit - fees
        rows.append({"exit_strategy": label,
                    "n": len(pnl),
                    "ev_$": round(pnl.sum() / b.sum(), 4),
                    "win_rate": round((pnl > 0).mean(), 3)})

    out.append(pd.DataFrame(rows).to_string(index=False))
    out.append("""
- Если ранний выход даёт больший EV → захватываем drift, можем закрываться раньше
- Если меньший — edge именно в финальном резолве, надо ждать
""")
    return "\n".join(out)


# ============================================================
# Check 8: Bayesian posterior на win-rate
# ============================================================

def check_bayesian(df_h1: pd.DataFrame) -> str:
    out = ["=== 8. Bayesian posterior на win-rate ==="]
    if len(df_h1) == 0:
        out.append("Нет данных")
        return "\n".join(out)
    n = len(df_h1)
    k = int(df_h1["resolved_yes"].sum())

    # Несколько priors
    priors = [
        ("uninformative Beta(1,1)", 1, 1),
        ("skeptical Beta(50,50)", 50, 50),
        ("strong skeptical Beta(100,100)", 100, 100),
        ("market efficient Beta(50,30) — пользу фавориту", 50, 30),
    ]

    avg_buy = (df_h1["entry_price"].mean() * (1 + SPREAD/2 + SLIP))
    be_wr = avg_buy  # break-even win-rate

    out.append(f"Observed: n={n}, wins={k}, raw win-rate={k/n:.4f}")
    out.append(f"Break-even win-rate ≈ {be_wr:.4f}\n")

    rows = []
    rng = np.random.default_rng(42)
    for label, a0, b0 in priors:
        # Posterior: Beta(a0 + k, b0 + n - k)
        a, b = a0 + k, b0 + (n - k)
        # Sample win-rate from posterior
        samples = rng.beta(a, b, 50_000)
        # EV/$ ≈ (win - buy) / buy = win/buy - 1
        ev_samples = (samples - avg_buy) / avg_buy
        rows.append({
            "prior": label,
            "post_mean_wr": round(a/(a+b), 4),
            "post_p05_wr": round(np.percentile(samples, 5), 4),
            "post_p95_wr": round(np.percentile(samples, 95), 4),
            "P(wr > BE)": round((samples > be_wr).mean(), 4),
            "EV_post_median": round(np.median(ev_samples), 4),
            "EV_post_p05": round(np.percentile(ev_samples, 5), 4),
        })
    out.append(pd.DataFrame(rows).to_string(index=False))
    out.append("""
- Uninformative (Beta(1,1)) — наша эмпирическая оценка
- Skeptical priors — если бы мы заранее не верили в edge
- Если даже с strong skeptical prior P(wr > BE) > 95% → edge почти точно реален
""")
    return "\n".join(out)


# ============================================================
# Main
# ============================================================

def main() -> None:
    pd.set_option("display.width", 220)
    pd.set_option("display.float_format", lambda x: f"{x:.4f}")

    t0 = time.time()
    print("=" * 70, flush=True)
    print("  Validation battery — 5 проверок параллельно", flush=True)
    print("=" * 70, flush=True)

    con = duckdb.connect()
    setup_db(con)

    print("[load] Загружаю trades с multi-horizons...", flush=True)
    df_all = load_trades_multi_horizon(con)
    print(f"[load] {len(df_all):,} trades total (after volume filter)", flush=True)

    # H1 subset
    df_h1 = df_all[(df_all["entry_price"] >= 0.50)
                   & (df_all["entry_price"] < 0.85)].copy()
    print(f"[load] H1 subset: {len(df_h1)} trades", flush=True)

    print("\n[run] 4 checks параллельно (ThreadPool 4 workers)...", flush=True)

    checks = [
        ("multi", lambda: check_multi_strategies(df_all)),
        ("perm",  lambda: check_permutation(df_h1)),
        ("exit",  lambda: check_early_exit(df_h1)),
        ("bayes", lambda: check_bayesian(df_h1)),
    ]

    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fn): name for name, fn in checks}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                results[name] = fut.result()
                print(f"  [done] {name}", flush=True)
            except Exception as e:
                results[name] = f"=== {name} — ERROR ===\n{type(e).__name__}: {e}"
                print(f"  [err]  {name}: {e}", flush=True)

    print(f"\n{'='*70}\n  RESULTS\n{'='*70}\n", flush=True)
    for name in ["multi", "perm", "exit", "bayes"]:
        print(results.get(name, ""), flush=True)
        print()

    con.close()
    print(f"Done за {time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
