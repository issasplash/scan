"""
Фоновый конвейер данных.
Все внешние API вызовы только здесь.
Пользователь получает данные из БД — всегда быстро.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta
from config import BLUE_CHIPS, RISKY_STOCKS

logger = logging.getLogger("sync")

ALL_TICKERS = list(BLUE_CHIPS.keys()) + list(RISKY_STOCKS.keys())


async def sync_prices() -> dict[str, float]:
    """Обновляет цены всех акций в БД одним батч-запросом к T-Invest."""
    from data.tinkoff_client import get_last_prices
    from db.models import SessionLocal, PricesCache
    from sqlalchemy.dialects.sqlite import insert

    try:
        prices = await get_last_prices(ALL_TICKERS)
    except Exception as e:
        logger.error("sync_prices: %s", e)
        return {}

    if not prices:
        logger.warning("sync_prices: T-Invest вернул пустой список")
        return {}

    now = datetime.utcnow()
    async with SessionLocal() as db:
        for ticker, price in prices.items():
            stmt = insert(PricesCache).values(
                ticker=ticker, price=price, updated_at=now,
            ).on_conflict_do_update(
                index_elements=["ticker"],
                set_={"price": price, "updated_at": now},
            )
            await db.execute(stmt)
        await db.commit()

    logger.info("sync_prices: обновлено %d цен", len(prices))
    return prices


async def sync_fundamentals():
    """Загружает фундаментальные данные для всех акций из T-Invest."""
    from data.tinkoff_client import get_fundamentals
    from db.models import SessionLocal, FundamentalsCache
    from sqlalchemy.dialects.sqlite import insert

    now = datetime.utcnow()
    synced = 0
    for ticker in ALL_TICKERS:
        try:
            fund = await get_fundamentals(ticker)
            if not fund:
                await asyncio.sleep(0.3)
                continue
            async with SessionLocal() as db:
                stmt = insert(FundamentalsCache).values(
                    ticker=ticker,
                    pe=fund.get("pe"),
                    pb=fund.get("pb"),
                    ev_ebitda=fund.get("ev_ebitda"),
                    div_yield=fund.get("div_yield"),
                    debt_ebitda=fund.get("debt_ebitda"),
                    revenue_growth=fund.get("revenue_growth"),
                    net_margin=fund.get("net_margin"),
                    updated_at=now,
                ).on_conflict_do_update(
                    index_elements=["ticker"],
                    set_={
                        "pe": fund.get("pe"),
                        "pb": fund.get("pb"),
                        "ev_ebitda": fund.get("ev_ebitda"),
                        "div_yield": fund.get("div_yield"),
                        "debt_ebitda": fund.get("debt_ebitda"),
                        "revenue_growth": fund.get("revenue_growth"),
                        "net_margin": fund.get("net_margin"),
                        "updated_at": now,
                    },
                )
                await db.execute(stmt)
                await db.commit()
            synced += 1
        except Exception as e:
            logger.warning("sync_fundamentals %s: %s", ticker, e)
        await asyncio.sleep(0.5)

    logger.info("sync_fundamentals: обновлено %d из %d", synced, len(ALL_TICKERS))


async def sync_dividends():
    """Загружает дивиденды и сохраняет в таблицу dividends."""
    from data.moex_client import get_dividends
    from db.models import SessionLocal, Dividend
    from sqlalchemy.dialects.sqlite import insert
    from analysis.fundamental import _parse_date

    saved = 0
    for ticker in ALL_TICKERS:
        try:
            divs = await get_dividends(ticker)
            if not divs:
                await asyncio.sleep(0.2)
                continue
            async with SessionLocal() as db:
                for d in divs:
                    ex = _parse_date(d["ex_date"])
                    if not ex:
                        continue
                    stmt = insert(Dividend).values(
                        ticker=ticker,
                        ex_date=ex,
                        amount=d["amount"],
                        currency=d.get("currency", "RUB"),
                    ).on_conflict_do_nothing()
                    await db.execute(stmt)
                await db.commit()
            saved += 1
        except Exception as e:
            logger.warning("sync_dividends %s: %s", ticker, e)
        await asyncio.sleep(0.3)

    logger.info("sync_dividends: обработано %d тикеров", saved)


async def sync_macro():
    """Обновляет макро-данные (нефть, рубль, ставка, IMOEX)."""
    from data.moex_client import get_imoex_history
    from analysis.macro import get_macro_context
    from db.models import SessionLocal, MacroData
    from sqlalchemy.dialects.sqlite import insert
    from datetime import date

    try:
        imoex_hist = await get_imoex_history(210)
        macro = await get_macro_context(imoex_hist)
        today = date.today()

        async with SessionLocal() as db:
            stmt = insert(MacroData).values(
                date=today,
                brent=macro.brent,
                usd_rub=macro.usd_rub,
                cbr_rate=macro.cbr_rate,
                imoex=macro.imoex,
                imoex_ma50=macro.imoex_ma50,
                market_regime=macro.market_regime,
            ).on_conflict_do_update(
                index_elements=["date"],
                set_={
                    "brent": macro.brent,
                    "usd_rub": macro.usd_rub,
                    "cbr_rate": macro.cbr_rate,
                    "imoex": macro.imoex,
                    "imoex_ma50": macro.imoex_ma50,
                    "market_regime": macro.market_regime,
                },
            )
            await db.execute(stmt)
            await db.commit()

        logger.info(
            "sync_macro: Brent=%.1f USD/RUB=%.1f ЦБ=%.1f%% IMOEX=%.0f [%s]",
            macro.brent or 0, macro.usd_rub or 0,
            macro.cbr_rate or 0, macro.imoex or 0, macro.market_regime,
        )
    except Exception as e:
        logger.error("sync_macro: %s", e)


async def sync_signals():
    """
    Запускает полный анализ для всех акций используя только кэш из БД.
    Результат сохраняется в signal_history.
    """
    from analysis.signals import generate_signal
    from db.models import SessionLocal, SignalHistory, PricesCache, FundamentalsCache, Dividend, MacroData
    from sqlalchemy import select
    from datetime import date
    from analysis.macro import MacroContext

    # Загружаем макро из кэша
    today = date.today()
    async with SessionLocal() as db:
        macro_row = (await db.execute(
            select(MacroData).where(MacroData.date == today)
        )).scalar_one_or_none()

        prices_rows = (await db.execute(select(PricesCache))).scalars().all()
        fund_rows   = (await db.execute(select(FundamentalsCache))).scalars().all()

    macro = MacroContext()
    if macro_row:
        macro = MacroContext(
            brent=macro_row.brent,
            usd_rub=macro_row.usd_rub,
            cbr_rate=macro_row.cbr_rate,
            imoex=macro_row.imoex,
            imoex_ma50=macro_row.imoex_ma50,
            market_regime=macro_row.market_regime or "neutral",
        )

    prices_cache = {r.ticker: r.price for r in prices_rows}
    fund_cache = {r.ticker: {
        "pe": r.pe, "pb": r.pb, "ev_ebitda": r.ev_ebitda,
        "div_yield": r.div_yield, "debt_ebitda": r.debt_ebitda,
        "revenue_growth": r.revenue_growth, "net_margin": r.net_margin,
    } for r in fund_rows}

    synced = 0
    for ticker in ALL_TICKERS:
        try:
            # Свечи из БД
            from bot.handlers.callbacks import _load_candles
            candles = await _load_candles(ticker)
            if candles.empty:
                continue

            # Дивиденды из БД
            async with SessionLocal() as db:
                div_rows = (await db.execute(
                    select(Dividend)
                    .where(Dividend.ticker == ticker)
                    .order_by(Dividend.ex_date.desc())
                    .limit(10)
                )).scalars().all()
            dividends = [
                {"ex_date": str(r.ex_date), "amount": r.amount, "currency": r.currency}
                for r in div_rows
            ]

            result = await generate_signal(
                ticker, candles,
                fund_cache.get(ticker, {}),
                dividends, macro,
                prices_cache.get(ticker),
            )

            async with SessionLocal() as db:
                db.add(SignalHistory(
                    ticker=ticker,
                    signal=result.signal,
                    confidence=result.confidence,
                    score_tech=float(result.tech.score),
                    score_fund=float(result.fund.score),
                    price=result.price,
                ))
                await db.commit()
            synced += 1
        except Exception as e:
            logger.warning("sync_signals %s: %s", ticker, e)

    logger.info("sync_signals: обновлено %d сигналов", synced)


async def initial_sync():
    """
    Запускается при старте бота.
    Проверяет актуальность кэша и синхронизирует то, что устарело.
    """
    from db.models import SessionLocal, PricesCache, FundamentalsCache, SignalHistory, MacroData
    from sqlalchemy import select, func
    from datetime import date

    logger.info("Проверяем кэш данных...")

    now = datetime.utcnow()
    today = date.today()

    async with SessionLocal() as db:
        latest_price  = (await db.execute(select(func.max(PricesCache.updated_at)))).scalar()
        latest_fund   = (await db.execute(select(func.max(FundamentalsCache.updated_at)))).scalar()
        latest_signal = (await db.execute(select(func.max(SignalHistory.created_at)))).scalar()
        macro_today   = (await db.execute(
            select(MacroData).where(MacroData.date == today)
        )).scalar_one_or_none()

    tasks = []

    # Цены устарели (>15 мин) или отсутствуют
    if not latest_price or (now - latest_price).total_seconds() > 900:
        logger.info("Цены устарели — запускаем sync_prices")
        tasks.append(asyncio.create_task(sync_prices()))

    # Фундаментал устарел (>6ч) или отсутствует
    if not latest_fund or (now - latest_fund).total_seconds() > 21600:
        logger.info("Фундаментал устарел — запускаем sync_fundamentals")
        tasks.append(asyncio.create_task(sync_fundamentals()))

    # Макро отсутствует для сегодня
    if not macro_today:
        logger.info("Макро нет — запускаем sync_macro")
        tasks.append(asyncio.create_task(sync_macro()))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    # Дивиденды (не часто меняются)
    asyncio.create_task(sync_dividends())

    # Сигналы устарели (>4ч) или отсутствуют
    if not latest_signal or (now - latest_signal).total_seconds() > 14400:
        logger.info("Сигналы устарели — запускаем sync_signals")
        asyncio.create_task(sync_signals())

    # Новости
    from data.news_fetcher import refresh_all_news
    asyncio.create_task(refresh_all_news())

    logger.info("Фоновые задачи запущены")
