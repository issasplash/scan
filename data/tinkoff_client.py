from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any
from config import TINKOFF_API_TOKEN

logger = logging.getLogger("tinkoff")


async def get_last_prices(tickers: list[str]) -> dict[str, float]:
    """Возвращает последние цены по тикерам через T-Invest API."""
    if not TINKOFF_API_TOKEN:
        return {}
    try:
        from tinkoff.invest import AsyncClient, InstrumentIdType
        from tinkoff.invest.utils import quotation_to_decimal

        async with AsyncClient(TINKOFF_API_TOKEN) as client:
            result = {}
            for ticker in tickers:
                try:
                    resp = await client.instruments.find_instrument(query=ticker)
                    instruments = [i for i in resp.instruments if i.ticker == ticker]
                    if not instruments:
                        continue
                    figi = instruments[0].figi
                    prices = await client.market_data.get_last_prices(figi=[figi])
                    if prices.last_prices:
                        result[ticker] = float(quotation_to_decimal(prices.last_prices[0].price))
                except Exception as e:
                    logger.debug("Price %s: %s", ticker, e)
            return result
    except ImportError:
        logger.warning("tinkoff-investments not installed")
        return {}
    except Exception as e:
        logger.warning("Tinkoff API error: %s", e)
        return {}


async def get_fundamentals(ticker: str) -> dict[str, Any]:
    """
    Возвращает фундаментальные показатели через T-Invest API.
    Fallback: пустой dict если API недоступно.
    """
    if not TINKOFF_API_TOKEN:
        return {}
    try:
        from tinkoff.invest import AsyncClient

        async with AsyncClient(TINKOFF_API_TOKEN) as client:
            resp = await client.instruments.find_instrument(query=ticker)
            instruments = [i for i in resp.instruments if i.ticker == ticker]
            if not instruments:
                return {}

            asset_uid = instruments[0].asset_uid
            if not asset_uid:
                return {}

            fund_resp = await client.instruments.get_asset_fundamentals(
                ids=[asset_uid]
            )
            if not fund_resp.fundamentals:
                return {}

            f = fund_resp.fundamentals[0]
            return {
                "pe":           _safe_float(f.pe_ratio_ttm),
                "pb":           _safe_float(f.price_to_book_ttm),
                "ev_ebitda":    _safe_float(f.ev_to_ebitda_ttm),
                "div_yield":    _safe_float(f.dividend_yield_daily_ttm),
                "debt_ebitda":  _safe_float(f.total_debt_to_equity_annual),
                "revenue_growth": _safe_float(f.revenue_change_one_year_ago),
                "net_margin":   _safe_float(f.net_margin_ttm),
            }
    except Exception as e:
        logger.debug("Fundamentals %s: %s", ticker, e)
        return {}


def _safe_float(v) -> float | None:
    try:
        val = float(v) if v is not None else None
        return val if val and val == val else None  # NaN check
    except (TypeError, ValueError):
        return None
