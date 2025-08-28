from __future__ import annotations
import logging
from typing import List, Dict, Any
from arbitrage_scanner import config
from arbitrage_scanner.utils.http_client import pool

logger = logging.getLogger("adapter.fonbet")

def _n(s: str) -> str:
    return " ".join((s or "").lower().replace(".", " ").replace("-", " ").split())

async def fetch() -> List[Dict[str, Any]]:
    errs = []
    for base in config.FONBET_HOSTS:
        url = base.rstrip("/") + "/line/events"
        data = await pool.get_json(url, params={"lang": "ru"}, timeout=config.FONBET_TIMEOUT)
        if data is None:
            errs.append(f"{url}: no data")
            continue

        events = data if isinstance(data, list) else data.get("events") or data.get("result") or []
        if not isinstance(events, list):
            errs.append(f"{url}: bad format")
            continue

        out: List[Dict[str, Any]] = []
        for ev in events:
            sport_id = ev.get("sportId") or ev.get("sport") or ev.get("sport_id")
            if sport_id not in (1, "soccer", "football", 3):
                continue

            team1 = ev.get("team1") or ev.get("home") or ev.get("team1Name") or ""
            team2 = ev.get("team2") or ev.get("away") or ev.get("team2Name") or ""

            league = ev.get("champName") or ev.get("league") or ev.get("tournamentName") or None
            if config.LEAGUE_ALLOWLIST:
                lname = (league or "").lower()
                if lname and not any(a in lname for a in config.LEAGUE_ALLOWLIST):
                    continue

            odds_map = None
            markets = ev.get("markets") or ev.get("customMarkets") or []
            for m in markets or []:
                mt = (m.get("marketType") or m.get("type") or "").lower()
                if mt in ("1x2", "result", "match_winner_3way"):
                    oc = m.get("outcomes") or m.get("outcome") or []
                    vals = {}
                    for o in oc:
                        k = (o.get("key") or o.get("name") or "").lower()
                        price = o.get("value") or o.get("odds") or o.get("price")
                        if price is None:
                            continue
                        if k in ("1","home","team1"):
                            vals["1"] = float(price)
                        elif k in ("x","draw","ничья"):
                            vals["X"] = float(price)
                        elif k in ("2","away","team2"):
                            vals["2"] = float(price)
                    if len(vals) == 3:
                        odds_map = vals
                        break

            if team1 and team2 and odds_map:
                out.append({
                    "bookmaker": "Fonbet",
                    "home": team1,
                    "away": team2,
                    "league": league,
                    "market": "1X2",
                    "odds": odds_map,
                    "match_key": f"{_n(team1)}__vs__{_n(team2)}",
                })

        if out:
            return out
    if errs:
        logger.debug("Fonbet errors: %s", "; ".join(errs))
    return []
