"""Probe какие фильтры Gamma поддерживает для sharded pagination.

Тестируем:
- start_date_min/start_date_max
- end_date_min/end_date_max
- volume_num_min/volume_num_max
- liquidity_num_min/liquidity_num_max

Запуск::

    python scripts/probe_gamma_filters.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

API = "https://gamma-api.polymarket.com/markets"


async def probe_one(client: httpx.AsyncClient, label: str, params: dict) -> str:
    p = dict(params)
    p["limit"] = 5
    try:
        r = await client.get(API, params=p, timeout=15)
        status = r.status_code
        if status != 200:
            return f"  {label:45} → status {status}, body: {r.text[:80]}"
        data = r.json()
        n = len(data) if isinstance(data, list) else 0
        # Sample endDate
        sample_end = data[0].get("endDate") if n > 0 else None
        sample_start = data[0].get("startDate") if n > 0 else None
        return f"  {label:45} → {n} markets, sample end={sample_end}, start={sample_start}"
    except Exception as e:
        return f"  {label:45} → ERROR {type(e).__name__}: {e}"


async def main() -> None:
    print("=" * 70)
    print("  Probe Gamma /markets filters for sharding")
    print("=" * 70)
    async with httpx.AsyncClient(trust_env=False) as client:
        probes = [
            ("base: closed=true", {"closed": "true"}),
            ("end_date_min=2025-01", {"closed": "true", "end_date_min": "2025-01-01"}),
            ("end_date_max=2025-01", {"closed": "true", "end_date_max": "2025-01-01"}),
            ("end_date_min+max range", {"closed": "true",
                                          "end_date_min": "2025-01-01",
                                          "end_date_max": "2025-06-30"}),
            ("start_date_min=2024-06", {"closed": "true", "start_date_min": "2024-06-01"}),
            ("start_date_max=2024-12", {"closed": "true", "start_date_max": "2024-12-31"}),
            ("startDateMin (camel)", {"closed": "true", "startDateMin": "2024-06-01"}),
            ("endDateMin (camel)", {"closed": "true", "endDateMin": "2025-01-01"}),
            ("volume_num_min=10000", {"closed": "true", "volume_num_min": 10000}),
            ("volume_num_max=10000", {"closed": "true", "volume_num_max": 10000}),
            ("volumeNumMin (camel)", {"closed": "true", "volumeNumMin": 10000}),
            ("liquidity_num_min=1000", {"closed": "true", "liquidity_num_min": 1000}),
            ("created_after=ts", {"closed": "true", "created_after": 1735000000}),
            ("offset=15000 (test cap)", {"closed": "true", "offset": 15000}),
        ]
        results = await asyncio.gather(*[probe_one(client, l, p) for l, p in probes])
        for r in results:
            print(r)
    print()
    print("Ищи: фильтры которые меняют результат (sample endDate)")
    print("Все 5-markets sample с тем же endDate = фильтр игнорится")


if __name__ == "__main__":
    asyncio.run(main())
