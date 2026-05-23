"""Сборщик metadata всех закрытых рынков Polymarket через Gamma API.

Использование::

    from src.collectors.gamma_markets import MarketsCollector

    async with MarketsCollector() as col:
        df = await col.collect_all_closed(page_size=500)
        df.to_parquet("data/raw/markets.parquet")

Производительность:
- pagination через offset, 500 рынков на запрос
- ~20 запросов на 10k рынков = ~5 секунд
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

from src.api.polymarket import PolymarketClient, PolymarketError


class MarketsCollector:
    """Тонкая обёртка над PolymarketClient под задачу 'скачать всё'."""

    def __init__(self, *, cache_dir: Path | str | None = None) -> None:
        self._client = PolymarketClient(cache_dir=cache_dir)

    async def __aenter__(self) -> "MarketsCollector":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._client.__aexit__(exc_type, exc, tb)

    # Polymarket Gamma режет limit максимум до 100 (проверено 2026-05-23)
    GAMMA_PAGE_CAP = 100

    async def collect_all_closed(
        self,
        *,
        page_size: int = 100,
        max_pages: int | None = None,
    ) -> pd.DataFrame:
        """Скачать все закрытые рынки через offset-пагинацию.

        Polymarket режет limit до 100 — больше за один запрос не дают.
        Останавливаемся когда страница пуста.
        """
        page_size = min(page_size, self.GAMMA_PAGE_CAP)
        all_rows: list[dict] = []
        offset = 0
        page_no = 0
        while True:
            try:
                page = await self._client.gamma_markets(
                    closed=True,
                    limit=page_size,
                    offset=offset,
                    order="endDate",
                    ascending=False,
                )
            except PolymarketError as e:
                # Polymarket Gamma не пускает offset > 10000.
                # Это hard cap — берём что есть и идём дальше.
                if "offset exceeds maximum" in str(e):
                    logger.warning(
                        "Достигнут лимит Gamma offset (>10000), "
                        "останавливаемся на {t} рынках",
                        t=len(all_rows),
                    )
                    break
                raise
            n = len(page)
            page_no += 1
            if page_no == 1 or page_no % 10 == 0:
                logger.info(
                    "page #{p}: +{n} markets (offset={o}, total так далеко {t})",
                    p=page_no,
                    n=n,
                    o=offset,
                    t=len(all_rows) + n,
                )
            all_rows.extend(page)
            if n == 0:
                break
            offset += n
            if max_pages and page_no >= max_pages:
                logger.info("max_pages={mp} достигнуто, останавливаемся", mp=max_pages)
                break

        df = self._to_dataframe(all_rows)
        logger.info("Собрано {n} закрытых рынков ({p} страниц)", n=len(df), p=page_no)
        return df

    @staticmethod
    def _to_dataframe(rows: list[dict]) -> pd.DataFrame:
        """Раскладывем raw-JSON по плоской схеме."""
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)

        # Парсим JSON-кодированные поля
        for col in ("outcomes", "outcomePrices", "clobTokenIds"):
            if col in df.columns:
                df[col + "_parsed"] = df[col].apply(_safe_json_loads)

        # Численные поля — Gamma часто отдаёт строкой
        for col in ("volume", "volumeNum", "liquidity"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Извлекаем первый/второй token_id для удобства
        if "clobTokenIds_parsed" in df.columns:
            df["token_id_yes"] = df["clobTokenIds_parsed"].apply(
                lambda x: x[0] if isinstance(x, list) and len(x) >= 1 else None
            )
            df["token_id_no"] = df["clobTokenIds_parsed"].apply(
                lambda x: x[1] if isinstance(x, list) and len(x) >= 2 else None
            )

        # Извлекаем финальную outcome-price для YES
        if "outcomePrices_parsed" in df.columns:
            df["final_price_yes"] = df["outcomePrices_parsed"].apply(
                lambda x: _to_float(x[0]) if isinstance(x, list) and len(x) >= 1 else None
            )
            df["final_price_no"] = df["outcomePrices_parsed"].apply(
                lambda x: _to_float(x[1]) if isinstance(x, list) and len(x) >= 2 else None
            )
            # Резолв = 1 если final_price_yes >= 0.5 (это рынок резолвнулся YES)
            df["resolved_yes"] = df["final_price_yes"].apply(
                lambda v: bool(v >= 0.5) if v is not None else None
            )

        # Удаляем nested-колонки которые plain-parquet не любит
        # (оставляем _parsed варианты тоже — parquet поддерживает списки)
        return df


def _safe_json_loads(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None
