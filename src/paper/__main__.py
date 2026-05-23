"""Точка входа paper trader'а.

Запуск локально::

    python -m src.paper

Запуск в Docker — entrypoint = python -m src.paper.
"""

from __future__ import annotations

import asyncio
import shutil
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


def _open_state_with_recovery(cfg: PaperConfig) -> PaperState:
    """F14: пытается открыть БД, при corruption восстанавливает из последнего backup'а.

    Без этого: corrupted DuckDB → каждый старт падает → Docker restart loop
    → ты вернёшься через месяц и узнаешь что бот «лежал 28 дней».
    """
    try:
        st = PaperState(cfg.db_path)
        if st.db_integrity_check():
            return st
        # Открылась, но integrity не прошёл — закрываем и идём в recovery
        try:
            st.close()
        except Exception:
            pass
        raise RuntimeError("integrity_check failed")
    except Exception as primary_err:
        logger.error("Не смог открыть БД: {}", primary_err)
        backup_dir = Path(cfg.backup_dir)
        if not backup_dir.exists():
            raise
        backups = sorted(
            backup_dir.glob("paper_*.duckdb"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if not backups:
            logger.error("Backups нет — БД невосстановима")
            raise
        latest = backups[0]
        # Откладываем повреждённую БД с timestamp'ом для пост-анализа
        corrupt_path = cfg.db_path.with_suffix(
            f".duckdb.corrupt-{int(asyncio.get_event_loop().time())}"
        )
        try:
            if cfg.db_path.exists():
                cfg.db_path.rename(corrupt_path)
                logger.warning(
                    "Повреждённая БД отложена как {}", corrupt_path
                )
        except OSError as e:
            logger.warning("Не смог переименовать corrupt БД: {}", e)
        shutil.copy(latest, cfg.db_path)
        logger.warning("Восстановил БД из backup'а: {}", latest.name)
        st = PaperState(cfg.db_path)
        st.log_event(
            "warning", "recovery",
            f"DB восстановлена из {latest.name} (старая БД: "
            f"{corrupt_path.name if corrupt_path.exists() else 'не сохранена'})",
        )
        return st


async def main() -> None:
    cfg = PaperConfig.from_env()
    logger.remove()
    logger.add(sys.stderr, level=cfg.log_level)
    # F24: file-sink для длительных запусков — Docker rotation 50 MB
    # перетирает контекст быстро, а тут архив на 10 файлов × 50 MB = 500 MB.
    log_dir = Path("/app/logs") if Path("/app").exists() else Path("logs")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_dir / "gadalka.log",
            level=cfg.log_level,
            rotation="50 MB",
            retention=10,
            compression="zip",
            enqueue=True,
        )
    except OSError as e:
        logger.warning("Не смог открыть file-log: {}", e)

    logger.info("=" * 60)
    logger.info("Gadalka Paper Trader стартует")
    logger.info("DB:      {}", cfg.db_path)
    logger.info(
        "Стратегия: H1 [{:.2f}, {:.2f}] @ T-24h",
        cfg.strategy_low, cfg.strategy_high,
    )
    logger.info(
        "ETL: {}s,  Resolve: {}s,  Trace: {}s",
        cfg.etl_interval_s, cfg.resolve_interval_s, cfg.trace_interval_s,
    )
    logger.info(
        "Backup: ежедневно {} UTC, retention {} дней",
        cfg.backup_time, cfg.backup_retention_days,
    )
    logger.info("=" * 60)

    state = _open_state_with_recovery(cfg)
    bot = GadalkaBot(cfg, state)

    await bot.notify(
        "🚀 <b>Gadalka стартовала</b>\n"
        f"Стратегия H1 [{cfg.strategy_low:.2f}–{cfg.strategy_high:.2f}]\n"
        f"ETL каждые {cfg.etl_interval_s // 60} мин, "
        f"резолв-чек каждые {cfg.resolve_interval_s // 60} мин\n"
        f"Trace каждые {cfg.trace_interval_s // 60} мин, "
        f"backup в {cfg.backup_time} UTC"
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

    async with PaperScheduler(
        cfg, state, notifier=bot.notify, bot=bot,
    ) as scheduler:
        await bot.start()
        sch_task = asyncio.create_task(
            scheduler.run_forever(), name="scheduler"
        )

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
