from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
import aiohttp
import feedparser
from config import BLUE_CHIPS, RISKY_STOCKS

logger = logging.getLogger("news")

# Google News RSS — работает с любого IP в мире
_GNEWS = "https://news.google.com/rss/search"

# Поисковые запросы для каждого тикера
_QUERIES: dict[str, str] = {
    "LKOH":  "Лукойл LKOH акции",
    "SBER":  "Сбербанк Сбер акции",
    "TATN":  "Татнефть TATN акции",
    "GAZP":  "Газпром GAZP акции",
    "NVTK":  "Новатэк NVTK акции",
    "ROSN":  "Роснефть ROSN акции",
    "GMKN":  "Норникель GMKN акции",
    "CHMF":  "Северсталь CHMF акции",
    "NLMK":  "НЛМК NLMK акции",
    "MAGN":  "ММК MAGN акции",
    "PLZL":  "Полюс PLZL акции",
    "ALRS":  "Алроса ALRS акции",
    "VTBR":  "ВТБ банк акции",
    "MGNT":  "Магнит MGNT акции",
    "FIVE":  "X5 RetailGroup акции",
    "YNDX":  "Яндекс YNDX акции",
    "MTSS":  "МТС MTSS акции",
    "PHOR":  "ФосАгро PHOR акции",
    "POSI":  "Позитив Технологии POSI акции",
    "HEAD":  "HeadHunter HEAD акции",
    "WUSH":  "Whoosh WUSH самокаты акции",
    "OZON":  "OZON маркетплейс акции",
    "ASTR":  "Астра ASTR акции",
}


def _gnews_url(ticker: str) -> str:
    q = _QUERIES.get(ticker, f"{ticker} акции Московская биржа")
    return f"{_GNEWS}?q={quote(q)}&hl=ru&gl=RU&ceid=RU:ru"


def _parse_published(entry) -> datetime | None:
    try:
        t = entry.get("published_parsed")
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    except Exception:
        pass
    return None


async def _fetch_gnews(session: aiohttp.ClientSession, ticker: str) -> list[dict]:
    url = _gnews_url(ticker)
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": "Mozilla/5.0"},
        ) as r:
            content = await r.read()
        feed = feedparser.parse(content)
        items = []
        for entry in feed.entries[:10]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "")
            pub = _parse_published(entry)
            if title:
                items.append({
                    "ticker": ticker,
                    "title": title,
                    "url": link,
                    "published_at": pub,
                })
        return items
    except Exception as e:
        logger.debug("GNews %s error: %s", ticker, e)
        return []


async def refresh_news_for_ticker(ticker: str) -> list[dict]:
    """Загружает свежие новости из Google News и сохраняет в БД."""
    from db.models import SessionLocal, NewsCache
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    async with aiohttp.ClientSession() as session:
        items = await _fetch_gnews(session, ticker)

    if items:
        async with SessionLocal() as db:
            for item in items:
                stmt = sqlite_insert(NewsCache).values(
                    ticker=item["ticker"],
                    title=item["title"],
                    url=item["url"] or "",
                    published_at=item["published_at"],
                    fetched_at=datetime.now(timezone.utc),
                ).on_conflict_do_nothing()
                await db.execute(stmt)
            await db.commit()
        logger.debug("News cached for %s: %d items", ticker, len(items))

    return items


async def refresh_all_news():
    """Обновляет новости для всех акций (вызывается планировщиком)."""
    all_tickers = list({**BLUE_CHIPS, **RISKY_STOCKS}.keys())
    async with aiohttp.ClientSession() as session:
        for ticker in all_tickers:
            items = await _fetch_gnews(session, ticker)
            if items:
                from db.models import SessionLocal, NewsCache
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                async with SessionLocal() as db:
                    for item in items:
                        stmt = sqlite_insert(NewsCache).values(
                            ticker=item["ticker"],
                            title=item["title"],
                            url=item["url"] or "",
                            published_at=item["published_at"],
                            fetched_at=datetime.now(timezone.utc),
                        ).on_conflict_do_nothing()
                        await db.execute(stmt)
                    await db.commit()
            await asyncio.sleep(0.5)
    logger.info("News refresh complete for %d tickers", len(all_tickers))


async def fetch_news_for_ticker(ticker: str, limit: int = 5) -> list[dict]:
    """
    Возвращает актуальные новости для тикера.
    Сначала смотрит в кэш БД (если свежее 2 часов), иначе загружает из сети.
    """
    from db.models import SessionLocal, NewsCache
    from sqlalchemy import select, delete

    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)

    async with SessionLocal() as db:
        result = await db.execute(
            select(NewsCache)
            .where(NewsCache.ticker == ticker)
            .where(NewsCache.fetched_at >= cutoff.replace(tzinfo=None))
            .order_by(NewsCache.fetched_at.desc())
            .limit(limit)
        )
        cached = result.scalars().all()

    if cached:
        return [{"title": n.title, "url": n.url} for n in cached]

    # Кэш устарел или пуст — загружаем
    fresh = await refresh_news_for_ticker(ticker)
    return [{"title": i["title"], "url": i["url"]} for i in fresh[:limit]]
