from __future__ import annotations
import logging
import ssl
import aiohttp
from typing import Any
from config import TINKOFF_API_TOKEN

logger = logging.getLogger("tinkoff")

_BASE = "https://invest-public-api.tinkoff.ru/rest"
_SSL = ssl.create_default_context()
_CONNECTOR = aiohttp.TCPConnector(force_close=True)


def _q(quotation: dict) -> float:
    """Quotation {units, nano} → float."""
    return float(quotation.get("units", 0)) + quotation.get("nano", 0) / 1_000_000_000


async def _post(path: str, body: dict) -> dict:
    headers = {
        "Authorization": f"Bearer {TINKOFF_API_TOKEN}",
        "Content-Type": "application/json",
    }
    url = f"{_BASE}/{path}"
    connector = aiohttp.TCPConnector(force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.post(
            url, json=body, headers=headers,
            ssl=_SSL, timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            r.raise_for_status()
            return await r.json()


# ─── Accounts ─────────────────────────────────────────────────────────────────

async def get_accounts() -> list[dict]:
    """Возвращает список счетов T-Invest."""
    if not TINKOFF_API_TOKEN:
        return []
    try:
        data = await _post(
            "tinkoff.public.invest.api.contract.v1.UsersService/GetAccounts", {}
        )
        return data.get("accounts", [])
    except Exception as e:
        logger.warning("GetAccounts error: %s", e)
        return []


# ─── Portfolio ────────────────────────────────────────────────────────────────

async def get_portfolio(account_id: str | None = None) -> dict:
    """
    Возвращает портфель по счёту.
    Если account_id не задан — берёт первый доступный счёт.
    """
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
    """Преобразует ответ API в удобный dict."""
    total_shares = _q(raw.get("totalAmountShares", {}))
    total_bonds  = _q(raw.get("totalAmountBonds", {}))
    total_etf    = _q(raw.get("totalAmountEtf", {}))
    total_curr   = _q(raw.get("totalAmountCurrencies", {}))
    total_value  = total_shares + total_bonds + total_etf + total_curr

    positions = []
    for pos in raw.get("positions", []):
        itype = pos.get("instrumentType", "")
        qty   = _q(pos.get("quantity", {}))
        if qty <= 0:
            continue
        avg   = _q(pos.get("averagePositionPrice", {}))
        cur   = _q(pos.get("currentPrice", {}))
        exp_y = _q(pos.get("expectedYield", {}))
        positions.append({
            "figi":          pos.get("figi", ""),
            "instrument_uid": pos.get("instrumentUid", ""),
            "type":          itype,
            "quantity":      qty,
            "avg_price":     avg,
            "current_price": cur,
            "expected_yield": exp_y,
            "currency":      pos.get("currentPrice", {}).get("currency", "rub"),
        })

    return {
        "account_id":    account_id,
        "total_value":   total_value,
        "total_shares":  total_shares,
        "total_bonds":   total_bonds,
        "total_etf":     total_etf,
        "total_cash":    total_curr,
        "positions":     positions,
    }


# ─── Instrument lookup ────────────────────────────────────────────────────────

_figi_cache: dict[str, str] = {}   # ticker → figi


async def _find_figi(ticker: str) -> str | None:
    if ticker in _figi_cache:
        return _figi_cache[ticker]
    try:
        data = await _post(
            "tinkoff.public.invest.api.contract.v1.InstrumentsService/FindInstrument",
            {"query": ticker, "instrumentKind": "INSTRUMENT_TYPE_UNSPECIFIED", "apiTradeAvailableFlag": True},
        )
        for inst in data.get("instruments", []):
            if inst.get("ticker") == ticker:
                _figi_cache[ticker] = inst["figi"]
                return inst["figi"]
    except Exception as e:
        logger.debug("FindInstrument %s: %s", ticker, e)
    return None


# ─── Prices ───────────────────────────────────────────────────────────────────

async def get_last_prices(tickers: list[str]) -> dict[str, float]:
    """Возвращает последние цены по тикерам через T-Invest REST API."""
    if not TINKOFF_API_TOKEN:
        return {}
    result = {}
    try:
        figis = {}
        for t in tickers:
            figi = await _find_figi(t)
            if figi:
                figis[t] = figi

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
    """Возвращает фундаментальные показатели через T-Invest REST API."""
    if not TINKOFF_API_TOKEN:
        return {}
    try:
        data = await _post(
            "tinkoff.public.invest.api.contract.v1.InstrumentsService/FindInstrument",
            {"query": ticker, "instrumentKind": "INSTRUMENT_TYPE_UNSPECIFIED", "apiTradeAvailableFlag": True},
        )
        instruments = [i for i in data.get("instruments", []) if i.get("ticker") == ticker]
        if not instruments:
            return {}

        asset_uid = instruments[0].get("assetUid", "")
        if not asset_uid:
            return {}

        fund_data = await _post(
            "tinkoff.public.invest.api.contract.v1.InstrumentsService/GetAssetFundamentals",
            {"ids": [asset_uid]},
        )
        fundamentals = fund_data.get("fundamentals", [])
        if not fundamentals:
            return {}

        f = fundamentals[0]
        return {
            "pe":             _safe_float(f.get("peRatioTtm")),
            "pb":             _safe_float(f.get("priceToBookTtm")),
            "ev_ebitda":      _safe_float(f.get("evToEbitdaTtm")),
            "div_yield":      _safe_float(f.get("dividendYieldDailyTtm")),
            "debt_ebitda":    _safe_float(f.get("totalDebtToEquityAnnual")),
            "revenue_growth": _safe_float(f.get("revenueChangeOneYearAgo")),
            "net_margin":     _safe_float(f.get("netMarginTtm")),
        }
    except Exception as e:
        logger.debug("GetFundamentals %s: %s", ticker, e)
        return {}


def _safe_float(v) -> float | None:
    try:
        val = float(v) if v is not None else None
        return val if val is not None and val == val else None  # NaN check
    except (TypeError, ValueError):
        return None
