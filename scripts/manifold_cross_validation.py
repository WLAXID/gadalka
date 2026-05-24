"""Cross-validation на Manifold Markets — золотой стандарт.

Зачем: если стратегия [0.50, 0.85] @ T-24h работает на ДРУГОЙ
prediction-market платформе с тем же EV — это структурный edge.
Если нет — Polymarket-специфичный artifact (вероятно selection bias).

Manifold API публичный, rate ~100 req/s без key.

Запуск::

    python scripts/manifold_cross_validation.py --max-markets 1000
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

API = "https://api.manifold.markets/v0"
SPREAD = 0.015
SLIP = 0.005
FEE = 0.0  # На Manifold нет % fee на manas, но есть market spread


async def fetch_resolved_markets(client: httpx.AsyncClient,
                                  max_markets: int) -> list[dict]:
    """Пагинирует /markets, отбирает resolved BINARY."""
    rows = []
    before = None
    while len(rows) < max_markets:
        params = {"limit": 1000}
        if before:
            params["before"] = before
        r = await client.get(f"{API}/markets", params=params)
        if r.status_code != 200:
            print(f"  err: {r.status_code} {r.text[:100]}", flush=True)
            break
        page = r.json()
        if not page:
            break
        for m in page:
            if m.get("outcomeType") == "BINARY" and m.get("isResolved"):
                res = m.get("resolution")
                if res in ("YES", "NO"):
                    rows.append(m)
        before = page[-1]["id"]
        print(f"  fetched page, total resolved BINARY = {len(rows)}", flush=True)
        if len(page) < 1000:
            break
    return rows[:max_markets]


async def fetch_bets(client: httpx.AsyncClient, market_id: str,
                     sem: asyncio.Semaphore) -> list[dict]:
    """Получить всю историю ставок рынка (paginate через before)."""
    bets = []
    before = None
    async with sem:
        for _ in range(20):  # max 20 страниц по 1000 = 20k bets
            params = {"contractId": market_id, "limit": 1000}
            if before:
                params["before"] = before
            try:
                r = await client.get(f"{API}/bets", params=params, timeout=15)
                if r.status_code != 200:
                    break
                page = r.json()
                if not page:
                    break
                bets.extend(page)
                if len(page) < 1000:
                    break
                before = page[-1]["id"]
            except Exception:
                break
    return bets


def probability_at_time(bets: list[dict], target_ms: int) -> float | None:
    """Найти probAfter ставки ближайшей к target_ms (но НЕ после)."""
    eligible = [b for b in bets if b.get("createdTime", 0) <= target_ms
                and "probAfter" in b]
    if not eligible:
        return None
    nearest = max(eligible, key=lambda b: b["createdTime"])
    return float(nearest["probAfter"])


def pnl_h1(entry: float, resolved_yes: bool, side: str = "yes") -> float:
    if side == "yes":
        buy = entry * (1 + SPREAD/2 + SLIP)
        payout = 1.0 if resolved_yes else 0.0
    else:
        buy = (1 - entry) * (1 + SPREAD/2 + SLIP)
        payout = 0.0 if resolved_yes else 1.0
    return payout - buy


async def main(max_markets: int, concurrency: int) -> None:
    t0 = time.time()
    print("="*70, flush=True)
    print("  Manifold Markets cross-validation", flush=True)
    print("="*70, flush=True)
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        print(f"[1] Fetching resolved BINARY markets (target {max_markets})...",
              flush=True)
        markets = await fetch_resolved_markets(client, max_markets)
        print(f"[1] Got {len(markets)} resolved BINARY markets", flush=True)

        if not markets:
            print("Нет данных, abort")
            return

        print(f"[2] Fetching bets history (concurrency={concurrency})...",
              flush=True)
        sem = asyncio.Semaphore(concurrency)
        tasks = [fetch_bets(client, m["id"], sem) for m in markets]
        bets_per_market = []
        done = 0
        for coro in asyncio.as_completed(tasks):
            b = await coro
            bets_per_market.append(b)
            done += 1
            if done % 50 == 0 or done == len(markets):
                print(f"    bets fetched for {done}/{len(markets)} markets",
                      flush=True)

    # Параллельно обрабатываем
    rows = []
    for m, bets in zip(markets, bets_per_market):
        close_ts = m.get("closeTime") or m.get("resolutionTime")
        if not close_ts:
            continue
        target_ms = close_ts - 24 * 3600 * 1000  # T-24h
        prob = probability_at_time(bets, target_ms)
        if prob is None:
            continue
        resolved_yes = m["resolution"] == "YES"
        volume = m.get("volume", 0)
        rows.append({
            "id": m["id"],
            "question": m.get("question", "")[:60],
            "volume": volume,
            "entry_prob": prob,
            "resolved_yes": resolved_yes,
            "close_ts": close_ts / 1000,
        })
    df = pd.DataFrame(rows)
    print(f"\n[3] Markets с T-24h coverage: {len(df)}", flush=True)

    # H1 на Manifold
    h1 = df[(df["entry_prob"] >= 0.50) & (df["entry_prob"] < 0.85)].copy()
    print(f"[3] H1 [0.50, 0.85] sample: {len(h1)}", flush=True)
    if len(h1) >= 20:
        h1["pnl_yes"] = h1.apply(
            lambda r: pnl_h1(r["entry_prob"], r["resolved_yes"], "yes"), axis=1)
        h1["buy_cost"] = h1["entry_prob"] * (1 + SPREAD/2 + SLIP)
        ev = h1["pnl_yes"].sum() / h1["buy_cost"].sum()
        win = h1["resolved_yes"].mean()
        print(f"\n=== H1 BUY YES в [0.50, 0.85] @ T-24h на Manifold ===")
        print(f"n: {len(h1)}")
        print(f"win-rate: {win:.4f}")
        print(f"EV/$:     {ev:+.4f}")
        # Inverse
        h1["pnl_no"] = h1.apply(
            lambda r: pnl_h1(r["entry_prob"], r["resolved_yes"], "no"), axis=1)
        h1["buy_no"] = (1 - h1["entry_prob"]) * (1 + SPREAD/2 + SLIP)
        ev_inv = h1["pnl_no"].sum() / h1["buy_no"].sum()
        print(f"H1 INVERSE BUY NO: EV/$ {ev_inv:+.4f}")

        # Сравним с Polymarket
        print("\n=== Сравнение с Polymarket ===")
        print(f"Polymarket H1 EV/$: ~+0.2003 (win=0.823, n=299)")
        print(f"Manifold H1 EV/$:    {ev:+.4f} (win={win:.4f}, n={len(h1)})")
        diff = ev - 0.2003
        if abs(diff) < 0.05:
            print("✅ Близко — edge структурный")
        elif ev > 0:
            print("✓ Положительный но меньше — частично структурный")
        else:
            print("⚠ Отрицательный — наш Polymarket edge скорее всего artifact")
    else:
        print(f"⚠ Слишком мало H1 ставок ({len(h1)}) для сравнения")

    # Save raw
    out_path = ROOT / "data" / "wide_backtest" / "manifold_h1_trades.parquet"
    df.to_parquet(out_path, index=False)
    print(f"\n💾 Saved {len(df)} trades to {out_path}")
    print(f"Done за {time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-markets", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=20)
    args = parser.parse_args()
    asyncio.run(main(args.max_markets, args.concurrency))
