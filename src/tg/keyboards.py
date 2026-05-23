"""Раскладки клавиатур для Telegram-панели."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


# --- Лейблы reply-кнопок (используются и в фильтрах роутинга) ---

BTN_STATS = "📊 Сводка"
BTN_PENDING = "💼 Открытые"
BTN_RECENT = "📜 Последние"
BTN_HEALTH = "❤️ Здоровье"
BTN_PAUSE = "⏸ Пауза"
BTN_RESUME = "▶ Запустить"
BTN_SETTINGS = "⚙ Настройки"
BTN_HELP = "❓ Помощь"


def main_keyboard(paused: bool = False) -> ReplyKeyboardMarkup:
    """Главное меню — большие кнопки внизу экрана."""
    toggle = BTN_RESUME if paused else BTN_PAUSE
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_STATS), KeyboardButton(text=BTN_PENDING)],
            [KeyboardButton(text=BTN_RECENT), KeyboardButton(text=BTN_HEALTH)],
            [KeyboardButton(text=toggle), KeyboardButton(text=BTN_SETTINGS)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def inline_refresh(callback_data: str) -> InlineKeyboardMarkup:
    """Inline-кнопка 🔄 Обновить."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=callback_data)]
        ]
    )


def inline_pending_actions(trade_id: int) -> InlineKeyboardMarkup:
    """Действия для одной открытой ставки."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔍 Детали", callback_data=f"trade:info:{trade_id}"),
                InlineKeyboardButton(text="❌ Отменить", callback_data=f"trade:cancel:{trade_id}"),
            ],
        ]
    )


def inline_settings(paused: bool) -> InlineKeyboardMarkup:
    """Меню настроек: пауза/возобновление, текущая стратегия."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="▶ Запустить" if paused else "⏸ Пауза",
                    callback_data="settings:toggle",
                ),
            ],
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="settings:refresh")],
        ]
    )
