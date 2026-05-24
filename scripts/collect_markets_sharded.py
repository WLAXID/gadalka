"""Sharded сбор ВСЕХ closed markets через диапазоны endDate.

Зачем: текущий collector упирался в hard cap offset=10000 и собирал
только самые «свежие» досрочно-резолвнутые рынки. На репрезентативной
выборке backtest даст честные цифры.

Стратегия: бьём end_date диапазон по полугодиям. Если в полугодии
>10k markets — дробим до квартала / месяца.

Запуск::

    python scripts/collect_markets_sharded.py             # все диапазоны
    python scripts/collect_markets_sharded.py --test      # один диапазон
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from loguru import logger  # noqa: E402

from src.api.polymarket import PolymarketClient, PolymarketError  # noqa: E402


# Диапазоны: полугодия от 2020 до 2026 + future для активных
RANGES = [
    ("2020-01-01", "2020-12-31"),
    ("2021-01-01", "2021-06-30"),
    ("2021-07-01", "2021-12-31"),
    ("2022-01-01", "2022-06-30"),
    ("2022-07-01", "2022-12-31"),
    ("2023-01-01", "2023-06-30"),
    ("2023-07-01", "2023-12-31"),
    ("2024-01-01", "2024-06-30"),
    ("2024-07-01", "2024-12-31"),
    ("2025-01-01", "2025-06-30"),
    ("2025-07-01", "2025-12-31"),
    ("2026-01-01", "2026-06-30"),
    ("2026-07-01", "2027-12-31"),
    ("2028-01-01", "2030-12-31"),
]


async def collect_range(client: PolymarketClient, date_min: str, date_max: str,
                        *, page_size: int = 100) -> list[dict]:
    """Собрать все markets в данном end_date диапазоне."""
    rows: list[dict] = []
    offset = 0
    while True:
        try:
            page = await client.gamma_markets(
                closed=True,
                limit=page_size, offset=offset,
                end_date_min=date_min, end_date_max=date_max,
                order="endDate", ascending=True,
            )
        except PolymarketError as e:
            if "offset exceeds maximum" in str(e).lower():
                logger.warning(
                    "  [range {a}..{b}] упёрлись в offset cap на {t} markets, "
                    "ДРОБИТЬ дальше нужно",
                    a=date_min, b=date_max, t=len(rows),
                )
                break
            raise
        n = len(page)
        if n == 0:
            break
        rows.extend(page)
        if n < page_size:
            break
        offset += n
        if offset > 10000:
            break
    return rows


async def main(test_only: bool) -> None:
    print("=" * 70, flush=True)
    print("  Sharded collect closed markets через end_date диапазоны", flush=True)
    print("=" * 70, flush=True)

    ranges = RANGES[:1] if test_only else RANGES
    print(f"[plan] {len(ranges)} диапазонов", flush=True)
    for a, b in ranges:
        print(f"  • {a} .. {b}", flush=True)
    print()

    all_rows: list[dict] = []
    range_stats: list[dict] = []

    t0 = time.time()
    async with PolymarketClient() as client:
        for i, (date_min, date_max) in enumerate(ranges):
            t1 = time.time()
            rows = await collect_range(client, date_min, date_max)
            dt = time.time() - t1
            all_rows.extend(rows)
            print(f"  [{i+1}/{len(ranges)}] {date_min}..{date_max}: "
                  f"{len(rows):>5} markets  ({dt:.1f}s, total {len(all_rows):,})",
                  flush=True)
            range_stats.append({"range": f"{date_min}..{date_max}",
                                "n": len(rows), "duration_s": round(dt, 1)})

    print(f"\n[total] {len(all_rows):,} markets за {time.time()-t0:.1f}s", flush=True)

    # Сохраним два файла:
    # 1) raw — все как есть
    # 2) dedup — без дублей по conditionId
    out_dir = ROOT / "data" / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(all_rows)
    print(f"[dedup] до: {len(df)}", flush=True)
    df = df.drop_duplicates(subset=["conditionId"], keep="first")
    print(f"[dedup] после dedup: {len(df)}", flush=True)

    # Сериализуем list/dict поля в JSON (для parquet)
    for col in df.columns:
        sample = next((v for v in df[col] if v is not None), None)
        if isinstance(sample, (list, dict)):
            df[col] = df[col].apply(
                lambda x: json.dumps(x, default=str) if x is not None else None
            )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = out_dir / f"markets_sharded_{today}.parquet"
    df.to_parquet(out_path, index=False)
    print(f"💾 Saved {len(df)} markets → {out_path.name}", flush=True)

    # Stats
    stats_path = ROOT / "data" / "wide_backtest" / "sharded_collect_stats.json"
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_collected": len(all_rows),
        "after_dedup": len(df),
        "ranges": range_stats,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"💾 Stats → {stats_path.name}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true",
                       help="Только первый диапазон для теста")
    args = parser.parse_args()
    asyncio.run(main(args.test))
