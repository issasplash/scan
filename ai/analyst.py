from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone, timedelta
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

_AI_CACHE_TTL_HOURS = 6  # кэш AI-анализа действует 6 часов


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

    lines += ["", "=== НОВОСТИ ==="]
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
        "На основе всех данных выше дай строго структурированный ответ:",
        "1. ВЕРДИКТ (одно из: 🟢 ПОКУПАТЬ / 🟡 ДЕРЖАТЬ / 🔴 ПРОДАВАТЬ / ⏳ ЖДАТЬ)",
        "2. УВЕРЕННОСТЬ (Высокая / Средняя / Низкая)",
        "3. ГОРИЗОНТ (на какой срок рекомендация)",
        "4. ОБЪЯСНЕНИЕ (3-4 предложения — почему именно сейчас)",
        "5. ГЛАВНЫЕ РИСКИ (2-3 пункта кратко)",
        "6. КАТАЛИЗАТОРЫ РОСТА (2-3 пункта кратко)",
    ]

    return "\n".join(l for l in lines if l is not None)


_GEMINI_MODELS = ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-flash-latest"]


async def _gemini_call(prompt: str, model: str) -> str:
    import aiohttp
    import ssl
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={GEMINI_API_KEY}"
    )
    body = {
        "system_instruction": {"parts": [{"text": _SYSTEM}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 1000},
    }
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    conn = aiohttp.TCPConnector(ssl=ssl_ctx, force_close=True)
    try:
        async with aiohttp.ClientSession(connector=conn) as session:
            async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=30)) as r:
                data = await r.json(content_type=None)
    finally:
        await conn.close()

    if "error" in data:
        code = data["error"].get("code", 0)
        msg = data["error"].get("message", "")
        raise RuntimeError(f"Gemini error {code}: {msg}")

    candidates = data.get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        if parts:
            return parts[0].get("text", "").strip()
    raise RuntimeError(f"Gemini ({model}): пустой ответ, data={data}")


async def _gemini(prompt: str) -> str:
    last_err: Exception | None = None
    for model in _GEMINI_MODELS:
        for attempt in range(2):
            if attempt > 0:
                await asyncio.sleep(10)
            try:
                result = await _gemini_call(prompt, model)
                logger.debug("Gemini OK (%s)", model)
                return result
            except RuntimeError as e:
                msg = str(e)
                if "429" in msg:
                    logger.warning("Gemini 429 (%s), жду 20s...", model)
                    await asyncio.sleep(20)
                    continue
                logger.warning("Gemini (%s) попытка %d: %s", model, attempt + 1, msg)
                last_err = e
                break  # этот model не работает, пробуем следующий
            except Exception as e:
                logger.warning("Gemini (%s) сетевая ошибка: %s", model, e)
                last_err = e
                break
    raise RuntimeError(f"Все Gemini модели недоступны. Последняя ошибка: {last_err}")


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


async def _get_cached(ticker: str) -> str | None:
    """Возвращает кэшированный AI-анализ если он свежее TTL часов."""
    try:
        from db.models import SessionLocal, AICache
        from sqlalchemy import select
        cutoff = datetime.now(timezone.utc) - timedelta(hours=_AI_CACHE_TTL_HOURS)
        async with SessionLocal() as db:
            row = await db.execute(
                select(AICache).where(AICache.ticker == ticker)
            )
            cached = row.scalar_one_or_none()
        if cached and cached.created_at >= cutoff.replace(tzinfo=None):
            logger.debug("AI cache hit for %s", ticker)
            return cached.analysis
    except Exception as e:
        logger.debug("AI cache read error: %s", e)
    return None


async def _save_cache(ticker: str, analysis: str):
    try:
        from db.models import SessionLocal, AICache
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        async with SessionLocal() as db:
            stmt = sqlite_insert(AICache).values(
                ticker=ticker,
                analysis=analysis,
                created_at=datetime.utcnow(),
            ).on_conflict_do_update(
                index_elements=["ticker"],
                set_={"analysis": analysis, "created_at": datetime.utcnow()},
            )
            await db.execute(stmt)
            await db.commit()
    except Exception as e:
        logger.debug("AI cache write error: %s", e)


async def get_ai_analysis(result: SignalResult, macro: MacroContext, news: list[dict]) -> str:
    """Отправляет контекст в ИИ и возвращает структурированный анализ."""
    # Проверяем кэш
    cached = await _get_cached(result.ticker)
    if cached:
        return cached

    prompt = _build_prompt(result, macro, news)

    try:
        if AI_PROVIDER == "gemini" and GEMINI_API_KEY:
            text = await _gemini(prompt)
        elif AI_PROVIDER == "openai" and OPENAI_API_KEY:
            text = await _openai(prompt)
        elif AI_PROVIDER == "claude" and ANTHROPIC_API_KEY:
            text = await _claude(prompt)
        else:
            logger.warning("AI provider не настроен")
            return ""

        await _save_cache(result.ticker, text)
        return text
    except Exception as e:
        logger.error("AI analysis error (%s): %s", AI_PROVIDER, e)
        return ""  # пустая строка — секция ИИ просто не показывается
