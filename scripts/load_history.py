"""
Скрипт первичной загрузки всей истории цен с MOEX ISS.
Запускать один раз перед первым запуском бота:

    python scripts/load_history.py

Загружает дневные свечи с начала торгов по каждой акции.
При повторном запуске пропускает уже загруженные даты (ON CONFLICT DO NOTHING).
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from datetime import date

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("load_history")

FROM_DATE = date(2010, 1, 1)  # MOEX данные доступны с ~2010+ для большинства акций


async def main():
    from db.models import init_db, SessionLocal, PriceHistory
    from data.moex_client import get_candles
    from config import BLUE_CHIPS, RISKY_STOCKS
    from sqlalchemy.dialects.sqlite import insert

    await init_db()
    logger.info("База данных инициализирована")

    all_tickers = list(BLUE_CHIPS.keys()) + list(RISKY_STOCKS.keys())
    today = date.today()

    for i, ticker in enumerate(all_tickers, 1):
        logger.info("[%d/%d] Загрузка %s...", i, len(all_tickers), ticker)
        try:
            df = await get_candles(ticker, FROM_DATE, today)
            if df.empty:
                logger.warning("%s — данные не получены", ticker)
                continue

            async with SessionLocal() as session:
                inserted = 0
                for _, row in df.iterrows():
                    stmt = insert(PriceHistory).values(
                        ticker=ticker,
                        date=row["date"],
                        open=row.get("open"),
                        high=row.get("high"),
                        low=row.get("low"),
                        close=row["close"],
                        volume=row.get("volume"),
                    ).on_conflict_do_nothing()
                    result = await session.execute(stmt)
                    inserted += result.rowcount
                await session.commit()

            logger.info("%s — загружено %d свечей (%d новых)", ticker, len(df), inserted)
        except Exception as e:
            logger.error("%s — ошибка: %s", ticker, e)

        await asyncio.sleep(0.5)  # уважаем MOEX ISS

    logger.info("✅ Загрузка истории завершена")


if __name__ == "__main__":
    asyncio.run(main())
