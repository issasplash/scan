import os

# --- Core ---
ROI_THRESHOLD = float(os.getenv("ROI_THRESHOLD", 0.02))         # 2% default
SCAN_INTERVAL_SEC = float(os.getenv("SCAN_INTERVAL_SEC", 25))
ALERT_SUPPRESS_MIN = float(os.getenv("ALERT_SUPPRESS_MIN", 30))
BANK_SIZE = float(os.getenv("BANK_SIZE", 1000.0))               # для расчёта распределения ставок

# --- Telegram ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY")  # http(s):// or socks5://

# --- HTTP / Stability ---
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", 10))
RETRIES = int(os.getenv("RETRIES", 3))
BACKOFF_BASE = float(os.getenv("BACKOFF_BASE", 0.7))
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", 60))

# --- Proxies ---
USE_PROXIES = os.getenv("USE_PROXIES", "false").lower() in ("1","true","yes")
HTTP_PROXY = os.getenv("HTTP_PROXY")
HTTPS_PROXY = os.getenv("HTTPS_PROXY")
PROXY_LIST = [p.strip() for p in os.getenv("PROXY_LIST", "").split(",") if p.strip()]
PROXY_ROTATION = os.getenv("PROXY_ROTATION", "round_robin")     # round_robin | random

# --- Caching ---
CACHE_TTL_SEC = float(os.getenv("CACHE_TTL_SEC", 10))           # TTL для GET-ответов
MIN_INTERVAL_PER_HOST = float(os.getenv("MIN_INTERVAL_PER_HOST", 0.5))  # межзапросный интервал

# --- Adapters config ---
FONBET_HOSTS = os.getenv("FONBET_HOSTS",
    "https://line04w.bkfon-resources.com,https://line06w.bkfon-resources.com").split(",")
FONBET_TIMEOUT = float(os.getenv("FONBET_TIMEOUT", 8))

WINLINE_EVENTS_URL = os.getenv("WINLINE_EVENTS_URL", "https://winline.ru/api/v2/events")
WINLINE_TIMEOUT = float(os.getenv("WINLINE_TIMEOUT", 8))

OLIMPBET_EVENTS_URL = os.getenv("OLIMPBET_EVENTS_URL", "")      # опционально
OLIMPBET_TIMEOUT = float(os.getenv("OLIMPBET_TIMEOUT", 8))

BETCITY_EVENTS_URL = os.getenv("BETCITY_EVENTS_URL", "")        # опционально
BETCITY_TIMEOUT = float(os.getenv("BETCITY_TIMEOUT", 8))

# --- Filters ---
LEAGUE_ALLOWLIST = [s.strip().lower() for s in os.getenv("LEAGUE_ALLOWLIST", "").split(",") if s.strip()]
