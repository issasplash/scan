from __future__ import annotations
import logging
from config import AI_PROVIDER, GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY
from analysis.signals import SignalResult
from analysis.macro import MacroContext

logger = logging.getLogger("ai")

_SYSTEM = (
    "Ты — профессиональный аналитик российского фондового рынка. "
    "Отвечай на русском языке. Будь конкретным и честным. "
    "Не давай обещаний гарантированной прибыли. "
    "Структурируй ответ чётко по запрошенным пунктам."
)


def _build_prompt(result: SignalResult, macro: MacroContext, news: list[dict]) -> str:
    t = result.tech
    f = result.fund
    lines = [
        f"Акция: {result.name} ({result.ticker})",
        f"Текущая цена: {result.price:.2f} ₽" if result.price else "Цена: н/д",
        "",
        "=== ТЕХНИЧЕСКИЙ АНАЛИЗ ===",
        f"RSI(14): {t.rsi:.1f}" if t.rsi else "RSI: н/д",
        f"MACD: {t.macd:.2f}, Signal: {t.macd_signal:.2f}" if t.macd else "MACD: н/д",
        f"MA20={t.ma20:.1f}, MA50={t.ma50:.1f}, MA200={t.ma200:.1f}" if t.ma200 else "",
        f"Изменение за 30 дней: {t.price_change_30d:+.1f}%" if t.price_change_30d else "",
        f"Технические сигналы: {'; '.join(t.signals)}" if t.signals else "",
        f"Технический score: {t.score} из ±5",
        "",
        "=== ФУНДАМЕНТАЛ ===",
        f"P/E: {f.pe:.1f}" if f.pe else "P/E: н/д",
        f"P/B: {f.pb:.1f}" if f.pb else "",
        f"EV/EBITDA: {f.ev_ebitda:.1f}" if f.ev_ebitda else "",
        f"Дивдоходность: {f.div_yield:.1f}%" if f.div_yield else "",
        f"Долг/EBITDA: {f.debt_ebitda:.1f}" if f.debt_ebitda else "",
        f"Рост выручки: {f.revenue_growth:+.0f}%" if f.revenue_growth else "",
        f"Ближайшая отсечка: {f.next_ex_date}" if f.next_ex_date else "",
        f"Последний дивиденд: {f.last_dividend} ₽" if f.last_dividend else "",
        f"Фундаментальный score: {f.score} из ±4",
        "",
        "=== МАКРО ===",
        f"Нефть Brent: ${macro.brent:.1f}" if macro.brent else "",
        f"USD/RUB: {macro.usd_rub:.1f}" if macro.usd_rub else "",
        f"Ставка ЦБ: {macro.cbr_rate}%" if macro.cbr_rate else "",
        f"IMOEX: {macro.imoex:.0f}, режим рынка: {macro.market_regime}" if macro.imoex else "",
        "",
        "=== ЗАЩИТНЫЕ ФИЛЬТРЫ ===",
        f"Заблокировано: {'ДА — не покупать сейчас' if result.filters.blocked else 'НЕТ'}",
    ]
    if result.filters.warnings:
        lines.append("Предупреждения: " + "; ".join(result.filters.warnings))

    lines += [
        "",
        "=== НОВОСТИ ===",
    ]
    if news:
        for n in news[:4]:
            lines.append(f"• {n['title']}")
    else:
        lines.append("Свежих новостей не найдено")

    lines += [
        "",
        f"=== ИТОГОВЫЙ SCORE: {result.total_score} из ±9 ===",
        f"Предварительный сигнал: {result.signal}, уверенность: {result.confidence}",
        "",
        "На основе всех данных выше дай мне строго структурированный ответ:",
        "1. ВЕРДИКТ (одно из: 🟢 ПОКУПАТЬ / 🟡 ДЕРЖАТЬ / 🔴 ПРОДАВАТЬ / ⏳ ЖДАТЬ)",
        "2. УВЕРЕННОСТЬ (Высокая / Средняя / Низкая)",
        "3. ГОРИЗОНТ (на какой срок рекомендация)",
        "4. ОБЪЯСНЕНИЕ (3-4 предложения — почему именно сейчас)",
        "5. ГЛАВНЫЕ РИСКИ (2-3 пункта кратко)",
        "6. КАТАЛИЗАТОРЫ РОСТА (2-3 пункта кратко)",
    ]

    return "\n".join(l for l in lines if l is not None)


async def _gemini(prompt: str) -> str:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = await client.aio.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM,
            max_output_tokens=1000,
        ),
    )
    return response.text.strip()


async def _openai(prompt: str) -> str:
    import openai
    client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)
    r = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        max_tokens=800,
    )
    return r.choices[0].message.content.strip()


async def _claude(prompt: str) -> str:
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    msg = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


async def get_ai_analysis(result: SignalResult, macro: MacroContext, news: list[dict]) -> str:
    """Отправляет контекст в ИИ и возвращает структурированный анализ."""
    prompt = _build_prompt(result, macro, news)

    try:
        if AI_PROVIDER == "gemini" and GEMINI_API_KEY:
            return await _gemini(prompt)
        elif AI_PROVIDER == "openai" and OPENAI_API_KEY:
            return await _openai(prompt)
        elif AI_PROVIDER == "claude" and ANTHROPIC_API_KEY:
            return await _claude(prompt)
        else:
            logger.warning("AI provider не настроен, пропускаем ИИ-анализ")
            return ""
    except Exception as e:
        logger.error("AI analysis error: %s", e)
        return f"(ИИ-анализ недоступен: {e})"
