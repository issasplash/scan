from __future__ import annotations
import asyncio
import logging
import re
from datetime import datetime, timezone
import aiohttp
import feedparser
from config import BLUE_CHIPS, RISKY_STOCKS

logger = logging.getLogger("news")

RSS_FEEDS = [
    "https://www.rbc.ru/v10/finance/rss",
    "https://tass.ru/rss/v2.xml",
    "https://www.interfax.ru/rss.asp",
    "https://smart-lab.ru/news/rss/",
    "https://investfuture.ru/rss/news",
]

_ALL_NAMES: dict[str, list[str]] = {}

def _build_name_index():
    all_stocks = {**BLUE_CHIPS, **RISKY_STOCKS}
    for ticker, info in all_stocks.items():
        name = info["name"].lower()
        _ALL_NAMES[ticker] = [ticker.lower(), name]
        # добавляем альтернативные названия
        extras = {
            "LKOH": ["лукойл", "lukoil"],
            "SBER": ["сбербанк", "сбер", "sber"],
            "TATN": ["татнефть", "tatneft"],
            "GAZP": ["газпром", "gazprom"],
            "NVTK": ["новатэк", "novatek"],
            "ROSN": ["роснефть", "rosneft"],
            "GMKN": ["норникель", "norilsk"],
            "CHMF": ["северсталь", "severstal"],
            "NLMK": ["нлмк", "nlmk"],
            "MAGN": ["ммк", "magnitogorsk"],
            "PLZL": ["полюс", "polyus"],
            "ALRS": ["алроса", "alrosa"],
            "VTBR": ["втб", "vtb"],
            "MGNT": ["магнит", "magnit"],
            "FIVE": ["x5", "х5", "пятёрочка", "перекрёсток"],
            "YNDX": ["яндекс", "yandex"],
            "MTSS": ["мтс", "mts"],
            "PHOR": ["фосагро", "phosagro"],
            "POSI": ["позитив", "positive technologies"],
            "HEAD": ["headhunter", "хедхантер"],
            "WUSH": ["whoosh", "вуш"],
            "OZON": ["ozon", "озон"],
            "ASTR": ["астра", "astra"],
        }
        if ticker in extras:
            _ALL_NAMES[ticker].extend(extras[ticker])

_build_name_index()


async def _fetch_feed(session: aiohttp.ClientSession, url: str) -> list[dict]:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            content = await r.read()
        feed = feedparser.parse(content)
        items = []
        for entry in feed.entries[:30]:
            title = entry.get("title", "")
            summary = entry.get("summary", entry.get("description", ""))
            link = entry.get("link", "")
            published = entry.get("published", "")
            items.append({"title": title, "summary": summary, "link": link, "published": published})
        return items
    except Exception as e:
        logger.debug("Feed %s error: %s", url, e)
        return []


async def fetch_all_news() -> list[dict]:
    """Загружает новости из всех RSS-лент."""
    async with aiohttp.ClientSession() as session:
        tasks = [_fetch_feed(session, url) for url in RSS_FEEDS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items = []
    for r in results:
        if isinstance(r, list):
            all_items.extend(r)
    return all_items


def filter_news_for_ticker(ticker: str, all_news: list[dict], limit: int = 5) -> list[dict]:
    """Фильтрует новости, упоминающие конкретную акцию."""
    keywords = _ALL_NAMES.get(ticker, [ticker.lower()])
    matched = []
    for item in all_news:
        text = (item["title"] + " " + item["summary"]).lower()
        if any(kw in text for kw in keywords):
            matched.append(item)
    return matched[:limit]


async def fetch_news_for_ticker(ticker: str, limit: int = 5) -> list[dict]:
    all_news = await fetch_all_news()
    return filter_news_for_ticker(ticker, all_news, limit)
