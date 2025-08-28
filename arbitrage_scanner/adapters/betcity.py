from __future__ import annotations
import logging
from typing import List, Dict, Any
from arbitrage_scanner import config
from arbitrage_scanner.utils.http_client import pool

logger = logging.getLogger("adapter.betcity")

def _n(s: str) -> str:
    return " ".join((s or "").lower().replace(".", " ").replace("-", " ").split())

async def fetch() -> List[Dict[str, Any]]:
    if not config.BETCITY_EVENTS_URL:
        return []
    data = await pool.get_json(config.BETCITY_EVENTS_URL, timeout=config.BETCITY_TIMEOUT)
    if data is None:
        return []
    events = data.get("events") if isinstance(data, dict) else data
    if not isinstance(events, list):
        return []
    out: List[Dict[str, Any]] = []
    for ev in events:
        home = ev.get("home") or ev.get("team1") or ""
        away = ev.get("away") or ev.get("team2") or ""
        league = ev.get("league") or None
        odds_map = None
        for m in ev.get("markets") or []:
            t = (m.get("type") or "").lower()
            if t in ("1x2","result","match_winner_3way"):
                vals = {}
                for o in m.get("outcomes") or []:
                    nm = (o.get("name") or "").lower()
                    price = o.get("odds") or o.get("price") or o.get("value")
                    if price is None: continue
                    if nm in ("1","home"): vals["1"] = float(price)
                    elif nm in ("x","draw","ничья"): vals["X"] = float(price)
                    elif nm in ("2","away"): vals["2"] = float(price)
                if len(vals) == 3:
                    odds_map = vals
                    break
        if home and away and odds_map:
            out.append({
                "bookmaker": "Betcity",
                "home": home, "away": away, "league": league,
                "market": "1X2", "odds": odds_map, "match_key": f"{_n(home)}__vs__{_n(away)}",
            })
    return out
