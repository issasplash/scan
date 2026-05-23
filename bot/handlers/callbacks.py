from __future__ import annotations
import logging
from datetime import date, timedelta
import pandas as pd
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from bot import keyboards as kb
from config import BLUE_CHIPS, RISKY_STOCKS
from analysis.signals import SignalResult, SIGNAL_BUY, SIGNAL_HOLD, SIGNAL_SELL, SIGNAL_WAIT

router = Router()
logger = logging.getLogger("callbacks")

_SIGNAL_LABEL = {
    SIGNAL_BUY:  "🟢 ПОКУПАТЬ",
    SIGNAL_HOLD: "🟡 ДЕРЖАТЬ",
    SIGNAL_SELL: "🔴 ПРОДАВАТЬ",
    SIGNAL_WAIT: "⏳ ЖДАТЬ",
}
_CONF_LABEL = {
    "HIGH":   "⭐⭐⭐ Высокая",
    "MEDIUM": "⭐⭐ Средняя",
    "LOW":    "⭐ Низкая",
}


# ─── Вспомогательные функции ──────────────────────────────────────────────────

async def _load_candles(ticker: str) -> pd.DataFrame:
    from db.models import SessionLocal, PriceHistory
    from sqlalchemy import select
    async with SessionLocal() as session:
        result = await session.execute(
            select(PriceHistory)
            .where(PriceHistory.ticker == ticker)
            .order_by(PriceHistory.date)
        )
        rows = result.scalars().all()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([{
        "date": r.date, "open": r.open, "high": r.high,
        "low": r.low, "close": r.close, "volume": r.volume,
    } for r in rows])


async def _get_macro():
    from db.models import SessionLocal, MacroData
    from sqlalchemy import select
    from analysis.macro import MacroContext, get_macro_context
    from data.moex_client import get_imoex_history

    # Сначала пробуем из кэша (сегодняшние данные)
    today = date.today()
    async with SessionLocal() as session:
        result = await session.execute(
            select(MacroData).where(MacroData.date == today)
        )
        cached = result.scalar_one_or_none()

    if cached:
        return MacroContext(
            brent=cached.brent,
            usd_rub=cached.usd_rub,
            cbr_rate=cached.cbr_rate,
            imoex=cached.imoex,
            market_regime="neutral",
        )

    imoex_hist = await get_imoex_history(210)
    return await get_macro_context(imoex_hist)


def _format_bar(value: float, max_val: float, width: int = 10) -> str:
    filled = min(int(value / max_val * width), width)
    return "▓" * filled + "░" * (width - filled)


def _format_analysis(result: SignalResult) -> str:
    t = result.tech
    f = result.fund
    p = f"{result.price:,.2f} ₽" if result.price else "н/д"
    ch = ""
    if t.price_change_30d is not None:
        sign = "▲" if t.price_change_30d >= 0 else "▼"
        ch = f" {sign} {abs(t.price_change_30d):.1f}%/30д"

    lines = [
        f"📊 <b>{result.name} ({result.ticker})</b>  •  {p}{ch}",
        "",
        f"<b>{_SIGNAL_LABEL.get(result.signal, result.signal)}</b>",
        f"Уверенность: {_CONF_LABEL.get(result.confidence, result.confidence)}",
        "",
    ]

    if result.filters.warnings:
        for w in result.filters.warnings:
            lines.append(w)
        lines.append("")

    lines += [
        "━━━━ ТЕХНИЧЕСКИЙ АНАЛИЗ ━━━━",
        f"RSI(14): <b>{t.rsi:.0f}</b>" if t.rsi else "RSI: н/д",
    ]
    if t.macd is not None and t.macd_signal is not None:
        macd_dir = "↑" if t.macd > t.macd_signal else "↓"
        lines.append(f"MACD: {t.macd:.1f} {macd_dir} Signal {t.macd_signal:.1f}")
    if t.ma20 and t.ma50 and t.ma200:
        lines.append(f"MA: 20={t.ma20:.0f}  50={t.ma50:.0f}  200={t.ma200:.0f}")
    if t.support and t.resistance:
        lines.append(f"Поддержка: {t.support:.1f}  Сопротивление: {t.resistance:.1f}")
    for s in t.signals[:3]:
        lines.append(f"  • {s}")

    lines += ["", "━━━━ ФУНДАМЕНТАЛ ━━━━"]
    if f.pe:
        lines.append(f"P/E: {f.pe:.1f}")
    if f.div_yield:
        lines.append(f"Дивдоходность: {f.div_yield:.1f}%")
    if f.debt_ebitda:
        lines.append(f"Долг/EBITDA: {f.debt_ebitda:.1f}")
    if f.next_ex_date:
        lines.append(f"📅 Ближайшая отсечка: {f.next_ex_date}")
    elif f.days_since_exdate:
        lines.append(f"Последняя отсечка: {f.days_since_exdate} дн. назад")
    for s in f.signals[:2]:
        lines.append(f"  • {s}")

    if result.ai_text:
        lines += ["", "━━━━ ИИ-АНАЛИЗ ━━━━", result.ai_text]

    return "\n".join(l for l in lines if l is not None)


# ─── Обработчики событий ──────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:main")
async def cb_main_menu(cq: CallbackQuery):
    await cq.message.edit_text("📱 <b>Главное меню</b>", parse_mode="HTML", reply_markup=kb.main_menu())
    await cq.answer()


@router.callback_query(F.data == "menu:pick_stock")
async def cb_pick_stock(cq: CallbackQuery):
    await cq.message.edit_text(
        "🔍 <b>Выбери акцию для анализа:</b>",
        parse_mode="HTML",
        reply_markup=kb.stocks_list(),
    )
    await cq.answer()


@router.callback_query(F.data == "menu:signals")
async def cb_signals(cq: CallbackQuery):
    await cq.answer("⏳ Загружаю сигналы...")
    await _handle_signals(cq.message, edit=True)


@router.callback_query(F.data == "menu:macro")
async def cb_macro(cq: CallbackQuery):
    await cq.answer("⏳ Загружаю макро...")
    await _handle_macro(cq.message, edit=True)


@router.callback_query(F.data == "menu:risky")
async def cb_risky(cq: CallbackQuery):
    await cq.answer("⏳ Ищу идеи...")
    await _handle_risky(cq.message, edit=True)


@router.callback_query(F.data == "menu:ofz")
async def cb_ofz(cq: CallbackQuery):
    await cq.answer("⏳ Загружаю ОФЗ...")
    await _handle_ofz(cq.message, edit=True)


@router.callback_query(F.data == "menu:settings")
async def cb_settings(cq: CallbackQuery):
    await _handle_settings(cq.message, user_id=cq.from_user.id, edit=True)
    await cq.answer()


@router.callback_query(F.data == "menu:watchlist")
async def cb_watchlist(cq: CallbackQuery):
    await _handle_watchlist(cq.message, cq.from_user.id, edit=True)
    await cq.answer()


@router.callback_query(F.data == "menu:portfolio")
async def cb_portfolio(cq: CallbackQuery):
    await cq.answer()
    await _handle_portfolio(cq.message, cq.from_user.id, edit=True)


@router.callback_query(F.data == "menu:news_pick")
async def cb_news_pick(cq: CallbackQuery):
    await cq.message.edit_text(
        "📰 <b>По какой акции смотрим новости?</b>",
        parse_mode="HTML",
        reply_markup=kb.news_pick(),
    )
    await cq.answer()


@router.callback_query(F.data.startswith("analyze:"))
async def cb_analyze(cq: CallbackQuery):
    ticker = cq.data.split(":")[1]
    await cq.answer(f"⏳ Анализирую {ticker}...")
    await _handle_analyze(cq.message, ticker, edit=True)


@router.callback_query(F.data.startswith("news:"))
async def cb_news(cq: CallbackQuery):
    ticker = cq.data.split(":")[1]
    await cq.answer(f"⏳ Загружаю новости по {ticker}...")
    await _handle_news(cq.message, ticker, edit=True)


@router.callback_query(F.data.startswith("setting:"))
async def cb_setting(cq: CallbackQuery):
    key = cq.data.split(":")[1]
    from db.models import SessionLocal, User
    async with SessionLocal() as session:
        user = await session.get(User, cq.from_user.id)
        if not user:
            await cq.answer("Сначала /start")
            return
        s = user.get_settings()
        if key == "bank":
            await cq.answer("Введи сумму банка командой:\n/setbank 500000", show_alert=True)
            return
        # toggle
        mapping = {"alerts": "alerts", "brief": "brief", "rsi_alerts": "rsi_alerts", "news_alerts": "news_alerts"}
        if key in mapping:
            s[mapping[key]] = not s.get(mapping[key], True)
            user.set_settings(s)
            await session.commit()
    await _handle_settings(cq.message, user_id=cq.from_user.id, edit=True)
    await cq.answer()


@router.callback_query(F.data == "wl_add")
async def cb_wl_add(cq: CallbackQuery):
    await cq.answer(
        "Добавить акцию: /watchlist add ТИКЕР\nНапример: /watchlist add SBER",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("alert_add:"))
async def cb_alert_add(cq: CallbackQuery):
    ticker = cq.data.split(":")[1]
    await cq.answer(
        f"✅ {ticker} отслеживается автоматически.\n"
        "Алерты по RSI приходят когда RSI < 30 или > 70.\n"
        "Управление в /settings → Алерты",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("wl_remove:"))
async def cb_wl_remove(cq: CallbackQuery):
    ticker = cq.data.split(":")[1]
    from db.models import SessionLocal, Watchlist
    from sqlalchemy import select, delete
    async with SessionLocal() as session:
        await session.execute(
            delete(Watchlist).where(
                Watchlist.user_id == cq.from_user.id,
                Watchlist.ticker == ticker,
            )
        )
        await session.commit()
    await _handle_watchlist(cq.message, cq.from_user.id, edit=True)
    await cq.answer(f"{ticker} удалён из вотчлиста")


# ─── Логика экранов ───────────────────────────────────────────────────────────

async def _handle_signals(target, edit: bool = False):
    from data.tinkoff_client import get_last_prices
    from config import BLUE_CHIPS

    prices = await get_last_prices(list(BLUE_CHIPS.keys()))
    macro = await _get_macro()

    lines = ["📊 <b>Сигналы по голубым фишкам</b>\n"]
    signals_map: dict[str, str] = {}

    for ticker, info in BLUE_CHIPS.items():
        candles = await _load_candles(ticker)
        if candles.empty:
            lines.append(f"⚪ <b>{ticker}</b> {info['name']} — нет данных")
            continue
        from analysis.signals import generate_signal
        from data.moex_client import get_dividends
        divs = await get_dividends(ticker)
        result = await generate_signal(ticker, candles, {}, divs, macro, prices.get(ticker))
        signals_map[ticker] = result.signal
        from bot.handlers.callbacks import _SIGNAL_LABEL
        label = _SIGNAL_LABEL.get(result.signal, result.signal)
        p = f"{result.price:,.0f} ₽" if result.price else ""
        lines.append(f"{label.split()[0]} <b>{ticker}</b> {info['name']}  {p}")

    text = "\n".join(lines)
    markup = kb.stocks_list(signals_map)

    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=markup)


async def _handle_analyze(target, ticker: str, edit: bool = False):
    from data.tinkoff_client import get_last_prices, get_fundamentals
    from data.moex_client import get_dividends
    from data.news_fetcher import fetch_news_for_ticker
    from analysis.signals import generate_signal
    from ai.analyst import get_ai_analysis

    all_stocks = {**BLUE_CHIPS, **RISKY_STOCKS}
    if ticker not in all_stocks:
        text = f"❌ Тикер <b>{ticker}</b> не найден в списке."
        if edit:
            await target.edit_text(text, parse_mode="HTML", reply_markup=kb.back_to_menu())
        else:
            await target.answer(text, parse_mode="HTML", reply_markup=kb.back_to_menu())
        return

    candles = await _load_candles(ticker)
    prices = await get_last_prices([ticker])
    fundamentals = await get_fundamentals(ticker)
    dividends = await get_dividends(ticker)
    macro = await _get_macro()
    news = await fetch_news_for_ticker(ticker)

    result = await generate_signal(ticker, candles, fundamentals, dividends, macro, prices.get(ticker))
    result.ai_text = await get_ai_analysis(result, macro, news)

    text = _format_analysis(result)
    markup = kb.stock_detail(ticker)

    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=markup)


async def _handle_macro(target, edit: bool = False):
    macro = await _get_macro()
    lines = ["🌍 <b>Макро-контекст</b>\n"]
    if macro.brent:
        lines.append(f"🛢 Нефть Brent: <b>${macro.brent:.1f}</b>")
    if macro.usd_rub:
        lines.append(f"💵 USD/RUB: <b>{macro.usd_rub:.2f}</b>")
    if macro.cbr_rate:
        lines.append(f"🏦 Ставка ЦБ: <b>{macro.cbr_rate}%</b>")
    if macro.imoex:
        regime_emoji = {"bullish": "📈", "bearish": "📉", "neutral": "➡️"}.get(macro.market_regime, "")
        lines.append(f"{regime_emoji} IMOEX: <b>{macro.imoex:.0f}</b>")
    if macro.signals:
        lines.append("")
        for s in macro.signals:
            lines.append(f"  • {s}")
    text = "\n".join(lines)

    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=kb.back_to_menu())
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=kb.back_to_menu())


async def _handle_news(target, ticker: str, edit: bool = False):
    from data.news_fetcher import fetch_news_for_ticker
    news = await fetch_news_for_ticker(ticker, limit=6)
    all_stocks = {**BLUE_CHIPS, **RISKY_STOCKS}
    name = all_stocks.get(ticker, {}).get("name", ticker)

    lines = [f"📰 <b>Новости: {name} ({ticker})</b>\n"]
    if news:
        for item in news:
            title = item["title"][:120]
            pub = item.get("published", "")[:16]
            link = item.get("link", "")
            if link:
                lines.append(f"• <a href='{link}'>{title}</a> <i>{pub}</i>")
            else:
                lines.append(f"• {title} <i>{pub}</i>")
    else:
        lines.append("Свежих новостей не найдено.")

    text = "\n".join(lines)
    markup = kb.news_detail(ticker)

    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)


async def _handle_risky(target, edit: bool = False):
    from data.tinkoff_client import get_last_prices, get_fundamentals
    from data.moex_client import get_dividends
    from analysis.signals import generate_signal

    macro = await _get_macro()
    prices = await get_last_prices(list(RISKY_STOCKS.keys()))
    lines = ["⚡ <b>Нестандартные идеи (15-20% капитала)</b>\n"]

    for ticker, info in RISKY_STOCKS.items():
        candles = await _load_candles(ticker)
        if candles.empty:
            lines.append(f"⚪ {ticker} {info['name']} — нет данных")
            continue
        fundamentals = await get_fundamentals(ticker)
        dividends = await get_dividends(ticker)
        result = await generate_signal(ticker, candles, fundamentals, dividends, macro, prices.get(ticker))
        label = _SIGNAL_LABEL.get(result.signal, result.signal)
        p = f"{result.price:,.0f} ₽" if result.price else ""
        lines.append(f"{label.split()[0]} <b>{ticker}</b> {info['name']}  {p}")
        if result.tech.rsi:
            lines.append(f"   RSI: {result.tech.rsi:.0f}, Score: {result.total_score:+d}")

    lines += ["", "⚠️ <i>Высокий риск — не более 15-20% портфеля на весь блок</i>"]
    text = "\n".join(lines)

    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=kb.back_to_menu())
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=kb.back_to_menu())


async def _handle_ofz(target, edit: bool = False):
    from data.moex_client import get_ofz_list
    macro = await _get_macro()
    ofz_list = await get_ofz_list()

    lines = ["🏦 <b>ОФЗ — государственные облигации</b>\n"]

    if macro.cbr_rate:
        if macro.cbr_rate >= 16:
            lines.append(f"📌 Ставка ЦБ {macro.cbr_rate}% — <b>рекомендую короткие ОФЗ</b> (1-2 года)")
            lines.append("При снижении ставки лучше переходить в длинные.\n")
        elif macro.cbr_rate <= 10:
            lines.append(f"📌 Ставка ЦБ {macro.cbr_rate}% — <b>рекомендую длинные ОФЗ</b> (5-10 лет)")
            lines.append("Тело облигации вырастет при снижении ставки.\n")

    if not ofz_list:
        lines.append("Данные по ОФЗ временно недоступны.")
    else:
        lines.append(f"{'Тикер':<14} {'Доходность':>10}  {'Дата погаш.':>12}")
        lines.append("─" * 40)
        for ofz in ofz_list[:10]:
            matdate = str(ofz["matdate"])[:10] if ofz["matdate"] else "н/д"
            lines.append(f"<code>{ofz['ticker']:<14} {ofz['yield']:>9.2f}%  {matdate:>12}</code>")

    text = "\n".join(lines)

    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=kb.back_to_menu())
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=kb.back_to_menu())


async def _handle_settings(target, user_id: int | None = None, edit: bool = False):
    from db.models import SessionLocal, User
    uid = user_id or (target.chat.id if hasattr(target, "chat") else 0)
    async with SessionLocal() as session:
        user = await session.get(User, uid)
        if not user:
            user = User(user_id=uid, chat_id=uid)
            session.add(user)
            await session.commit()

    text = "⚙️ <b>Настройки</b>\nНажми кнопку чтобы переключить:"
    markup = kb.settings(
        alerts=user.alerts_enabled,
        brief=user.brief_enabled,
        rsi_alerts=user.rsi_alerts,
        news_alerts=user.news_alerts,
        bank_size=user.bank_size,
    )

    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=markup)


async def _handle_portfolio(target, user_id: int, edit: bool = False):
    from db.models import SessionLocal, User, Watchlist
    from sqlalchemy import select
    from config import SECTOR_LABELS

    async with SessionLocal() as session:
        user = await session.get(User, user_id)
        wl_result = await session.execute(
            select(Watchlist).where(Watchlist.user_id == user_id)
        )
        wl_tickers = [r.ticker for r in wl_result.scalars().all()]

    all_stocks = {**BLUE_CHIPS, **RISKY_STOCKS}
    bank = user.bank_size if user else None

    lines = ["💼 <b>Портфель</b>\n"]

    if bank:
        lines.append(f"💰 Размер банка: <b>{bank:,.0f} ₽</b>")
        lines.append(f"   Голубые фишки (80%): {bank * 0.8:,.0f} ₽")
        lines.append(f"   Идеи роста (20%): {bank * 0.2:,.0f} ₽\n")
    else:
        lines.append("💰 Размер банка не задан → /setbank 500000\n")

    if wl_tickers:
        lines.append("📋 <b>Мой вотчлист:</b>")
        sector_groups: dict[str, list[str]] = {}
        for t in wl_tickers:
            sec = all_stocks.get(t, {}).get("sector", "other")
            sector_groups.setdefault(sec, []).append(t)

        for sec, tickers in sector_groups.items():
            label = SECTOR_LABELS.get(sec, sec)
            lines.append(f"{label}: {', '.join(tickers)}")

        if len(sector_groups) == 1:
            lines.append("\n⚠️ <i>Весь вотчлист в одном секторе — риск концентрации</i>")
    else:
        lines.append("📋 Вотчлист пуст → добавь акции через /watchlist add TICKER")

    lines += [
        "",
        "━━━━ РАСПРЕДЕЛЕНИЕ ━━━━",
    ]
    for sec, info in SECTOR_LABELS.items():
        lines.append(f"{info}")

    lines.append("\n<i>Рекомендуется: не более 40% в одном секторе</i>")

    text = "\n".join(lines)
    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=kb.back_to_menu())
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=kb.back_to_menu())


async def _handle_watchlist(target, user_id: int, edit: bool = False):
    from db.models import SessionLocal, Watchlist
    from sqlalchemy import select
    async with SessionLocal() as session:
        result = await session.execute(
            select(Watchlist).where(Watchlist.user_id == user_id)
        )
        rows = result.scalars().all()

    tickers = [r.ticker for r in rows]
    text = "📋 <b>Мой вотчлист</b>" + (f"\n{len(tickers)} акций" if tickers else "\nПусто — добавь акции")
    markup = kb.watchlist_manage(tickers)

    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=markup)
