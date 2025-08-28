from __future__ import annotations
import logging
from typing import List, Dict, Any
from arbitrage_scanner import config
from arbitrage_scanner.utils.http_client import pool

logger = logging.getLogger("adapter.winline")

def _n(s: str) -> str:
    return " ".join((s or "").lower().replace(".", " ").replace("-", " ").split())

async def fetch() -> List[Dict[str, Any]]:
    data = await pool.get_json(config.WINLINE_EVENTS_URL, params={"discipline": "football"}, timeout=config.WINLINE_TIMEOUT)
    if data is None:
        return []

    events = data.get("events") if isinstance(data, dict) else data
    if not isinstance(events, list):
        logger.debug("Winline unexpected payload")
        return []

    out: List[Dict[str, Any]] = []
    for ev in events:
        participants = ev.get("participants") or []
        if len(participants) < 2:
            continue
        home = participants[0].get("name","")
        away = participants[1].get("name","")

        league = (ev.get("tournament") or {}).get("name") or ev.get("league") or None
        if config.LEAGUE_ALLOWLIST:
            lname = (league or "").lower()
            if lname and not any(a in lname for a in config.LEAGUE_ALLOWLIST):
                continue

        odds_map = None
        for m in ev.get("markets") or []:
            mtype = (m.get("type") or "").lower()
            if mtype in ("1x2","match_winner_3way","result"):
                oc = m.get("outcomes") or []
                vals = {}
                for o in oc:
                    nm = (o.get("name") or "").lower()
                    price = o.get("odds") or o.get("price") or o.get("value")
                    if price is None:
                        continue
                    if nm in ("1","home"):
                        vals["1"] = float(price)
                    elif nm in ("x","draw","ничья"):
                        vals["X"] = float(price)
                    elif nm in ("2","away"):
                        vals["2"] = float(price)
                if len(vals) == 3:
                    odds_map = vals
                    break

        if home and away and odds_map:
            out.append({
                "bookmaker": "Winline",
                "home": home,
                "away": away,
                "league": league,
                "market": "1X2",
                "odds": odds_map,
                "match_key": f"{_n(home)}__vs__{_n(away)}",
            })
    return out
