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


# Telegram message limit = 4096 chars. Берём с запасом.
_TG_MAX = 3900


WELCOME = (
    "👋 <b>Привет!</b> Я — гадалка.\n\n"
    "Слежу за рынками <a href='https://polymarket.com'>Polymarket</a> "
    "и делаю <b>виртуальные ставки</b> (без реальных денег) по проверенной "
    "стратегии. Когда рынок закрывается — считаю, прав я был или нет.\n\n"
    "Цель: 4 недели накопить статистику и убедиться, что стратегия "
    "реально работает на живых рынках. Если работает — потом можно "
    "запустить с настоящими деньгами.\n\n"
    "📊 Жми кнопки ниже чтобы посмотреть что происходит 👇"
)

HELP = (
    "<b>Что умеют кнопки:</b>\n\n"
    "📊 <b>Итоги</b> — сколько ставок сделано, сколько выиграл/проиграл, "
    "общая прибыль (виртуальная)\n\n"
    "🔍 <b>Что вижу</b> — какие рынки я сейчас сканирую и почему "
    "не делаю/делаю ставку\n\n"
    "⏳ <b>Ждут результата</b> — открытые ставки на рынках, которые "
    "ещё не закрылись\n\n"
    "✅ <b>Закрытые ставки</b> — последние 10 завершённых ставок с "
    "результатом (выиграл/проиграл)\n\n"
    "🩺 <b>Состояние</b> — работает ли бот, когда сканировал в последний "
    "раз, есть ли ошибки\n\n"
    "📋 <b>События</b> — журнал последних 20 действий бота "
    "(сканы, ставки, резолвы, ошибки)\n\n"
    "⏸ <b>Пауза / ▶ Запустить</b> — остановить или возобновить новые ставки\n\n"
    "⚙ <b>Настройки</b> — какая сейчас стратегия и параметры\n\n"
    "💾 <b>Скачать данные</b> — забрать всю базу ставок к себе "
    "(DuckDB-файл или CSV для Excel)\n\n"
    "<b>Как работает:</b>\n"
    "• Каждые 15 минут смотрю топ-100 активных рынков\n"
    "• Если цена «Да» за 24ч до закрытия в полосе 50¢–85¢ → ставлю $1\n"
    "• Каждый час проверяю — закрылся рынок или нет\n"
    "• В 23:59 UTC шлю краткий отчёт за день\n"
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
        # outer_middleware получает Update, у него внутри либо message,
        # либо callback_query, либо другое. Достаём from_user из всех типов.
        user_id = None
        msg = getattr(event, "message", None)
        cb = getattr(event, "callback_query", None)
        inline = getattr(event, "inline_query", None)
        edited = getattr(event, "edited_message", None)

        if msg and msg.from_user:
            user_id = msg.from_user.id
        elif cb and cb.from_user:
            user_id = cb.from_user.id
        elif inline and inline.from_user:
            user_id = inline.from_user.id
        elif edited and edited.from_user:
            user_id = edited.from_user.id
        else:
            fu = getattr(event, "from_user", None)
            if fu:
                user_id = fu.id

        # fail-closed: если не смогли определить — дропаем
        if user_id is None or int(user_id) != self.owner_id:
            logger.warning(f"[tg] drop event from {user_id}")
            return
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
            "⏸ Поставил на паузу. Новых ставок делать не буду.\n"
            "Уже открытые ставки буду проверять как обычно.",
            reply_markup=main_keyboard(paused=True),
        )

    async def on_resume(self, m: Message) -> None:
        self.state.set_paused(False)
        await m.answer(
            "▶ Возобновил работу. При следующем скане буду снова искать сигналы.",
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

    @staticmethod
    def _truncate(text: str) -> str:
        """Обрезает текст до Telegram limit (4096), оставляя метку."""
        if len(text) <= _TG_MAX:
            return text
        return text[: _TG_MAX - 30] + "\n…\n<i>(обрезано)</i>"

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

        lines = [
            "📊 <b>Итоги</b>",
            "━━━━━━━━━━━━━━━━━",
            "",
            f"📝 <b>Всего ставок:</b> {n_total}",
            f"   • ждут результата: <b>{s['pending']}</b>  "
            f"(вложено ${s['pending_cost']:.2f})",
            f"   • уже закрыты: <b>{s['resolved']}</b>",
            "",
        ]

        if s["resolved"] > 0:
            lines.extend([
                "<b>Из закрытых ставок:</b>",
                f"   ✅ выиграно: <b>{s['wins']}</b>",
                f"   ❌ проиграно: <b>{s['losses']}</b>",
            ])
            if wr is not None:
                lines.append(f"   📊 % успешных: <b>{wr:.1%}</b>")
            lines.append("")
            lines.extend([
                "<b>💰 Виртуальная прибыль:</b>",
                f"   • всего: <b>${s['total_pnl']:+.2f}</b>",
            ])
            if ev is not None:
                pct = ev * 100
                emoji = "📈" if pct > 0 else "📉"
                lines.append(
                    f"   {emoji} в среднем на каждый $1 ставки: <b>{pct:+.1f}¢</b>"
                )
                lines.append(
                    "      <i>(если число положительное — стратегия зарабатывает)</i>"
                )
        else:
            lines.append("<i>Пока ни одна ставка не закрылась — нужно подождать. "
                         "Резолв обычно занимает от часов до недель в зависимости от рынка.</i>")

        lines.append("")
        lines.append(
            "ℹ <i>Деньги виртуальные. Цель — накопить ~100 ставок и сравнить "
            "результат с бэктестом (там было +13.5%).</i>"
        )
        return "\n".join(lines)

    def _format_pending(self) -> str:
        rows = self.state.pending_summary(limit=15)
        if not rows:
            return (
                "⏳ <b>Ждут результата</b>\n\n"
                "<i>Открытых ставок пока нет.</i>\n\n"
                "Это значит, что подходящих рынков для стратегии "
                "сейчас не нашлось. Жми <b>🔍 Что вижу</b> чтобы "
                "узнать почему."
            )
        lines = [
            "⏳ <b>Ждут результата</b>",
            "━━━━━━━━━━━━━━━━━",
            f"<i>{len(rows)} открытых ставок. Закроются когда рынок резолвится.</i>",
            "",
        ]
        for r in rows:
            end = (r.get("end_date_iso") or "?")[:10]
            q = _short(r.get("market_question") or "", 65)
            price_cents = (r.get("entry_price") or 0) * 100
            lines.append(
                f"<b>#{r['trade_id']}</b>  поставил по <b>{price_cents:.1f}¢</b>  "
                f"закрытие до {end}\n"
                f"  объём рынка ${(r.get('volume') or 0):,.0f}\n"
                f"  <i>{html.escape(q)}</i>"
            )
        return self._truncate("\n".join(lines))

    def _format_recent(self) -> str:
        rows = self.state.recent_resolutions(limit=10)
        if not rows:
            return (
                "✅ <b>Закрытые ставки</b>\n\n"
                "<i>Пока ни одна ставка не закрылась.</i>\n\n"
                "Когда рынок резолвится (закрывается с результатом), "
                "ставка переедет сюда."
            )
        lines = [
            "✅ <b>Закрытые ставки</b> — последние 10",
            "━━━━━━━━━━━━━━━━━",
        ]
        for r in rows:
            pnl = r.get("pnl") or 0
            emoji = "✅" if pnl > 0 else "❌"
            q = _short(r.get("market_question") or "", 65)
            res = "Да" if r.get("resolved_yes") else "Нет"
            lines.append(
                f"{emoji} <b>#{r['trade_id']}</b>  итог: <b>{res}</b>  "
                f"прибыль: <b>${pnl:+.4f}</b>\n"
                f"  <i>{html.escape(q)}</i>"
            )
        return self._truncate("\n".join(lines))

    def _format_health(self) -> str:
        import json as _json
        last_etl = self.state.get_setting("last_etl_ts")
        last_resolve = self.state.get_setting("last_resolve_ts")
        last_scan_raw = self.state.get_setting("last_scan_stats")
        paused = self.state.is_paused()
        now = int(time.time())

        def _ago(ts_str: str | None) -> str:
            if not ts_str:
                return "<i>пока не запускалось</i>"
            try:
                ago = now - int(ts_str)
                if ago < 60:
                    return f"{ago} сек назад"
                if ago < 3600:
                    return f"{ago // 60} мин назад"
                return f"{ago // 3600} ч назад"
            except Exception:
                return "?"

        lines = [
            "🩺 <b>Состояние</b>",
            "━━━━━━━━━━━━━━━━━",
            "",
            f"⚡ <b>Бот:</b> {'⏸ на паузе' if paused else '▶ работает'}",
            "",
            "<b>Когда что делал в последний раз:</b>",
            f"  🔍 Сканировал рынки: {_ago(last_etl)}",
            f"  ✅ Проверял закрылись ли ставки: {_ago(last_resolve)}",
        ]

        if last_scan_raw:
            try:
                s = _json.loads(last_scan_raw)
                lines.extend([
                    "",
                    "<b>Что видел в последний раз:</b>",
                    f"  📊 Просмотрел рынков: <b>{s.get('total_active', 0)}</b>",
                    f"  🎯 Подходят по цене (50¢–85¢): <b>{s.get('in_range', 0)}</b>",
                    f"  ⏬ Слишком дёшево (&lt;50¢): {s.get('skip_below', 0)}",
                    f"  ⏫ Слишком дорого (≥85¢): {s.get('skip_above', 0)}",
                ])
                if s.get("skip_no_history", 0) > 0:
                    lines.append(
                        f"  ⏳ Слишком новые рынки (&lt;24ч): {s.get('skip_no_history', 0)}"
                    )
            except Exception:
                pass

        errs = self.state.last_events(limit=3, level="ERROR")
        if errs:
            lines.extend(["", "<b>⚠ Свежие ошибки:</b>"])
            for e in errs:
                lines.append(
                    f"  • {html.escape(e['component'])}: "
                    f"{html.escape(_short(e['message'], 90))}"
                )
        else:
            lines.append("\n✅ <i>Ошибок нет, всё работает штатно.</i>")

        return "\n".join(lines)

    def _format_scan(self) -> str:
        import json as _json
        last_scan_raw = self.state.get_setting("last_scan_stats")
        last_etl = self.state.get_setting("last_etl_ts")
        now = int(time.time())
        ago = "—"
        if last_etl:
            try:
                a = now - int(last_etl)
                ago = (
                    f"{a} сек назад" if a < 60
                    else f"{a // 60} мин назад" if a < 3600
                    else f"{a // 3600} ч назад"
                )
            except Exception:
                pass

        if not last_scan_raw:
            return (
                "🔍 <b>Что вижу</b>\n\n"
                "<i>Сканирование ещё не запускалось. Подожди до 15 минут — "
                "первый скан будет автоматически.</i>"
            )
        try:
            s = _json.loads(last_scan_raw)
        except Exception:
            return "🔍 <b>Что вижу</b>\n\n<i>Не удалось прочитать данные скана.</i>"

        lo = self.cfg.strategy_low
        hi = self.cfg.strategy_high
        lines = [
            f"🔍 <b>Что вижу</b>",
            "━━━━━━━━━━━━━━━━━",
            f"<i>Последний раз смотрел: {ago}</i>",
            "",
            f"🎯 <b>Я ищу рынки</b>, где цена «Да» стоит между "
            f"<b>{int(lo*100)}¢</b> и <b>{int(hi*100)}¢</b> "
            f"за 24 часа до закрытия рынка.",
            "",
            "<b>📊 Что было в последнем скане:</b>",
            "",
            f"1️⃣ Скачал самые активные рынки: <b>{s.get('total_active', 0)}</b>",
            f"2️⃣ Отбросил с малым объёмом (&lt;$100): {s.get('skip_low_volume', 0)}",
            f"3️⃣ Отбросил без нормальных токенов: {s.get('skip_no_token', 0)}",
            f"4️⃣ Отбросил рынки, где уже ставил: {s.get('skip_already_taken', 0)}",
            f"5️⃣ Отбросил слишком новые (моложе 24ч): {s.get('skip_no_history', 0)}",
            "",
            "<b>📉 Из оставшихся — по цене «Да»:</b>",
            f"   ⏬ Слишком дёшево (&lt;{int(lo*100)}¢): <b>{s.get('skip_below', 0)}</b>  "
            f"<i>— почти точно «Нет»</i>",
            f"   ⏫ Слишком дорого (≥{int(hi*100)}¢): <b>{s.get('skip_above', 0)}</b>  "
            f"<i>— почти точно «Да»</i>",
            f"   ⭐ <b>В нашем диапазоне: {s.get('in_range', 0)}</b>",
        ]

        if s.get("in_range", 0) > 0:
            lines.append("")
            lines.append("🎉 <i>Эти рынки уже добавлены как ставки.</i>")

        nb = s.get("near_below") or []
        if nb:
            lines.append("")
            lines.append("<b>🔻 Чуть ниже диапазона (могут вырасти и зайти):</b>")
            for it in nb[:5]:
                p = it.get("price_yes_t24h") or 0
                vol = it.get("volume") or 0
                q = _short(it.get("question") or "", 60)
                lines.append(
                    f"  <b>{int(p*100)}¢</b>  объём ${vol:,.0f}\n"
                    f"    <i>{html.escape(q)}</i>"
                )

        na = s.get("near_above") or []
        if na:
            lines.append("")
            lines.append("<b>🔺 Чуть выше диапазона (могут упасть и зайти):</b>")
            for it in na[:5]:
                p = it.get("price_yes_t24h") or 0
                vol = it.get("volume") or 0
                q = _short(it.get("question") or "", 60)
                lines.append(
                    f"  <b>{int(p*100)}¢</b>  объём ${vol:,.0f}\n"
                    f"    <i>{html.escape(q)}</i>"
                )

        if s.get("in_range", 0) == 0:
            lines.append("")
            lines.append(
                "ℹ <i><b>Почему 0 ставок?</b> Топ-100 рынков сейчас в основном "
                "на крайних ценах: явные фавориты (~99¢) и явные аутсайдеры (~1¢). "
                "Стратегия ищет середнячков — это редко: 1-2 раза в день. "
                "Жди или загляни через час.</i>"
            )

        return self._truncate("\n".join(lines))

    def _format_events(self) -> str:
        events = self.state.last_events(limit=20)
        if not events:
            return (
                "📋 <b>События</b>\n\n"
                "<i>Журнал пуст. Подожди — бот начнёт писать сюда после "
                "первого скана.</i>"
            )
        now = int(time.time())
        lines = [
            "📋 <b>События</b> — последние 20",
            "━━━━━━━━━━━━━━━━━",
            "<i>Что бот делал в хронологическом порядке (новые сверху).</i>",
            "",
        ]

        # Переводим название компонента на русский
        comp_names = {
            "scan": "🔍 скан",
            "etl": "🔄 сканер",
            "resolve": "✅ проверка",
            "resolver": "✅ проверка",
            "scheduler": "⚙ планировщик",
            "daily": "📅 отчёт",
            "signal": "🎯 сигнал",
        }
        level_emoji = {"INFO": "•", "WARNING": "⚠️", "ERROR": "❌"}

        for e in events:
            ago = now - int(e["ts"])
            ago_str = (
                f"{ago} сек" if ago < 60
                else f"{ago // 60} мин" if ago < 3600
                else f"{ago // 3600} ч"
            )
            level = e["level"]
            emoji = level_emoji.get(level, "·")
            comp_raw = e["component"]
            comp = comp_names.get(comp_raw, html.escape(comp_raw))
            msg = html.escape(_short(e["message"], 110))
            lines.append(f"{emoji} <b>{comp}</b> ({ago_str} назад)\n   {msg}")
        return self._truncate("\n".join(lines))

    def _format_dump_info(self) -> str:
        s = self.state.summary_stats()
        try:
            size = Path(self.cfg.db_path).stat().st_size
            size_str = f"{size / 1024:.1f} КБ" if size < 1024 * 1024 else f"{size / 1024 / 1024:.2f} МБ"
        except OSError:
            size_str = "?"
        total = s['pending'] + s['resolved'] + s['cancelled']
        return (
            "💾 <b>Скачать данные</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "\n"
            f"📁 База данных: <code>{self.cfg.db_path.name}</code>\n"
            f"💾 Размер: <b>{size_str}</b>\n"
            f"📊 Всего записей: <b>{total}</b> "
            f"(ждут — {s['pending']}, закрыто — {s['resolved']})\n"
            "\n"
            "<b>Выбери формат:</b>\n"
            "  <b>🗄 DuckDB-файл</b> — оригинал базы, открывается через\n"
            "      DuckDB или Python (для технического анализа)\n"
            "  <b>📑 CSV-архив</b> — 4 таблицы как CSV, открываются\n"
            "      в Excel / LibreOffice / любом редакторе"
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
        lo = self.cfg.strategy_low
        hi = self.cfg.strategy_high
        return (
            "⚙ <b>Настройки</b>\n"
            "━━━━━━━━━━━━━━━━━\n"
            "\n"
            "<b>🎯 Стратегия</b>\n"
            f"   Покупаю «Да» если его цена за 24ч до закрытия рынка\n"
            f"   в диапазоне <b>{int(lo*100)}¢ – {int(hi*100)}¢</b>\n"
            "\n"
            "<b>💰 Размер ставки</b>\n"
            f"   <b>${self.cfg.stake_amount:.2f}</b> на каждый сигнал (виртуально)\n"
            "\n"
            "<b>💸 Учёт издержек</b>\n"
            f"   Комиссия Polymarket: <b>{self.cfg.fee_rate:.1%}</b> от прибыли\n"
            f"   Спред (между ценой покупки и серединой): <b>{self.cfg.spread_pct:.1%}</b>\n"
            f"   Проскальзывание: <b>{self.cfg.slippage_pct:.1%}</b>\n"
            "\n"
            "<b>⏱ Периоды</b>\n"
            f"   Сканирование рынков: каждые <b>{self.cfg.etl_interval_s // 60} мин</b>\n"
            f"   Проверка закрытий: каждые <b>{self.cfg.resolve_interval_s // 60} мин</b>\n"
            f"   Ежедневный отчёт: <b>{self.cfg.daily_report_time} UTC</b>\n"
            "\n"
            f"<b>⚡ Сейчас:</b> "
            f"{'⏸ на паузе' if self.state.is_paused() else '▶ работает'}"
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
