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


_SECTOR_PE_NORM = {
    "oil": 6.0, "metals": 7.0, "finance": 5.0,
    "consumer": 12.0, "tech": 20.0, "chemicals": 8.0,
}

_SECTOR_NAME_RU = {
    "oil": "нефть и газ", "metals": "металлы", "finance": "финансы",
    "consumer": "потребительский", "tech": "технологии", "chemicals": "химия",
}


def _build_prompt(result: SignalResult, macro: MacroContext, news: list[dict]) -> str:
    from config import BLUE_CHIPS, RISKY_STOCKS
    t = result.tech
    f = result.fund

    all_stocks = {**BLUE_CHIPS, **RISKY_STOCKS}
    sector = all_stocks.get(result.ticker, {}).get("sector", "unknown")

    # Техника
    tech_parts = []
    if t.rsi is not None:
        rsi_comment = "перепродано ✅" if t.rsi < 35 else ("перекуплено ⚠️" if t.rsi > 65 else "нейтрально")
        tech_parts.append(f"RSI={t.rsi:.0f} ({rsi_comment})")
    if t.price_change_30d is not None:
        tech_parts.append(f"изм.за30д={t.price_change_30d:+.1f}%")
    if t.ma50 and t.ma200:
        trend = "восходящий ✅" if t.ma50 > t.ma200 else "нисходящий ⚠️"
        tech_parts.append(f"тренд MA50/MA200={trend}")
    if t.support and t.resistance and result.price:
        rng = t.resistance - t.support
        if rng > 0:
            pos_pct = (result.price - t.support) / rng * 100
            tech_parts.append(f"позиция в диапазоне={pos_pct:.0f}% (0%=поддержка, 100%=сопротивление)")
    if t.bb_lower and t.bb_upper and result.price:
        if result.price <= t.bb_lower * 1.02:
            tech_parts.append("цена у нижней полосы BB — статистически дёшево ✅")
        elif result.price >= t.bb_upper * 0.98:
            tech_parts.append("цена у верхней полосы BB — статистически дорого ⚠️")

    # Фундаментал
    fund_parts = []
    pe_norm = _SECTOR_PE_NORM.get(sector)
    if f.pe and f.pe > 0:
        if pe_norm:
            discount = (pe_norm - f.pe) / pe_norm * 100
            if discount > 0:
                fund_parts.append(f"P/E={f.pe:.1f} (норма сектора {pe_norm:.0f} — дисконт {discount:.0f}% ✅)")
            else:
                fund_parts.append(f"P/E={f.pe:.1f} (норма сектора {pe_norm:.0f} — премия {-discount:.0f}% ⚠️)")
        else:
            fund_parts.append(f"P/E={f.pe:.1f}")
    if f.pb:
        fund_parts.append(f"P/B={f.pb:.1f}")
    if f.div_yield:
        fund_parts.append(f"дивдоходность={f.div_yield:.1f}%")
    if f.debt_ebitda:
        lev = "✅ низкий" if f.debt_ebitda < 1.5 else ("⚠️ высокий" if f.debt_ebitda > 3.0 else "умеренный")
        fund_parts.append(f"долг/EBITDA={f.debt_ebitda:.1f} ({lev})")
    if f.revenue_growth:
        fund_parts.append(f"рост выручки={f.revenue_growth:+.0f}%")
    if f.next_ex_date:
        from datetime import date
        days = (f.next_ex_date - date.today()).days
        fund_parts.append(f"отсечка через {days} дн. ({f.next_ex_date})")
    elif f.days_since_exdate and f.days_since_exdate < 30:
        fund_parts.append(f"отсечка была {f.days_since_exdate} дн. назад — акция может быть слабее ⚠️")

    # Макро
    macro_parts = []
    if macro.brent:
        macro_parts.append(f"Brent=${macro.brent:.1f}")
    if macro.usd_rub:
        macro_parts.append(f"USD/RUB={macro.usd_rub:.1f}")
    if macro.cbr_rate:
        macro_parts.append(f"ставка_ЦБ={macro.cbr_rate}%")
    if macro.imoex:
        macro_parts.append(f"IMOEX={macro.imoex:.0f} режим={macro.market_regime}")

    # Защитные фильтры
    filters_str = ""
    if result.filters.blocked:
        filters_str = f"\nФИЛЬТРЫ-СТОП (не покупать!): {'; '.join(result.filters.warnings)}"
    elif result.filters.warnings:
        filters_str = f"\nПРЕДУПРЕЖДЕНИЯ: {'; '.join(result.filters.warnings)}"

    # Новости с инструкцией для анализа
    if news:
        news_lines = []
        for n in news[:5]:
            news_lines.append(f"• {n['title']}")
        news_ctx = "\n".join(news_lines)
        news_instruction = "Для каждой новости кратко укажи: позитив/негатив/нейтрально для акции и почему."
    else:
        news_ctx = "нет свежих новостей"
        news_instruction = ""

    price_str = f"{result.price:,.0f} ₽" if result.price else "н/д"
    sector_ru = _SECTOR_NAME_RU.get(sector, sector)

    # Уровни входа только для BUY/WAIT
    levels_line = ""
    if result.price and result.signal in ("BUY", "WAIT"):
        levels_line = f"\n💰 УРОВНИ: Купить от {result.price*0.95:,.0f}₽, стоп {result.price*0.88:,.0f}₽, цель {result.price*1.15:,.0f}₽"

    prompt = f"""Ты — аналитик российского фондового рынка ({sector_ru}). Инвестор ждёт конкретный ответ: покупать сейчас или нет.

АКЦИЯ: {result.name} ({result.ticker}), сектор: {sector_ru}, цена {price_str}
СКОРИНГ: {result.total_score:+d}/±9 | предв.сигнал={result.signal} | уверенность={result.confidence}

ТЕХНИКА: {' | '.join(tech_parts) if tech_parts else 'недостаточно данных'}
ФУНДАМЕНТАЛ: {' | '.join(fund_parts) if fund_parts else 'нет данных'}
МАКРО: {' | '.join(macro_parts) if macro_parts else 'нет данных'}{filters_str}

НОВОСТИ (последние):
{news_ctx}
{news_instruction}

Дай структурированный ответ СТРОГО в таком формате (не добавляй ничего лишнего):

🎯 ВЫВОД: [ПОКУПАТЬ СЕЙЧАС / ПОДОЖДАТЬ / ДЕРЖАТЬ / ПРОДАВАТЬ] | Горизонт: [1-3 мес / 3-6 мес / 6+ мес]

💡 ПОЧЕМУ: [2-3 предложения. Объясни главные причины конкретными цифрами. Упомяни если новости важны для решения.]{levels_line}

⚠️ РИСКИ: [ровно 2 конкретных риска через " ; "]

🚀 КАТАЛИЗАТОРЫ: [ровно 2 конкретных катализатора через " ; "]

Отвечай на русском. Максимум 6 предложений суммарно. Инвестор принимает реальное решение."""

    return prompt


_GEMINI_MODELS = ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-flash"]


async def _gemini_call(prompt: str, model: str) -> str:
    import aiohttp
    import ssl
    # v1beta для flash-lite, v1 для основных моделей
    api_ver = "v1beta" if "lite" in model else "v1"
    url = (
        f"https://generativelanguage.googleapis.com/{api_ver}/models/"
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
