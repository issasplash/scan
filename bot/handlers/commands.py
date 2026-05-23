from __future__ import annotations
import logging
from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from bot.keyboards import main_menu

router = Router()
logger = logging.getLogger("commands")

WELCOME = (
    "👋 <b>Добро пожаловать в инвестиционного помощника!</b>\n\n"
    "Я анализирую российский фондовый рынок и даю рекомендации по акциям.\n\n"
    "<b>Что умею:</b>\n"
    "📊 <b>Сигналы</b> — голубые фишки с рекомендациями\n"
    "🔍 <b>Анализ</b> — 5-слойный разбор любой акции\n"
    "🌍 <b>Макро</b> — нефть, рубль, ставка ЦБ, ММВБ\n"
    "📰 <b>Новости</b> — свежие новости с ИИ-оценкой\n"
    "⚡ <b>Идеи роста</b> — нестандартные возможности\n"
    "🏦 <b>ОФЗ</b> — облигации под текущую ставку\n\n"
    "⚠️ <i>Бот даёт аналитику, не инвестиционные советы.</i>\n"
    "Используй как один из инструментов принятия решений."
)


@router.message(CommandStart())
async def cmd_start(message: Message):
    from db.models import SessionLocal, User
    async with SessionLocal() as session:
        user = await session.get(User, message.from_user.id)
        if not user:
            user = User(user_id=message.from_user.id, chat_id=message.chat.id)
            session.add(user)
            await session.commit()

    await message.answer(WELCOME, parse_mode="HTML", reply_markup=main_menu())


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer("📱 <b>Главное меню</b>", parse_mode="HTML", reply_markup=main_menu())


@router.message(Command("signals"))
async def cmd_signals(message: Message):
    from bot.handlers.callbacks import _handle_signals
    await _handle_signals(message)


@router.message(Command("analyze"))
async def cmd_analyze(message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Укажи тикер: <code>/analyze LKOH</code>", parse_mode="HTML")
        return
    ticker = parts[1].upper()
    from bot.handlers.callbacks import _handle_analyze
    await _handle_analyze(message, ticker)


@router.message(Command("macro"))
async def cmd_macro(message: Message):
    from bot.handlers.callbacks import _handle_macro
    await _handle_macro(message)


@router.message(Command("news"))
async def cmd_news(message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Укажи тикер: <code>/news SBER</code>", parse_mode="HTML")
        return
    ticker = parts[1].upper()
    from bot.handlers.callbacks import _handle_news
    await _handle_news(message, ticker)


@router.message(Command("settings"))
async def cmd_settings(message: Message):
    from bot.handlers.callbacks import _handle_settings
    await _handle_settings(message)


@router.message(Command("setbank"))
async def cmd_setbank(message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "Укажи размер банка: <code>/setbank 500000</code>",
            parse_mode="HTML",
        )
        return
    try:
        amount = float(parts[1].replace(",", "").replace(" ", ""))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Укажи число: <code>/setbank 500000</code>", parse_mode="HTML")
        return

    from db.models import SessionLocal, User
    async with SessionLocal() as session:
        user = await session.get(User, message.from_user.id)
        if not user:
            user = User(user_id=message.from_user.id, chat_id=message.chat.id)
            session.add(user)
        s = user.get_settings()
        s["bank_size"] = amount
        user.set_settings(s)
        await session.commit()

    await message.answer(
        f"✅ Размер банка сохранён: <b>{amount:,.0f} ₽</b>\n"
        f"  Голубые фишки (80%): {amount * 0.8:,.0f} ₽\n"
        f"  Идеи роста (20%): {amount * 0.2:,.0f} ₽",
        parse_mode="HTML",
    )


@router.message(Command("watchlist"))
async def cmd_watchlist(message: Message):
    from db.models import SessionLocal, Watchlist
    from sqlalchemy import select
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy import delete
    from config import ALL_TICKERS

    parts = message.text.split()
    if len(parts) == 1:
        from bot.handlers.callbacks import _handle_watchlist
        await _handle_watchlist(message, message.from_user.id)
        return

    action = parts[1].lower() if len(parts) > 1 else ""
    ticker = parts[2].upper() if len(parts) > 2 else ""

    if action == "add":
        if not ticker:
            await message.answer("Укажи тикер: <code>/watchlist add SBER</code>", parse_mode="HTML")
            return
        if ticker not in ALL_TICKERS:
            from config import BLUE_CHIPS, RISKY_STOCKS
            known = ", ".join(list(BLUE_CHIPS.keys()) + list(RISKY_STOCKS.keys()))
            await message.answer(
                f"❌ Тикер <b>{ticker}</b> не найден.\nДоступные: <code>{known}</code>",
                parse_mode="HTML",
            )
            return
        async with SessionLocal() as session:
            stmt = sqlite_insert(Watchlist).values(
                user_id=message.from_user.id, ticker=ticker
            ).on_conflict_do_nothing()
            await session.execute(stmt)
            await session.commit()
        await message.answer(f"✅ <b>{ticker}</b> добавлен в вотчлист", parse_mode="HTML")

    elif action == "remove":
        if not ticker:
            await message.answer("Укажи тикер: <code>/watchlist remove SBER</code>", parse_mode="HTML")
            return
        async with SessionLocal() as session:
            await session.execute(
                delete(Watchlist).where(
                    Watchlist.user_id == message.from_user.id,
                    Watchlist.ticker == ticker,
                )
            )
            await session.commit()
        await message.answer(f"✅ <b>{ticker}</b> удалён из вотчлиста", parse_mode="HTML")

    else:
        await message.answer(
            "Управление вотчлистом:\n"
            "<code>/watchlist</code> — показать\n"
            "<code>/watchlist add LKOH</code> — добавить\n"
            "<code>/watchlist remove LKOH</code> — удалить",
            parse_mode="HTML",
        )
