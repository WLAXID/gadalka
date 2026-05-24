"""Генератор сигналов: ходит в Polymarket API и решает нужно ли войти.

Алгоритм H1 live (упрощённая версия бэктестовой стратегии):
1. Достать ВСЕ active рынки через `gamma_markets(active=True, closed=False)`
   с пагинацией по 100 (hard cap Gamma) до пустой страницы или
   `cfg.scan_max_markets`. Сортировка volumeNum DESC — если упрёмся в cap,
   приоритет получают крупные рынки.
2. Отфильтровать:
   - volumeNum >= min_market_volume
   - enableOrderBook=True (без legacy FPMM)
   - валидный clobTokenIds[0]
   - endDate <= now + max_market_ttl_days (иначе не успеет резолвиться)
   - condition_id не уже взят
   - event_id не уже взят (защита от корреляции)
3. Прочитать текущую цену YES из `outcomePrices[0]`.
4. Если цена в [strategy_low, strategy_high) → создать paper trade.
5. Записать все candidate/near-miss в paper_scan_dump для пост-анализа.

Прим: бэктест смотрел цену за T-24h до closedTime. В live мы используем
текущую цену из outcomePrices как proxy — edge был стабилен по всем
горизонтам T-1h..T-7d на исторических данных (см. Phase 1 findings).
Поле Signal.price_at_t24h оставлено для обратной совместимости с
форматтерами; хранит ту же current_mid.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

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
    event_id: str | None = None


@dataclass
class ScanStats:
    """Что увидел сканер в одном проходе."""

    ts: int
    total_active: int = 0
    skip_low_volume: int = 0
    skip_no_token: int = 0
    skip_already_taken: int = 0
    skip_event_taken: int = 0
    skip_ttl_too_far: int = 0
    candidates: int = 0
    skip_no_history: int = 0
    skip_below: int = 0
    skip_above: int = 0
    in_range: int = 0
    near_below: list[dict] = field(default_factory=list)
    near_above: list[dict] = field(default_factory=list)
    duration_s: float = 0.0


def _parse_end_date(end_iso: str | None) -> int | None:
    """ISO-8601 → unix-ts. Принимает 'Z' и микросекунды."""
    if not end_iso:
        return None
    s = end_iso.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return None


def _extract_event_id(market: dict) -> str | None:
    """Достаём event_id из gamma /markets ответа.

    Polymarket кладёт его в разных местах: `events[0].id`, `eventId`,
    иногда вообще нет — для standalone рынков.
    """
    eid = market.get("eventId")
    if eid:
        return str(eid)
    events = market.get("events")
    if isinstance(events, list) and events:
        first = events[0]
        if isinstance(first, dict) and first.get("id"):
            return str(first["id"])
    return None


class SignalGenerator:
    """H1 baseline scanner."""

    def __init__(self, config: PaperConfig, state: PaperState) -> None:
        self.cfg = config
        self.state = state

    async def scan(
        self, client: PolymarketClient
    ) -> tuple[list[Signal], ScanStats]:
        """Один проход: вернуть (сигналы, детальная статистика скана)."""
        t_start = time.time()
        now_ts = int(t_start)
        stats = ScanStats(ts=now_ts)

        if self.state.is_paused():
            logger.info("[signal] на паузе, пропускаем скан")
            stats.duration_s = time.time() - t_start
            return [], stats

        signals: list[Signal] = []

        # Пагинируем по всем active. Gamma режет limit до 100 за запрос,
        # offset до 10k. Сортировка volumeNum DESC — на случай если упрёмся
        # в cap, первые страницы дают самые ликвидные рынки.
        markets: list[dict] = []
        page_size = 100
        offset = 0
        cap = self.cfg.scan_max_markets
        try:
            while len(markets) < cap:
                page = await client.gamma_markets(
                    active=True, closed=False,
                    limit=page_size, offset=offset,
                    order="volumeNum", ascending=False,
                )
                if not page:
                    break
                markets.extend(page)
                if len(page) < page_size:
                    break
                offset += len(page)
        except PolymarketError as e:
            # offset > 10000 — hard cap Gamma. Берём что собрали и идём дальше.
            if "offset exceeds maximum" in str(e).lower():
                logger.warning(
                    "[signal] Gamma offset cap, остановились на {n}", n=len(markets)
                )
            else:
                self.state.log_event("error", "signal", f"gamma_markets failed: {e}")
                stats.duration_s = time.time() - t_start
                return [], stats

        stats.total_active = len(markets)
        logger.info("[signal] получено {n} active рынков", n=len(markets))

        # Один раз достаём все взятые ключи — вместо N round-trip'ов в БД.
        taken_keys, taken_events = self.state.existing_trade_keys()

        near_below_acc: list[dict] = []
        near_above_acc: list[dict] = []
        scan_dump_rows: list[dict] = []

        ttl_cutoff = now_ts + self.cfg.max_market_ttl_days * 86400

        for m in markets:
            vol = m.get("volumeNum") or 0
            if vol < self.cfg.min_market_volume:
                stats.skip_low_volume += 1
                continue
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
            if not token_ids:
                stats.skip_no_token += 1
                continue
            token_yes = str(token_ids[0])
            condition_id = m.get("conditionId")
            if not condition_id:
                stats.skip_no_token += 1
                continue

            end_iso = m.get("endDate")
            end_ts = _parse_end_date(end_iso)
            # Отсечём рынки, которые точно не успеют резолвиться за месяц —
            # иначе они займут pending и не дадут результата.
            if end_ts is not None and end_ts > ttl_cutoff:
                stats.skip_ttl_too_far += 1
                continue

            event_id = _extract_event_id(m)

            if (condition_id, token_yes) in taken_keys:
                stats.skip_already_taken += 1
                continue
            if event_id and event_id in taken_events:
                stats.skip_event_taken += 1
                continue

            op_raw = m.get("outcomePrices")
            if not op_raw:
                stats.skip_no_history += 1
                continue
            try:
                op = json.loads(op_raw) if isinstance(op_raw, str) else op_raw
            except Exception:
                stats.skip_no_history += 1
                continue
            if not op:
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
                "end_date_iso": end_iso,
                "condition_id": condition_id,
                "token_id": token_yes,
                "event_id": event_id,
            }

            if price_yes < self.cfg.strategy_low:
                stats.skip_below += 1
                near_below_acc.append(meta)
                scan_dump_rows.append({
                    "bucket": "near_below",
                    "condition_id": condition_id,
                    "token_id": token_yes,
                    "event_id": event_id,
                    "slug": meta["slug"],
                    "price_yes": price_yes,
                    "volume": float(vol),
                    "end_date_iso": end_iso,
                })
            elif price_yes >= self.cfg.strategy_high:
                stats.skip_above += 1
                near_above_acc.append(meta)
                scan_dump_rows.append({
                    "bucket": "near_above",
                    "condition_id": condition_id,
                    "token_id": token_yes,
                    "event_id": event_id,
                    "slug": meta["slug"],
                    "price_yes": price_yes,
                    "volume": float(vol),
                    "end_date_iso": end_iso,
                })
            else:
                stats.in_range += 1
                signals.append(Signal(
                    condition_id=condition_id,
                    token_id=token_yes,
                    slug=meta["slug"],
                    question=meta["question"],
                    end_date_iso=end_iso,
                    volume=float(vol),
                    price_at_t24h=price_yes,
                    current_mid=price_yes,
                    event_id=event_id,
                ))
                scan_dump_rows.append({
                    "bucket": "in_range",
                    "condition_id": condition_id,
                    "token_id": token_yes,
                    "event_id": event_id,
                    "slug": meta["slug"],
                    "price_yes": price_yes,
                    "volume": float(vol),
                    "end_date_iso": end_iso,
                })

        near_below_acc.sort(key=lambda x: -x["price_yes_t24h"])
        stats.near_below = near_below_acc[:5]
        near_above_acc.sort(key=lambda x: x["price_yes_t24h"])
        stats.near_above = near_above_acc[:5]

        # Один батчевый INSERT всех candidate-записей за скан
        try:
            self.state.insert_scan_dump(now_ts, scan_dump_rows)
        except Exception as e:
            logger.warning("[signal] insert_scan_dump failed: {}", e)

        stats.duration_s = time.time() - t_start
        logger.info(
            "[signal] {n} signals; below={b} above={a} ttl_far={t} "
            "event_taken={e} taken={tk} (за {d:.1f}s)",
            n=stats.in_range,
            b=stats.skip_below,
            a=stats.skip_above,
            t=stats.skip_ttl_too_far,
            e=stats.skip_event_taken,
            tk=stats.skip_already_taken,
            d=stats.duration_s,
        )
        return signals, stats
