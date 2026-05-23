import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TINKOFF_API_TOKEN = os.getenv("TINKOFF_API_TOKEN", "")

AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

ALERT_RSI_OVERSOLD = float(os.getenv("ALERT_RSI_OVERSOLD", 30))
ALERT_RSI_OVERBOUGHT = float(os.getenv("ALERT_RSI_OVERBOUGHT", 70))
ALERT_PRICE_CHANGE_PCT = float(os.getenv("ALERT_PRICE_CHANGE_PCT", 3.0))
ALERT_VOLUME_SPIKE = float(os.getenv("ALERT_VOLUME_SPIKE", 3.0))

MORNING_BRIEF_TIME = os.getenv("MORNING_BRIEF_TIME", "08:30")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/bot.db")

# Голубые фишки: тикер → (название, сектор)
BLUE_CHIPS: dict[str, dict] = {
    "LKOH": {"name": "Лукойл",        "sector": "oil"},
    "TATN": {"name": "Татнефть",       "sector": "oil"},
    "ROSN": {"name": "Роснефть",       "sector": "oil"},
    "NVTK": {"name": "Новатэк",        "sector": "oil"},
    "GAZP": {"name": "Газпром",        "sector": "oil"},
    "GMKN": {"name": "Норникель",      "sector": "metals"},
    "CHMF": {"name": "Северсталь",     "sector": "metals"},
    "NLMK": {"name": "НЛМК",          "sector": "metals"},
    "MAGN": {"name": "ММК",            "sector": "metals"},
    "PLZL": {"name": "Полюс",          "sector": "metals"},
    "ALRS": {"name": "АЛРОСА",         "sector": "metals"},
    "SBER": {"name": "Сбербанк",       "sector": "finance"},
    "VTBR": {"name": "ВТБ",            "sector": "finance"},
    "MGNT": {"name": "Магнит",         "sector": "consumer"},
    "FIVE": {"name": "X5 Group",       "sector": "consumer"},
    "YNDX": {"name": "Яндекс",         "sector": "tech"},
    "MTSS": {"name": "МТС",            "sector": "tech"},
    "PHOR": {"name": "ФосАгро",        "sector": "chemicals"},
}

# Идеи роста (15-20% капитала)
RISKY_STOCKS: dict[str, dict] = {
    "POSI": {"name": "Позитив",        "sector": "tech"},
    "HEAD": {"name": "HeadHunter",     "sector": "tech"},
    "WUSH": {"name": "Whoosh",         "sector": "tech"},
    "OZON": {"name": "Ozon",           "sector": "tech"},
    "ASTR": {"name": "Астра",          "sector": "tech"},
}

SECTOR_LABELS = {
    "oil":       "🛢 Нефть и газ",
    "metals":    "⚒️ Металлы",
    "finance":   "🏦 Финансы",
    "consumer":  "🛒 Потребит. сектор",
    "tech":      "💻 Технологии",
    "chemicals": "🧪 Удобрения",
}

ALL_TICKERS = list(BLUE_CHIPS.keys()) + list(RISKY_STOCKS.keys())
