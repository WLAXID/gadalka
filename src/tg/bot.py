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
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any as _Any

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    Message,
    TelegramObject,
)
from loguru import logger

from src.paper.config import PaperConfig
from src.paper.state import PaperState
from src.tg.keyboards import (
    BTN_HEALTH,
    BTN_HELP,
    BTN_PAUSE,
    BTN_PENDING,
    BTN_RECENT,
    BTN_RESUME,
    BTN_SETTINGS,
    BTN_STATS,
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
    "/pending — открытые ставки\n"
    "/recent — последние резолвы\n"
    "/health — статус loops\n"
    "/pause /resume — пауза/возобновить новые ставки\n"
    "/settings — настройки\n\n"
    "<b>Логика:</b>\n"
    "• Каждые 15 мин — скан рынков и новые ставки\n"
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

        d.message.register(self.on_stats, F.text == BTN_STATS)
        d.message.register(self.on_pending, F.text == BTN_PENDING)
        d.message.register(self.on_recent, F.text == BTN_RECENT)
        d.message.register(self.on_health, F.text == BTN_HEALTH)
        d.message.register(self.on_pause, F.text == BTN_PAUSE)
        d.message.register(self.on_resume, F.text == BTN_RESUME)
        d.message.register(self.on_settings, F.text == BTN_SETTINGS)
        d.message.register(self.on_help, F.text == BTN_HELP)

        # Inline callbacks
        d.callback_query.register(self.cb_refresh_stats, F.data == "refresh:stats")
        d.callback_query.register(self.cb_refresh_pending, F.data == "refresh:pending")
        d.callback_query.register(self.cb_refresh_recent, F.data == "refresh:recent")
        d.callback_query.register(self.cb_refresh_health, F.data == "refresh:health")
        d.callback_query.register(self.cb_settings_toggle, F.data == "settings:toggle")
        d.callback_query.register(self.cb_settings_refresh, F.data == "settings:refresh")

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
        last_etl = self.state.get_setting("last_etl_ts")
        last_resolve = self.state.get_setting("last_resolve_ts")
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
            f"🎯 Последний резолв-чек: {_ago(last_resolve)}\n"
            + err_block
        )

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
