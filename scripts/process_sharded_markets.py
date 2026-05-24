"""Применить derive-логику (token_id_yes/no, resolved_yes, final_price_*)
к raw sharded markets parquet.

Использует MarketsCollector._to_dataframe — ту же логику что в обычном pipeline.

Запуск::

    python scripts/process_sharded_markets.py
"""
from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import json  # noqa: E402

import pandas as pd  # noqa: E402

from src.collectors.gamma_markets import MarketsCollector  # noqa: E402


def main() -> None:
    raw_files = sorted((ROOT / "data" / "raw").glob("markets_sharded_*.parquet"))
    if not raw_files:
        raise SystemExit("Нет sharded raw parquet")
    raw_path = raw_files[-1]
    print(f"[in]  {raw_path.name}")
    df = pd.read_parquet(raw_path)
    print(f"[in]  {len(df):,} markets")

    # Распарсить JSON-сериализованные поля обратно в list/dict
    for col in ("outcomes", "outcomePrices", "clobTokenIds", "events"):
        if col in df.columns:
            def _parse(v):
                if v is None or pd.isna(v):
                    return None
                if isinstance(v, (list, dict)):
                    return v
                try:
                    return json.loads(v)
                except Exception:
                    return None
            df[col] = df[col].apply(_parse)

    # Применить derive
    df_processed = MarketsCollector._to_dataframe(df.to_dict("records"))
    print(f"[out] {len(df_processed):,} markets after derive")

    # Selection bias check
    n_past = df_processed[df_processed["endDate"].apply(
        lambda x: x is not None and x < "2026-05-24"
    )].shape[0] if "endDate" in df_processed.columns else 0
    n_future = len(df_processed) - n_past
    print(f"[bias] past endDate: {n_past:,}   future endDate: {n_future:,}")

    resolved = df_processed["resolved_yes"].notna().sum() \
        if "resolved_yes" in df_processed.columns else 0
    print(f"[bias] with resolved_yes: {resolved:,}")

    # Re-сериализуем перед сохранением
    df_save = df_processed.copy()
    for col in df_save.columns:
        sample = next((v for v in df_save[col] if v is not None), None)
        if isinstance(sample, (list, dict)):
            df_save[col] = df_save[col].apply(
                lambda x: json.dumps(x, default=str) if x is not None else None
            )

    out_path = raw_path.parent / raw_path.name.replace(
        "markets_sharded_", "markets_processed_")
    df_save.to_parquet(out_path, index=False)
    print(f"💾 Saved → {out_path.name}")


if __name__ == "__main__":
    main()
