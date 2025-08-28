import logging, asyncio, time
from fastapi import FastAPI
from arbitrage_scanner import config
from arbitrage_scanner.detector import scan_once
from arbitrage_scanner.utils.http_client import pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("server")

app = FastAPI(title="Arb Scanner RU (Stable+Proxy+StakeSplit)", version="0.7")

_last_arbs = []
_last_run = 0.0
_lock = asyncio.Lock()

async def _loop():
    global _last_arbs, _last_run
    while True:
        try:
            arbs = await scan_once()
            async with _lock:
                _last_arbs = arbs
                _last_run = time.time()
            logger.info("Scan complete: %d arbs", len(arbs))
        except Exception as e:
            logger.exception("Scan error: %s", e)
        await asyncio.sleep(config.SCAN_INTERVAL_SEC)

@app.on_event("startup")
async def startup():
    asyncio.create_task(_loop())

@app.on_event("shutdown")
async def shutdown():
    await pool.aclose()

@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "interval": config.SCAN_INTERVAL_SEC,
        "roi_threshold": config.ROI_THRESHOLD,
        "bank": config.BANK_SIZE,
        "use_proxies": config.USE_PROXIES,
        "rate_limit_per_min": config.RATE_LIMIT_PER_MIN
    }

@app.get("/arbs")
async def get_arbs():
    async with _lock:
        return {"last_run": _last_run, "total": len(_last_arbs), "items": _last_arbs}
