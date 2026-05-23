from __future__ import annotations
import asyncio
import logging
from datetime import date, timedelta
from typing import Any
import aiohttp
import pandas as pd

logger = logging.getLogger("moex")

BASE = "https://iss.moex.com/iss"
HEADERS = {"Accept-Encoding": "gzip"}


async def _get(session: aiohttp.ClientSession, url: str, params: dict | None = None) -> dict:
    async with session.get(url, params=params or {}, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=20)) as r:
        r.raise_for_status()
        return await r.json(content_type=None)


async def _get_candles_from_board(
    session: aiohttp.ClientSession,
    ticker: str,
    from_date: date,
    till_date: date,
    board: str,
) -> list[dict]:
    """Загружает свечи с конкретной доски MOEX ISS (с retry)."""
    rows = []
    start = 0
    url = f"{BASE}/history/engines/stock/markets/shares/boards/{board}/securities/{ticker}/candles.json"

    while True:
        params = {"from": str(from_date), "till": str(till_date), "interval": 24, "start": start}
        data = None
        for attempt in range(3):
            try:
                data = await _get(session, url, params)
                break
            except Exception as e:
                if attempt == 2:
                    logger.warning("MOEX %s/%s error (3 попытки): %s", board, ticker, e)
                else:
                    await asyncio.sleep(2 ** attempt)

        if data is None:
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
        await asyncio.sleep(0.3)

    return rows


async def get_candles(ticker: str, from_date: date, till_date: date) -> pd.DataFrame:
    """Загружает дневные свечи с MOEX ISS. Пробует TQBR, затем TQNE как fallback."""
    async with aiohttp.ClientSession() as session:
        rows: list[dict] = []
        for board in ("TQBR", "TQNE"):
            rows = await _get_candles_from_board(session, ticker, from_date, till_date, board)
            if rows:
                break
            await asyncio.sleep(0.5)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    return df


async def get_dividends(ticker: str) -> list[dict]:
    """Возвращает историю дивидендов и ближайшие выплаты."""
    url = f"{BASE}/securities/{ticker}/dividends.json"
    async with aiohttp.ClientSession() as session:
        try:
            data = await _get(session, url)
        except Exception as e:
            logger.warning("MOEX dividends %s: %s", ticker, e)
            return []

    divs = data.get("dividends", {})
    cols = divs.get("columns", [])
    rows = divs.get("data", [])
    if not cols or not rows:
        return []

    col_map = {c: i for i, c in enumerate(cols)}
    result = []
    for row in rows:
        ex_date_str = row[col_map.get("registryclosedate", 0)] if "registryclosedate" in col_map else None
        value = row[col_map.get("value", 1)] if "value" in col_map else None
        currency = row[col_map.get("currencyid", 2)] if "currencyid" in col_map else "RUB"
        if ex_date_str and value:
            result.append({"ex_date": ex_date_str, "amount": float(value), "currency": currency or "RUB"})

    return sorted(result, key=lambda x: x["ex_date"], reverse=True)


async def get_usd_rub() -> float | None:
    """Текущий курс USD/RUB с MOEX."""
    url = f"{BASE}/engines/currency/markets/selt/boards/CETS/securities/USD000UTSTOM.json"
    async with aiohttp.ClientSession() as session:
        try:
            data = await _get(session, url)
            md = data.get("marketdata", {})
            cols = md.get("columns", [])
            rows = md.get("data", [])
            if cols and rows:
                col_map = {c: i for i, c in enumerate(cols)}
                last = col_map.get("LAST") or col_map.get("LCURRENTPRICE")
                if last is not None and rows[0][last]:
                    return float(rows[0][last])
        except Exception as e:
            logger.warning("USD/RUB error: %s", e)
    return None


async def get_imoex() -> float | None:
    """Текущее значение индекса ММВБ (IMOEX)."""
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
            logger.warning("IMOEX error: %s", e)
    return None


async def get_imoex_history(days: int = 210) -> pd.DataFrame:
    """История IMOEX за последние N дней для расчёта MA."""
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
                rows.append({
                    "date":  row[col_map["begin"]][:10],
                    "close": row[col_map["close"]],
                })
            start += len(batch)
            if len(batch) < 100:
                break
            await asyncio.sleep(0.2)

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


async def get_ofz_list() -> list[dict]:
    """Список ОФЗ с доходностью (TQOB board)."""
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
        # только ОФЗ
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
                "ticker":   secid,
                "name":     name,
                "matdate":  matdate,
                "yield":    float(yieldatprevwa),
                "coupon":   float(coupon) if coupon else None,
                "price":    float(last_price) if last_price else None,
                "facevalue": float(facevalue) if facevalue else 1000,
            })

    result.sort(key=lambda x: x["yield"], reverse=True)
    return result[:20]
