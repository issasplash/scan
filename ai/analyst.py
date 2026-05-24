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

    # Собираем контекст компактно
    tech_ctx = []
    if t.rsi:
        tech_ctx.append(f"RSI={t.rsi:.0f}")
    if t.price_change_30d is not None:
        tech_ctx.append(f"изм.30д={t.price_change_30d:+.1f}%")
    if t.ma50 and t.ma200:
        tech_ctx.append(f"MA50={t.ma50:.0f}/MA200={t.ma200:.0f}")
    if t.support and t.resistance and result.price:
        tech_ctx.append(f"поддержка={t.support:.0f}/сопр={t.resistance:.0f}")
    if t.signals:
        tech_ctx.append("Сигналы: " + "; ".join(t.signals[:3]))

    fund_ctx = []
    if f.pe:
        fund_ctx.append(f"P/E={f.pe:.1f}")
    if f.pb:
        fund_ctx.append(f"P/B={f.pb:.1f}")
    if f.ev_ebitda:
        fund_ctx.append(f"EV/EBITDA={f.ev_ebitda:.1f}")
    if f.div_yield:
        fund_ctx.append(f"дивдоходность={f.div_yield:.1f}%")
    if f.debt_ebitda:
        fund_ctx.append(f"долг/EBITDA={f.debt_ebitda:.1f}")
    if f.revenue_growth:
        fund_ctx.append(f"рост выручки={f.revenue_growth:+.0f}%")
    if f.next_ex_date:
        fund_ctx.append(f"отсечка={f.next_ex_date}")
    if f.last_dividend:
        fund_ctx.append(f"посл.дивиденд={f.last_dividend}₽")
    if f.signals:
        fund_ctx.append("Сигналы: " + "; ".join(f.signals[:2]))

    macro_ctx = []
    if macro.brent:
        macro_ctx.append(f"Brent=${macro.brent:.1f}")
    if macro.usd_rub:
        macro_ctx.append(f"USD/RUB={macro.usd_rub:.1f}")
    if macro.cbr_rate:
        macro_ctx.append(f"ставка_ЦБ={macro.cbr_rate}%")
    if macro.imoex:
        macro_ctx.append(f"IMOEX={macro.imoex:.0f}[{macro.market_regime}]")

    filters_ctx = ""
    if result.filters.blocked:
        filters_ctx = "⚠️ СТОП: " + "; ".join(result.filters.warnings)
    elif result.filters.warnings:
        filters_ctx = "Предупреждения: " + "; ".join(result.filters.warnings)

    news_ctx = "\n".join(f"• {n['title']}" for n in news[:5]) if news else "нет свежих новостей"

    price_str = f"{result.price:.2f} ₽" if result.price else "н/д"

    prompt = f"""Ты — аналитик российского фондового рынка. Дай профессиональный инвестиционный разбор.

АКЦИЯ: {result.name} ({result.ticker}), цена {price_str}
СИСТЕМА: score {result.total_score:+d}/±9, предв.сигнал={result.signal}, уверенность={result.confidence}

ТЕХНИКА: {' | '.join(tech_ctx) if tech_ctx else 'нет данных'}
ФУНДАМЕНТАЛ: {' | '.join(fund_ctx) if fund_ctx else 'нет данных'}
МАКРО: {' | '.join(macro_ctx) if macro_ctx else 'нет данных'}
{('ФИЛЬТРЫ: ' + filters_ctx) if filters_ctx else ''}

НОВОСТИ:
{news_ctx}

Дай краткий (5-7 предложений СУММАРНО) структурированный ответ строго в таком формате:

🎯 ВЫВОД: [одно из: ПОКУПАТЬ / ДЕРЖАТЬ / ПРОДАВАТЬ / ЖДАТЬ] | Горизонт: [1-3 мес / 3-6 мес / 6+ мес]

💡 ПОЧЕМУ: [2-3 предложения — главные причины рекомендации, конкретно и честно]

⚠️ РИСКИ: [2 конкретных риска одной строкой через ;]

🚀 КАТАЛИЗАТОРЫ: [2 конкретных катализатора одной строкой через ;]

{'💰 УРОВНИ: Покупать от ' + f'{result.price*0.95:,.0f}' + '₽, стоп ' + f'{result.price*0.88:,.0f}' + '₽, цель ' + f'{result.price*1.15:,.0f}' + '₽' if result.price and result.signal in ('BUY', 'WAIT') else ''}

Отвечай на русском. Будь конкретным — инвестор принимает реальное решение."""

    return prompt


_GEMINI_MODELS = ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-1.5-flash-latest"]


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
