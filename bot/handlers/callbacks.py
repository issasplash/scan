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
        ctx = MacroContext(
            brent=cached.brent,
            usd_rub=cached.usd_rub,
            cbr_rate=cached.cbr_rate,
            imoex=cached.imoex,
            imoex_ma50=cached.imoex_ma50,
            market_regime=cached.market_regime or "neutral",
        )
        if cached.cbr_rate:
            if cached.cbr_rate >= 18:
                ctx.signals.append(f"Ставка ЦБ {cached.cbr_rate}% — высокая, давление на акции ⚠️")
            elif cached.cbr_rate <= 10:
                ctx.signals.append(f"Ставка ЦБ {cached.cbr_rate}% — низкая, позитив для акций ✅")
        if cached.imoex and cached.imoex_ma50:
            if ctx.market_regime == "bullish":
                ctx.signals.append(f"IMOEX {cached.imoex:.0f} выше MA50 ({cached.imoex_ma50:.0f}) — рынок растёт ✅")
            elif ctx.market_regime == "bearish":
                ctx.signals.append(f"IMOEX {cached.imoex:.0f} ниже MA50 ({cached.imoex_ma50:.0f}) — рынок падает ⚠️")
        return ctx

    imoex_hist = await get_imoex_history(210)
    return await get_macro_context(imoex_hist)


def _esc(s: str) -> str:
    """Экранирует HTML-спецсимволы в тексте сигналов."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
        f"📊 <b>{_esc(result.name)} ({result.ticker})</b>  •  {p}{ch}",
        "",
        f"<b>{_SIGNAL_LABEL.get(result.signal, result.signal)}</b>",
        f"Уверенность: {_CONF_LABEL.get(result.confidence, result.confidence)}",
        "",
    ]

    if result.filters.warnings:
        for w in result.filters.warnings:
            lines.append(_esc(w))
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
        lines.append(f"  • {_esc(s)}")

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
        lines.append(f"  • {_esc(s)}")

    if result.ai_text:
        ai_safe = result.ai_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        lines += ["", "━━━━ ИИ-АНАЛИЗ ━━━━", ai_safe]

    text = "\n".join(l for l in lines if l is not None)
    # Telegram limit 4096 chars
    if len(text) > 4000:
        text = text[:3990] + "\n…"
    return text


# ─── Обработчики событий ──────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:main")
async def cb_main_menu(cq: CallbackQuery):
    await cq.message.edit_text("📱 <b>Главное меню</b>", parse_mode="HTML", reply_markup=kb.main_menu())
    await cq.answer()


@router.callback_query(F.data == "menu:pick_stock")
async def cb_pick_stock(cq: CallbackQuery):
    await cq.answer()
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
    try:
        await _handle_analyze(cq.message, ticker, edit=True)
    except Exception as e:
        logger.error("analyze %s: %s", ticker, e, exc_info=True)
        try:
            await cq.message.edit_text(
                f"⚠️ Ошибка при анализе <b>{ticker}</b>. Попробуй ещё раз.",
                parse_mode="HTML",
                reply_markup=kb.back_to_menu(),
            )
        except Exception:
            pass


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
        if key in ("alerts", "brief", "rsi_alerts", "news_alerts"):
            s[key] = not s.get(key, True)
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
    """Показывает список акций с кэшированными сигналами из БД. Нет API-запросов."""
    from db.models import SessionLocal, SignalHistory
    from sqlalchemy import select

    signals_map: dict[str, str] = {}
    last_prices: dict[str, float] = {}

    async with SessionLocal() as session:
        for ticker in BLUE_CHIPS:
            row = (await session.execute(
                select(SignalHistory)
                .where(SignalHistory.ticker == ticker)
                .order_by(SignalHistory.created_at.desc())
                .limit(1)
            )).scalar_one_or_none()
            if row:
                signals_map[ticker] = row.signal
                if row.price:
                    last_prices[ticker] = row.price

    lines = ["📊 <b>Голубые фишки</b>"]
    if signals_map:
        lines.append("<i>Кэшированные сигналы. Нажми на акцию для свежего анализа.</i>\n")
    else:
        lines.append("<i>Нет данных. Нажми на любую акцию чтобы запустить анализ.</i>\n")

    for ticker, info in BLUE_CHIPS.items():
        sig = signals_map.get(ticker)
        emoji = _SIGNAL_LABEL.get(sig, "⚪ —").split()[0] if sig else "⚪"
        p = f"  {last_prices[ticker]:,.0f} ₽" if ticker in last_prices else ""
        lines.append(f"{emoji} <b>{ticker}</b> {info['name']}{p}")

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

    # Сохраняем сигнал в историю
    try:
        from db.models import SessionLocal, SignalHistory
        async with SessionLocal() as db:
            db.add(SignalHistory(
                ticker=ticker,
                signal=result.signal,
                confidence=result.confidence,
                score_tech=float(result.tech.score) if result.tech else None,
                score_fund=float(result.fund.score) if result.fund else None,
                price=result.price,
            ))
            await db.commit()
    except Exception:
        pass

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
            title = _esc(item.get("title", "")[:120])
            link = item.get("url", "")
            if link:
                lines.append(f'• <a href="{link}">{title}</a>')
            else:
                lines.append(f"• {title}")
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
    )

    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=markup)


async def _handle_portfolio(target, user_id: int, edit: bool = False):
    from data.tinkoff_client import get_portfolio

    portfolio = await get_portfolio()

    if not portfolio:
        text = (
            "💼 <b>Портфель</b>\n\n"
            "⚠️ Портфель T-Invest недоступен.\n"
            "Проверь TINKOFF_API_TOKEN в .env"
        )
        if edit:
            await target.edit_text(text, parse_mode="HTML", reply_markup=kb.back_to_menu())
        else:
            await target.answer(text, parse_mode="HTML", reply_markup=kb.back_to_menu())
        return

    total = portfolio["total_value"]
    shares = portfolio["total_shares"]
    bonds  = portfolio["total_bonds"]
    cash   = portfolio["total_cash"]
    positions = portfolio["positions"]

    lines = [f"💼 <b>Мой портфель T-Invest</b>\n"]

    # Итоговая сумма
    lines.append(f"💰 <b>Итого: {total:,.0f} ₽</b>")
    if shares > 0:
        lines.append(f"  📈 Акции:     {shares:>12,.0f} ₽  ({shares/total*100:.0f}%)")
    if bonds > 0:
        lines.append(f"  🏦 Облигации: {bonds:>12,.0f} ₽  ({bonds/total*100:.0f}%)")
    if cash > 0:
        lines.append(f"  💵 Кэш:       {cash:>12,.0f} ₽  ({cash/total*100:.0f}%)")

    # Позиции (только акции, первые 15)
    stock_positions = [p for p in positions if p["type"] == "share"]
    if stock_positions:
        lines.append("\n━━━━ ПОЗИЦИИ ━━━━")
        total_invested = sum(p["avg_price"] * p["quantity"] for p in stock_positions if p["avg_price"])

        for p in sorted(stock_positions, key=lambda x: x["current_price"] * x["quantity"], reverse=True)[:15]:
            qty   = p["quantity"]
            cur   = p["current_price"]
            avg   = p["avg_price"]
            value = cur * qty
            pnl   = p["expected_yield"]

            pnl_str = ""
            if pnl != 0 and avg > 0:
                pnl_pct = (cur - avg) / avg * 100
                pnl_sign = "▲" if pnl > 0 else "▼"
                pnl_str = f" {pnl_sign}{abs(pnl_pct):.1f}%"

            figi = p["figi"]
            # Попробуем найти тикер по figi из нашего кэша
            from data.tinkoff_client import _figi_cache
            rev = {v: k for k, v in _figi_cache.items()}
            label = rev.get(figi, figi[:8])

            lines.append(
                f"<code>{label:<8}</code> {qty:.0f} шт  "
                f"<b>{value:,.0f} ₽</b>{pnl_str}"
            )

    # Суммарный P&L
    total_pnl = sum(p["expected_yield"] for p in stock_positions)
    if total_pnl != 0:
        sign = "▲" if total_pnl > 0 else "▼"
        lines.append(f"\n{sign} Незафикс. прибыль: <b>{abs(total_pnl):,.0f} ₽</b>")

    text = "\n".join(lines)
    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=kb.portfolio_menu())
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=kb.portfolio_menu())


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
