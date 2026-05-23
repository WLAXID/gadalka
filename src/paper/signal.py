"""Генератор сигналов: ходит в Polymarket API и решает нужно ли войти.

Алгоритм H1 baseline:
1. Достать все active рынки через `gamma_markets(active=True, closed=False)`
2. Для каждого с volumeNum >= 100, получить `clob_prices_history(token_id_yes, interval=1h)`
3. Найти цену за T-24h до now
4. Если цена в [low, high] И нет уже открытой ставки → создать paper trade

NB: используем "now - 24h" а не "close_ts - 24h" потому что мы ловим
рынок ДО резолва (т.е. до close_ts).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Iterable

from loguru import logger

from src.api.polymarket import PolymarketClient, PolymarketError
from src.paper.config import PaperConfig
from src.paper.state import PaperState


@dataclass
class Signal:
    condition_id: str
    token_id: str
    slug: str
    question: str
    end_date_iso: str | None
    volume: float
    price_at_t24h: float
    current_mid: float


class SignalGenerator:
    """H1 baseline scanner."""

    def __init__(self, config: PaperConfig, state: PaperState) -> None:
        self.cfg = config
        self.state = state

    async def scan(self, client: PolymarketClient) -> list[Signal]:
        """Один проход: вернуть список валидных сигналов."""
        if self.state.is_paused():
            logger.info("[signal] на паузе, пропускаем скан")
            return []

        signals: list[Signal] = []
        now_ts = int(time.time())

        # 1) Достаём активные рынки. Пагинация — берём топ по volumeNum
        try:
            markets = await client.gamma_markets(
                active=True, closed=False, limit=100,
                order="volumeNum", ascending=False,
            )
        except PolymarketError as e:
            self.state.log_event("error", "signal", f"gamma_markets failed: {e}")
            return []

        logger.info("[signal] получено {n} active рынков", n=len(markets))

        # 2) Фильтруем по volume и парсим clobTokenIds
        candidates = []
        for m in markets:
            vol = m.get("volumeNum") or 0
            if vol < self.cfg.min_market_volume:
                continue
            clob_ids_raw = m.get("clobTokenIds")
            if not clob_ids_raw:
                continue
            try:
                token_ids = (
                    json.loads(clob_ids_raw)
                    if isinstance(clob_ids_raw, str)
                    else clob_ids_raw
                )
            except Exception:
                continue
            if not token_ids or len(token_ids) < 1:
                continue
            token_yes = token_ids[0]
            condition_id = m.get("conditionId")
            if not condition_id:
                continue
            # пропускаем рынки на которые уже сделали ставку
            if self.state.has_trade_for(condition_id, str(token_yes)):
                continue
            candidates.append((m, condition_id, str(token_yes), float(vol)))

        logger.info("[signal] {n} кандидатов после фильтров", n=len(candidates))

        # 3) Для каждого кандидата — prices-history (concurrency=5)
        sem = asyncio.Semaphore(5)

        async def _check(market, condition_id, token_id, volume):
            async with sem:
                try:
                    points = await client.clob_prices_history(
                        market=token_id, interval="1h"
                    )
                except PolymarketError as e:
                    return None
                if not points:
                    return None
                # Ищем точку ближе всего к now - 86400
                target = now_ts - 86400
                # points отсортированы по t возрастающе. Берём последнюю с t <= target
                price_t24h = None
                for p in points:
                    t = p.get("t")
                    pp = p.get("p")
                    if t is None or pp is None:
                        continue
                    if t <= target:
                        price_t24h = float(pp)
                    else:
                        break
                if price_t24h is None:
                    # рынок моложе 24h — пропускаем
                    return None
                current_mid = float(points[-1].get("p") or 0)
                if not (self.cfg.strategy_low <= price_t24h < self.cfg.strategy_high):
                    return None
                return Signal(
                    condition_id=condition_id,
                    token_id=token_id,
                    slug=market.get("slug") or "",
                    question=market.get("question") or "",
                    end_date_iso=market.get("endDate"),
                    volume=volume,
                    price_at_t24h=price_t24h,
                    current_mid=current_mid,
                )

        tasks = [_check(m, c, t, v) for (m, c, t, v) in candidates]
        for coro in asyncio.as_completed(tasks):
            r = await coro
            if r is not None:
                signals.append(r)

        logger.info(
            "[signal] найдено {n} сигналов в [{lo}, {hi}]",
            n=len(signals),
            lo=self.cfg.strategy_low,
            hi=self.cfg.strategy_high,
        )
        return signals
