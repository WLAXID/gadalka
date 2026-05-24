"""Сборщик прайс-истории по token_id через CLOB /prices-history.

Использование::

    from src.collectors.prices_history import PricesHistoryCollector

    async with PricesHistoryCollector(concurrency=7) as col:
        df = await col.collect_for_tokens(
            tokens=[(condition_id, token_id), ...],
            interval="max",
        )

Производительность:
- 0.12s на запрос, 7 параллельно = ~17 markets/sec
- На 10k рынков → ~10 минут
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Iterable

import pandas as pd
from loguru import logger

from src.api.polymarket import PolymarketClient, PolymarketError


class PricesHistoryCollector:
    """Сборщик исторических цен Polymarket с ограничением concurrency."""

    def __init__(
        self,
        *,
        concurrency: int = 7,
        cache_dir: Path | str | None = None,
        max_retries_per_token: int = 2,
    ) -> None:
        self._client = PolymarketClient(cache_dir=cache_dir)
        self._sem = asyncio.Semaphore(concurrency)
        self._max_retries = max_retries_per_token

    async def __aenter__(self) -> "PricesHistoryCollector":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._client.__aexit__(exc_type, exc, tb)

    async def _fetch_one(
        self,
        condition_id: str,
        token_id: str,
        outcome: str,
        interval: str,
        start_ts: int | None = None,
    ) -> tuple[list[dict], str | None]:
        """Скачать историю одного token_id. Возвращает (points, error).

        Если start_ts передан — API вернёт ПОЛНУЮ историю от этой даты.
        Без start_ts Polymarket возвращает короткое окно (~30d на 'max').
        """
        for attempt in range(self._max_retries):
            try:
                async with self._sem:
                    points = await self._client.clob_prices_history(
                        market=token_id, interval=interval, start_ts=start_ts,
                    )
                return points, None
            except PolymarketError as e:
                if attempt + 1 < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return [], str(e)[:200]
            except Exception as e:
                return [], f"{type(e).__name__}: {str(e)[:150]}"
        return [], "exhausted"

    async def collect_for_tokens(
        self,
        tokens: Iterable[tuple[str, str, str]],
        interval: str = "max",
        progress_every: int = 500,
        start_ts_by_condition: dict[str, int] | None = None,
    ) -> pd.DataFrame:
        """tokens: iterable of (condition_id, token_id, outcome_name).

        start_ts_by_condition: опциональная карта condition_id → unix_ts.
        Если задана — API получит start_ts для каждого токена → ПОЛНАЯ
        history (без этого Polymarket возвращает ~30d).

        Возвращает long DataFrame: одна строка = одна (token, ts) точка.
        """
        tokens_list = list(tokens)
        total = len(tokens_list)
        if total == 0:
            return pd.DataFrame(
                columns=["condition_id", "token_id", "outcome", "t", "p"]
            )

        logger.info(
            "Скачиваем prices-history: {n} tokens, concurrency={c}, interval={i}",
            n=total,
            c=self._sem._value,  # type: ignore[attr-defined]
            i=interval,
        )

        results: list[pd.DataFrame] = []
        errors: list[tuple[str, str, str]] = []
        done = 0
        t0 = time.time()

        async def _worker(condition_id: str, token_id: str, outcome: str):
            start_ts = (
                start_ts_by_condition.get(condition_id)
                if start_ts_by_condition else None
            )
            points, err = await self._fetch_one(
                condition_id, token_id, outcome, interval, start_ts=start_ts,
            )
            return condition_id, token_id, outcome, points, err

        tasks = [_worker(c, t, o) for c, t, o in tokens_list]
        for coro in asyncio.as_completed(tasks):
            condition_id, token_id, outcome, points, err = await coro
            done += 1
            if err:
                errors.append((condition_id, token_id, err))
            elif points:
                df_one = pd.DataFrame(points)
                df_one["condition_id"] = condition_id
                df_one["token_id"] = token_id
                df_one["outcome"] = outcome
                results.append(df_one)
            if done % progress_every == 0 or done == total:
                dt = time.time() - t0
                rps = done / max(dt, 0.001)
                eta = (total - done) / max(rps, 0.001)
                logger.info(
                    "  ...{d}/{t}  ({rps:.1f} req/s, ETA {eta:.0f}s, errors={e})",
                    d=done,
                    t=total,
                    rps=rps,
                    eta=eta,
                    e=len(errors),
                )

        if errors:
            logger.warning("Ошибок: {e} из {t}", e=len(errors), t=total)
            # Первые 3 ошибки в лог
            for cid, tid, err in errors[:3]:
                logger.warning("  {cid} {tid}: {err}", cid=cid[:20], tid=tid[:20], err=err)

        if not results:
            return pd.DataFrame(
                columns=["condition_id", "token_id", "outcome", "t", "p"]
            )

        df = pd.concat(results, ignore_index=True)
        df = df[["condition_id", "token_id", "outcome", "t", "p"]].copy()
        # Приведение типов
        df["t"] = pd.to_numeric(df["t"], errors="coerce").astype("Int64")
        df["p"] = pd.to_numeric(df["p"], errors="coerce")
        df = df.dropna(subset=["t", "p"])
        return df
