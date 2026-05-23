from __future__ import annotations
import logging
import re
import aiohttp
import pandas as pd
from dataclasses import dataclass, field

logger = logging.getLogger("macro")


@dataclass
class MacroContext:
    brent: float | None = None
    usd_rub: float | None = None
    cbr_rate: float | None = None
    imoex: float | None = None
    imoex_ma50: float | None = None
    market_regime: str = "neutral"   # bullish / bearish / neutral
    signals: list[str] = field(default_factory=list)


async def _get_brent() -> float | None:
    """Цена нефти Brent через Yahoo Finance API."""
    url = "https://query1.finance.yahoo.com/v8/finance/chart/BZ=F"
    params = {"interval": "1d", "range": "5d"}
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(force_close=True)) as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10),
                                   headers={"User-Agent": "Mozilla/5.0"}) as r:
                data = await r.json()
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        return round(closes[-1], 2) if closes else None
    except Exception as e:
        logger.debug("Brent error: %s", e)
        return None


async def _get_cbr_rate() -> float | None:
    """Текущая ключевая ставка ЦБ РФ (парсинг сайта ЦБ)."""
    url = "https://www.cbr.ru/hd_base/KeyRate/"
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(force_close=True)) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                html = await r.text(encoding="utf-8", errors="ignore")
        # Ищем строку вида "21,00" или "16,00" в таблице
        matches = re.findall(r"(\d{1,2}),(\d{2})", html)
        for m in matches:
            val = float(f"{m[0]}.{m[1]}")
            if 1 <= val <= 30:
                return val
    except Exception as e:
        logger.debug("CBR rate error: %s", e)
    return None


async def get_macro_context(imoex_history_df: pd.DataFrame | None = None) -> MacroContext:
    """Собирает макро-контекст: нефть, рубль, ставка, IMOEX."""
    from data.moex_client import get_usd_rub, get_imoex

    brent, usd_rub_val, cbr, imoex_val = None, None, None, None

    # Параллельный запрос
    import asyncio
    results = await asyncio.gather(
        _get_brent(),
        get_usd_rub(),
        _get_cbr_rate(),
        get_imoex(),
        return_exceptions=True,
    )

    brent      = results[0] if not isinstance(results[0], Exception) else None
    usd_rub_val = results[1] if not isinstance(results[1], Exception) else None
    cbr        = results[2] if not isinstance(results[2], Exception) else None
    imoex_val  = results[3] if not isinstance(results[3], Exception) else None

    ctx = MacroContext(
        brent=brent,
        usd_rub=usd_rub_val,
        cbr_rate=cbr,
        imoex=imoex_val,
    )

    # IMOEX vs MA50 для определения режима рынка
    if imoex_history_df is not None and not imoex_history_df.empty and len(imoex_history_df) >= 50:
        ma50 = float(imoex_history_df["close"].tail(50).mean())
        ctx.imoex_ma50 = ma50
        if imoex_val and imoex_val > ma50 * 1.02:
            ctx.market_regime = "bullish"
            ctx.signals.append(f"IMOEX {imoex_val:.0f} выше MA50 ({ma50:.0f}) — рынок растёт ✅")
        elif imoex_val and imoex_val < ma50 * 0.98:
            ctx.market_regime = "bearish"
            ctx.signals.append(f"IMOEX {imoex_val:.0f} ниже MA50 ({ma50:.0f}) — рынок падает ⚠️")
        else:
            ctx.signals.append(f"IMOEX {imoex_val:.0f} около MA50 — рынок нейтральный")

    # Ставка ЦБ
    if cbr:
        if cbr >= 18:
            ctx.signals.append(f"Ставка ЦБ {cbr}% — высокая, давление на акции ⚠️")
        elif cbr <= 10:
            ctx.signals.append(f"Ставка ЦБ {cbr}% — низкая, позитив для акций ✅")
        else:
            ctx.signals.append(f"Ставка ЦБ {cbr}%")

    return ctx
