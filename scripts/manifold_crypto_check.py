"""Manifold cross-validation для crypto стратегий.

Используем уже собранные Manifold trades (manifold_h1_trades.parquet).
Фильтруем crypto по question heuristic, прогоняем стратегии:
- CRYPTO + drift>+10pp BUY YES
- CRYPTO underdog [0.15, 0.50] BUY YES

Если на Manifold edge тоже есть → structural, не Polymarket artifact.

Запуск::

    python scripts/manifold_crypto_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


SPREAD = 0.015
SLIP = 0.005
FEE = 0.0  # Manifold = manas, не USD


def categorize(q: str) -> str:
    if not isinstance(q, str):
        return "other"
    ql = q.lower()
    if any(w in ql for w in ["bitcoin", "btc", "ethereum", "eth", "crypto",
                              "solana", "sol ", "fdv", "token", "memecoin",
                              "altcoin", "defi", "nft", "dogecoin", "doge"]):
        return "crypto"
    if any(w in ql for w in ["nba", "nfl", "fifa", "world cup", "olympics",
                              "champion", "uefa", "f1", "ufc", "tennis",
                              "match", "premier", "tournament"]):
        return "sport"
    if any(w in ql for w in ["trump", "biden", "election", "vote", "senate",
                              "congress", "president", "putin", "ukraine"]):
        return "politics"
    return "other"


def pnl(entry: np.ndarray, resolved: np.ndarray, side: str) -> tuple[np.ndarray, np.ndarray]:
    base = entry if side == "yes" else (1 - entry)
    buy = base * (1 + SPREAD/2 + SLIP)
    payout = resolved.astype(float) if side == "yes" else (~resolved).astype(float)
    profit = payout - buy
    fees = np.where(profit > 0, profit * FEE, 0.0)
    return profit - fees, buy


def eval_one(sub: pd.DataFrame, side: str, label: str) -> dict:
    if len(sub) < 10:
        return {"strategy": label, "n": len(sub)}
    e = sub["entry_prob"].to_numpy()
    r = sub["resolved_yes"].to_numpy(dtype=bool)
    p, b = pnl(e, r, side)
    n = len(p)
    win = (p > 0).sum() / n
    z = 1.96
    denom = 1 + z**2/n
    half = z * np.sqrt(win*(1-win)/n + z**2/(4*n**2)) / denom
    center = (win + z**2/(2*n)) / denom
    return {
        "strategy": label,
        "n": n,
        "win_rate": round(win, 3),
        "wilson_lo": round(center - half, 3),
        "ev_$": round(p.sum() / b.sum(), 4),
    }


def main() -> None:
    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda x: f"{x:.4f}")

    print("=" * 70)
    print("  Manifold cross-validation для crypto стратегий")
    print("=" * 70)

    df = pd.read_parquet(ROOT / "data" / "wide_backtest" / "manifold_h1_trades.parquet")
    print(f"[load] {len(df):,} Manifold trades с T-24h coverage")
    print(f"[load] columns: {list(df.columns)}")

    # Категоризация
    df["category"] = df["question"].apply(categorize)
    cat_counts = df["category"].value_counts()
    print(f"\n[cats] {cat_counts.to_dict()}")

    crypto = df[df["category"] == "crypto"].copy()
    print(f"\n[crypto] {len(crypto)} crypto trades на Manifold")
    if len(crypto) == 0:
        print("Не нашли crypto trades на Manifold")
        return

    # Прогон стратегий, аналогично Polymarket
    print("\n=== Стратегии на Manifold crypto subset ===")
    rows = []

    # 1. Crypto underdog [0.15, 0.50] BUY YES
    sub = crypto[(crypto["entry_prob"] >= 0.15) & (crypto["entry_prob"] < 0.50)]
    rows.append(eval_one(sub, "yes", "CRYPTO underdog [0.15,0.50] YES"))

    # 2. Crypto favorite [0.50, 0.85] BUY YES — наш бывший H1
    sub = crypto[(crypto["entry_prob"] >= 0.50) & (crypto["entry_prob"] < 0.85)]
    rows.append(eval_one(sub, "yes", "CRYPTO H1 [0.50,0.85] BUY YES"))

    # 3. Crypto favorite [0.50, 0.85] BUY NO
    sub = crypto[(crypto["entry_prob"] >= 0.50) & (crypto["entry_prob"] < 0.85)]
    rows.append(eval_one(sub, "no", "CRYPTO H1 [0.50,0.85] BUY NO"))

    # 4. Все crypto BUY YES (no filter)
    rows.append(eval_one(crypto, "yes", "CRYPTO all BUY YES"))

    # 5. Все crypto BUY NO (no filter)
    rows.append(eval_one(crypto, "no", "CRYPTO all BUY NO"))

    rep = pd.DataFrame(rows)
    print(rep.to_string(index=False))

    print("\n=== Сравнение с Polymarket репрезентативной выборкой ===")
    print(f"Polymarket CRYPTO underdog [0.15,0.50] YES: EV/$ +11.33% (n=415)")
    print(f"Manifold   CRYPTO underdog [0.15,0.50] YES: см. выше")
    print()
    print(f"Polymarket CRYPTO H1 [0.50,0.85] YES:       EV/$ +1.84% (n=2242)")
    print(f"Manifold   CRYPTO H1 [0.50,0.85] YES:       см. выше")
    print()
    print("Если на Manifold crypto edge тоже есть → структурный.")
    print("Если -EV → Polymarket-specific.")


if __name__ == "__main__":
    main()
