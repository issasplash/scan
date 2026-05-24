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


async def _startup_sync():
    """Фоновая синхронизация данных при старте."""
    await asyncio.sleep(5)
    try:
        from scheduler.sync import initial_sync
        await initial_sync()
    except Exception as e:
        logger.warning("Startup sync error: %s", e)


async def main():
    await init_db()
    logger.info("БД инициализирована")

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    # Один коннектор на весь lifetime бота — не создаём новый при каждом запросе
    connector = aiohttp.TCPConnector(ssl=ssl_ctx, force_close=False, enable_cleanup_closed=True)

    class NoVerifySession(AiohttpSession):
        _tg_client: aiohttp.ClientSession | None = None

        async def create_session(self) -> aiohttp.ClientSession:
            if self._tg_client is None or self._tg_client.closed:
                self._tg_client = aiohttp.ClientSession(
                    connector=connector, connector_owner=False,
                )
            return self._tg_client

        async def close(self) -> None:
            if self._tg_client and not self._tg_client.closed:
                await self._tg_client.close()
            await super().close()

    tg_session = NoVerifySession()

    bot = Bot(
        token=TELEGRAM_TOKEN,
        session=tg_session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(commands.router)
    dp.include_router(callbacks.router)

    scheduler = setup_scheduler(bot)
    scheduler.start()
    logger.info("Планировщик запущен")

    asyncio.create_task(_startup_sync())

    logger.info("Бот запущен")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        scheduler.shutdown()
        await bot.session.close()
        await connector.close()


if __name__ == "__main__":
    asyncio.run(main())
