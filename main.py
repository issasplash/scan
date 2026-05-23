import asyncio
import logging
import ssl
import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from config import TELEGRAM_TOKEN
from db.models import init_db
from bot.handlers import commands, callbacks
from scheduler.tasks import setup_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("main")


async def main():
    await init_db()
    logger.info("БД инициализирована")

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    class NoVerifySession(AiohttpSession):
        async def create_session(self):
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            return aiohttp.ClientSession(connector=connector)

    session = NoVerifySession()

    bot = Bot(
        token=TELEGRAM_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(commands.router)
    dp.include_router(callbacks.router)

    scheduler = setup_scheduler(bot)
    scheduler.start()
    logger.info("Планировщик запущен")

    logger.info("Бот запущен")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
