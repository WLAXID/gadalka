"""Резолвер pending-сделок: ходит в Gamma за `outcomePrices` и закрывает.

Если у рынка `closed=true`, читаем `outcomePrices` (JSON-список), берём цену YES,
считаем payout (1 если >=0.5, 0 иначе для бинарных рынков), считаем pnl.
"""

from __future__ import annotations

import asyncio
import json

from loguru import logger

from src.api.polymarket import PolymarketClient, PolymarketError
from src.backtest.costs import CostModel
from src.paper.config import PaperConfig
from src.paper.state import PaperState, Trade


class Resolver:
    """Опрашивает pending trades и резолвит закрывшиеся."""

    def __init__(self, config: PaperConfig, state: PaperState) -> None:
        self.cfg = config
        self.state = state
        self.cost_model = CostModel(
            fee_rate=config.fee_rate,
            spread_pct=config.spread_pct,
            slippage_pct=config.slippage_pct,
        )

    async def resolve_all(self, client: PolymarketClient) -> dict:
        pending = self.state.pending_trades()
        if not pending:
            return {"checked": 0, "resolved": 0, "cancelled": 0}

        logger.info("[resolver] проверяем {n} pending trades", n=len(pending))

        sem = asyncio.Semaphore(5)
        results = {"checked": 0, "resolved": 0, "cancelled": 0}

        async def _check(trade: Trade):
            async with sem:
                try:
                    market = await client.gamma_market_by_id(
                        market_id=int(trade.condition_id) if trade.condition_id.isdigit() else trade.condition_id
                    )
                except PolymarketError:
                    # Возможно gamma_market_by_id по conditionId не работает —
                    # делаем через query-filter
                    try:
                        page = await client.gamma_markets(
                            condition_ids=[trade.condition_id], limit=1,
                        )
                        market = page[0] if page else None
                    except PolymarketError as e:
                        self.state.log_event(
                            "warning", "resolver",
                            f"не удалось получить market {trade.condition_id[:18]}: {e}",
                        )
                        return None
                if not market:
                    return None
                if not market.get("closed"):
                    return None
                # Резолв: outcomePrices
                op_raw = market.get("outcomePrices")
                if not op_raw:
                    return None
                try:
                    op = json.loads(op_raw) if isinstance(op_raw, str) else op_raw
                except Exception:
                    return None
                if not op or len(op) < 1:
                    return None
                final_yes_price = float(op[0])
                resolved_yes = final_yes_price >= 0.5
                payout = 1.0 if resolved_yes else 0.0
                pnl = self.cost_model.realize_pnl(trade.buy_cost, payout)
                return (trade, resolved_yes, final_yes_price, payout, pnl)

        tasks = [_check(t) for t in pending]
        for coro in asyncio.as_completed(tasks):
            r = await coro
            results["checked"] += 1
            if r is None:
                continue
            trade, resolved_yes, final_yes_price, payout, pnl = r
            self.state.resolve_trade(
                trade.trade_id,
                resolved_yes=resolved_yes,
                final_price_yes=final_yes_price,
                payout=payout,
                pnl=pnl,
            )
            results["resolved"] += 1
            self.state.log_event(
                "info", "resolver",
                f"резолв trade {trade.trade_id}: {'YES' if resolved_yes else 'NO'} pnl=${pnl:+.4f}",
                payload={
                    "trade_id": trade.trade_id,
                    "condition_id": trade.condition_id,
                    "slug": trade.market_slug,
                    "resolved_yes": resolved_yes,
                    "pnl": pnl,
                },
            )

        logger.info(
            "[resolver] проверено {c}, резолвнуто {r}, отменено {x}",
            c=results["checked"], r=results["resolved"], x=results["cancelled"],
        )
        return results
