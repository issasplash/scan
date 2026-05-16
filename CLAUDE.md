# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

A sports arbitrage ("surebet") scanner for Russian bookmakers. It polls bookmaker APIs every N seconds, finds cross-bookmaker 1X2 football odds where the inverse-odds sum < 1 (guaranteed profit), computes optimal stake splits, and sends Telegram alerts. Results are also exposed via a REST API.

## Running the service

**With Docker (recommended):**
```bash
docker-compose up --build
```

**Directly (requires Python 3.11 and installed deps):**
```bash
pip install -r requirements.txt
python run_server.py
# or: uvicorn arbitrage_scanner.server:app --host 0.0.0.0 --port 8000
```

No test suite or linter is configured. There is no `pytest`, `mypy`, or `ruff` setup in the project.

**API endpoints:**
- `GET /healthz` — config summary and health check
- `GET /arbs` — current detected arbitrage opportunities

## Configuration (all via environment variables)

All settings live in `arbitrage_scanner/config.py` and are read from env vars at startup. A `.env` file is loaded by docker-compose. Key variables:

| Variable | Default | Purpose |
|---|---|---|
| `ROI_THRESHOLD` | `0.02` | Minimum ROI (2%) to report a surebet |
| `SCAN_INTERVAL_SEC` | `25` | Seconds between full scans |
| `ALERT_SUPPRESS_MIN` | `30` | Minutes before re-alerting the same match |
| `BANK_SIZE` | `1000` | Bank size for stake calculations |
| `TELEGRAM_TOKEN` | — | Bot token (alerts disabled if unset) |
| `TELEGRAM_CHAT_ID` | — | Chat/channel ID for alerts |
| `TELEGRAM_PROXY` | — | Proxy for Telegram requests specifically |
| `USE_PROXIES` | `false` | Enable proxy rotation for bookmaker requests |
| `PROXY_LIST` | — | Comma-separated proxy URLs |
| `PROXY_ROTATION` | `round_robin` | `round_robin` or `random` |
| `LEAGUE_ALLOWLIST` | — | Comma-separated league name substrings to filter (empty = all) |
| `FONBET_HOSTS` | two default hosts | Comma-separated Fonbet API base URLs |
| `OLIMPBET_EVENTS_URL` | — | Optional; adapter is disabled if empty |
| `BETCITY_EVENTS_URL` | — | Optional; adapter is disabled if empty |

## Architecture

### Data flow

```
server._loop()  →  detector.scan_once()
                        ↓
              [fonbet, winline, olimpbet, betcity].fetch()
                        ↓
              pool.get_json()   (HttpPool singleton)
                        ↓
              group events by normalized match key
                        ↓
              find best odds per outcome across bookmakers
                        ↓
              calc ROI = 1 - (1/o1 + 1/oX + 1/o2)
                        ↓
              if ROI ≥ threshold → stake split + Telegram alert
                        ↓
              return arbs list → stored in server._last_arbs
```

### Key modules

**`arbitrage_scanner/config.py`** — single source of truth for all settings; imported by every other module directly (no dependency injection). Changing a config value means touching this file and the corresponding env var.

**`arbitrage_scanner/utils/http_client.py`** — the `HttpPool` singleton (`pool`) used by all adapters. Handles:
- In-memory response cache keyed by `(url, sorted_params)` with TTL
- Sliding-window rate limiter (global, across all hosts)
- Per-host minimum interval gate (`HostGate`)
- Proxy rotation from `PROXY_LIST`
- Retry with exponential backoff (`BACKOFF_BASE ** attempt + jitter`)
- Random User-Agent rotation

**`arbitrage_scanner/adapters/`** — one module per bookmaker, each exports `async def fetch() -> List[NormalizedEvent]`. The `NormalizedEvent` shape (defined in `adapters/base.py`) is:
```python
{
    'bookmaker': str,
    'home': str, 'away': str,
    'league': str | None,
    'market': '1X2',
    'odds': {'1': float, 'X': float, '2': float}
}
```

**`arbitrage_scanner/detector.py`** — `scan_once()` orchestrates all adapters, groups events, and computes surebets. Match grouping uses `_pair_key()`: normalize both team names (lowercase, `.`/`-` → space, collapse whitespace), sort them, join with `__vs__`. This is a heuristic — different bookmakers may spell team names differently, causing missed matches.

**`arbitrage_scanner/notifier.py`** — sends Telegram alerts using its own `ProxyRotator` instance (separate from the adapter pool), respecting `TELEGRAM_PROXY` first.

**`arbitrage_scanner/server.py`** — FastAPI app with a single background asyncio task (`_loop`) started at `startup`. Results are stored in module-level `_last_arbs` behind an `asyncio.Lock`. Graceful shutdown closes the HTTP pool.

### Adding a new bookmaker adapter

1. Create `arbitrage_scanner/adapters/<name>.py` with an `async def fetch() -> List[Dict[str, Any]]` that returns `NormalizedEvent` dicts.
2. Import and add it to the `fetcher` tuple in `detector.scan_once()` (`arbitrage_scanner/detector.py:38`).
3. Add any needed URL/timeout config vars to `config.py`.
4. If the URL may be empty/unset, guard with `if not config.XYZ_URL: return []` (see `olimpbet.py` and `betcity.py`).

### Olimpbet and Betcity adapters

These are **heuristic stubs** — they parse generic field names but have not been validated against real API responses. They require `OLIMPBET_EVENTS_URL` / `BETCITY_EVENTS_URL` to be set, and the parsing logic in their `fetch()` functions will likely need adjustment once the actual API payload structure is known.

### Building a Windows .exe

Use GitHub Actions: push to the repo and the workflow in `.github/workflows/` picks it up, or locally:
```bash
pip install pyinstaller==6.6.0
pyinstaller --onefile --name arb-scanner --add-data "arbitrage_scanner;arbitrage_scanner" run_server.py
```
Place `.env` next to the resulting `dist/arb-scanner.exe`.
