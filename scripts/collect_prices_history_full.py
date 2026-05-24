"""Пересборка prices-history с явным start_ts → ПОЛНАЯ история.

Зачем:
- Текущий датасет prices_history покрывает только ~30 дней (см.
  plans/phase-2-wide-backtest-findings.md), потому что collector
  дёргал API без start_ts.
- Polymarket CLOB /prices-history без start_ts даёт короткое окно.
  С передачей start_ts=market.startDate API возвращает полную историю
  (проверено 2026-05-24).

Что делает:
1. Читает существующий markets_*.parquet
2. Для каждого рынка извлекает startDate → unix_ts
3. Дёргает clob_prices_history(market=token_id, interval=max, start_ts=...)
4. Пишет батчами в data/raw/prices_history_full/batch_*.parquet

Запуск::

    python scripts/collect_prices_history_full.py
    python scripts/collect_prices_history_full.py --max-markets 100  # тест
    python scripts/collect_prices_history_full.py --concurrency 10  # быстрее

После прогона переименовать `prices_history_full/` → `prices_history/` (или
обновить путь в `duckdb_loader.py`).
"""
from __future__ import annotations

import argparse
import asyncio
import json as _json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from loguru import logger  # noqa: E402

from src.collectors.prices_history import PricesHistoryCollector  # noqa: E402


def _parse_iso_to_unix(iso: str | None) -> int | None:
    if not iso or not isinstance(iso, str):
        return None
    s = iso.replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(s).timestamp())
    except (ValueError, TypeError):
        return None


def build_tokens_with_start_ts(
    markets_path: Path,
    *,
    fallback_days_before_end: int = 365,
    max_markets: int | None = None,
) -> tuple[list[tuple[str, str, str]], dict[str, int]]:
    """Прочитать markets parquet, вернуть (tokens, start_ts_by_condition).

    Для каждого рынка start_ts = startDate (если есть),
    иначе endDate - fallback_days_before_end (запас в год).
    """
    df = pd.read_parquet(markets_path)
    if max_markets:
        df = df.head(max_markets)

    tokens: list[tuple[str, str, str]] = []
    start_ts_by_condition: dict[str, int] = {}

    for _, row in df.iterrows():
        cid = row.get("conditionId")
        if not cid:
            continue

        token_yes = row.get("token_id_yes")
        token_no = row.get("token_id_no")

        # Определяем start_ts
        st = _parse_iso_to_unix(row.get("startDate"))
        if st is None:
            # Fallback — endDate минус год
            end_ts = _parse_iso_to_unix(row.get("endDate"))
            if end_ts is not None:
                st = end_ts - fallback_days_before_end * 86400
        if st is None:
            # Нет ни start ни end — пропускаем (хвост артефактов)
            continue

        start_ts_by_condition[str(cid)] = st

        if token_yes:
            tokens.append((str(cid), str(token_yes), "Yes"))
        if token_no:
            tokens.append((str(cid), str(token_no), "No"))

    return tokens, start_ts_by_condition


async def main(args) -> None:
    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  FULL prices-history pull (с явным start_ts)")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # 1. Источник markets — параметр или самый свежий
    if args.markets_file:
        markets_path = ROOT / "data" / "raw" / args.markets_file
        if not markets_path.exists():
            raise SystemExit(f"Не найден файл {markets_path}")
    else:
        markets_files = sorted((ROOT / "data" / "raw").glob("markets_*.parquet"))
        if not markets_files:
            raise SystemExit("Нет markets parquet")
        markets_path = markets_files[-1]
    print(f"📥 Markets source: {markets_path.name}")

    tokens, start_ts_by_condition = build_tokens_with_start_ts(
        markets_path, max_markets=args.max_markets,
    )
    print(f"📊 Будем тянуть {len(tokens):,} token-историй "
          f"({len(start_ts_by_condition):,} рынков с start_ts)")

    if not tokens:
        return

    # 2. Запуск
    out_dir = ROOT / "data" / "raw" / args.out_subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"💾 Out: {out_dir}")

    t0 = time.time()
    BATCH_SIZE = args.batch_size
    batches = [tokens[i : i + BATCH_SIZE] for i in range(0, len(tokens), BATCH_SIZE)]

    async with PricesHistoryCollector(concurrency=args.concurrency) as col:
        for i, batch in enumerate(batches):
            print(f"\n  Batch {i+1}/{len(batches)} ({len(batch)} tokens)")
            df_batch = await col.collect_for_tokens(
                batch,
                interval=args.interval,
                start_ts_by_condition=start_ts_by_condition,
            )
            out_path = out_dir / f"batch_{i:04d}.parquet"
            df_batch.to_parquet(out_path, index=False)
            size_kb = out_path.stat().st_size / 1024
            elapsed = time.time() - t0
            eta_min = (elapsed / (i + 1)) * (len(batches) - i - 1) / 60
            print(f"    💾 {out_path.name} → {len(df_batch):,} точек "
                  f"({size_kb:.0f} KB)  ETA {eta_min:.1f} мин")

    dt = time.time() - t0
    print(f"\n✅ Готово за {dt/60:.1f} мин")
    print(f"\nДальше:\n"
          f"  1. Проверь покрытие: python scripts/run_wide_backtest.py\n"
          f"  2. Если ок — переименуй data/raw/{args.out_subdir} → prices_history "
          f"(сделать backup старого!)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-markets", type=int, default=None,
                       help="Ограничить число рынков для теста")
    parser.add_argument("--interval", default="max",
                       help="prices-history interval (1h/6h/1d/max)")
    parser.add_argument("--concurrency", type=int, default=7,
                       help="Параллельных запросов")
    parser.add_argument("--batch-size", type=int, default=500,
                       help="Токенов на один parquet")
    parser.add_argument("--out-subdir", default="prices_history_full",
                       help="Подпапка в data/raw/ для результатов")
    parser.add_argument("--markets-file", default=None,
                       help="Имя файла в data/raw/ (например markets_processed_2026-05-24.parquet)")
    args = parser.parse_args()
    asyncio.run(main(args))
