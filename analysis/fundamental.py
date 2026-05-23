from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass
class FundamentalResult:
    pe: float | None = None
    pb: float | None = None
    ev_ebitda: float | None = None
    div_yield: float | None = None
    debt_ebitda: float | None = None
    revenue_growth: float | None = None
    next_ex_date: date | None = None
    last_dividend: float | None = None
    days_since_exdate: int | None = None
    score: int = 0
    signals: list[str] = field(default_factory=list)


# Исторические нормальные значения P/E для ориентира
_SECTOR_PE_NORM = {
    "oil":       6.0,
    "metals":    7.0,
    "finance":   5.0,
    "consumer":  12.0,
    "tech":      20.0,
    "chemicals": 8.0,
}


def analyze(
    ticker: str,
    sector: str,
    fundamentals: dict,
    dividends: list[dict],
) -> FundamentalResult:
    """
    Оценивает фундаментальные показатели.
    dividends — список dict(ex_date: str, amount: float, currency: str) от свежих к старым.
    """
    result = FundamentalResult()
    score = 0
    signals = []

    # --- Fundamentals from T-Invest ---
    result.pe           = fundamentals.get("pe")
    result.pb           = fundamentals.get("pb")
    result.ev_ebitda    = fundamentals.get("ev_ebitda")
    result.div_yield    = fundamentals.get("div_yield")
    result.debt_ebitda  = fundamentals.get("debt_ebitda")
    result.revenue_growth = fundamentals.get("revenue_growth")

    # P/E оценка
    if result.pe is not None and result.pe > 0:
        norm = _SECTOR_PE_NORM.get(sector, 10.0)
        if result.pe < norm * 0.7:
            score += 2
            signals.append(f"P/E {result.pe:.1f} — значительно ниже нормы ({norm}) ✅")
        elif result.pe < norm:
            score += 1
            signals.append(f"P/E {result.pe:.1f} — ниже нормы ({norm}) ✅")
        elif result.pe > norm * 1.5:
            score -= 1
            signals.append(f"P/E {result.pe:.1f} — выше нормы ({norm}) ⚠️")

    # Долг
    if result.debt_ebitda is not None:
        if result.debt_ebitda < 1.5:
            score += 1
            signals.append(f"Долг/EBITDA {result.debt_ebitda:.1f} — низкий ✅")
        elif result.debt_ebitda > 3.5:
            score -= 2
            signals.append(f"Долг/EBITDA {result.debt_ebitda:.1f} — высокий ⚠️")
        elif result.debt_ebitda > 2.5:
            score -= 1
            signals.append(f"Долг/EBITDA {result.debt_ebitda:.1f} — умеренно высокий ⚠️")

    # Дивидендная доходность
    if result.div_yield is not None and result.div_yield > 0:
        if result.div_yield > 10:
            score += 2
            signals.append(f"Дивдоходность {result.div_yield:.1f}% — отличная ✅")
        elif result.div_yield > 6:
            score += 1
            signals.append(f"Дивдоходность {result.div_yield:.1f}% — хорошая ✅")

    # Рост выручки
    if result.revenue_growth is not None:
        if result.revenue_growth > 15:
            score += 1
            signals.append(f"Рост выручки +{result.revenue_growth:.0f}% ✅")
        elif result.revenue_growth < -10:
            score -= 1
            signals.append(f"Выручка упала {result.revenue_growth:.0f}% ⚠️")

    # --- Дивиденды из MOEX ---
    today = date.today()
    if dividends:
        # Найдём последнюю отсечку
        past = [d for d in dividends if _parse_date(d["ex_date"]) and _parse_date(d["ex_date"]) <= today]
        future = [d for d in dividends if _parse_date(d["ex_date"]) and _parse_date(d["ex_date"]) > today]

        if past:
            last_ex = _parse_date(past[0]["ex_date"])
            result.days_since_exdate = (today - last_ex).days
            result.last_dividend = past[0]["amount"]

            if result.days_since_exdate < 14:
                score -= 1
                signals.append(f"⚠️ Отсечка {last_ex} — всего {result.days_since_exdate} дн. назад, акция может быть слабее")

        if future:
            result.next_ex_date = _parse_date(future[-1]["ex_date"])
            days_to = (result.next_ex_date - today).days
            if 7 <= days_to <= 30:
                score += 1
                signals.append(f"📅 Ближайшая отсечка через {days_to} дн. — {result.next_ex_date}")

    result.score = max(-4, min(4, score))
    result.signals = signals
    return result


def _parse_date(s: str) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except Exception:
        return None
