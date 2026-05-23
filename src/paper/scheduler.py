"""Async scheduler: ETL loop + Resolve loop + Trace loop + Heartbeat + Daily + Backup.

Запуск:
    from src.paper.scheduler import PaperScheduler

    async with PaperScheduler(config, state, notifier) as sch:
        await sch.run_forever()

Дополнительно: следит за здоровьем всех loops и Telegram-polling task'а,
авто-рестартит сдохшие.
"""

from __future__ import annotations

import asyncio
import json as _json
import time
from dataclasses import asdict as _asdict
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable

from loguru import logger

from src.api.polymarket import PolymarketClient, PolymarketError
from src.backtest.costs import CostModel
from src.paper.config import PaperConfig
from src.paper.resolver import Resolver
from src.paper.signal import Signal, SignalGenerator
from src.paper.state import PaperState
from src.tg.bot import GadalkaBot


Notifier = Callable[[str], Awaitable[None]]


def _book_metrics(book: dict | None) -> dict:
    """Из CLOB orderbook → bid/ask/mid + сумма топ-5 уровней.

    Polymarket /book возвращает:
      {"bids": [{"price": "0.6", "size": "100"}, ...],
       "asks": [{"price": "0.62", "size": "80"}, ...]}
    Уровни не гарантированно отсортированы, поэтому сортируем сами.
    """
    out = {
        "bid": None, "ask": None, "mid": None,
        "bid_depth_5": None, "ask_depth_5": None,
    }
    if not book:
        return out

    def _levels(side_raw):
        out_lvls: list[tuple[float, float]] = []
        if not isinstance(side_raw, list):
            return out_lvls
        for lvl in side_raw:
            if not isinstance(lvl, dict):
                continue
            try:
                p = float(lvl.get("price"))
                s = float(lvl.get("size") or 0)
            except (TypeError, ValueError):
                continue
            out_lvls.append((p, s))
        return out_lvls

    bids = _levels(book.get("bids"))
    asks = _levels(book.get("asks"))

    if bids:
        bids.sort(key=lambda x: -x[0])
        out["bid"] = bids[0][0]
        out["bid_depth_5"] = sum(s for _, s in bids[:5])
    if asks:
        asks.sort(key=lambda x: x[0])
        out["ask"] = asks[0][0]
        out["ask_depth_5"] = sum(s for _, s in asks[:5])
    if out["bid"] is not None and out["ask"] is not None:
        out["mid"] = (out["bid"] + out["ask"]) / 2
    return out


class PaperScheduler:
    """Координатор циклов paper-trader'а."""

    # Имена loops для watchdog'а — порядок = порядок в _build_tasks
    LOOP_NAMES = (
        "etl", "resolve", "trace",
        "heartbeat", "backup", "daily", "tg_watchdog",
    )

    def __init__(
        self,
        config: PaperConfig,
        state: PaperState,
        notifier: Notifier | None = None,
        bot: GadalkaBot | None = None,
    ) -> None:
        self.cfg = config
        self.state = state
        self.notifier = notifier or self._noop_notifier
        self.bot = bot
        self._client: PolymarketClient | None = None
        self._sigs = SignalGenerator(config, state)
        self._resolver = Resolver(config, state)
        self._tasks: dict[str, asyncio.Task] = {}
        self._stop = asyncio.Event()
        self._last_etl_ts: int = 0
        self._last_etl_success_ts: int = 0
        self._last_resolve_ts: int = 0

    @staticmethod
    async def _noop_notifier(_msg: str) -> None:
        return None

    async def _notify_safe(self, msg: str) -> None:
        """Нотификация с timeout — не должна блокировать caller на минуты."""
        try:
            await asyncio.wait_for(self.notifier(msg), timeout=10)
        except asyncio.TimeoutError:
            logger.warning("[notify] таймаут (>10s)")
        except Exception as e:
            logger.warning("[notify] ошибка: {}", e)

    async def __aenter__(self) -> "PaperScheduler":
        self._client = PolymarketClient(
            cache_dir=None,  # paper не кэширует — нужна свежая цена
        )
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # F9: даём loops добежать до текущего await — все они проверяют _stop
        self._stop.set()
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=15,
                )
            except asyncio.TimeoutError:
                logger.warning("[scheduler] graceful shutdown превысил 15s")
        if self._client:
            await self._client.__aexit__(exc_type, exc, tb)
            self._client = None

    # ---------- Loops ----------

    async def _etl_loop(self) -> None:
        while not self._stop.is_set():
            try:
                signals, scan_stats = await self._sigs.scan(self._client)
                now = int(time.time())
                self._last_etl_ts = now
                # F7: success = ответ от API был получен (хоть пустой list).
                # Если scan упал в exception до scan_stats — это НЕ success.
                if scan_stats.total_active >= 0:
                    self._last_etl_success_ts = now
                    self.state.set_setting(
                        "last_etl_success_ts", str(now)
                    )
                self.state.set_setting(
                    "last_scan_stats",
                    _json.dumps(_asdict(scan_stats), ensure_ascii=False),
                )
                self.state.log_event(
                    "info", "scan",
                    (
                        f"active={scan_stats.total_active}, "
                        f"candidates={scan_stats.candidates}, "
                        f"in_range={scan_stats.in_range}, "
                        f"below={scan_stats.skip_below}, "
                        f"above={scan_stats.skip_above}, "
                        f"ttl_far={scan_stats.skip_ttl_too_far}, "
                        f"event_taken={scan_stats.skip_event_taken}, "
                        f"already_taken={scan_stats.skip_already_taken}"
                    ),
                )
                for s in signals:
                    await self._open_trade(s)
                self.state.set_setting("last_etl_ts", str(self._last_etl_ts))
            except Exception as e:
                logger.exception("[etl] error")
                self.state.log_event("error", "etl", f"{type(e).__name__}: {e}")
            await self._sleep(self.cfg.etl_interval_s)

    async def _open_trade(self, s: Signal) -> None:
        """Создать paper-trade + (best-effort) снимок orderbook."""
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
            event_id=s.event_id,
        )
        if trade_id is None:
            return

        # Best-effort снимок orderbook: ошибка не валит весь trade
        try:
            book = await self._client.clob_book(s.token_id)
            metrics = _book_metrics(book)
            self.state.insert_trade_snapshot(
                trade_id=trade_id,
                bid=metrics["bid"], ask=metrics["ask"], mid=metrics["mid"],
                bid_depth_5=metrics["bid_depth_5"],
                ask_depth_5=metrics["ask_depth_5"],
                raw_book=_json.dumps(book, ensure_ascii=False)[:8000],
            )
        except (PolymarketError, Exception) as e:
            self.state.log_event(
                "warning", "scheduler",
                f"snapshot failed for trade #{trade_id}: "
                f"{type(e).__name__}: {e}",
            )

        await self._notify_safe(
            f"📍 <b>Новая ставка #{trade_id}</b>\n"
            f"<i>{_short(s.question, 100)}</i>\n"
            f"💵 цена YES: {s.current_mid:.4f}\n"
            f"📊 volume: ${s.volume:,.0f}"
        )
        self.state.log_event(
            "info", "scheduler", f"new trade #{trade_id} on {s.slug}",
        )

    async def _resolve_loop(self) -> None:
        while not self._stop.is_set():
            try:
                r = await self._resolver.resolve_all(self._client)
                self._last_resolve_ts = int(time.time())
                self.state.set_setting(
                    "last_resolve_ts", str(self._last_resolve_ts)
                )
                total_new = r["resolved"] + r["cancelled"]
                if total_new > 0:
                    recent = self.state.recent_resolutions(limit=total_new)
                    for tr in recent:
                        pnl = tr.get("pnl") or 0
                        is_cancelled = tr.get("resolved_yes") is None
                        if not is_cancelled and abs(pnl) <= 0.1:
                            continue
                        if is_cancelled:
                            emoji = "⚪"
                            outcome = "ОТМЕНЁН"
                        else:
                            emoji = "✅" if pnl > 0 else "❌"
                            outcome = "YES" if tr.get("resolved_yes") else "NO"
                        await self._notify_safe(
                            f"{emoji} <b>Резолв #{tr['trade_id']}</b>\n"
                            f"<i>{_short(tr.get('market_question') or '', 80)}</i>\n"
                            f"PnL: <b>{pnl:+.4f}</b> (резолв: {outcome})"
                        )
            except Exception as e:
                logger.exception("[resolve] error")
                self.state.log_event(
                    "error", "resolve", f"{type(e).__name__}: {e}"
                )
            await self._sleep(self.cfg.resolve_interval_s)

    async def _trace_loop(self) -> None:
        """Каждые `trace_interval_s` снимает mid/bid/ask для всех pending.

        Даёт траекторию цены между entry и resolve — основа для
        пост-фактум анализа drawdown / optimal exit.

        F18: батчевый INSERT всех точек одним lock-acquire — иначе на 100
        pending был бы N×lock acquire/release против scan'а и backup'а.
        """
        sem = asyncio.Semaphore(5)

        async def _trace_one(trade) -> dict | None:
            async with sem:
                try:
                    book = await self._client.clob_book(trade.token_id)
                    metrics = _book_metrics(book)
                    return {
                        "trade_id": trade.trade_id,
                        "mid": metrics["mid"],
                        "bid": metrics["bid"],
                        "ask": metrics["ask"],
                    }
                except PolymarketError as e:
                    logger.debug(
                        "[trace] failed trade={} {}", trade.trade_id, e,
                    )
                    return None

        while not self._stop.is_set():
            try:
                pending = self.state.pending_trades()
                if pending:
                    results = await asyncio.gather(
                        *(_trace_one(t) for t in pending),
                        return_exceptions=True,
                    )
                    points = [
                        r for r in results
                        if isinstance(r, dict)
                    ]
                    if points:
                        self.state.insert_trace_points_batch(points)
                    logger.info(
                        "[trace] записал {n} точек из {p} pending",
                        n=len(points), p=len(pending),
                    )
            except Exception as e:
                logger.exception("[trace] error")
                self.state.log_event(
                    "error", "trace", f"{type(e).__name__}: {e}"
                )
            await self._sleep(self.cfg.trace_interval_s)

    async def _heartbeat_loop(self) -> None:
        """Каждые 10 мин: если УСПЕШНЫЙ scan молчит >threshold → alert.

        F7: смотрит на last_etl_success_ts, не last_etl_ts. Иначе если
        Polymarket лежит, ETL-loop крутится (last_etl_ts обновляется), но
        signals не приходят — а мы молчим как сторож.
        """
        check_interval = 600  # 10 минут — фиксировано
        await self._sleep(check_interval)

        while not self._stop.is_set():
            try:
                last_success = self.state.get_setting("last_etl_success_ts")
                now = int(time.time())
                if last_success:
                    try:
                        last_ts = int(last_success)
                    except ValueError:
                        last_ts = 0
                    age = now - last_ts
                    if age > self.cfg.heartbeat_threshold_s:
                        last_alert_str = self.state.get_setting(
                            "last_heartbeat_alert_ts"
                        )
                        last_alert = (
                            int(last_alert_str) if last_alert_str else 0
                        )
                        if now - last_alert >= self.cfg.heartbeat_throttle_s:
                            mins = age // 60
                            await self._notify_safe(
                                f"⚠ <b>Heartbeat: успешного скана нет "
                                f"{mins} мин</b>\n"
                                "Возможно, Polymarket недоступен или "
                                "упал ETL-loop. Проверь /health."
                            )
                            self.state.set_setting(
                                "last_heartbeat_alert_ts", str(now)
                            )
                            self.state.log_event(
                                "warning", "heartbeat",
                                f"sent alert (success age={mins}min)",
                            )
            except Exception as e:
                logger.exception("[heartbeat] error")
                self.state.log_event(
                    "error", "heartbeat", f"{type(e).__name__}: {e}"
                )
            await self._sleep(check_interval)

    async def _tg_watchdog_loop(self) -> None:
        """Следит, что Telegram-polling работает. Авто-рестарт при крахе.

        F2: если polling-task умер от network blip или aiogram-бага, без
        watchdog'а ты узнаешь только когда вернёшься. Раз в 5 мин делаем
        getMe + смотрим что _task ещё жив; если нет — restart.
        """
        if self.bot is None:
            return  # без bot нечего сторожить

        await self._sleep(self.cfg.tg_watchdog_interval_s)
        consecutive_fails = 0

        while not self._stop.is_set():
            try:
                alive = await self.bot.is_alive()
                if alive:
                    consecutive_fails = 0
                else:
                    consecutive_fails += 1
                    self.state.log_event(
                        "warning", "tg_watchdog",
                        f"polling не отвечает (#{consecutive_fails})",
                    )
                    # Перезапускаем после 2 подряд failed (≈10 мин)
                    if consecutive_fails >= 2:
                        try:
                            await self.bot.restart_polling()
                            self.state.log_event(
                                "info", "tg_watchdog",
                                "polling перезапущен",
                            )
                            consecutive_fails = 0
                        except Exception as e:
                            self.state.log_event(
                                "error", "tg_watchdog",
                                f"restart failed: {type(e).__name__}: {e}",
                            )
            except Exception as e:
                logger.exception("[tg_watchdog] error")
                self.state.log_event(
                    "error", "tg_watchdog", f"{type(e).__name__}: {e}"
                )
            await self._sleep(self.cfg.tg_watchdog_interval_s)

    async def _backup_loop(self) -> None:
        """Ежедневный backup БД + cleanup scan_dump + stuck-pending warning."""
        # Конфиг уже валидирован в from_env — здесь падение невозможно
        backup_time = dtime.fromisoformat(self.cfg.backup_time + ":00")

        while not self._stop.is_set():
            now = datetime.now(timezone.utc)
            target = now.replace(
                hour=backup_time.hour, minute=backup_time.minute,
                second=0, microsecond=0,
            )
            if target <= now:
                target = target + timedelta(days=1)
            sleep_s = (target - now).total_seconds()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep_s)
                return
            except asyncio.TimeoutError:
                pass

            try:
                # F4/F5: make_dump + cleanup могут занять секунды на большой
                # БД — выполняем в thread, чтобы не блокировать event loop.
                await asyncio.to_thread(self._run_backup)
            except Exception as e:
                logger.exception("[backup] error")
                self.state.log_event(
                    "error", "backup", f"{type(e).__name__}: {e}"
                )

    def _run_backup(self) -> None:
        """Сам backup: dump → rotation → cleanup scan_dump → stuck warning."""
        backup_dir = Path(self.cfg.backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        dump_path = backup_dir / f"paper_{stamp}.duckdb"
        self.state.make_dump(dump_path)
        self.state.set_setting("last_backup_ts", str(int(time.time())))
        self.state.log_event(
            "info", "backup", f"dump created: {dump_path.name}",
        )

        # Rotation: удалить дампы старше N дней (по mtime)
        cutoff = time.time() - self.cfg.backup_retention_days * 86400
        removed = 0
        for f in backup_dir.glob("paper_*.duckdb"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
        if removed:
            self.state.log_event(
                "info", "backup", f"rotation removed {removed} old dumps"
            )

        # Cleanup scan_dump
        try:
            deleted = self.state.cleanup_scan_dump(
                older_than_s=self.cfg.scan_dump_retention_days * 86400
            )
            if deleted:
                self.state.log_event(
                    "info", "backup",
                    f"scan_dump cleanup: {deleted} rows removed",
                )
        except Exception as e:
            self.state.log_event(
                "warning", "backup", f"scan_dump cleanup failed: {e}"
            )

        # Stuck-pending warning (одноразово в день)
        try:
            stuck = self.state.stuck_pending(
                after_endDate_s=self.cfg.stuck_pending_after_days * 86400
            )
            if stuck:
                self.state.log_event(
                    "warning", "backup",
                    f"{len(stuck)} stuck-pending trades (endDate "
                    f"+{self.cfg.stuck_pending_after_days}d прошёл)",
                )
        except Exception as e:
            self.state.log_event(
                "warning", "backup", f"stuck_pending check failed: {e}"
            )

    async def _daily_report_loop(self) -> None:
        # Конфиг валидирован в from_env
        report_time = dtime.fromisoformat(self.cfg.daily_report_time + ":00")

        while not self._stop.is_set():
            now = datetime.now(timezone.utc)
            target = now.replace(
                hour=report_time.hour, minute=report_time.minute,
                second=0, microsecond=0,
            )
            if target <= now:
                target = target + timedelta(days=1)
            sleep_s = (target - now).total_seconds()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep_s)
                return
            except asyncio.TimeoutError:
                pass

            try:
                # F5: summary_stats делает несколько COUNT(*) — на большой
                # БД счёт идёт на сотни ms. Выносим в thread.
                s = await asyncio.to_thread(self.state.summary_stats)
                last_backup = self.state.get_setting("last_backup_ts")
                backup_age_s: int | None = None
                if last_backup:
                    try:
                        backup_age_s = int(time.time()) - int(last_backup)
                    except ValueError:
                        pass
                # F10: количество backup-файлов как ранний сигнал.
                try:
                    backups_dir = Path(self.cfg.backup_dir)
                    backup_files = list(backups_dir.glob("paper_*.duckdb"))
                except Exception:
                    backup_files = []
                msg = self._format_daily_report(
                    s,
                    backup_age_s=backup_age_s,
                    backup_count=len(backup_files),
                    pending_growth_alert=self.cfg.pending_growth_alert,
                )
                await self._notify_safe(msg)
            except Exception as e:
                logger.exception("[daily] error")
                self.state.log_event(
                    "error", "daily", f"{type(e).__name__}: {e}"
                )

    @staticmethod
    def _format_daily_report(
        s: dict,
        *,
        backup_age_s: int | None = None,
        backup_count: int = 0,
        pending_growth_alert: int = 200,
    ) -> str:
        """Ежедневный отчёт. Безопасно для случая нулевых данных."""
        wr = s.get("win_rate")
        ev = s.get("ev_per_dollar")
        pending = s.get("pending", 0)
        parts = [
            "📅 <b>Ежедневный отчёт</b>",
            "━━━━━━━━━━━━━━━━━",
            f"💼 Открытых ставок: <b>{pending}</b>",
            f"✔ Резолвнутых: <b>{s.get('resolved', 0)}</b>",
            "━━━━━━━━━━━━━━━━━",
        ]
        if wr is not None:
            parts.append(f"📊 % успешных: <b>{wr:.1%}</b>")
        else:
            parts.append("📊 % успешных: <i>пока нет резолвов</i>")
        parts.append(
            f"💰 Прибыль/убыток: <b>${s.get('total_pnl', 0):+.2f}</b>"
        )
        if ev is not None:
            parts.append(f"📈 EV / $1: <b>{ev:+.2%}</b>")

        # Здоровье системы
        parts.append("")
        parts.append("<b>🔧 Здоровье:</b>")
        transient = s.get("transient_errors_24h", 0)
        fatal = s.get("fatal_errors_24h", 0)
        if transient or fatal:
            parts.append(
                f"  • ошибок за 24ч: <b>{transient}</b> "
                f"transient + <b>{fatal}</b> fatal"
            )
        else:
            parts.append("  • ошибок за 24ч: <b>0</b> ✅")
        parts.append(
            f"  • точек траектории за 24ч: "
            f"<b>{s.get('trace_points_24h', 0)}</b>"
        )
        if backup_age_s is None:
            parts.append("  • backup: <i>ещё не было</i>")
        elif backup_age_s < 86400:
            parts.append(
                f"  • backup: <b>{backup_age_s // 3600}ч назад</b> "
                f"({backup_count} файлов в архиве)"
            )
        else:
            parts.append(
                f"  • backup: <b>{backup_age_s // 86400}д назад</b> ⚠ "
                f"({backup_count} файлов)"
            )

        # F20: pending backlog alert
        if pending > pending_growth_alert:
            parts.append("")
            parts.append(
                f"⚠ <b>Pending ≥ {pending_growth_alert}</b> — "
                "резолвер может не справляться. "
                "Проверь /pending и stuck-маркеты в /events."
            )

        # F10: напоминание про offsite-backup
        if backup_count > 0:
            parts.append("")
            parts.append(
                f"💾 <i>Backups локально в Docker volume. "
                "Раз в неделю рекомендую скопировать на сторонний диск "
                "(<code>docker cp gadalka-paper:/app/data/backups ./</code>).</i>"
            )

        return "\n".join(parts)

    async def _sleep(self, seconds: int) -> None:
        """Прерываемый сон — будит self._stop."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def _build_loop(self, name: str):
        """Возвращает coroutine для loop по имени. Используется для рестарта."""
        return {
            "etl": self._etl_loop,
            "resolve": self._resolve_loop,
            "trace": self._trace_loop,
            "heartbeat": self._heartbeat_loop,
            "backup": self._backup_loop,
            "daily": self._daily_report_loop,
            "tg_watchdog": self._tg_watchdog_loop,
        }[name]

    def _spawn_loop(self, name: str) -> asyncio.Task:
        coro = self._build_loop(name)()
        task = asyncio.create_task(coro, name=f"paper-{name}")
        task.add_done_callback(lambda t: self._on_loop_done(name, t))
        self._tasks[name] = task
        return task

    def _on_loop_done(self, name: str, task: asyncio.Task) -> None:
        """F3: callback при смерти loop'а — лог + (если не shutdown) restart.

        К моменту вызова task может быть Cancelled (shutdown) или с
        exception (баг). Cancelled — норма, exception — нет.
        """
        if task.cancelled():
            return  # graceful shutdown
        exc = task.exception()
        if exc is None:
            # Loop вышел чисто (например, _daily_report_loop при stop_event)
            return
        # Залог: пишем в БД + рестартим
        try:
            self.state.log_event(
                "error", "loop_watchdog",
                f"loop '{name}' умер: {type(exc).__name__}: {exc}",
            )
        except Exception:
            pass
        logger.error("[watchdog] loop '{}' умер: {}: {}",
                     name, type(exc).__name__, exc)
        if self._stop.is_set():
            return
        # Рестарт после короткой паузы
        async def _delayed_restart():
            await asyncio.sleep(5)
            if not self._stop.is_set():
                self._spawn_loop(name)
                self.state.log_event(
                    "info", "loop_watchdog", f"loop '{name}' перезапущен"
                )
                await self._notify_safe(
                    f"⚠ <b>Watchdog:</b> loop <code>{name}</code> упал, "
                    "перезапустил. Проверь /events."
                )
        asyncio.create_task(_delayed_restart(), name=f"restart-{name}")

    async def run_forever(self) -> None:
        for name in self.LOOP_NAMES:
            # tg_watchdog запускается только если есть bot
            if name == "tg_watchdog" and self.bot is None:
                continue
            self._spawn_loop(name)
        logger.info(
            "PaperScheduler стартует {} задач: {}",
            len(self._tasks), list(self._tasks.keys()),
        )
        await self._stop.wait()
        for t in self._tasks.values():
            t.cancel()

    def stop(self) -> None:
        self._stop.set()


def _short(text: str, n: int) -> str:
    if not text:
        return ""
    return text if len(text) <= n else text[: n - 1] + "…"
