# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Правила общения
- Всегда отвечать **на русском языке**
- Перед удалением файлов или веток — спрашивать подтверждение
- Не пушить в `main` без явного разрешения
- Разработка ведётся в feature-ветках

## Проект
Telegram-бот для анализа российского фондового рынка (Московская биржа).  
Инвестиционный помощник для личного использования.

**Стратегия:**
- 80-85% капитала → голубые фишки (LKOH, SBER, TATN и др.)
- 15-20% → нестандартные идеи (рост, дивиденды, ОФЗ)

## Команды разработки

```bash
# Установка зависимостей
pip install -r requirements.txt

# Первичная загрузка исторических данных (запустить один раз)
python scripts/load_history.py

# Запуск бота локально
python main.py

# Запуск через Docker
docker-compose up --build -d

# Просмотр логов Docker
docker logs -f invest-bot
```

## Архитектура (5-слойный анализ)

```
Слой 1: Макро (analysis/macro.py)      — нефть, рубль, ставка ЦБ, IMOEX тренд
Слой 2: Фундаментал (analysis/fundamental.py) — P/E, дивиденды, долг, отсечки
Слой 3: Техника (analysis/technical.py) — RSI, MACD, MA20/50/200, Bollinger
Слой 4: Защита (analysis/filters.py)   — блокирует покупку при ловушках
Слой 5: ИИ (ai/analyst.py)             — Gemini/OpenAI/Claude формирует текст
```

**Поток данных:**
- `data/moex_client.py` — MOEX ISS API (история свечей, ОФЗ, дивиденды, IMOEX, USD/RUB)
- `data/tinkoff_client.py` — T-Invest API (real-time цены, фундаментал)
- `data/news_fetcher.py` — RSS-агрегатор (RBC, ТАСС, Интерфакс, Smart-lab)
- `db/models.py` — SQLite (price_history, macro_data, users, watchlist, dividends)
- `scheduler/tasks.py` — APScheduler (брифинг 8:30 мск, RSI-алерты каждые 30 мин, обновление истории в 19:00)

**Бот:**
- `bot/handlers/commands.py` — текстовые команды (/start, /analyze, /signals...)
- `bot/handlers/callbacks.py` — inline-кнопки, вся логика экранов
- `bot/keyboards.py` — все клавиатуры

## Ключевые соглашения

- `analysis/signals.py::generate_signal()` — центральная функция, вызывает все слои
- `SignalResult` — результат анализа одной акции, передаётся в ИИ и в форматтер
- `_format_analysis()` в callbacks.py — форматирует ответ для Telegram (HTML)
- Конфиг акций в `config.py`: `BLUE_CHIPS` и `RISKY_STOCKS` (тикер → {name, sector})
- ИИ-провайдер переключается через `.env`: `AI_PROVIDER=gemini|openai|claude`
- История цен хранится в таблице `price_history`, пополняется скриптом и планировщиком

## Добавление новой акции

1. Добавить в `config.py` в `BLUE_CHIPS` или `RISKY_STOCKS`
2. Загрузить историю: `python scripts/load_history.py` (только для новой акции)
3. При необходимости добавить альтернативные названия в `data/news_fetcher.py::_ALL_NAMES`
