from __future__ import annotations
import asyncio
import logging
import ssl
import aiohttp
from datetime import date
from typing import Any
from config import TINKOFF_API_TOKEN
import pandas as pd

logger = logging.getLogger("tinkoff")

_BASE = "https://invest-public-api.tinkoff.ru/rest"
_SSL = ssl.create_default_context()


def _q(quotation: dict) -> float:
    return float(quotation.get("units", 0)) + quotation.get("nano", 0) / 1_000_000_000


async def _post(path: str, body: dict) -> dict:
    headers = {
        "Authorization": f"Bearer {TINKOFF_API_TOKEN}",
        "Content-Type": "application/json",
    }
    url = f"{_BASE}/{path}"
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, json=body, headers=headers,
            ssl=_SSL, timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            r.raise_for_status()
            return await r.json()


# ─── Accounts ─────────────────────────────────────────────────────────────────

async def get_accounts() -> list[dict]:
    if not TINKOFF_API_TOKEN:
        return []
    try:
        data = await _post("tinkoff.public.invest.api.contract.v1.UsersService/GetAccounts", {})
        return data.get("accounts", [])
    except Exception as e:
        logger.warning("GetAccounts error: %s", e)
        return []


# ─── Portfolio ────────────────────────────────────────────────────────────────

async def get_portfolio(account_id: str | None = None) -> dict:
    if not TINKOFF_API_TOKEN:
        return {}
    try:
        if not account_id:
            accounts = await get_accounts()
            if not accounts:
                return {}
            account_id = accounts[0]["id"]
        data = await _post(
            "tinkoff.public.invest.api.contract.v1.OperationsService/GetPortfolio",
            {"accountId": account_id},
        )
        return _parse_portfolio(data, account_id)
    except Exception as e:
        logger.warning("GetPortfolio error: %s", e)
        return {}


def _parse_portfolio(raw: dict, account_id: str) -> dict:
    total_shares = _q(raw.get("totalAmountShares", {}))
    total_bonds  = _q(raw.get("totalAmountBonds", {}))
    total_etf    = _q(raw.get("totalAmountEtf", {}))
    total_curr   = _q(raw.get("totalAmountCurrencies", {}))
    total_value  = total_shares + total_bonds + total_etf + total_curr

    positions = []
    for pos in raw.get("positions", []):
        qty = _q(pos.get("quantity", {}))
        if qty <= 0:
            continue
        positions.append({
            "figi":           pos.get("figi", ""),
            "instrument_uid": pos.get("instrumentUid", ""),
            "type":           pos.get("instrumentType", ""),
            "quantity":       qty,
            "avg_price":      _q(pos.get("averagePositionPrice", {})),
            "current_price":  _q(pos.get("currentPrice", {})),
            "expected_yield": _q(pos.get("expectedYield", {})),
            "currency":       pos.get("currentPrice", {}).get("currency", "rub"),
        })

    return {
        "account_id":   account_id,
        "total_value":  total_value,
        "total_shares": total_shares,
        "total_bonds":  total_bonds,
        "total_etf":    total_etf,
        "total_cash":   total_curr,
        "positions":    positions,
    }


# ─── Instrument lookup ────────────────────────────────────────────────────────

_figi_cache: dict[str, str] = {}
_uid_cache: dict[str, str] = {}   # ticker → assetUid


async def _find_instrument(ticker: str) -> dict | None:
    """Ищет инструмент по тикеру, возвращает первый точный match."""
    for flag in [False, True]:  # сначала без фильтра, потом с
        try:
            body: dict = {"query": ticker, "instrumentKind": "INSTRUMENT_TYPE_SHARE"}
            if flag:
                body["apiTradeAvailableFlag"] = True
            data = await _post(
                "tinkoff.public.invest.api.contract.v1.InstrumentsService/FindInstrument",
                body,
            )
            for inst in data.get("instruments", []):
                if inst.get("ticker") == ticker:
                    return inst
        except Exception as e:
            logger.warning("FindInstrument %s (flag=%s): %s", ticker, flag, e)
    return None


async def _find_figi(ticker: str) -> str | None:
    if ticker in _figi_cache:
        return _figi_cache[ticker]
    # ShareBy возвращает полный объект инструмента включая assetUid
    for class_code in ("TQBR", "TQTF", "TQIF"):
        try:
            data = await _post(
                "tinkoff.public.invest.api.contract.v1.InstrumentsService/ShareBy",
                {"idType": "ID_TYPE_TICKER", "classCode": class_code, "id": ticker},
            )
            inst = data.get("instrument", {})
            figi = inst.get("figi", "")
            if figi:
                _figi_cache[ticker] = figi
                uid = inst.get("assetUid", "")
                if uid:
                    _uid_cache[ticker] = uid
                return figi
        except Exception:
            pass
    # Fallback: FindInstrument (не содержит assetUid, но хотя бы figi)
    inst = await _find_instrument(ticker)
    if inst:
        figi = inst.get("figi", "")
        if figi:
            _figi_cache[ticker] = figi
            uid = inst.get("assetUid", "")
            if uid:
                _uid_cache[ticker] = uid
            return figi
    logger.warning("FIGI не найден для %s", ticker)
    return None


# ─── Historical candles ───────────────────────────────────────────────────────

async def get_candles_history(ticker: str, from_date: date, till_date: date) -> pd.DataFrame:
    if not TINKOFF_API_TOKEN:
        return pd.DataFrame()

    figi = await _find_figi(ticker)
    if not figi:
        return pd.DataFrame()

    all_rows = []
    current = from_date
    while current < till_date:
        chunk_end = min(date(current.year + 1, current.month, current.day), till_date)
        try:
            data = await _post(
                "tinkoff.public.invest.api.contract.v1.MarketDataService/GetCandles",
                {
                    "figi": figi,
                    "from": f"{current.isoformat()}T00:00:00Z",
                    "to":   f"{chunk_end.isoformat()}T00:00:00Z",
                    "interval": "CANDLE_INTERVAL_DAY",
                },
            )
            for c in data.get("candles", []):
                dt = c.get("time", "")[:10]
                if dt:
                    all_rows.append({
                        "date":   dt,
                        "open":   _q(c.get("open", {})),
                        "high":   _q(c.get("high", {})),
                        "low":    _q(c.get("low", {})),
                        "close":  _q(c.get("close", {})),
                        "volume": float(c.get("volume", 0)),
                    })
        except Exception as e:
            logger.warning("GetCandles %s %s-%s: %s", ticker, current, chunk_end, e)

        current = chunk_end
        await asyncio.sleep(0.2)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df[df["close"] > 0]
    return df.drop_duplicates("date").sort_values("date").reset_index(drop=True)


# ─── Prices ───────────────────────────────────────────────────────────────────

async def get_last_prices(tickers: list[str]) -> dict[str, float]:
    if not TINKOFF_API_TOKEN:
        return {}
    result = {}
    try:
        # Параллельный поиск FIGI
        figi_tasks = await asyncio.gather(*[_find_figi(t) for t in tickers], return_exceptions=True)
        figis = {t: f for t, f in zip(tickers, figi_tasks) if isinstance(f, str) and f}

        if not figis:
            return {}

        data = await _post(
            "tinkoff.public.invest.api.contract.v1.MarketDataService/GetLastPrices",
            {"figi": list(figis.values())},
        )
        figi_to_price = {
            lp["figi"]: _q(lp.get("price", {}))
            for lp in data.get("lastPrices", [])
        }
        for ticker, figi in figis.items():
            if figi in figi_to_price and figi_to_price[figi] > 0:
                result[ticker] = figi_to_price[figi]
    except Exception as e:
        logger.warning("GetLastPrices error: %s", e)
    return result


# ─── Fundamentals ─────────────────────────────────────────────────────────────

async def get_fundamentals(ticker: str) -> dict[str, Any]:
    if not TINKOFF_API_TOKEN:
        return {}
    try:
        # Используем кэш assetUid если уже есть
        asset_uid = _uid_cache.get(ticker)
        if not asset_uid:
            # ShareBy возвращает полный объект с assetUid
            for class_code in ("TQBR", "TQTF", "TQIF"):
                try:
                    data = await _post(
                        "tinkoff.public.invest.api.contract.v1.InstrumentsService/ShareBy",
                        {"idType": "ID_TYPE_TICKER", "classCode": class_code, "id": ticker},
                    )
                    uid = data.get("instrument", {}).get("assetUid", "")
                    if uid:
                        _uid_cache[ticker] = uid
                        asset_uid = uid
                        break
                except Exception:
                    pass

        if not asset_uid:
            logger.debug("Fundamentals: assetUid не найден для %s", ticker)
            return {}

        fund_data = await _post(
            "tinkoff.public.invest.api.contract.v1.InstrumentsService/GetAssetFundamentals",
            {"ids": [asset_uid]},
        )
        fundamentals = fund_data.get("fundamentals", [])
        if not fundamentals:
            logger.info("Fundamentals: нет данных для %s (assetUid=%s)", ticker, asset_uid)
            return {}

        f = fundamentals[0]
        result = {
            "pe":             _safe_float(f.get("peRatioTtm")),
            "pb":             _safe_float(f.get("priceToBookTtm")),
            "ev_ebitda":      _safe_float(f.get("evToEbitdaTtm")),
            "div_yield":      _safe_float(f.get("dividendYieldDailyTtm")),
            "debt_ebitda":    _safe_float(f.get("totalDebtToEquityAnnual")),
            "revenue_growth": _safe_float(f.get("revenueChangeOneYearAgo")),
            "net_margin":     _safe_float(f.get("netMarginTtm")),
        }
        non_null = {k: v for k, v in result.items() if v is not None}
        logger.info("Fundamentals %s: %s", ticker, non_null)
        return result
    except Exception as e:
        logger.warning("GetFundamentals %s: %s", ticker, e)
        return {}


def _safe_float(v) -> float | None:
    try:
        val = float(v) if v is not None else None
        return val if val is not None and val == val else None  # NaN check
    except (TypeError, ValueError):
        return None


# ─── Dividends ────────────────────────────────────────────────────────────────

async def get_dividends_tinkoff(ticker: str) -> list[dict]:
    """Дивиденды через T-Invest API (fallback когда MOEX недоступен)."""
    if not TINKOFF_API_TOKEN:
        return []
    try:
        figi = await _find_figi(ticker)
        if not figi:
            return []
        data = await _post(
            "tinkoff.public.invest.api.contract.v1.InstrumentsService/GetDividends",
            {
                "figi": figi,
                "from": "2018-01-01T00:00:00Z",
                "to":   "2030-01-01T00:00:00Z",
            },
        )
        dividends = data.get("dividends", [])
        result = []
        for d in dividends:
            # recordDate — дата закрытия реестра (аналог ex_date)
            ex_date = (d.get("recordDate") or d.get("lastBuyDate") or "")[:10]
            net = d.get("dividendNet", {})
            amount = _safe_float(net.get("units", 0)) + (net.get("nano", 0) or 0) / 1_000_000_000
            currency = net.get("currency", "rub").upper()
            if ex_date and amount and amount > 0:
                result.append({"ex_date": ex_date, "amount": amount, "currency": currency})
        result.sort(key=lambda x: x["ex_date"], reverse=True)
        logger.info("T-Invest dividends %s: %d records", ticker, len(result))
        return result
    except Exception as e:
        logger.warning("GetDividends %s: %s", ticker, e)
        return []
