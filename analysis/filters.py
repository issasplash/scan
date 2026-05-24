from __future__ import annotations
from dataclasses import dataclass, field
from analysis.technical import TechnicalResult
from analysis.fundamental import FundamentalResult
from analysis.macro import MacroContext


@dataclass
class FilterResult:
    blocked: bool = False          # True = не рекомендуем покупать сейчас
    warnings: list[str] = field(default_factory=list)


def check(
    tech: TechnicalResult,
    fund: FundamentalResult,
    macro: MacroContext,
) -> FilterResult:
    """
    Слой 4 — защитные фильтры.
    Блокирует сигнал «покупать» при явных стоп-признаках.
    """
    result = FilterResult()
    warnings = []

    # --- Технические ловушки ---
    if tech.rsi is not None and tech.rsi > 75:
        result.blocked = True
        warnings.append(f"🚫 RSI {tech.rsi:.0f} > 75 — акция перекуплена, ждать отката")

    if tech.price_change_30d is not None and tech.price_change_30d > 20:
        result.blocked = True
        warnings.append(f"🚫 Рост +{tech.price_change_30d:.0f}% за 30 дней — возможна коррекция")

    # Объём падает при росте цены — признак распределения
    if (tech.volume_last is not None and tech.volume_avg is not None
            and tech.volume_last < tech.volume_avg * 0.5
            and tech.price_change_30d is not None and tech.price_change_30d > 5):
        warnings.append("⚠️ Объём падает при росте цены — признак слабости")

    # Акция у 52-недельного максимума (resistance = 40-дневный максимум как прокси цены)
    if tech.high_52w is not None and tech.resistance is not None:
        if abs(tech.resistance - tech.high_52w) / tech.high_52w < 0.03:
            warnings.append("⚠️ Акция у 52-недельного максимума — осторожно при входе")

    # --- Дивидендные ловушки ---
    if fund.days_since_exdate is not None and fund.days_since_exdate < 14:
        warnings.append(f"⚠️ Отсечка {fund.days_since_exdate} дн. назад — акция ещё не восстановилась")

    # --- Долговая ловушка при высокой ставке ---
    if (fund.debt_ebitda is not None and fund.debt_ebitda > 2.5
            and macro.cbr_rate is not None and macro.cbr_rate >= 16):
        warnings.append(f"⚠️ Высокий долг ({fund.debt_ebitda:.1f}x EBITDA) при ставке ЦБ {macro.cbr_rate}%")

    # --- Медвежий рынок ---
    if macro.market_regime == "bearish":
        warnings.append("⚠️ Рынок в нисходящем тренде — повышенный риск любых покупок")

    result.warnings = warnings
    return result
