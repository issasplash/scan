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
