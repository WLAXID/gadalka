"""Генератор сигналов: ходит в Polymarket API и решает нужно ли войти.

Алгоритм H1 live (упрощённая версия бэктестовой стратегии):
1. Достать топ-100 active рынков через `gamma_markets(active=True, closed=False)`
   с сортировкой по volumeNum DESC.
2. Отфильтровать: volumeNum >= min_market_volume, enableOrderBook=True,
   валидный clobTokenIds, не уже взятый condition_id.
3. Прочитать текущую цену YES из `outcomePrices[0]`.
4. Если цена в [strategy_low, strategy_high) → создать paper trade.

Прим: бэктест смотрел цену за T-24h до closedTime. В live мы используем
текущую цену из outcomePrices как proxy — edge был стабилен по всем
горизонтам T-1h..T-7d на исторических данных (см. Phase 1 findings).
Поле Signal.price_at_t24h оставлено для обратной совместимости с
форматтерами; хранит ту же current_mid.
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
    total_active: int = 0
    skip_low_volume: int = 0
    skip_no_token: int = 0
    skip_already_taken: int = 0
    candidates: int = 0
    skip_no_history: int = 0
    skip_below: int = 0
    skip_above: int = 0
    in_range: int = 0
    near_below: list[dict] = field(default_factory=list)
    near_above: list[dict] = field(default_factory=list)
    duration_s: float = 0.0


class SignalGenerator:
    """H1 baseline scanner."""

    def __init__(self, config: PaperConfig, state: PaperState) -> None:
        self.cfg = config
        self.state = state

    async def scan(self, client: PolymarketClient) -> tuple[list[Signal], ScanStats]:
        """Один проход: вернуть (сигналы, детальная статистика скана).

        Логика live-стратегии:
        - Текущая цена YES (из outcomePrices) в [strategy_low, strategy_high]
        - Volume >= min_market_volume
        - Рынок активный, ещё не закрылся
        - Не делали ставку на этот рынок ранее

        Прим: бэктест смотрел цену за T-24h до closedTime. В live мы
        используем текущую цену из outcomePrices как proxy — edge был
        стабилен по всем горизонтам T-1h..T-7d.
        """
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

        near_below_acc: list[dict] = []
        near_above_acc: list[dict] = []

        for m in markets:
            vol = m.get("volumeNum") or 0
            if vol < self.cfg.min_market_volume:
                stats.skip_low_volume += 1
                continue
            # Только рынки с активным orderbook (CLOB) — без legacy FPMM
            if not m.get("enableOrderBook", True):
                stats.skip_no_token += 1
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
            token_yes = str(token_ids[0])
            condition_id = m.get("conditionId")
            if not condition_id:
                stats.skip_no_token += 1
                continue
            if self.state.has_trade_for(condition_id, token_yes):
                stats.skip_already_taken += 1
                continue

            # Парсим outcomePrices — текущая цена YES
            op_raw = m.get("outcomePrices")
            if not op_raw:
                stats.skip_no_history += 1
                continue
            try:
                op = json.loads(op_raw) if isinstance(op_raw, str) else op_raw
            except Exception:
                stats.skip_no_history += 1
                continue
            if not op or len(op) < 1:
                stats.skip_no_history += 1
                continue
            try:
                price_yes = float(op[0])
            except (ValueError, TypeError):
                stats.skip_no_history += 1
                continue

            stats.candidates += 1
            meta = {
                "slug": m.get("slug") or "",
                "question": m.get("question") or "",
                "volume": float(vol),
                "price_yes_t24h": price_yes,
                "current_mid": price_yes,
                "end_date_iso": m.get("endDate"),
                "condition_id": condition_id,
                "token_id": token_yes,
            }

            if price_yes < self.cfg.strategy_low:
                stats.skip_below += 1
                near_below_acc.append(meta)
            elif price_yes >= self.cfg.strategy_high:
                stats.skip_above += 1
                near_above_acc.append(meta)
            else:
                stats.in_range += 1
                signals.append(Signal(
                    condition_id=condition_id,
                    token_id=token_yes,
                    slug=meta["slug"],
                    question=meta["question"],
                    end_date_iso=meta["end_date_iso"],
                    volume=float(vol),
                    price_at_t24h=price_yes,
                    current_mid=price_yes,
                ))

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
