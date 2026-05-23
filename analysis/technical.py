from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass, field


@dataclass
class TechnicalResult:
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    ma20: float | None = None
    ma50: float | None = None
    ma200: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None
    support: float | None = None
    resistance: float | None = None
    volume_avg: float | None = None
    volume_last: float | None = None
    price_change_30d: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    score: int = 0          # итоговый score от -5 до +5
    signals: list[str] = field(default_factory=list)


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_rsi(closes: pd.Series, period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else None


def calc_macd(closes: pd.Series) -> tuple[float | None, float | None]:
    if len(closes) < 35:
        return None, None
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = ema12 - ema26
    signal_line = _ema(macd_line, 9)
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1])


def calc_bollinger(closes: pd.Series, period: int = 20) -> tuple[float | None, float | None]:
    if len(closes) < period:
        return None, None
    ma = closes.rolling(period).mean()
    std = closes.rolling(period).std()
    upper = ma + 2 * std
    lower = ma - 2 * std
    return float(upper.iloc[-1]), float(lower.iloc[-1])


def calc_ma(closes: pd.Series, period: int) -> float | None:
    if len(closes) < period:
        return None
    return float(closes.rolling(period).mean().iloc[-1])


def find_support_resistance(closes: pd.Series, window: int = 20) -> tuple[float | None, float | None]:
    """Простые уровни: минимум/максимум последних N дней."""
    if len(closes) < window:
        return None, None
    recent = closes.tail(window)
    return float(recent.min()), float(recent.max())


def analyze(df: pd.DataFrame) -> TechnicalResult:
    """
    df: DataFrame с колонками date, open, high, low, close, volume (дневные свечи).
    Возвращает TechnicalResult с score от -5 до +5.
    """
    result = TechnicalResult()
    if df.empty or len(df) < 5:
        return result

    closes = df["close"].astype(float)
    volumes = df["volume"].astype(float) if "volume" in df.columns else pd.Series(dtype=float)
    price_now = float(closes.iloc[-1])

    # RSI
    result.rsi = calc_rsi(closes)

    # MACD
    result.macd, result.macd_signal = calc_macd(closes)

    # Moving averages
    result.ma20 = calc_ma(closes, 20)
    result.ma50 = calc_ma(closes, 50)
    result.ma200 = calc_ma(closes, 200)

    # Bollinger
    result.bb_upper, result.bb_lower = calc_bollinger(closes)

    # Levels
    result.support, result.resistance = find_support_resistance(closes, 40)

    # Volume
    if not volumes.empty and len(volumes) >= 20:
        result.volume_avg = float(volumes.tail(20).mean())
        result.volume_last = float(volumes.iloc[-1])

    # 52-week range
    if len(closes) >= 252:
        result.high_52w = float(closes.tail(252).max())
        result.low_52w = float(closes.tail(252).min())
    else:
        result.high_52w = float(closes.max())
        result.low_52w = float(closes.min())

    # 30-day price change %
    if len(closes) >= 30:
        price_30d_ago = float(closes.iloc[-30])
        result.price_change_30d = (price_now - price_30d_ago) / price_30d_ago * 100

    # --- Scoring ---
    score = 0
    signals = []

    # RSI signals
    if result.rsi is not None:
        if result.rsi < 35:
            score += 2
            signals.append(f"RSI {result.rsi:.0f} — перепродано ✅")
        elif result.rsi < 50:
            score += 1
            signals.append(f"RSI {result.rsi:.0f} — нейтрально")
        elif result.rsi > 70:
            score -= 2
            signals.append(f"RSI {result.rsi:.0f} — перекуплено ⚠️")
        elif result.rsi > 60:
            score -= 1
            signals.append(f"RSI {result.rsi:.0f} — повышенный")

    # MACD
    if result.macd is not None and result.macd_signal is not None:
        if result.macd > result.macd_signal:
            score += 1
            signals.append("MACD > Signal ✅")
        else:
            score -= 1
            signals.append("MACD < Signal ⚠️")

    # Moving averages trend
    if result.ma20 and result.ma50:
        if price_now > result.ma20 > result.ma50:
            score += 1
            signals.append("Цена > MA20 > MA50 ✅")
        elif price_now < result.ma20 < result.ma50:
            score -= 1
            signals.append("Цена < MA20 < MA50 ⚠️")

    if result.ma200:
        if price_now > result.ma200:
            score += 1
            signals.append(f"Выше MA200 ({result.ma200:.1f}) ✅")
        else:
            score -= 1
            signals.append(f"Ниже MA200 ({result.ma200:.1f}) ⚠️")

    # Golden/Death cross
    if result.ma50 and result.ma200:
        if result.ma50 > result.ma200:
            score += 1
            signals.append("Золотой крест MA50>MA200 ✅")
        else:
            score -= 1
            signals.append("Крест смерти MA50<MA200 ⚠️")

    result.score = max(-5, min(5, score))
    result.signals = signals
    return result
