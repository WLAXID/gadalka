"""Точка входа paper trader'а.

Запуск локально::

    python -m src.paper

Запуск в Docker — entrypoint = python -m src.paper.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from loguru import logger

from src.paper.config import PaperConfig
from src.paper.scheduler import PaperScheduler
from src.paper.state import PaperState
from src.tg.bot import GadalkaBot


async def main() -> None:
    cfg = PaperConfig.from_env()
    logger.remove()
    logger.add(sys.stderr, level=cfg.log_level)

    logger.info("=" * 60)
    logger.info("Gadalka Paper Trader стартует")
    logger.info("DB:      {}", cfg.db_path)
    logger.info("Стратегия: H1 [{:.2f}, {:.2f}] @ T-24h", cfg.strategy_low, cfg.strategy_high)
    logger.info("ETL: {}s,  Resolve: {}s", cfg.etl_interval_s, cfg.resolve_interval_s)
    logger.info("=" * 60)

    state = PaperState(cfg.db_path)
    bot = GadalkaBot(cfg, state)

    await bot.notify(
        "🚀 <b>Gadalka стартовала</b>\n"
        f"Стратегия H1 [{cfg.strategy_low:.2f}–{cfg.strategy_high:.2f}] @ T-24h\n"
        f"ETL каждые {cfg.etl_interval_s}s, резолв-чек каждые {cfg.resolve_interval_s}s"
    )

    # Graceful shutdown через SIGTERM/SIGINT
    stop = asyncio.Event()

    def _signal_handler():
        logger.info("Получен сигнал на остановку")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows не поддерживает add_signal_handler
            signal.signal(sig, lambda *_: _signal_handler())

    async with PaperScheduler(cfg, state, notifier=bot.notify) as scheduler:
        await bot.start()
        sch_task = asyncio.create_task(scheduler.run_forever(), name="scheduler")

        await stop.wait()
        logger.info("Останавливаем scheduler и bot...")
        scheduler.stop()
        await bot.stop()
        try:
            await asyncio.wait_for(sch_task, timeout=10)
        except asyncio.TimeoutError:
            sch_task.cancel()

    state.close()
    logger.info("Gadalka остановлена.")


if __name__ == "__main__":
    asyncio.run(main())
