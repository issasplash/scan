from __future__ import annotations
import asyncio, random, time, logging
from typing import Optional, Dict, Any, List, Tuple
import httpx
from urllib.parse import urlparse
from arbitrage_scanner import config

logger = logging.getLogger("http_pool")

_UA = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
]

class RateLimiter:
    def __init__(self, per_min: int):
        self.capacity = max(1, per_min)
        self.window = 60.0
        self.ts: List[float] = []

    async def acquire(self):
        now = time.time()
        self.ts = [t for t in self.ts if now - t < self.window]
        if len(self.ts) >= self.capacity:
            sleep_for = self.window - (now - self.ts[0])
            await asyncio.sleep(max(0.01, sleep_for))
        self.ts.append(time.time())

class HostGate:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self.last: Dict[str, float] = {}

    async def wait(self, url: str):
        host = urlparse(url).netloc
        now = time.time()
        prev = self.last.get(host, 0.0)
        delta = now - prev
        if delta < self.min_interval:
            await asyncio.sleep(self.min_interval - delta)
        self.last[host] = time.time()

class ProxyRotator:
    def __init__(self):
        self.use = config.USE_PROXIES
        self.items = config.PROXY_LIST[:]
        self.mode = config.PROXY_ROTATION
        self.idx = 0

    def pick(self) -> Optional[str]:
        if not self.use or not self.items:
            return None
        if self.mode == "random":
            return random.choice(self.items)
        p = self.items[self.idx % len(self.items)]
        self.idx += 1
        return p

class Cache:
    def __init__(self, ttl: float):
        self.ttl = ttl
        self.store: Dict[Tuple[str, Tuple[Tuple[str,str], ...]], Tuple[float, Any]] = {}

    def _key(self, url: str, params: Dict[str, Any] | None) -> Tuple[str, Tuple[Tuple[str,str], ...]]:
        pairs = tuple(sorted((str(k), str(v)) for k, v in (params or {}).items()))
        return (url, pairs)

    def get(self, url: str, params: Dict[str, Any] | None):
        k = self._key(url, params)
        v = self.store.get(k)
        if not v: return None
        ts, data = v
        if time.time() - ts <= self.ttl:
            return data
        self.store.pop(k, None)
        return None

    def set(self, url: str, params: Dict[str, Any] | None, data: Any):
        self.store[self._key(url, params)] = (time.time(), data)

class HttpPool:
    def __init__(self):
        self._clients: Dict[str, httpx.AsyncClient] = {}
        self._limiter = RateLimiter(config.RATE_LIMIT_PER_MIN)
        self._rotator = ProxyRotator()
        self._cache = Cache(config.CACHE_TTL_SEC)
        self._gate = HostGate(config.MIN_INTERVAL_PER_HOST)

    async def get_json(self, url: str, *, params: Dict[str, Any] | None=None, timeout: float | None=None) -> Any:
        cached = self._cache.get(url, params)
        if cached is not None:
            return cached

        await self._limiter.acquire()
        await self._gate.wait(url)
        headers = {
            "User-Agent": random.choice(_UA),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        t = timeout or config.REQUEST_TIMEOUT
        attempt = 0
        last_exc: Exception | None = None
        while attempt < config.RETRIES:
            proxy = self._rotator.pick() or (config.HTTPS_PROXY or config.HTTP_PROXY)
            key = proxy or "direct"
            client = self._clients.get(key)
            if client is None:
                self._clients[key] = client = httpx.AsyncClient(
                    timeout=t,
                    proxies=proxy,
                    headers=headers,
                    verify=True,
                )
            try:
                r = await client.get(url, params=params)
                r.raise_for_status()
                ct = r.headers.get("Content-Type","")
                data = None
                if "application/json" in ct or r.text.strip().startswith(("{","[")):
                    data = r.json()
                else:
                    try:
                        data = r.json()
                    except Exception:
                        logger.warning("Non-JSON response from %s (ct=%s)", url, ct)
                        data = None
                if data is not None:
                    self._cache.set(url, params, data)
                return data
            except Exception as e:
                last_exc = e
                backoff = (config.BACKOFF_BASE ** attempt) + random.uniform(0.05, 0.25)
                logger.debug("GET %s failed (attempt %d/%d): %s; retry in %.2fs", url, attempt+1, config.RETRIES, e, backoff)
                await asyncio.sleep(backoff)
                attempt += 1
        logger.warning("GET %s failed after %d retries: %s", url, config.RETRIES, last_exc)
        return None

    async def aclose(self):
        for c in self._clients.values():
            try:
                await c.aclose()
            except Exception:
                pass
        self._clients.clear()

pool = HttpPool()
