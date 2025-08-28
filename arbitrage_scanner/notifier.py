import httpx
import logging
from arbitrage_scanner import config
from arbitrage_scanner.utils.http_client import ProxyRotator

logger = logging.getLogger("notifier")

_rotator = ProxyRotator()

async def send_telegram_message(text: str):
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set; skipping Telegram alert")
        return
    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    proxy = config.TELEGRAM_PROXY or _rotator.pick()
    try:
        async with httpx.AsyncClient(timeout=10, proxies=proxy) as client:
            r = await client.post(url, json=payload)
            if r.status_code != 200:
                logger.error("Telegram send failed: %s %s", r.status_code, r.text)
    except Exception as e:
        logger.exception("Telegram send exception: %s", e)
