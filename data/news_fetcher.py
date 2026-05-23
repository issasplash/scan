from __future__ import annotations
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import quote
import aiohttp
import feedparser
from config import BLUE_CHIPS, RISKY_STOCKS

_executor = ThreadPoolExecutor(max_workers=2)

logger = logging.getLogger("news")

_GNEWS = "https://news.google.com/rss/search"

_QUERIES: dict[str, str] = {
    "LKOH":  "Лукойл дивиденды отчёт прибыль санкции",
    "SBER":  "Сбербанк дивиденды отчёт прибыль результаты",
    "TATN":  "Татнефть дивиденды отчёт прибыль",
    "GAZP":  "Газпром дивиденды отчёт экспорт газ",
    "NVTK":  "Новатэк СПГ дивиденды санкции отчёт",
    "ROSN":  "Роснефть дивиденды отчёт нефть добыча",
    "GMKN":  "Норникель дивиденды отчёт никель палладий",
    "CHMF":  "Северсталь дивиденды отчёт сталь производство",
    "NLMK":  "НЛМК дивиденды отчёт сталь прокат",
    "MAGN":  "ММК дивиденды отчёт сталь Магнитогорск",
    "PLZL":  "Полюс золото дивиденды отчёт добыча",
    "ALRS":  "Алроса алмазы дивиденды отчёт санкции",
    "VTBR":  "ВТБ банк дивиденды отчёт прибыль капитал",
    "MGNT":  "Магнит дивиденды отчёт выручка сеть",
    "FIVE":  "X5 RetailGroup Пятёрочка дивиденды отчёт выручка",
    "YNDX":  "Яндекс отчёт выручка прибыль сделка",
    "MTSS":  "МТС дивиденды отчёт выручка абоненты",
    "PHOR":  "ФосАгро дивиденды отчёт удобрения прибыль",
    "POSI":  "Позитив технологии отчёт выручка кибербезопасность",
    "HEAD":  "HeadHunter отчёт выручка вакансии прибыль",
    "WUSH":  "Whoosh самокаты отчёт выручка IPO",
    "OZON":  "Озон OZON отчёт выручка GMV прибыль",
    "ASTR":  "Астра ОС отчёт выручка импортозамещение",
}

_BORING_PATTERNS = [
    "торгуются у уровня",
    "торгуется у уровня",
    "технический анализ на бкс",
    "акции пао",
    "рынок мосбиржа",
    "цена акций сегодня",
    "котировки акций",
    "курс акций",
]


def _gnews_url(ticker: str) -> str:
    q = _QUERIES.get(ticker, f"{ticker} акции Московская биржа")
    return f"{_GNEWS}?q={quote(q)}&hl=ru&gl=RU&ceid=RU:ru"


def _parse_published(entry) -> datetime | None:
    try:
        t = entry.get("published_parsed")
        if t:
            return datetime(*t[:6])  # naive UTC datetime для хранения в SQLite
    except Exception:
        pass
    return None


def _is_interesting(title: str) -> bool:
    low = title.lower()
    return not any(p in low for p in _BORING_PATTERNS)


async def _fetch_gnews(session: aiohttp.ClientSession, ticker: str) -> list[dict]:
    url = _gnews_url(ticker)
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": "Mozilla/5.0"},
        ) as r:
            content = await r.read()
        # feedparser is synchronous — run in executor to avoid blocking event loop
        loop = asyncio.get_event_loop()
        feed = await loop.run_in_executor(_executor, feedparser.parse, content)
        items = []
        for entry in feed.entries[:20]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "")
            pub = _parse_published(entry)
            if title and _is_interesting(title):
                source = ""
                if " - " in title:
                    parts = title.rsplit(" - ", 1)
                    title = parts[0].strip()
                    source = parts[1].strip()
                items.append({
                    "ticker": ticker,
                    "title": title,
                    "source": source,
                    "url": link,
                    "published_at": pub,
                })
        return items
    except Exception as e:
        logger.debug("GNews %s error: %s", ticker, e)
        return []


def _now_naive() -> datetime:
    """Текущее UTC время без timezone (для хранения в SQLite)."""
    return datetime.utcnow()


async def _save_news_to_db(items: list[dict]) -> None:
    """Сохраняет новости в БД, обновляет fetched_at при дублях."""
    if not items:
        return
    from db.models import SessionLocal, NewsCache
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    now = _now_naive()
    async with SessionLocal() as db:
        for item in items:
            stmt = sqlite_insert(NewsCache).values(
                ticker=item["ticker"],
                title=item["title"],
                source=item.get("source", ""),
                url=item["url"] or "",
                published_at=item["published_at"],
                fetched_at=now,
            ).on_conflict_do_update(
                index_elements=["ticker", "url"],
                set_={"fetched_at": now},
            )
            await db.execute(stmt)
        await db.commit()


async def refresh_news_for_ticker(ticker: str) -> list[dict]:
    """Загружает свежие новости из Google News и сохраняет в БД."""
    connector = aiohttp.TCPConnector(force_close=True)
    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            items = await _fetch_gnews(session, ticker)
    finally:
        await connector.close()
    await _save_news_to_db(items)
    if items:
        logger.debug("News cached for %s: %d items", ticker, len(items))
    return items


async def refresh_all_news():
    """Обновляет новости для всех акций (вызывается планировщиком)."""
    all_tickers = list({**BLUE_CHIPS, **RISKY_STOCKS}.keys())
    connector = aiohttp.TCPConnector(force_close=True, limit=3)
    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            for ticker in all_tickers:
                items = await _fetch_gnews(session, ticker)
                await _save_news_to_db(items)
                await asyncio.sleep(1.0)
    finally:
        await connector.close()
    logger.info("News refresh complete for %d tickers", len(all_tickers))


async def fetch_news_for_ticker(ticker: str, limit: int = 5) -> list[dict]:
    """
    Новости из кэша БД (если свежее 2 часов), иначе подгружает из сети.
    """
    try:
        from db.models import SessionLocal, NewsCache
        from sqlalchemy import select

        cutoff = _now_naive() - timedelta(hours=2)

        async with SessionLocal() as db:
            result = await db.execute(
                select(NewsCache.title, NewsCache.url)
                .where(NewsCache.ticker == ticker)
                .where(NewsCache.fetched_at >= cutoff)
                .order_by(NewsCache.fetched_at.desc())
                .limit(limit)
            )
            cached = result.all()

        if cached:
            return [{"title": row.title, "url": row.url or ""} for row in cached]

        fresh = await refresh_news_for_ticker(ticker)
        return [{"title": i["title"], "url": i["url"]} for i in fresh[:limit]]
    except Exception as e:
        logger.warning("fetch_news_for_ticker %s: %s", ticker, e)
        return []
