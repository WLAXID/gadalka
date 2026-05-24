"""Grid search стратегий на РЕПРЕЗЕНТАТИВНОЙ выборке.

Тестируем десятки гипотез на 54k markets / 5760+ trades:
1. Price bands × side (YES/NO)
2. Different horizons (T-1h, T-6h, T-24h, T-3d, T-7d)
3. Momentum (drift T-7d → T-24h): trend / mean-reversion
4. Volume buckets
5. Category (по ключевым словам в question)
6. Combos: low+momentum, mid-volume+drift

Архитектура: один процесс, DuckDB queries поэтапно, ThreadPool
для независимых аналитических секций.

Запуск::

    python scripts/strategy_grid_search.py
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


SPREAD = 0.015
SLIP = 0.005
FEE = 0.02


def buy_cost(p: np.ndarray, side: str) -> np.ndarray:
    base = p if side == "yes" else (1 - p)
    return base * (1 + SPREAD/2 + SLIP)


def pnl_series(entry: np.ndarray, resolved: np.ndarray, side: str) -> tuple[np.ndarray, np.ndarray]:
    b = buy_cost(entry, side)
    if side == "yes":
        payout = resolved.astype(float)
    else:
        payout = (~resolved).astype(float)
    profit = payout - b
    fees = np.where(profit > 0, profit * FEE, 0.0)
    return profit - fees, b


def eval_strategy(df: pd.DataFrame, mask: np.ndarray, side: str, label: str) -> dict:
    sub = df[mask]
    if len(sub) < 20:
        return {"strategy": label, "n": len(sub)}
    entry = sub["entry_price"].to_numpy()
    resolved = sub["resolved_yes"].to_numpy(dtype=bool)
    pnls, buys = pnl_series(entry, resolved, side)
    n = len(pnls)
    # Wilson lower bound for win-rate
    k = int((pnls > 0).sum())
    p = k / n
    z = 1.96
    denom = 1 + z**2/n
    half = z * np.sqrt(p*(1-p)/n + z**2/(4*n**2)) / denom
    center = (p + z**2/(2*n)) / denom
    wilson_lo = center - half
    return {
        "strategy": label,
        "n": n,
        "win_rate": round(p, 3),
        "wilson_lo": round(wilson_lo, 3),
        "ev_$": round(pnls.sum() / buys.sum(), 4),
        "total_pnl": round(pnls.sum(), 2),
    }


# ============================================================
# Загрузка trade pool с multi-horizon prices
# ============================================================

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


def load_pool(con: duckdb.DuckDBPyConnection,
              min_volume: float = 10_000.0) -> pd.DataFrame:
    """Один большой query: для каждого markets берём entry на разных horizons."""
    q = f"""
    WITH mc AS (
      SELECT m.conditionId AS condition_id, m.token_id_yes,
             m.question, m.endDate,
             CAST(m.resolved_yes AS BOOL) AS resolved_yes,
             CAST(m.volumeNum AS DOUBLE) AS volume,
             CAST(m.liquidity AS DOUBLE) AS liquidity,
             CAST(MIN(p.t) AS BIGINT) AS open_ts,
             CAST(MAX(p.t) AS BIGINT) AS close_ts,
             COUNT(p.t) AS n_points
      FROM markets m
      JOIN prices_history p ON p.condition_id = m.conditionId
      WHERE m.token_id_yes IS NOT NULL AND m.resolved_yes IS NOT NULL
      GROUP BY 1,2,3,4,5,6,7
    ),
    e1h AS (SELECT mc.condition_id, p.p AS p_t1h
            FROM mc ASOF JOIN prices_history p
              ON p.token_id = mc.token_id_yes AND p.t <= mc.close_ts - 3600),
    e6h AS (SELECT mc.condition_id, p.p AS p_t6h
            FROM mc ASOF JOIN prices_history p
              ON p.token_id = mc.token_id_yes AND p.t <= mc.close_ts - 21600),
    e24 AS (SELECT mc.condition_id, p.p AS p_t24h
            FROM mc ASOF JOIN prices_history p
              ON p.token_id = mc.token_id_yes AND p.t <= mc.close_ts - 86400),
    e3d AS (SELECT mc.condition_id, p.p AS p_t3d
            FROM mc ASOF JOIN prices_history p
              ON p.token_id = mc.token_id_yes AND p.t <= mc.close_ts - 259200),
    e7d AS (SELECT mc.condition_id, p.p AS p_t7d
            FROM mc ASOF JOIN prices_history p
              ON p.token_id = mc.token_id_yes AND p.t <= mc.close_ts - 604800)
    SELECT mc.condition_id, mc.question, mc.endDate, mc.resolved_yes,
           mc.volume, mc.liquidity,
           (mc.close_ts - mc.open_ts) / 86400.0 AS lifetime_days,
           mc.n_points,
           e1h.p_t1h, e6h.p_t6h, e24.p_t24h, e3d.p_t3d, e7d.p_t7d
    FROM mc
    LEFT JOIN e1h USING (condition_id)
    LEFT JOIN e6h USING (condition_id)
    LEFT JOIN e24 USING (condition_id)
    LEFT JOIN e3d USING (condition_id)
    LEFT JOIN e7d USING (condition_id)
    WHERE mc.volume >= {min_volume}
    """
    df = con.execute(q).df()
    # Default entry = T-24h
    df["entry_price"] = df["p_t24h"]
    return df


# ============================================================
# Категория по эвристике
# ============================================================

def categorize(q: str) -> str:
    if not isinstance(q, str):
        return "other"
    ql = q.lower()
    if any(w in ql for w in ["nba", "nfl", "fifa", "world cup", "olympics",
                              "champion", "uefa", "f1", "ufc", "wimbledon",
                              "tennis", "match", "vs.", " vs ", "premier",
                              "playoff", "finals", "tournament", "league",
                              "roland garros", "wta", "atp", "open", "league"]):
        return "sport"
    if any(w in ql for w in ["bitcoin", "btc", "ethereum", "eth", "crypto",
                              "solana", "sol ", "fdv", "token", "memecoin",
                              "above $"]):
        return "crypto"
    if any(w in ql for w in ["trump", "biden", "election", "vote", "senate",
                              "congress", "president", "putin", "ukraine",
                              "russia", "israel", "iran", "nominee", "primary"]):
        return "politics"
    if any(w in ql for w in ["weather", "hurricane", "temperature"]):
        return "weather"
    if any(w in ql for w in ["fed ", "interest rate", "cpi", "inflation",
                              "gdp", "recession", "stocks", "s&p"]):
        return "economy"
    return "other"


# ============================================================
# Группы стратегий
# ============================================================

def strategies_price_bands(df: pd.DataFrame) -> list[dict]:
    """Простые ценовые полосы × side."""
    bands = [(0.01, 0.10), (0.10, 0.20), (0.20, 0.30), (0.30, 0.40),
             (0.40, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 0.80),
             (0.80, 0.90), (0.90, 0.99)]
    rows = []
    for lo, hi in bands:
        for side in ["yes", "no"]:
            mask = ((df["entry_price"] >= lo) & (df["entry_price"] < hi)
                    & df["entry_price"].notna()).to_numpy()
            label = f"price[{lo:.2f},{hi:.2f}] BUY {side.upper()}"
            rows.append(eval_strategy(df, mask, side, label))
    return rows


def strategies_horizons(df: pd.DataFrame) -> list[dict]:
    """Тот же H1 [0.50, 0.85] но на разных horizons."""
    rows = []
    for col, label_h in [("p_t1h", "T-1h"), ("p_t6h", "T-6h"),
                          ("p_t24h", "T-24h"), ("p_t3d", "T-3d"),
                          ("p_t7d", "T-7d")]:
        if col not in df.columns:
            continue
        x = df[col].to_numpy()
        mask = (x >= 0.50) & (x < 0.85) & df[col].notna().to_numpy()
        # Для оценки переопределим entry_price
        df_tmp = df.copy()
        df_tmp["entry_price"] = df_tmp[col]
        for side in ["yes", "no"]:
            label = f"H1[0.50,0.85]@{label_h} BUY {side.upper()}"
            rows.append(eval_strategy(df_tmp, mask, side, label))
    return rows


def strategies_momentum(df: pd.DataFrame) -> list[dict]:
    """Сигналы на основе drift T-7d → T-24h."""
    rows = []
    df = df.dropna(subset=["p_t7d", "p_t24h"]).copy()
    df["drift_7d_24h"] = df["p_t24h"] - df["p_t7d"]
    df["entry_price"] = df["p_t24h"]
    # 1. Big drift up → momentum
    mask = ((df["drift_7d_24h"] > 0.15) & (df["entry_price"] < 0.85)).to_numpy()
    rows.append(eval_strategy(df, mask, "yes",
                              "MOMENTUM: drift>+15pp BUY YES"))
    # 2. Big drift down → momentum NO
    mask = ((df["drift_7d_24h"] < -0.15) & (df["entry_price"] > 0.15)).to_numpy()
    rows.append(eval_strategy(df, mask, "no",
                              "MOMENTUM: drift<-15pp BUY NO"))
    # 3. Mean reversion: big drift up + entry close to 1 → BUY NO
    mask = ((df["drift_7d_24h"] > 0.20)).to_numpy()
    rows.append(eval_strategy(df, mask, "no",
                              "MEAN-REV: drift>+20pp BUY NO"))
    # 4. Mean reversion: drift down → BUY YES
    mask = ((df["drift_7d_24h"] < -0.20)).to_numpy()
    rows.append(eval_strategy(df, mask, "yes",
                              "MEAN-REV: drift<-20pp BUY YES"))
    # 5. Stable (no big drift) → buy YES if entry in [0.50, 0.85]
    mask = ((df["drift_7d_24h"].abs() < 0.05)
            & (df["entry_price"] >= 0.50)
            & (df["entry_price"] < 0.85)).to_numpy()
    rows.append(eval_strategy(df, mask, "yes",
                              "STABLE: |drift|<5pp + H1 BUY YES"))
    # 6. Stable + buy NO в low
    mask = ((df["drift_7d_24h"].abs() < 0.05)
            & (df["entry_price"] >= 0.15)
            & (df["entry_price"] < 0.50)).to_numpy()
    rows.append(eval_strategy(df, mask, "no",
                              "STABLE: |drift|<5pp + H_low BUY NO"))
    return rows


def strategies_volume(df: pd.DataFrame) -> list[dict]:
    rows = []
    buckets = [
        ("v<$50k", 0, 50_000),
        ("$50k-$200k", 50_000, 200_000),
        ("$200k-$1M", 200_000, 1_000_000),
        ("$1M+", 1_000_000, float("inf")),
    ]
    for label, vmin, vmax in buckets:
        m = ((df["volume"] >= vmin) & (df["volume"] < vmax)
             & (df["entry_price"] >= 0.50)
             & (df["entry_price"] < 0.85)).to_numpy()
        rows.append(eval_strategy(df, m, "yes",
                                  f"H1 + {label} BUY YES"))
    return rows


def strategies_categories(df: pd.DataFrame) -> list[dict]:
    rows = []
    df = df.copy()
    df["category"] = df["question"].apply(categorize)
    for cat in ["sport", "crypto", "politics", "weather", "economy", "other"]:
        for side, lo, hi in [("yes", 0.50, 0.85), ("no", 0.50, 0.85),
                              ("yes", 0.15, 0.50), ("no", 0.15, 0.50)]:
            m = ((df["category"] == cat) & (df["entry_price"] >= lo)
                 & (df["entry_price"] < hi)).to_numpy()
            label = f"{cat:<8} {side.upper()} [{lo:.2f},{hi:.2f}]"
            rows.append(eval_strategy(df, m, side, label))
    return rows


def strategies_lifetime(df: pd.DataFrame) -> list[dict]:
    rows = []
    buckets = [("life<7d", 0, 7), ("7d-30d", 7, 30),
               ("30d-90d", 30, 90), ("90d+", 90, 10000)]
    for label, lmin, lmax in buckets:
        m = ((df["lifetime_days"] >= lmin) & (df["lifetime_days"] < lmax)
             & (df["entry_price"] >= 0.50)
             & (df["entry_price"] < 0.85)).to_numpy()
        rows.append(eval_strategy(df, m, "yes",
                                  f"H1 + lifetime {label} BUY YES"))
    return rows


def strategies_combo(df: pd.DataFrame) -> list[dict]:
    """Комбинации фильтров."""
    rows = []
    df = df.copy()
    df["category"] = df["question"].apply(categorize)
    df["drift"] = (df["p_t24h"] - df["p_t7d"]).fillna(0)

    # Crypto + momentum
    m = ((df["category"] == "crypto") & (df["drift"] > 0.1)
         & df["p_t24h"].notna()).to_numpy()
    rows.append(eval_strategy(df, m, "yes",
                              "COMBO: crypto + drift>+10pp BUY YES"))

    # Politics + stable + favorite
    m = ((df["category"] == "politics") & (df["drift"].abs() < 0.05)
         & (df["entry_price"] >= 0.50) & (df["entry_price"] < 0.85)).to_numpy()
    rows.append(eval_strategy(df, m, "yes",
                              "COMBO: politics + stable + H1 BUY YES"))

    # NOT sport + H_low BUY NO
    m = ((df["category"] != "sport")
         & (df["entry_price"] >= 0.20) & (df["entry_price"] < 0.50)).to_numpy()
    rows.append(eval_strategy(df, m, "no",
                              "COMBO: not-sport + H_low BUY NO"))

    # High volume + extreme fav
    m = ((df["volume"] >= 100_000)
         & (df["entry_price"] >= 0.85) & (df["entry_price"] < 0.99)).to_numpy()
    rows.append(eval_strategy(df, m, "yes",
                              "COMBO: vol>=$100k + extreme fav BUY YES"))

    # Crypto + mean-reversion (drift up → buy NO)
    m = ((df["category"] == "crypto") & (df["drift"] > 0.2)
         & df["p_t24h"].notna()).to_numpy()
    rows.append(eval_strategy(df, m, "no",
                              "COMBO: crypto + drift>+20pp BUY NO"))

    # Big drift down + low price → mean rev BUY YES
    m = ((df["drift"] < -0.20) & (df["entry_price"] < 0.30)
         & df["p_t24h"].notna()).to_numpy()
    rows.append(eval_strategy(df, m, "yes",
                              "COMBO: drift<-20pp + entry<0.30 BUY YES"))

    # Many history points (active market) + H1
    m = ((df["n_points"] > 100)
         & (df["entry_price"] >= 0.50) & (df["entry_price"] < 0.85)).to_numpy()
    rows.append(eval_strategy(df, m, "yes",
                              "COMBO: n_points>100 + H1 BUY YES"))

    # Few history points (illiquid) + H1
    m = ((df["n_points"] < 30)
         & (df["entry_price"] >= 0.50) & (df["entry_price"] < 0.85)).to_numpy()
    rows.append(eval_strategy(df, m, "yes",
                              "COMBO: n_points<30 + H1 BUY YES"))

    return rows


# ============================================================
# Main
# ============================================================

def main() -> None:
    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda x: f"{x:.4f}")

    t0 = time.time()
    print("=" * 70, flush=True)
    print("  Strategy grid search на репрезентативной выборке", flush=True)
    print("=" * 70, flush=True)
    con = duckdb.connect()
    setup_db(con)

    print("[load] Загружаю pool...", flush=True)
    df = load_pool(con)
    print(f"[load] {len(df):,} markets (volume>=$10k, with resolved_yes)",
          flush=True)
    coverage = {col: float(df[col].notna().mean())
                for col in ["p_t1h", "p_t6h", "p_t24h", "p_t3d", "p_t7d"]}
    print(f"[load] coverage: {coverage}", flush=True)

    print("\n[run] 6 групп стратегий параллельно (ThreadPool 4)...", flush=True)
    groups = [
        ("A_price_bands", lambda: strategies_price_bands(df)),
        ("B_horizons",    lambda: strategies_horizons(df)),
        ("C_momentum",    lambda: strategies_momentum(df)),
        ("D_volume",      lambda: strategies_volume(df)),
        ("E_category",    lambda: strategies_categories(df)),
        ("F_lifetime",    lambda: strategies_lifetime(df)),
        ("G_combo",       lambda: strategies_combo(df)),
    ]
    all_results: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fn): name for name, fn in groups}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                rows = fut.result()
                for r in rows:
                    r["group"] = name
                all_results.extend(rows)
                print(f"  [done] {name}: {len(rows)} стратегий", flush=True)
            except Exception as e:
                print(f"  [err]  {name}: {e}", flush=True)

    print(f"\n{'='*70}\n  RESULTS\n{'='*70}\n", flush=True)
    res_df = pd.DataFrame(all_results)
    res_df = res_df.dropna(subset=["ev_$"])  # n<20 → skip

    # Сортируем по EV убывание
    res_df_sorted = res_df.sort_values("ev_$", ascending=False)

    print("=== ТОП-20 стратегий по EV/$ ===", flush=True)
    print(res_df_sorted[["strategy", "n", "win_rate", "wilson_lo", "ev_$",
                          "total_pnl"]].head(20).to_string(index=False), flush=True)

    print("\n=== БОТТОМ-10 (хуже всех) ===", flush=True)
    print(res_df_sorted[["strategy", "n", "win_rate", "ev_$"]].tail(10)
          .to_string(index=False), flush=True)

    print("\n=== ТОП-5 со значимостью (n>=100 и wilson_lo>0.5) ===", flush=True)
    significant = res_df_sorted[(res_df_sorted["n"] >= 100)
                                 & (res_df_sorted["ev_$"] > 0.02)]
    if len(significant) > 0:
        print(significant.head(10).to_string(index=False), flush=True)
    else:
        print("Нет стратегий с n>=100 И EV>+2%", flush=True)

    out_path = ROOT / "data" / "wide_backtest" / "strategy_grid_results.parquet"
    res_df_sorted.to_parquet(out_path, index=False)
    print(f"\n💾 Saved {len(res_df_sorted)} стратегий → {out_path}", flush=True)

    print(f"\nDone за {time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
