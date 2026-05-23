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
from dataclasses import dataclass, field
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


@dataclass
class ScanStats:
    """Что увидел сканер в одном проходе."""

    ts: int
    total_active: int = 0                # пришло из /markets
    skip_low_volume: int = 0             # отфильтровали по volume < min
    skip_no_token: int = 0               # без token_id (битый)
    skip_already_taken: int = 0          # уже есть наша ставка
    candidates: int = 0                  # пошли в prices-history
    skip_no_history: int = 0             # рынок моложе 24h или нет данных
    skip_below: int = 0                  # price < low
    skip_above: int = 0                  # price >= high
    in_range: int = 0                    # ⭐ попали в [low, high]
    near_below: list[dict] = field(default_factory=list)
    near_above: list[dict] = field(default_factory=list)
    duration_s: float = 0.0


class SignalGenerator:
    """H1 baseline scanner."""

    def __init__(self, config: PaperConfig, state: PaperState) -> None:
        self.cfg = config
        self.state = state

    async def scan(self, client: PolymarketClient) -> tuple[list[Signal], ScanStats]:
        """Один проход: вернуть (сигналы, детальная статистика скана)."""
        t_start = time.time()
        now_ts = int(t_start)
        stats = ScanStats(ts=now_ts)

        if self.state.is_paused():
            logger.info("[signal] на паузе, пропускаем скан")
            stats.duration_s = time.time() - t_start
            return [], stats

        signals: list[Signal] = []

        try:
            markets = await client.gamma_markets(
                active=True, closed=False, limit=100,
                order="volumeNum", ascending=False,
            )
        except PolymarketError as e:
            self.state.log_event("error", "signal", f"gamma_markets failed: {e}")
            stats.duration_s = time.time() - t_start
            return [], stats

        stats.total_active = len(markets)
        logger.info("[signal] получено {n} active рынков", n=len(markets))

        candidates = []
        for m in markets:
            vol = m.get("volumeNum") or 0
            if vol < self.cfg.min_market_volume:
                stats.skip_low_volume += 1
                continue
            clob_ids_raw = m.get("clobTokenIds")
            if not clob_ids_raw:
                stats.skip_no_token += 1
                continue
            try:
                token_ids = (
                    json.loads(clob_ids_raw)
                    if isinstance(clob_ids_raw, str)
                    else clob_ids_raw
                )
            except Exception:
                stats.skip_no_token += 1
                continue
            if not token_ids or len(token_ids) < 1:
                stats.skip_no_token += 1
                continue
            token_yes = token_ids[0]
            condition_id = m.get("conditionId")
            if not condition_id:
                stats.skip_no_token += 1
                continue
            if self.state.has_trade_for(condition_id, str(token_yes)):
                stats.skip_already_taken += 1
                continue
            candidates.append((m, condition_id, str(token_yes), float(vol)))

        stats.candidates = len(candidates)
        logger.info("[signal] {n} кандидатов после фильтров", n=stats.candidates)

        sem = asyncio.Semaphore(5)

        async def _check(market, condition_id, token_id, volume):
            async with sem:
                try:
                    points = await client.clob_prices_history(
                        market=token_id, interval="1h"
                    )
                except PolymarketError:
                    return ("no_history", None, None)
                if not points:
                    return ("no_history", None, None)
                target = now_ts - 86400
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
                    return ("no_history", None, None)
                current_mid = float(points[-1].get("p") or 0)
                meta = {
                    "slug": market.get("slug") or "",
                    "question": market.get("question") or "",
                    "volume": volume,
                    "price_yes_t24h": price_t24h,
                    "current_mid": current_mid,
                    "end_date_iso": market.get("endDate"),
                    "condition_id": condition_id,
                    "token_id": token_id,
                }
                if price_t24h < self.cfg.strategy_low:
                    return ("below", meta, None)
                if price_t24h >= self.cfg.strategy_high:
                    return ("above", meta, None)
                sig = Signal(
                    condition_id=condition_id,
                    token_id=token_id,
                    slug=meta["slug"],
                    question=meta["question"],
                    end_date_iso=meta["end_date_iso"],
                    volume=volume,
                    price_at_t24h=price_t24h,
                    current_mid=current_mid,
                )
                return ("in_range", meta, sig)

        tasks = [_check(m, c, t, v) for (m, c, t, v) in candidates]
        near_below_acc: list[dict] = []
        near_above_acc: list[dict] = []
        for coro in asyncio.as_completed(tasks):
            tag, meta, sig = await coro
            if tag == "no_history":
                stats.skip_no_history += 1
            elif tag == "below":
                stats.skip_below += 1
                near_below_acc.append(meta)
            elif tag == "above":
                stats.skip_above += 1
                near_above_acc.append(meta)
            elif tag == "in_range":
                stats.in_range += 1
                signals.append(sig)

        # Топ-5 near-miss ниже диапазона (приближаются снизу к low)
        near_below_acc.sort(key=lambda x: -x["price_yes_t24h"])
        stats.near_below = near_below_acc[:5]
        # Топ-5 near-miss выше диапазона (приближаются сверху к high)
        near_above_acc.sort(key=lambda x: x["price_yes_t24h"])
        stats.near_above = near_above_acc[:5]

        stats.duration_s = time.time() - t_start
        logger.info(
            "[signal] {n} signals; below={b} above={a} no_hist={h} (за {d:.1f}s)",
            n=stats.in_range,
            b=stats.skip_below,
            a=stats.skip_above,
            h=stats.skip_no_history,
            d=stats.duration_s,
        )
        return signals, stats
