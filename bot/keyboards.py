from __future__ import annotations
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import BLUE_CHIPS, RISKY_STOCKS
from analysis.signals import SIGNAL_BUY, SIGNAL_HOLD, SIGNAL_SELL, SIGNAL_WAIT

_SIGNAL_EMOJI = {
    SIGNAL_BUY:  "🟢",
    SIGNAL_HOLD: "🟡",
    SIGNAL_SELL: "🔴",
    SIGNAL_WAIT: "⏳",
}


def main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Сигналы",     callback_data="menu:signals"),
        InlineKeyboardButton(text="🔍 Анализ акции", callback_data="menu:pick_stock"),
    )
    builder.row(
        InlineKeyboardButton(text="🌍 Макро",        callback_data="menu:macro"),
        InlineKeyboardButton(text="📰 Новости",      callback_data="menu:news_pick"),
    )
    builder.row(
        InlineKeyboardButton(text="⚡ Идеи роста",   callback_data="menu:risky"),
        InlineKeyboardButton(text="🏦 ОФЗ",          callback_data="menu:ofz"),
    )
    builder.row(
        InlineKeyboardButton(text="💼 Портфель",     callback_data="menu:portfolio"),
        InlineKeyboardButton(text="⚙️ Настройки",    callback_data="menu:settings"),
    )
    return builder.as_markup()


def stocks_list(signals: dict[str, str] | None = None) -> InlineKeyboardMarkup:
    """Список голубых фишек с цветными индикаторами сигналов."""
    builder = InlineKeyboardBuilder()
    signals = signals or {}

    items = list(BLUE_CHIPS.items())
    for i in range(0, len(items), 3):
        row_items = items[i:i+3]
        row = []
        for ticker, info in row_items:
            sig = signals.get(ticker, "")
            emoji = _SIGNAL_EMOJI.get(sig, "⚪")
            row.append(InlineKeyboardButton(
                text=f"{emoji} {ticker}",
                callback_data=f"analyze:{ticker}",
            ))
        builder.row(*row)

    builder.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu:main"))
    return builder.as_markup()


def stock_detail(ticker: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📰 Новости",       callback_data=f"news:{ticker}"),
        InlineKeyboardButton(text="🔔 Добавить алерт", callback_data=f"alert_add:{ticker}"),
    )
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить",      callback_data=f"analyze:{ticker}"),
        InlineKeyboardButton(text="◀️ Назад",          callback_data="menu:pick_stock"),
    )
    return builder.as_markup()


def news_pick() -> InlineKeyboardMarkup:
    """Выбор акции для просмотра новостей."""
    builder = InlineKeyboardBuilder()
    all_tickers = {**BLUE_CHIPS, **RISKY_STOCKS}
    items = list(all_tickers.items())
    for i in range(0, len(items), 3):
        row_items = items[i:i+3]
        builder.row(*[
            InlineKeyboardButton(text=t, callback_data=f"news:{t}")
            for t, _ in row_items
        ])
    builder.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu:main"))
    return builder.as_markup()


def news_detail(ticker: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔍 Анализ акции", callback_data=f"analyze:{ticker}"),
        InlineKeyboardButton(text="🔄 Обновить",     callback_data=f"news:{ticker}"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu:main"))
    return builder.as_markup()


def settings(
    alerts: bool, brief: bool, rsi_alerts: bool, news_alerts: bool, bank_size: float | None
) -> InlineKeyboardMarkup:
    def tog(val: bool) -> str:
        return "✅ ВКЛ" if val else "❌ ВЫКЛ"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=f"🔔 Алерты: {tog(alerts)}",   callback_data="setting:alerts"),
        InlineKeyboardButton(text=f"📅 Брифинг: {tog(brief)}",   callback_data="setting:brief"),
    )
    builder.row(
        InlineKeyboardButton(text=f"📊 RSI: {tog(rsi_alerts)}",  callback_data="setting:rsi_alerts"),
        InlineKeyboardButton(text=f"📰 Новости: {tog(news_alerts)}", callback_data="setting:news_alerts"),
    )
    bank_label = f"{bank_size:,.0f} ₽" if bank_size else "не задан"
    builder.row(InlineKeyboardButton(text=f"💰 Размер банка: {bank_label}", callback_data="setting:bank"))
    builder.row(
        InlineKeyboardButton(text="📋 Мой вотчлист", callback_data="menu:watchlist"),
        InlineKeyboardButton(text="◀️ Меню",          callback_data="menu:main"),
    )
    return builder.as_markup()


def watchlist_manage(tickers: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for ticker in tickers:
        builder.row(
            InlineKeyboardButton(text=f"🔍 {ticker}", callback_data=f"analyze:{ticker}"),
            InlineKeyboardButton(text="❌ Удалить",   callback_data=f"wl_remove:{ticker}"),
        )
    builder.row(InlineKeyboardButton(text="➕ Добавить акцию", callback_data="wl_add"))
    builder.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu:main"))
    return builder.as_markup()


def back_to_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="◀️ Меню", callback_data="menu:main"))
    return builder.as_markup()
