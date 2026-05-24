from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
import pandas as pd

from analysis.technical import TechnicalResult, analyze as tech_analyze
from analysis.fundamental import FundamentalResult, analyze as fund_analyze
from analysis.filters import FilterResult, check as filter_check
from analysis.macro import MacroContext
from config import BLUE_CHIPS, RISKY_STOCKS

logger = logging.getLogger("signals")

SIGNAL_BUY  = "BUY"
SIGNAL_HOLD = "HOLD"
SIGNAL_SELL = "SELL"
SIGNAL_WAIT = "WAIT"


@dataclass
class SignalResult:
    ticker: str
    name: str
    price: float | None
    signal: str                         # BUY / HOLD / SELL / WAIT
    confidence: str                     # HIGH / MEDIUM / LOW
    tech: TechnicalResult = field(default_factory=TechnicalResult)
    fund: FundamentalResult = field(default_factory=FundamentalResult)
    filters: FilterResult = field(default_factory=FilterResult)
    total_score: int = 0
    ai_text: str = ""                   # заполняется позже через ai/analyst.py


def _confidence(score: int, blocked: bool, has_fundamentals: bool) -> str:
    if blocked:
        return "LOW"
    if abs(score) >= 6:
        return "HIGH"
    if abs(score) >= 3:
        # Без фундаментала уверенность снижаем — нет полной картины
        return "MEDIUM" if has_fundamentals else "LOW"
    return "LOW"


def _signal_from_score(score: int, blocked: bool, macro_regime: str, has_fundamentals: bool) -> str:
    if blocked:
        return SIGNAL_WAIT

    # Медвежий рынок: нужен более сильный сигнал для BUY
    buy_threshold = 3 if macro_regime == "bearish" else 2
    sell_threshold = -2

    if score >= buy_threshold + 2:
        return SIGNAL_BUY
    if score <= sell_threshold - 2:
        return SIGNAL_SELL
    if score >= buy_threshold:
        # Без фундаментала BUY → WAIT (нет полной уверенности)
        return SIGNAL_BUY if has_fundamentals else SIGNAL_WAIT
    if score <= sell_threshold:
        return SIGNAL_SELL
    return SIGNAL_HOLD


async def generate_signal(
    ticker: str,
    candles_df: pd.DataFrame,
    fundamentals: dict,
    dividends: list[dict],
    macro: MacroContext,
    price: float | None = None,
) -> SignalResult:
    all_stocks = {**BLUE_CHIPS, **RISKY_STOCKS}
    info = all_stocks.get(ticker, {"name": ticker, "sector": "unknown"})
    name = info["name"]
    sector = info["sector"]

    # Слой 3: технический
    tech = tech_analyze(candles_df)

    # Слой 2: фундаментальный
    fund = fund_analyze(ticker, sector, fundamentals, dividends)

    # Слой 1: макро (уже готов)
    macro_score = 0
    if macro.market_regime == "bullish":
        macro_score = 1
    elif macro.market_regime == "bearish":
        macro_score = -1

    # Итоговый score
    total = tech.score + fund.score + macro_score
    total = max(-9, min(9, total))

    # Слой 4: защитные фильтры
    filters = filter_check(tech, fund, macro)

    # Есть ли фундаментальные данные? (влияет на уверенность)
    has_fund = any([
        fund.pe, fund.div_yield, fund.debt_ebitda,
        fund.revenue_growth, fund.pb,
    ])

    signal = _signal_from_score(total, filters.blocked, macro.market_regime, has_fund)
    confidence = _confidence(total, filters.blocked, has_fund)

    # Текущая цена (из свечей если не передана)
    if price is None and not candles_df.empty:
        price = float(candles_df["close"].iloc[-1])

    return SignalResult(
        ticker=ticker,
        name=name,
        price=price,
        signal=signal,
        confidence=confidence,
        tech=tech,
        fund=fund,
        filters=filters,
        total_score=total,
    )
