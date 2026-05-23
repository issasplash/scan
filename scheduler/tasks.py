from __future__ import annotations
import asyncio
import logging
from datetime import date, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from config import MORNING_BRIEF_TIME, BLUE_CHIPS, RISKY_STOCKS, ALERT_RSI_OVERSOLD, ALERT_RSI_OVERBOUGHT, ALERT_VOLUME_SPIKE
from aiogram import Bot

logger = logging.getLogger("scheduler")


async def _send_to_all_users(bot: Bot, text: str, check_setting: str = "alerts"):
    from db.models import SessionLocal, User
    from sqlalchemy import select
    async with SessionLocal() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()

    for user in users:
        s = user.get_settings()
        if check_setting and not s.get(check_setting, True):
            continue
        try:
            await bot.send_message(user.chat_id, text, parse_mode="HTML")
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.debug("Send to %s: %s", user.chat_id, e)


async def task_morning_brief(bot: Bot):
    """Утренний брифинг — топ сигналов и макро."""
    logger.info("Morning brief started")
    from analysis.macro import get_macro_context
    from data.moex_client import get_imoex_history, get_dividends
    from data.tinkoff_client import get_last_prices
    from analysis.signals import generate_signal, SIGNAL_BUY

    imoex_hist = await get_imoex_history(210)
    macro = await get_macro_context(imoex_hist)
    prices = await get_last_prices(list(BLUE_CHIPS.keys()))

    buy_signals = []
    for ticker in BLUE_CHIPS:
        try:
            from bot.handlers.callbacks import _load_candles
            candles = await _load_candles(ticker)
            if candles.empty:
                continue
            divs = await get_dividends(ticker)
            result = await generate_signal(ticker, candles, {}, divs, macro, prices.get(ticker))
            if result.signal == SIGNAL_BUY and result.confidence in ("HIGH", "MEDIUM"):
                buy_signals.append(result)
        except Exception as e:
            logger.debug("Brief %s: %s", ticker, e)

    lines = ["☀️ <b>Утренний брифинг</b>\n"]

    if macro.brent:
        lines.append(f"🛢 Brent ${macro.brent:.1f}  💵 USD/RUB {macro.usd_rub:.1f}" if macro.usd_rub else f"🛢 Brent ${macro.brent:.1f}")
    if macro.cbr_rate:
        lines.append(f"🏦 Ставка ЦБ {macro.cbr_rate}%  📈 IMOEX {macro.imoex:.0f}" if macro.imoex else f"🏦 Ставка ЦБ {macro.cbr_rate}%")

    if buy_signals:
        lines.append(f"\n🟢 <b>Интересные идеи дня:</b>")
        for r in buy_signals[:3]:
            p = f"{r.price:,.0f} ₽" if r.price else ""
            lines.append(f"  • <b>{r.ticker}</b> {r.name}  {p}")
    else:
        lines.append("\n🟡 Явных покупок не найдено — рынок требует внимания")

    lines.append("\n/signals — все сигналы  /macro — детали")

    await _send_to_all_users(bot, "\n".join(lines), check_setting="brief")
    logger.info("Morning brief sent to %d users", 1)


async def task_check_alerts(bot: Bot):
    """Проверяет RSI-алерты и объёмные аномалии."""
    from data.moex_client import get_dividends
    from data.tinkoff_client import get_last_prices
    from analysis.macro import get_macro_context
    from analysis.signals import generate_signal
    from analysis.technical import calc_rsi
    from bot.handlers.callbacks import _load_candles
    import pandas as pd

    prices = await get_last_prices(list(BLUE_CHIPS.keys()))
    macro = await get_macro_context()

    for ticker in BLUE_CHIPS:
        try:
            candles = await _load_candles(ticker)
            if candles.empty or len(candles) < 20:
                continue

            closes = candles["close"].astype(float)
            rsi = calc_rsi(closes)

            name = BLUE_CHIPS[ticker]["name"]
            price = prices.get(ticker)
            price_str = f"{price:,.0f} ₽" if price else ""

            alert_text = None

            if rsi is not None and rsi <= ALERT_RSI_OVERSOLD:
                alert_text = (
                    f"🟢 <b>Сигнал: перепродано</b>\n"
                    f"{name} ({ticker}) {price_str}\n"
                    f"RSI = <b>{rsi:.0f}</b> ≤ {ALERT_RSI_OVERSOLD} — возможная точка входа\n"
                    f"/analyze {ticker}"
                )
            elif rsi is not None and rsi >= ALERT_RSI_OVERBOUGHT:
                alert_text = (
                    f"🔴 <b>Сигнал: перекуплено</b>\n"
                    f"{name} ({ticker}) {price_str}\n"
                    f"RSI = <b>{rsi:.0f}</b> ≥ {ALERT_RSI_OVERBOUGHT} — возможна коррекция\n"
                    f"/analyze {ticker}"
                )

            # Объёмная аномалия
            if "volume" in candles.columns:
                vols = candles["volume"].astype(float)
                vol_avg = float(vols.tail(20).mean())
                vol_last = float(vols.iloc[-1])
                if vol_avg > 0 and vol_last > vol_avg * ALERT_VOLUME_SPIKE:
                    vol_text = (
                        f"⚡ <b>Необычный объём</b>\n"
                        f"{name} ({ticker}) {price_str}\n"
                        f"Объём в {vol_last/vol_avg:.1f}x от среднего\n"
                        f"/news {ticker}"
                    )
                    await _send_to_all_users(bot, vol_text, check_setting="alerts")

            if alert_text:
                await _send_to_all_users(bot, alert_text, check_setting="rsi_alerts")

        except Exception as e:
            logger.debug("Alert %s: %s", ticker, e)


async def task_update_history(bot: Bot):
    """Дозаписывает вчерашние свечи в БД."""
    from data.moex_client import get_candles
    from db.models import SessionLocal, PriceHistory
    from sqlalchemy.dialects.sqlite import insert

    yesterday = date.today() - timedelta(days=1)

    for ticker in list(BLUE_CHIPS.keys()) + list(RISKY_STOCKS.keys()):
        try:
            df = await get_candles(ticker, yesterday, date.today())
            if df.empty:
                continue
            async with SessionLocal() as session:
                for _, row in df.iterrows():
                    stmt = insert(PriceHistory).values(
                        ticker=ticker,
                        date=row["date"],
                        open=row["open"], high=row["high"],
                        low=row["low"], close=row["close"],
                        volume=row.get("volume"),
                    ).on_conflict_do_nothing()
                    await session.execute(stmt)
                await session.commit()
            logger.debug("History updated: %s", ticker)
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.warning("History update %s: %s", ticker, e)


async def task_cache_macro():
    """Кэширует макро-данные в БД раз в день."""
    from data.moex_client import get_imoex_history
    from analysis.macro import get_macro_context
    from db.models import SessionLocal, MacroData
    from sqlalchemy.dialects.sqlite import insert

    imoex_hist = await get_imoex_history(210)
    macro = await get_macro_context(imoex_hist)
    today = date.today()

    async with SessionLocal() as session:
        stmt = insert(MacroData).values(
            date=today,
            brent=macro.brent,
            usd_rub=macro.usd_rub,
            cbr_rate=macro.cbr_rate,
            imoex=macro.imoex,
        ).on_conflict_do_nothing()
        await session.execute(stmt)
        await session.commit()


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    h, m = MORNING_BRIEF_TIME.split(":")
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

    # Утренний брифинг
    scheduler.add_job(
        task_morning_brief, CronTrigger(hour=int(h), minute=int(m)),
        args=[bot], id="morning_brief", replace_existing=True,
    )
    # Проверка алертов каждые 30 минут (в торговые часы)
    scheduler.add_job(
        task_check_alerts, CronTrigger(minute="*/30", hour="9-18", day_of_week="mon-fri"),
        args=[bot], id="check_alerts", replace_existing=True,
    )
    # Обновление истории в 19:00 по будням
    scheduler.add_job(
        task_update_history, CronTrigger(hour=19, minute=0, day_of_week="mon-fri"),
        args=[bot], id="update_history", replace_existing=True,
    )
    # Кэш макро в 9:00
    scheduler.add_job(
        task_cache_macro, CronTrigger(hour=9, minute=5, day_of_week="mon-fri"),
        id="cache_macro", replace_existing=True,
    )

    return scheduler
