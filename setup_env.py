import sys

print("=== Настройка .env файла ===")
print("Введи каждый токен и нажми Enter\n")

telegram = input("TELEGRAM_TOKEN: ").strip()
tinkoff = input("TINKOFF_API_TOKEN (Enter чтобы пропустить): ").strip()
gemini = input("GEMINI_API_KEY (Enter чтобы пропустить): ").strip()

lines = [
    f"TELEGRAM_TOKEN={telegram}",
    f"TINKOFF_API_TOKEN={tinkoff}",
    f"GEMINI_API_KEY={gemini}",
    "AI_PROVIDER=gemini",
    "ALERT_RSI_OVERSOLD=30",
    "ALERT_RSI_OVERBOUGHT=70",
    "ALERT_PRICE_CHANGE_PCT=3.0",
    "ALERT_VOLUME_SPIKE=3.0",
    "MORNING_BRIEF_TIME=08:30",
    "DATABASE_URL=sqlite+aiosqlite:///./data/bot.db",
]

with open(".env", "w") as f:
    f.write("\n".join(lines) + "\n")

print("\n.env создан успешно!")
