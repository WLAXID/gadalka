"""Полный pipeline сбора датасета Day 3:

1. Собрать все закрытые рынки через Gamma — `data/raw/markets_<date>.parquet`
2. Собрать prices-history для каждого token_id — `data/raw/prices_history/*.parquet`
3. Зарегистрировать views в DuckDB

Запуск::

    python scripts/collect_dataset.py             # полный pull
    python scripts/collect_dataset.py --max-pages 2  # только 1000 рынков для теста
    python scripts/collect_dataset.py --skip-prices  # только metadata
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from loguru import logger  # noqa: E402

from src.collectors.gamma_markets import MarketsCollector  # noqa: E402
from src.collectors.prices_history import PricesHistoryCollector  # noqa: E402
from src.storage.duckdb_loader import GadalkaDB  # noqa: E402


CACHE_DIR = ROOT / "data" / "cache" / "polymarket"


async def step_markets(*, page_size: int, max_pages: int | None):
    """Шаг 1 — скачать metadata закрытых рынков."""
    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  STEP 1 — закрытые рынки (Gamma /markets?closed=true)")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    t0 = time.time()
    async with MarketsCollector(cache_dir=None) as col:
        df = await col.collect_all_closed(page_size=page_size, max_pages=max_pages)

    dt = time.time() - t0
    print(f"\n✅ Собрано {len(df):,} рынков за {dt:.1f}s")

    # Сохраняем — pyarrow не любит mixed nested типы, нормализуем dict-колонки
    out_path = ROOT / "data" / "raw" / f"markets_{datetime.now(timezone.utc):%Y-%m-%d}.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Удаляем колонки с проблемными типами (vложенный dict без схемы)
    df_save = df.copy()
    # Колонка events часто содержит list[dict] — оставляем как JSON-строки
    import json as _json
    for col in df_save.columns:
        sample = next((v for v in df_save[col] if v is not None), None)
        if isinstance(sample, (list, dict)):
            df_save[col] = df_save[col].apply(
                lambda x: _json.dumps(x, default=str) if x is not None else None
            )

    df_save.to_parquet(out_path, index=False)
    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"💾 Сохранено: {out_path.name} ({size_mb:.1f} MB)")

    return df


async def step_prices(df_markets, *, interval: str = "max", concurrency: int = 7):
    """Шаг 2 — скачать prices-history для каждого outcome-токена."""
    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  STEP 2 — prices history (interval={interval}, concurrency={concurrency})")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Собираем (condition_id, token_id, outcome) — обычно 2 outcome на рынок
    tokens: list[tuple[str, str, str]] = []
    for _, row in df_markets.iterrows():
        cid = row.get("conditionId")
        if not cid:
            continue
        token_yes = row.get("token_id_yes")
        token_no = row.get("token_id_no")
        if token_yes:
            tokens.append((cid, str(token_yes), "Yes"))
        if token_no:
            tokens.append((cid, str(token_no), "No"))

    print(f"📊 Будем тянуть {len(tokens):,} token-историй для {len(df_markets):,} рынков")

    t0 = time.time()
    out_dir = ROOT / "data" / "raw" / "prices_history"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Бьём токены на батчи по 500, чтобы писать в parquet по ходу
    BATCH_SIZE = 500
    batches = [tokens[i : i + BATCH_SIZE] for i in range(0, len(tokens), BATCH_SIZE)]

    async with PricesHistoryCollector(concurrency=concurrency) as col:
        for i, batch in enumerate(batches):
            print(f"\n  Batch {i+1}/{len(batches)} ({len(batch)} tokens)")
            df_batch = await col.collect_for_tokens(batch, interval=interval)
            out_path = out_dir / f"batch_{i:04d}.parquet"
            df_batch.to_parquet(out_path, index=False)
            size_kb = out_path.stat().st_size / 1024
            print(f"    💾 {out_path.name} → {len(df_batch):,} точек ({size_kb:.0f} KB)")

    dt = time.time() - t0
    print(f"\n✅ Готово за {dt/60:.1f} мин")


def step_duckdb():
    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  STEP 3 — DuckDB views")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    with GadalkaDB(ROOT / "data" / "processed" / "gadalka.duckdb") as db:
        db.register_parquet_views(root=ROOT)
        n_markets = db.df("SELECT COUNT(*) AS n FROM markets").iloc[0]["n"]
        n_prices = db.df("SELECT COUNT(*) AS n FROM prices_history").iloc[0]["n"]
        print(f"  markets:         {n_markets:>10,}")
        print(f"  prices_history:  {n_prices:>10,}")


async def main(args) -> None:
    df = await step_markets(page_size=args.page_size, max_pages=args.max_pages)
    if not args.skip_prices:
        await step_prices(
            df, interval=args.interval, concurrency=args.concurrency
        )
    step_duckdb()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--max-pages", type=int, default=None,
                       help="Ограничить количество страниц (для тестов)")
    parser.add_argument("--interval", default="max",
                       help="prices-history interval: 1m/1h/1d/max")
    parser.add_argument("--concurrency", type=int, default=7,
                       help="Параллельных запросов prices-history")
    parser.add_argument("--skip-prices", action="store_true",
                       help="Не тянуть prices-history (только metadata)")
    args = parser.parse_args()
    asyncio.run(main(args))
