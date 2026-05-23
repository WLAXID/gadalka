"""Telegram-панель gadalka — управление paper-трейдером.

Aiogram 3 long-polling. Ограничен по chat_id (только владелец).
Команды:
    /start, /help — главное меню
    /stats        — сводка
    /pending      — открытые ставки
    /recent       — последние резолвы
    /health       — статус loops
    /pause /resume
    /settings     — настройки
"""

from __future__ import annotations

import asyncio
import html
import shutil
import tempfile
import time
import zipfile
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any as _Any

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    Message,
    TelegramObject,
)
from loguru import logger

from src.paper.config import PaperConfig
from src.paper.state import PaperState
from src.tg.keyboards import (
    BTN_DUMP,
    BTN_EVENTS,
    BTN_HEALTH,
    BTN_HELP,
    BTN_PAUSE,
    BTN_PENDING,
    BTN_RECENT,
    BTN_RESUME,
    BTN_SCAN,
    BTN_SETTINGS,
    BTN_STATS,
    inline_dump_choice,
    inline_refresh,
    inline_settings,
    main_keyboard,
)


WELCOME = (
    "👋 <b>Привет!</b> Я гадалка — paper-трейдер по Polymarket.\n\n"
    "Я каждые 15 минут сканирую активные рынки и записываю виртуальные "
    "ставки по стратегии H1 (Buy YES когда цена в полосе 0.50–0.85 за "
    "24 часа до резолва). Когда рынок резолвится, считаю реальный P&L.\n\n"
    "Пользуйся кнопками ниже 👇"
)

HELP = (
    "<b>Команды:</b>\n"
    "/stats — сводка по всем ставкам\n"
    "/scan — что я видел в последнем скане (рынки)\n"
    "/pending — открытые ставки\n"
    "/recent — последние резолвы\n"
    "/health — статус loops\n"
    "/events — журнал последних событий бота\n"
    "/pause /resume — пауза/возобновить новые ставки\n"
    "/settings — настройки\n"
    "/dump — скачать БД (DuckDB-файл или CSV-архив)\n\n"
    "<b>Логика:</b>\n"
    "• Каждые 15 мин — скан 100 топ-active рынков\n"
    "• Если цена YES @ T-24h в полосе [0.50, 0.85] → ставка\n"
    "• Каждый час — проверка резолвов pending\n"
    "• 23:59 UTC — ежедневный отчёт\n"
)


class GadalkaBot:
    """Telegram-панель + push-нотификации."""

    def __init__(self, config: PaperConfig, state: PaperState) -> None:
        self.cfg = config
        self.state = state
        self.bot = Bot(
            token=config.tg_bot_token,
            default=DefaultBotProperties(parse_mode="HTML"),
        )
        self.dp = Dispatcher()
        self.owner_id = int(config.tg_owner_id)
        self._task: asyncio.Task[None] | None = None
        self.dp.update.outer_middleware(self._auth)
        self._register_handlers()

    # ---------- Middleware ----------

    async def _auth(
        self,
        handler: Callable[[TelegramObject, dict[str, _Any]], Awaitable[_Any]],
        event: TelegramObject,
        data: dict[str, _Any],
    ) -> _Any:
        user_id = None
        if hasattr(event, "from_user") and getattr(event, "from_user"):
            user_id = event.from_user.id
        elif hasattr(event, "message") and event.message:
            user_id = event.message.from_user.id
        if user_id and int(user_id) != self.owner_id:
            logger.warning(f"[tg] drop event from {user_id}")
            return  # игнорируем чужих
        return await handler(event, data)

    # ---------- Notifier ----------

    async def notify(self, text: str) -> None:
        """Push-сообщение владельцу. Используется scheduler'ом."""
        try:
            await self.bot.send_message(
                chat_id=self.owner_id, text=text, parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"[tg] notify failed: {e}")

    # ---------- Handlers ----------

    def _register_handlers(self) -> None:
        d = self.dp
        d.message.register(self.on_start, Command("start"))
        d.message.register(self.on_help, Command("help"))
        d.message.register(self.on_stats, Command("stats"))
        d.message.register(self.on_pending, Command("pending"))
        d.message.register(self.on_recent, Command("recent"))
        d.message.register(self.on_health, Command("health"))
        d.message.register(self.on_pause, Command("pause"))
        d.message.register(self.on_resume, Command("resume"))
        d.message.register(self.on_settings, Command("settings"))
        d.message.register(self.on_dump, Command("dump"))
        d.message.register(self.on_dump, Command("backup"))
        d.message.register(self.on_scan, Command("scan"))
        d.message.register(self.on_events, Command("events"))

        d.message.register(self.on_stats, F.text == BTN_STATS)
        d.message.register(self.on_pending, F.text == BTN_PENDING)
        d.message.register(self.on_recent, F.text == BTN_RECENT)
        d.message.register(self.on_health, F.text == BTN_HEALTH)
        d.message.register(self.on_pause, F.text == BTN_PAUSE)
        d.message.register(self.on_resume, F.text == BTN_RESUME)
        d.message.register(self.on_settings, F.text == BTN_SETTINGS)
        d.message.register(self.on_dump, F.text == BTN_DUMP)
        d.message.register(self.on_scan, F.text == BTN_SCAN)
        d.message.register(self.on_events, F.text == BTN_EVENTS)
        d.message.register(self.on_help, F.text == BTN_HELP)

        # Inline callbacks
        d.callback_query.register(self.cb_refresh_stats, F.data == "refresh:stats")
        d.callback_query.register(self.cb_refresh_pending, F.data == "refresh:pending")
        d.callback_query.register(self.cb_refresh_recent, F.data == "refresh:recent")
        d.callback_query.register(self.cb_refresh_health, F.data == "refresh:health")
        d.callback_query.register(self.cb_settings_toggle, F.data == "settings:toggle")
        d.callback_query.register(self.cb_settings_refresh, F.data == "settings:refresh")
        d.callback_query.register(self.cb_dump_duckdb, F.data == "dump:duckdb")
        d.callback_query.register(self.cb_dump_csv, F.data == "dump:csv")
        d.callback_query.register(self.cb_dump_info, F.data == "dump:info")
        d.callback_query.register(self.cb_refresh_scan, F.data == "refresh:scan")
        d.callback_query.register(self.cb_refresh_events, F.data == "refresh:events")

    # ---- Команды ----

    async def on_start(self, m: Message) -> None:
        await m.answer(WELCOME, reply_markup=main_keyboard(self.state.is_paused()))

    async def on_help(self, m: Message) -> None:
        await m.answer(HELP)

    async def on_stats(self, m: Message) -> None:
        await m.answer(self._format_stats(), reply_markup=inline_refresh("refresh:stats"))

    async def on_pending(self, m: Message) -> None:
        await m.answer(
            self._format_pending(),
            reply_markup=inline_refresh("refresh:pending"),
        )

    async def on_recent(self, m: Message) -> None:
        await m.answer(
            self._format_recent(),
            reply_markup=inline_refresh("refresh:recent"),
        )

    async def on_health(self, m: Message) -> None:
        await m.answer(
            self._format_health(),
            reply_markup=inline_refresh("refresh:health"),
        )

    async def on_pause(self, m: Message) -> None:
        self.state.set_paused(True)
        await m.answer(
            "⏸ Поставил на паузу — новых ставок не будет.",
            reply_markup=main_keyboard(paused=True),
        )

    async def on_resume(self, m: Message) -> None:
        self.state.set_paused(False)
        await m.answer(
            "▶ Запустил скан рынков.",
            reply_markup=main_keyboard(paused=False),
        )

    async def on_settings(self, m: Message) -> None:
        await m.answer(
            self._format_settings(),
            reply_markup=inline_settings(self.state.is_paused()),
        )

    async def on_dump(self, m: Message) -> None:
        await m.answer(self._format_dump_info(), reply_markup=inline_dump_choice())

    async def on_scan(self, m: Message) -> None:
        await m.answer(self._format_scan(), reply_markup=inline_refresh("refresh:scan"))

    async def on_events(self, m: Message) -> None:
        await m.answer(
            self._format_events(),
            reply_markup=inline_refresh("refresh:events"),
        )

    # ---- Callbacks ----

    async def cb_refresh_stats(self, cb: CallbackQuery) -> None:
        await self._edit_or_ignore(cb, self._format_stats(), inline_refresh("refresh:stats"))

    async def cb_refresh_pending(self, cb: CallbackQuery) -> None:
        await self._edit_or_ignore(cb, self._format_pending(), inline_refresh("refresh:pending"))

    async def cb_refresh_recent(self, cb: CallbackQuery) -> None:
        await self._edit_or_ignore(cb, self._format_recent(), inline_refresh("refresh:recent"))

    async def cb_refresh_health(self, cb: CallbackQuery) -> None:
        await self._edit_or_ignore(cb, self._format_health(), inline_refresh("refresh:health"))

    async def cb_settings_toggle(self, cb: CallbackQuery) -> None:
        paused = self.state.is_paused()
        self.state.set_paused(not paused)
        await cb.answer(
            "⏸ Поставлено на паузу" if not paused else "▶ Возобновлено"
        )
        await self._edit_or_ignore(
            cb, self._format_settings(), inline_settings(self.state.is_paused()),
        )

    async def cb_settings_refresh(self, cb: CallbackQuery) -> None:
        await self._edit_or_ignore(
            cb, self._format_settings(), inline_settings(self.state.is_paused()),
        )

    async def cb_dump_duckdb(self, cb: CallbackQuery) -> None:
        await cb.answer("Готовлю DuckDB-снимок…")
        await self._send_dump_duckdb(cb.message)

    async def cb_dump_csv(self, cb: CallbackQuery) -> None:
        await cb.answer("Готовлю CSV-архив…")
        await self._send_dump_csv(cb.message)

    async def cb_dump_info(self, cb: CallbackQuery) -> None:
        await self._edit_or_ignore(cb, self._format_dump_info(), inline_dump_choice())

    async def cb_refresh_scan(self, cb: CallbackQuery) -> None:
        await self._edit_or_ignore(cb, self._format_scan(), inline_refresh("refresh:scan"))

    async def cb_refresh_events(self, cb: CallbackQuery) -> None:
        await self._edit_or_ignore(cb, self._format_events(), inline_refresh("refresh:events"))

    async def _edit_or_ignore(self, cb: CallbackQuery, text: str, markup):
        try:
            await cb.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest:
            pass  # сообщение не изменилось — это норма
        finally:
            try:
                await cb.answer()
            except Exception:
                pass

    # ---------- Форматтеры ----------

    def _format_stats(self) -> str:
        s = self.state.summary_stats()
        n_total = s["pending"] + s["resolved"]
        wr = s.get("win_rate")
        ev = s.get("ev_per_dollar")
        return (
            "📊 <b>Сводка</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            f"💼 Открытых: <b>{s['pending']}</b> (вложено ${s['pending_cost']:.2f})\n"
            f"✔ Резолвнутых: <b>{s['resolved']}</b>\n"
            f"━━━━━━━━━━━━━━━━━\n"
            + (
                f"📊 Win rate: <b>{wr:.1%}</b> ({s['wins']}/{s['wins']+s['losses']})\n"
                if wr is not None
                else "📊 Win rate: <i>пока нет резолвов</i>\n"
            )
            + f"💰 Всего PnL: <b>${s['total_pnl']:+.2f}</b>\n"
            + (
                f"📈 EV / $1 invested: <b>{ev:+.2%}</b>"
                if ev is not None
                else "📈 EV / $: <i>—</i>"
            )
        )

    def _format_pending(self) -> str:
        rows = self.state.pending_summary(limit=15)
        if not rows:
            return "💼 <b>Открытые</b>\n\n<i>Пока ни одной ставки.</i>"
        lines = ["💼 <b>Открытые</b>\n━━━━━━━━━━━━━━━━━"]
        for r in rows:
            end = (r.get("end_date_iso") or "?")[:10]
            q = _short(r.get("market_question") or "", 60)
            lines.append(
                f"#{r['trade_id']} <b>{r['entry_price']:.3f}</b> → {end}  "
                f"vol ${(r.get('volume') or 0):,.0f}\n"
                f"  <i>{html.escape(q)}</i>"
            )
        return "\n".join(lines)

    def _format_recent(self) -> str:
        rows = self.state.recent_resolutions(limit=10)
        if not rows:
            return "📜 <b>Последние резолвы</b>\n\n<i>Пока пусто.</i>"
        lines = ["📜 <b>Последние резолвы</b>\n━━━━━━━━━━━━━━━━━"]
        for r in rows:
            emoji = "✅" if (r.get("pnl") or 0) > 0 else "❌"
            q = _short(r.get("market_question") or "", 60)
            res = "YES" if r.get("resolved_yes") else "NO"
            lines.append(
                f"{emoji} #{r['trade_id']} {res} pnl <b>{r['pnl']:+.4f}</b>\n"
                f"  <i>{html.escape(q)}</i>"
            )
        return "\n".join(lines)

    def _format_health(self) -> str:
        import json as _json
        last_etl = self.state.get_setting("last_etl_ts")
        last_resolve = self.state.get_setting("last_resolve_ts")
        last_scan_raw = self.state.get_setting("last_scan_stats")
        paused = self.state.is_paused()
        now = int(time.time())

        def _ago(ts_str: str | None) -> str:
            if not ts_str:
                return "<i>никогда</i>"
            try:
                ago = now - int(ts_str)
                if ago < 60:
                    return f"{ago}s назад"
                if ago < 3600:
                    return f"{ago // 60}мин назад"
                return f"{ago // 3600}ч назад"
            except Exception:
                return "?"

        scan_block = ""
        if last_scan_raw:
            try:
                s = _json.loads(last_scan_raw)
                scan_block = (
                    "\n<b>Последний скан:</b>\n"
                    f"• активных: <b>{s.get('total_active', 0)}</b>\n"
                    f"• кандидатов: {s.get('candidates', 0)} "
                    f"(низ. volume отброшено: {s.get('skip_low_volume', 0)})\n"
                    f"• цена ниже {self.cfg.strategy_low:.2f}: <b>{s.get('skip_below', 0)}</b>\n"
                    f"• цена ≥ {self.cfg.strategy_high:.2f}: <b>{s.get('skip_above', 0)}</b>\n"
                    f"• ⭐ <b>в диапазоне: {s.get('in_range', 0)}</b>\n"
                    f"• нет 24h истории: {s.get('skip_no_history', 0)}\n"
                    f"• уже взяли: {s.get('skip_already_taken', 0)}\n"
                    f"• длительность скана: {s.get('duration_s', 0):.1f}s"
                )
            except Exception:
                pass

        errs = self.state.last_events(limit=3, level="ERROR")
        err_block = ""
        if errs:
            err_block = "\n<b>⚠ Ошибки:</b>\n" + "\n".join(
                f"• {html.escape(e['component'])}: {html.escape(_short(e['message'], 80))}"
                for e in errs
            )

        return (
            "❤️ <b>Здоровье</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            f"⏯ Статус: <b>{'⏸ Пауза' if paused else '▶ Активен'}</b>\n"
            f"🔄 Последний ETL: {_ago(last_etl)}\n"
            f"🎯 Последний резолв-чек: {_ago(last_resolve)}"
            + scan_block
            + err_block
        )

    def _format_scan(self) -> str:
        import json as _json
        last_scan_raw = self.state.get_setting("last_scan_stats")
        last_etl = self.state.get_setting("last_etl_ts")
        now = int(time.time())
        ago = "—"
        if last_etl:
            try:
                a = now - int(last_etl)
                ago = f"{a}s" if a < 60 else f"{a // 60}мин" if a < 3600 else f"{a // 3600}ч"
            except Exception:
                pass

        if not last_scan_raw:
            return (
                "🔍 <b>Что вижу</b>\n\n<i>Скан ещё не был выполнен. Подожди до 15 минут.</i>"
            )
        try:
            s = _json.loads(last_scan_raw)
        except Exception:
            return "🔍 <b>Что вижу</b>\n\n<i>Не удалось распарсить данные скана.</i>"

        lines = [
            f"🔍 <b>Последний скан</b> ({ago} назад)",
            "━━━━━━━━━━━━━━━━━",
            f"📊 Топ-{s.get('total_active', 0)} активных рынков отсканировано",
            f"🎯 Стратегия: <b>цена YES @ T-24h ∈ [{self.cfg.strategy_low:.2f}, {self.cfg.strategy_high:.2f})</b>",
            "",
            f"📈 <b>Воронка:</b>",
            f"• Скачано: <b>{s.get('total_active', 0)}</b>",
            f"• Прошли фильтр volume ≥ ${self.cfg.min_market_volume:.0f}: <b>{s.get('candidates', 0)}</b>",
            f"  ↳ откинуто volume: {s.get('skip_low_volume', 0)}",
            f"  ↳ битые токены: {s.get('skip_no_token', 0)}",
            f"  ↳ уже наша ставка: {s.get('skip_already_taken', 0)}",
            f"• Из кандидатов с историей: <b>"
            f"{s.get('candidates', 0) - s.get('skip_no_history', 0)}</b>",
            f"  ↳ моложе 24h: {s.get('skip_no_history', 0)}",
            f"",
            f"📉 <b>По цене:</b>",
            f"• Слишком дёшево (< {self.cfg.strategy_low:.2f}): <b>{s.get('skip_below', 0)}</b>",
            f"• Слишком дорого (≥ {self.cfg.strategy_high:.2f}): <b>{s.get('skip_above', 0)}</b>",
            f"• ⭐ <b>В диапазоне: {s.get('in_range', 0)}</b>",
        ]

        nb = s.get("near_below") or []
        if nb:
            lines.append(f"\n<b>↑ Близко к нижней границе (могут зайти):</b>")
            for it in nb[:5]:
                p = it.get("price_yes_t24h") or 0
                vol = it.get("volume") or 0
                q = _short(it.get("question") or "", 55)
                lines.append(f"  <b>{p:.3f}</b>  vol ${vol:,.0f}\n    <i>{html.escape(q)}</i>")

        na = s.get("near_above") or []
        if na:
            lines.append(f"\n<b>↓ Близко к верхней границе:</b>")
            for it in na[:5]:
                p = it.get("price_yes_t24h") or 0
                vol = it.get("volume") or 0
                q = _short(it.get("question") or "", 55)
                lines.append(f"  <b>{p:.3f}</b>  vol ${vol:,.0f}\n    <i>{html.escape(q)}</i>")

        if s.get("in_range", 0) == 0 and not nb and not na:
            lines.append(
                "\n<i>Все рынки сейчас на крайних ценах "
                "(<0.50 — longshots, ≥0.85 — fav). "
                "H1 catches «середняков», их сейчас просто нет в топ-100.</i>"
            )

        return "\n".join(lines)

    def _format_events(self) -> str:
        events = self.state.last_events(limit=20)
        if not events:
            return "📋 <b>Журнал</b>\n\n<i>События ещё не записаны.</i>"
        now = int(time.time())
        lines = ["📋 <b>Журнал событий</b>\n━━━━━━━━━━━━━━━━━"]
        for e in events:
            ago = now - int(e["ts"])
            ago_str = (
                f"{ago}s" if ago < 60
                else f"{ago // 60}m" if ago < 3600
                else f"{ago // 3600}h"
            )
            level = e["level"]
            emoji = {"INFO": "•", "WARNING": "⚠", "ERROR": "❌"}.get(level, "·")
            comp = html.escape(e["component"])
            msg = html.escape(_short(e["message"], 90))
            lines.append(f"{emoji} <b>{comp}</b> ({ago_str}): {msg}")
        return "\n".join(lines)

    def _format_dump_info(self) -> str:
        s = self.state.summary_stats()
        try:
            size = Path(self.cfg.db_path).stat().st_size
            size_str = f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.2f} MB"
        except OSError:
            size_str = "?"
        return (
            "🗄 <b>Дамп БД</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            f"📁 Файл: <code>{self.cfg.db_path.name}</code>\n"
            f"💾 Размер: <b>{size_str}</b>\n"
            f"📊 Записей: {s['pending'] + s['resolved'] + s['cancelled']} "
            f"(open {s['pending']}, done {s['resolved']})\n\n"
            "<i>DuckDB-файл</i> — открывается DuckDB CLI / Python\n"
            "<i>CSV-архив</i> — 4 таблицы, открывается в любом редакторе"
        )

    async def _send_dump_duckdb(self, m: Message) -> None:
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir) / (
                    f"gadalka_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.duckdb"
                )
                self.state.make_dump(tmp)
                size = tmp.stat().st_size
                file = FSInputFile(tmp, filename=tmp.name)
                await self.bot.send_document(
                    chat_id=self.owner_id,
                    document=file,
                    caption=(
                        f"🗄 <b>{tmp.name}</b>\n"
                        f"💾 {size / 1024:.1f} KB"
                    ),
                )
        except Exception as e:
            logger.exception("[dump] duckdb failed")
            await m.answer(f"❌ Ошибка: <code>{html.escape(str(e))}</code>")

    async def _send_dump_csv(self, m: Message) -> None:
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                csv_dir = Path(tmpdir) / "csv"
                self.state.export_csv_bundle(csv_dir)

                stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                zip_path = Path(tmpdir) / f"gadalka_{stamp}.zip"
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
                    for f in csv_dir.iterdir():
                        z.write(f, arcname=f.name)
                size = zip_path.stat().st_size
                file = FSInputFile(zip_path, filename=zip_path.name)
                await self.bot.send_document(
                    chat_id=self.owner_id,
                    document=file,
                    caption=(
                        f"📑 <b>{zip_path.name}</b>\n"
                        f"💾 {size / 1024:.1f} KB (4 CSV таблицы)"
                    ),
                )
        except Exception as e:
            logger.exception("[dump] csv failed")
            await m.answer(f"❌ Ошибка: <code>{html.escape(str(e))}</code>")

    def _format_settings(self) -> str:
        return (
            "⚙ <b>Настройки</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            f"📐 Стратегия: <code>H1 [{self.cfg.strategy_low:.2f}–{self.cfg.strategy_high:.2f}] @ T-24h</code>\n"
            f"💰 Размер ставки: <code>${self.cfg.stake_amount:.2f}</code>\n"
            f"💸 Fee: <code>{self.cfg.fee_rate:.1%}</code>\n"
            f"📊 Spread: <code>{self.cfg.spread_pct:.1%}</code>\n"
            f"⏱ ETL interval: <code>{self.cfg.etl_interval_s}s</code>\n"
            f"⏱ Resolve interval: <code>{self.cfg.resolve_interval_s}s</code>\n"
            f"📅 Daily report: <code>{self.cfg.daily_report_time} UTC</code>\n"
            f"⏯ <b>{'⏸ Пауза' if self.state.is_paused() else '▶ Активен'}</b>"
        )

    # ---------- Lifecycle ----------

    async def start(self) -> None:
        """Запустить long-polling в фоне."""
        logger.info("[tg] start polling for owner {}", self.owner_id)
        await self.bot.delete_webhook(drop_pending_updates=True)
        self._task = asyncio.create_task(
            self.dp.start_polling(self.bot, handle_signals=False),
            name="tg-polling",
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            await self.dp.stop_polling()
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.bot.session.close()


def _short(text: str, n: int) -> str:
    if not text:
        return ""
    return text if len(text) <= n else text[: n - 1] + "…"
