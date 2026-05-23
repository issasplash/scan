from __future__ import annotations
import asyncio
import logging
from datetime import date, timedelta
import aiohttp
import pandas as pd

logger = logging.getLogger("moex")

BASE = "https://iss.moex.com/iss"
HEADERS = {"Accept-Encoding": "gzip"}


async def _get(session: aiohttp.ClientSession, url: str, params: dict | None = None) -> dict:
    async with session.get(url, params=params or {}, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as r:
        r.raise_for_status()
        return await r.json(content_type=None)


async def _load_board(ticker: str, from_date: date, till_date: date, board: str) -> pd.DataFrame:
    rows = []
    start = 0
    url = f"{BASE}/history/engines/stock/markets/shares/boards/{board}/securities/{ticker}/candles.json"

    async with aiohttp.ClientSession() as session:
        while True:
            params = {"from": str(from_date), "till": str(till_date), "interval": 24, "start": start}
            try:
                data = await _get(session, url, params)
            except Exception as e:
                logger.debug("MOEX %s/%s: %s", board, ticker, e)
                break

            candles = data.get("candles", {})
            cols = candles.get("columns", [])
            batch = candles.get("data", [])
            if not batch:
                break

            col_map = {c: i for i, c in enumerate(cols)}
            for row in batch:
                rows.append({
                    "date":   row[col_map["begin"]][:10],
                    "open":   row[col_map["open"]],
                    "high":   row[col_map["high"]],
                    "low":    row[col_map["low"]],
                    "close":  row[col_map["close"]],
                    "volume": row[col_map["volume"]],
                })

            start += len(batch)
            if len(batch) < 100:
                break
            await asyncio.sleep(0.2)

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.drop_duplicates("date").sort_values("date").reset_index(drop=True)


async def get_candles(ticker: str, from_date: date, till_date: date) -> pd.DataFrame:
    """MOEX ISS TQBR → TQNE → T-Invest → Yahoo Finance."""
    df = await _load_board(ticker, from_date, till_date, "TQBR")
    if df.empty:
        df = await _load_board(ticker, from_date, till_date, "TQNE")
    if df.empty:
        from data.tinkoff_client import get_candles_history
        logger.info("MOEX недоступен, пробуем T-Invest для %s", ticker)
        df = await get_candles_history(ticker, from_date, till_date)
    if df.empty:
        df = await _load_yfinance(ticker, from_date, till_date)
    return df


async def _load_yfinance(ticker: str, from_date: date, till_date: date) -> pd.DataFrame:
    """Fallback через Yahoo Finance (TICKER.ME)."""
    try:
        import yfinance as yf
        yf_ticker = f"{ticker}.ME"
        loop = asyncio.get_event_loop()
        hist = await loop.run_in_executor(
            None,
            lambda: yf.download(
                yf_ticker, start=str(from_date), end=str(till_date),
                progress=False, auto_adjust=True,
            ),
        )
        if hist.empty:
            return pd.DataFrame()
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)
        df = pd.DataFrame({
            "date":   hist.index.date,
            "open":   hist["Open"].values,
            "high":   hist["High"].values,
            "low":    hist["Low"].values,
            "close":  hist["Close"].values,
            "volume": hist["Volume"].values,
        })
        df = df.dropna(subset=["close"])
        df = df[df["close"] > 0]
        logger.info("yfinance: %d свечей для %s", len(df), ticker)
        return df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    except Exception as e:
        logger.warning("yfinance %s: %s", ticker, e)
        return pd.DataFrame()


async def _yfinance_last(yf_ticker: str) -> float | None:
    """Последняя цена через Yahoo Finance."""
    try:
        import yfinance as yf
        loop = asyncio.get_event_loop()
        hist = await loop.run_in_executor(
            None,
            lambda: yf.download(yf_ticker, period="5d", progress=False, auto_adjust=True),
        )
        if hist.empty:
            return None
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)
        closes = hist["Close"].dropna()
        return round(float(closes.iloc[-1]), 2) if not closes.empty else None
    except Exception as e:
        logger.debug("yfinance last %s: %s", yf_ticker, e)
        return None


async def _yfinance_history(yf_ticker: str, days: int) -> pd.DataFrame:
    """История закрытий через Yahoo Finance."""
    try:
        import yfinance as yf
        loop = asyncio.get_event_loop()
        hist = await loop.run_in_executor(
            None,
            lambda: yf.download(yf_ticker, period=f"{days}d", progress=False, auto_adjust=True),
        )
        if hist.empty:
            return pd.DataFrame()
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)
        df = pd.DataFrame({
            "date":  hist.index.date,
            "close": hist["Close"].values,
        })
        return df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    except Exception as e:
        logger.debug("yfinance history %s: %s", yf_ticker, e)
        return pd.DataFrame()


async def get_dividends(ticker: str) -> list[dict]:
    """Дивиденды: MOEX ISS → T-Invest API."""
    url = f"{BASE}/securities/{ticker}/dividends.json"
    async with aiohttp.ClientSession() as session:
        try:
            data = await _get(session, url)
            divs = data.get("dividends", {})
            cols = divs.get("columns", [])
            rows = divs.get("data", [])
            if cols and rows:
                col_map = {c: i for i, c in enumerate(cols)}
                result = []
                for row in rows:
                    ex_date_str = row[col_map.get("registryclosedate", 0)] if "registryclosedate" in col_map else None
                    value = row[col_map.get("value", 1)] if "value" in col_map else None
                    currency = row[col_map.get("currencyid", 2)] if "currencyid" in col_map else "RUB"
                    if ex_date_str and value:
                        result.append({"ex_date": ex_date_str, "amount": float(value), "currency": currency or "RUB"})
                if result:
                    return sorted(result, key=lambda x: x["ex_date"], reverse=True)
        except Exception as e:
            logger.debug("MOEX dividends %s: %s", ticker, e)

    # T-Invest fallback
    try:
        from data.tinkoff_client import get_dividends_tinkoff
        return await get_dividends_tinkoff(ticker)
    except Exception as e:
        logger.debug("T-Invest dividends %s: %s", ticker, e)
    return []


async def get_usd_rub() -> float | None:
    """USD/RUB: MOEX → Yahoo Finance."""
    url = f"{BASE}/engines/currency/markets/selt/boards/CETS/securities/USD000UTSTOM.json"
    async with aiohttp.ClientSession() as session:
        try:
            data = await _get(session, url)
            md = data.get("marketdata", {})
            cols = md.get("columns", [])
            rows = md.get("data", [])
            if cols and rows:
                col_map = {c: i for i, c in enumerate(cols)}
                for key in ("LAST", "LCURRENTPRICE"):
                    idx = col_map.get(key)
                    if idx is not None and rows[0][idx]:
                        return float(rows[0][idx])
        except Exception as e:
            logger.debug("USD/RUB MOEX: %s", e)

    return await _yfinance_last("USDRUB=X")


async def get_imoex() -> float | None:
    """IMOEX текущий: MOEX → Yahoo Finance."""
    url = f"{BASE}/engines/stock/markets/index/securities/IMOEX.json"
    async with aiohttp.ClientSession() as session:
        try:
            data = await _get(session, url)
            md = data.get("marketdata", {})
            cols = md.get("columns", [])
            rows = md.get("data", [])
            if cols and rows:
                col_map = {c: i for i, c in enumerate(cols)}
                for key in ("CURRENTVALUE", "LAST", "LCURRENTPRICE"):
                    idx = col_map.get(key)
                    if idx is not None and rows[0][idx]:
                        return float(rows[0][idx])
        except Exception as e:
            logger.debug("IMOEX MOEX: %s", e)

    return await _yfinance_last("IMOEX.ME")


async def get_imoex_history(days: int = 210) -> pd.DataFrame:
    """История IMOEX: MOEX → Yahoo Finance."""
    till = date.today()
    frm = till - timedelta(days=days)
    url = f"{BASE}/history/engines/stock/markets/index/boards/SNDX/securities/IMOEX/candles.json"
    rows = []
    start = 0
    async with aiohttp.ClientSession() as session:
        while True:
            params = {"from": str(frm), "till": str(till), "interval": 24, "start": start}
            try:
                data = await _get(session, url, params)
            except Exception:
                break
            candles = data.get("candles", {})
            cols = candles.get("columns", [])
            batch = candles.get("data", [])
            if not batch:
                break
            col_map = {c: i for i, c in enumerate(cols)}
            for row in batch:
                rows.append({"date": row[col_map["begin"]][:10], "close": row[col_map["close"]]})
            start += len(batch)
            if len(batch) < 100:
                break
            await asyncio.sleep(0.2)

    if rows:
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df.sort_values("date").reset_index(drop=True)

    logger.info("IMOEX MOEX недоступен, пробуем Yahoo Finance")
    return await _yfinance_history("IMOEX.ME", days)


async def get_ofz_list() -> list[dict]:
    """Список ОФЗ (только MOEX, недоступно с зарубежного VPS)."""
    url = f"{BASE}/engines/stock/markets/bonds/boards/TQOB/securities.json"
    async with aiohttp.ClientSession() as session:
        try:
            data = await _get(session, url)
        except Exception as e:
            logger.warning("OFZ list error: %s", e)
            return []

    sec = data.get("securities", {})
    md = data.get("marketdata", {})
    sec_cols = sec.get("columns", [])
    sec_rows = sec.get("data", [])
    md_cols = md.get("columns", [])
    md_rows = md.get("data", [])

    if not sec_cols or not sec_rows:
        return []

    sc = {c: i for i, c in enumerate(sec_cols)}
    mc = {c: i for i, c in enumerate(md_cols)}

    md_by_sec = {}
    for row in md_rows:
        sid = row[mc.get("SECID", 0)]
        md_by_sec[sid] = row

    result = []
    for row in sec_rows:
        secid = row[sc.get("SECID", 0)]
        if not str(secid).startswith("SU"):
            continue
        name = row[sc.get("SECNAME", 1)]
        matdate = row[sc.get("MATDATE", sc.get("MATURITYDATE", 2))] if "MATDATE" in sc else None
        coupon = row[sc.get("COUPONVALUE", 3)] if "COUPONVALUE" in sc else None
        facevalue = row[sc.get("FACEVALUE", 4)] if "FACEVALUE" in sc else 1000

        md_row = md_by_sec.get(secid, [])
        yieldatprevwa = None
        if md_row and "YIELDATPREVWA" in mc:
            yieldatprevwa = md_row[mc["YIELDATPREVWA"]]
        last_price = None
        if md_row and "LAST" in mc:
            last_price = md_row[mc["LAST"]]

        if yieldatprevwa and matdate:
            result.append({
                "ticker":    secid,
                "name":      name,
                "matdate":   matdate,
                "yield":     float(yieldatprevwa),
                "coupon":    float(coupon) if coupon else None,
                "price":     float(last_price) if last_price else None,
                "facevalue": float(facevalue) if facevalue else 1000,
            })

    result.sort(key=lambda x: x["yield"], reverse=True)
    return result[:20]
