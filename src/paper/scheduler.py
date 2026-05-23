"""Async scheduler: ETL loop + Resolve loop + daily report.

Запуск:
    from src.paper.scheduler import PaperScheduler

    async with PaperScheduler(config, state, notifier) as sch:
        await sch.run_forever()
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, time as dtime, timezone
from typing import Awaitable, Callable

from loguru import logger

from src.api.polymarket import PolymarketClient
from src.backtest.costs import CostModel
from src.paper.config import PaperConfig
from src.paper.resolver import Resolver
from src.paper.signal import SignalGenerator
from src.paper.state import PaperState


Notifier = Callable[[str], Awaitable[None]]


class PaperScheduler:
    """Координатор циклов paper-trader'а."""

    def __init__(
        self,
        config: PaperConfig,
        state: PaperState,
        notifier: Notifier | None = None,
    ) -> None:
        self.cfg = config
        self.state = state
        self.notifier = notifier or (lambda _msg: asyncio.sleep(0))
        self._client: PolymarketClient | None = None
        self._sigs = SignalGenerator(config, state)
        self._resolver = Resolver(config, state)
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._last_etl_ts: int = 0
        self._last_resolve_ts: int = 0

    async def __aenter__(self) -> "PaperScheduler":
        self._client = PolymarketClient(
            cache_dir=None,  # paper не кэширует — нужна свежая цена
        )
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        if self._client:
            await self._client.__aexit__(exc_type, exc, tb)
            self._client = None

    # ---------- Loops ----------

    async def _etl_loop(self) -> None:
        while not self._stop.is_set():
            try:
                signals = await self._sigs.scan(self._client)
                self._last_etl_ts = int(time.time())
                for s in signals:
                    cost_model = CostModel(
                        fee_rate=self.cfg.fee_rate,
                        spread_pct=self.cfg.spread_pct,
                        slippage_pct=self.cfg.slippage_pct,
                    )
                    buy_cost = cost_model.effective_buy_price(s.current_mid)
                    trade_id = self.state.insert_trade(
                        condition_id=s.condition_id,
                        token_id=s.token_id,
                        market_slug=s.slug,
                        market_question=s.question,
                        entry_price=s.current_mid,
                        buy_cost=buy_cost,
                        stake=self.cfg.stake_amount,
                        strategy=f"H1[{self.cfg.strategy_low:.2f}-{self.cfg.strategy_high:.2f}]",
                        end_date_iso=s.end_date_iso,
                        volume=s.volume,
                    )
                    if trade_id is not None:
                        msg = (
                            f"📍 <b>Новая ставка #{trade_id}</b>\n"
                            f"<i>{_short(s.question, 100)}</i>\n"
                            f"💵 цена YES: {s.current_mid:.4f} "
                            f"(@T-24h было {s.price_at_t24h:.4f})\n"
                            f"📊 volume: ${s.volume:,.0f}"
                        )
                        await self.notifier(msg)
                        self.state.log_event(
                            "info", "scheduler",
                            f"new trade #{trade_id} on {s.slug}",
                        )
                self.state.set_setting("last_etl_ts", str(self._last_etl_ts))
            except Exception as e:
                logger.exception("[etl] error")
                self.state.log_event("error", "etl", f"{type(e).__name__}: {e}")
            await self._sleep(self.cfg.etl_interval_s)

    async def _resolve_loop(self) -> None:
        while not self._stop.is_set():
            try:
                r = await self._resolver.resolve_all(self._client)
                self._last_resolve_ts = int(time.time())
                self.state.set_setting("last_resolve_ts", str(self._last_resolve_ts))
                if r["resolved"] > 0:
                    # Уведомляем о крупных движениях
                    recent = self.state.recent_resolutions(limit=r["resolved"])
                    for tr in recent:
                        if abs(tr.get("pnl") or 0) > 0.1:
                            emoji = "✅" if (tr.get("pnl") or 0) > 0 else "❌"
                            await self.notifier(
                                f"{emoji} <b>Резолв #{tr['trade_id']}</b>\n"
                                f"<i>{_short(tr.get('market_question') or '', 80)}</i>\n"
                                f"PnL: <b>{tr['pnl']:+.4f}</b> "
                                f"(резолв: {'YES' if tr.get('resolved_yes') else 'NO'})"
                            )
            except Exception as e:
                logger.exception("[resolve] error")
                self.state.log_event("error", "resolve", f"{type(e).__name__}: {e}")
            await self._sleep(self.cfg.resolve_interval_s)

    async def _daily_report_loop(self) -> None:
        try:
            report_time = dtime.fromisoformat(self.cfg.daily_report_time + ":00")
        except Exception:
            report_time = dtime(23, 59)

        while not self._stop.is_set():
            now = datetime.now(timezone.utc)
            target = now.replace(
                hour=report_time.hour, minute=report_time.minute,
                second=0, microsecond=0,
            )
            if target <= now:
                target = target.replace(day=target.day + 1)
            sleep_s = (target - now).total_seconds()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep_s)
                return
            except asyncio.TimeoutError:
                pass

            try:
                s = self.state.summary_stats()
                msg = self._format_daily_report(s)
                await self.notifier(msg)
            except Exception as e:
                logger.exception("[daily] error")
                self.state.log_event("error", "daily", f"{type(e).__name__}: {e}")

    @staticmethod
    def _format_daily_report(s: dict) -> str:
        wr = s.get("win_rate")
        ev = s.get("ev_per_dollar")
        return (
            "📅 <b>Ежедневный отчёт</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"💼 Открытых ставок: <b>{s['pending']}</b>\n"
            f"✔ Резолвнутых: <b>{s['resolved']}</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📊 Win rate: <b>{wr:.1%}</b>" + (" \n" if wr else " (нет данных)\n")
            + f"💰 Total PnL: <b>${s['total_pnl']:+.2f}</b>\n"
            f"📈 EV / $: <b>{ev:+.2%}</b>" if ev is not None else ""
        )

    async def _sleep(self, seconds: int) -> None:
        """Прерываемый сон — будит self._stop."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def run_forever(self) -> None:
        self._tasks = [
            asyncio.create_task(self._etl_loop(), name="paper-etl"),
            asyncio.create_task(self._resolve_loop(), name="paper-resolve"),
            asyncio.create_task(self._daily_report_loop(), name="paper-daily"),
        ]
        logger.info("PaperScheduler стартует {} задач", len(self._tasks))
        await self._stop.wait()
        for t in self._tasks:
            t.cancel()

    def stop(self) -> None:
        self._stop.set()


def _short(text: str, n: int) -> str:
    if not text:
        return ""
    return text if len(text) <= n else text[: n - 1] + "…"
