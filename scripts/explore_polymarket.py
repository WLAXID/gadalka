"""Exploration-скрипт по Polymarket API: sample-запросы во все endpoint'ы.

Цели Day 2:
- Подтвердить что клиент работает
- Получить sample-ответы для документации схем (docs/api-schemas.md)
- Замерить latency базовых запросов
- Понять размер ответов

Артефакты:
- ``data/raw/samples/<endpoint>.json`` — сырые ответы
- stdout — сводка
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from loguru import logger  # noqa: E402

from src.api.polymarket import PolymarketClient  # noqa: E402


SAMPLES_DIR = ROOT / "data" / "raw" / "samples"
SAMPLES_DIR.mkdir(parents=True, exist_ok=True)


def save(name: str, payload: Any) -> Path:
    path = SAMPLES_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def describe(payload: Any) -> str:
    """Один-строчное описание объекта/массива."""
    if isinstance(payload, list):
        n = len(payload)
        if n == 0:
            return "list[0]"
        sample = payload[0]
        if isinstance(sample, dict):
            return f"list[{n}] | keys: {sorted(sample.keys())[:8]}..."
        return f"list[{n}] | item_type={type(sample).__name__}"
    if isinstance(payload, dict):
        return f"dict | keys: {sorted(payload.keys())[:10]}"
    return f"{type(payload).__name__}"


async def main() -> None:
    print("━" * 70)
    print("  Polymarket API exploration — Day 2 Фазы 0")
    print("━" * 70)

    async with PolymarketClient(cache_dir=ROOT / "data" / "cache" / "polymarket") as pm:
        # 1. GAMMA — закрытые рынки
        t0 = time.time()
        markets_closed = await pm.gamma_markets(closed=True, limit=10)
        dt = time.time() - t0
        save("gamma_markets_closed", markets_closed)
        print(f"\n[gamma_markets closed=true limit=10]  {dt:.2f}s")
        print(f"  → {describe(markets_closed)}")
        if markets_closed:
            print(f"  пример: id={markets_closed[0].get('id')}, "
                  f"slug={markets_closed[0].get('slug', '')[:40]!r}")

        # 2. GAMMA — топ активный рынок (нужен для CLOB/prices-history тестов)
        t0 = time.time()
        markets_active = await pm.gamma_markets(
            active=True, closed=False, limit=10,
            order="volumeNum", ascending=False,
        )
        dt = time.time() - t0
        save("gamma_markets_active", markets_active)
        print(f"\n[gamma_markets active=true ordered by volume]  {dt:.2f}s")
        print(f"  → {describe(markets_active)}")

        # 3. Выбираем sample — берём топ активный рынок (там есть orderbook)
        if markets_active:
            first = markets_active[0]
            market_id = first.get("id")
            condition_id = first.get("conditionId")
            slug = first.get("slug")
            volume = first.get("volumeNum", 0)
            print(f"\nSample (active high-volume): id={market_id}, "
                  f"slug={slug!r}, volume=${volume:,.0f}")

            # token IDs из clobTokenIds (нужны для CLOB endpoints)
            clob_token_ids_str = first.get("clobTokenIds") or "[]"
            try:
                token_ids = json.loads(clob_token_ids_str) if isinstance(
                    clob_token_ids_str, str
                ) else clob_token_ids_str
            except Exception:
                token_ids = []

            # 4. GAMMA — single market
            try:
                t0 = time.time()
                market_full = await pm.gamma_market_by_id(market_id)
                dt = time.time() - t0
                save("gamma_market_by_id", market_full)
                print(f"\n[gamma_market_by_id {market_id}]  {dt:.2f}s")
                print(f"  → {describe(market_full)}")
            except Exception as e:
                print(f"  ✗ gamma_market_by_id: {e}")

            # 5. CLOB prices-history (TimeSeries)
            if token_ids:
                token_id = token_ids[0]
                for interval in ("1h", "1d", "max"):
                    try:
                        t0 = time.time()
                        ph = await pm.clob_prices_history(
                            market=token_id, interval=interval
                        )
                        dt = time.time() - t0
                        save(f"clob_prices_history_{interval}", ph)
                        print(
                            f"\n[clob_prices_history interval={interval}]"
                            f"  {dt:.2f}s"
                        )
                        if ph and isinstance(ph, list):
                            print(f"  → {len(ph)} точек")
                            print(f"  первая: {ph[0]}")
                            print(f"  последняя: {ph[-1]}")
                        else:
                            print(f"  → {describe(ph)}")
                    except Exception as e:
                        print(f"  ✗ clob_prices_history interval={interval}: {e}")

                # 6. CLOB book
                try:
                    t0 = time.time()
                    book = await pm.clob_book(token_id=token_id)
                    dt = time.time() - t0
                    save("clob_book", book)
                    print(f"\n[clob_book token={token_id[:20]}...]  {dt:.2f}s")
                    print(f"  → {describe(book)}")
                except Exception as e:
                    print(f"  ✗ clob_book: {e}")

                # 7. CLOB price + midpoint
                try:
                    t0 = time.time()
                    price = await pm.clob_price(token_id=token_id, side="buy")
                    midpoint = await pm.clob_midpoint(token_id=token_id)
                    dt = time.time() - t0
                    save("clob_price", price)
                    save("clob_midpoint", midpoint)
                    print(f"\n[clob_price+midpoint]  {dt:.2f}s")
                    print(f"  price → {price}")
                    print(f"  midpoint → {midpoint}")
                except Exception as e:
                    print(f"  ✗ clob_price/midpoint: {e}")

            # 8. CLOB market
            if condition_id:
                try:
                    t0 = time.time()
                    clob_m = await pm.clob_market(condition_id=condition_id)
                    dt = time.time() - t0
                    save("clob_market", clob_m)
                    print(f"\n[clob_market {condition_id[:20]}...]  {dt:.2f}s")
                    print(f"  → {describe(clob_m)}")
                except Exception as e:
                    print(f"  ✗ clob_market: {e}")

                # 9. Data API trades
                try:
                    t0 = time.time()
                    trades = await pm.data_trades(market=condition_id, limit=20)
                    dt = time.time() - t0
                    save("data_trades", trades)
                    print(f"\n[data_trades market={condition_id[:20]}...]  {dt:.2f}s")
                    print(f"  → {describe(trades)}")
                except Exception as e:
                    print(f"  ✗ data_trades: {e}")

                # 10. Data API holders
                try:
                    t0 = time.time()
                    holders = await pm.data_holders(market=condition_id, limit=20)
                    dt = time.time() - t0
                    save("data_holders", holders)
                    print(f"\n[data_holders market={condition_id[:20]}...]  {dt:.2f}s")
                    print(f"  → {describe(holders)}")
                except Exception as e:
                    print(f"  ✗ data_holders: {e}")

        # 11. CLOB markets (пагинация)
        try:
            t0 = time.time()
            clob_list = await pm.clob_markets()
            dt = time.time() - t0
            save("clob_markets_page1", clob_list)
            print(f"\n[clob_markets first page]  {dt:.2f}s")
            print(f"  → {describe(clob_list)}")
            if isinstance(clob_list, dict):
                print(f"  data: {len(clob_list.get('data', []))} markets")
                print(f"  next_cursor: {clob_list.get('next_cursor', '<none>')[:30]}")
        except Exception as e:
            print(f"  ✗ clob_markets: {e}")

        # 12. Gamma events
        try:
            t0 = time.time()
            events = await pm.gamma_events(closed=True, limit=10)
            dt = time.time() - t0
            save("gamma_events_closed", events)
            print(f"\n[gamma_events closed=true limit=10]  {dt:.2f}s")
            print(f"  → {describe(events)}")
            if events:
                print(f"  пример: id={events[0].get('id')}, "
                      f"slug={events[0].get('slug', '')[:40]!r}, "
                      f"markets={len(events[0].get('markets', []))}")
        except Exception as e:
            print(f"  ✗ gamma_events: {e}")

    print("\n" + "━" * 70)
    print(f"  Сэмплы сохранены в: {SAMPLES_DIR}")
    print("━" * 70)


if __name__ == "__main__":
    asyncio.run(main())
