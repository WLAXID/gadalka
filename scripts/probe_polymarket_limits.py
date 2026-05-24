"""Probe реальных лимитов Polymarket Gamma API.

Зачем: понимаем сколько активных и закрытых рынков существует на самом
деле, и можем ли мы их все получить — или upper-bound = hard cap.

Параллельно дёргаем разные комбинации фильтров и считаем результат.

Запуск::

    python scripts/probe_polymarket_limits.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.api.polymarket import PolymarketClient, PolymarketError  # noqa: E402


async def count_with_filter(
    client: PolymarketClient,
    label: str,
    **kwargs,
) -> tuple[str, int, int, str | None]:
    """Прогон пагинации с заданными фильтрами. Возвращает (label, total, last_offset, error)."""
    t0 = time.time()
    total = 0
    offset = 0
    page_size = 100
    error = None
    try:
        while True:
            page = await client.gamma_markets(limit=page_size, offset=offset, **kwargs)
            n = len(page)
            total += n
            if n < page_size:
                break
            offset += n
            if offset > 12000:  # safety
                error = "stopped_at_12k"
                break
    except PolymarketError as e:
        if "offset exceeds maximum" in str(e).lower():
            error = f"offset_cap@{offset}"
        else:
            error = f"{type(e).__name__}: {str(e)[:80]}"
    dt = time.time() - t0
    print(f"  [{dt:5.1f}s] {label:40} → {total:>6,} markets  (err: {error})")
    return label, total, offset, error


async def main() -> None:
    print("="*70)
    print("  Probe Polymarket Gamma API limits")
    print("="*70)
    print()

    async with PolymarketClient() as client:
        # Запускаем 6 параллельных пагинаций с разными фильтрами
        print("Запускаем 6 параллельных probe...\n")

        tasks = [
            count_with_filter(
                client, "active=True, closed=False",
                active=True, closed=False, order="endDate", ascending=True,
            ),
            count_with_filter(
                client, "active=True, closed=False, vol DESC",
                active=True, closed=False, order="volumeNum", ascending=False,
            ),
            count_with_filter(
                client, "active=True (без closed-фильтра)",
                active=True, order="endDate", ascending=False,
            ),
            count_with_filter(
                client, "closed=True (только закрытые)",
                closed=True, order="endDate", ascending=False,
            ),
            count_with_filter(
                client, "closed=True ascending (старые сверху)",
                closed=True, order="endDate", ascending=True,
            ),
            count_with_filter(
                client, "archived=False",
                archived=False, order="endDate", ascending=False,
            ),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    print("\n=== Итог ===")
    for r in results:
        if isinstance(r, Exception):
            print(f"  ❌ {type(r).__name__}: {r}")
            continue
        label, total, last_offset, error = r
        cap_hit = error and "offset_cap" in error
        verdict = "🚧 CAPPED" if cap_hit else "✅ полный обход" if error is None else f"⚠ {error}"
        print(f"  {label:42} → {total:>6,} markets  [{verdict}]")
    print()
    print("Интерпретация:")
    print("- Если CAPPED — реальное число рынков > что мы получаем")
    print("- Если ✅ полный обход — собрали всё, дальше не идти")
    print("- Сравни active vs closed для оценки масштаба")


if __name__ == "__main__":
    asyncio.run(main())
