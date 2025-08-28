from __future__ import annotations
import logging, time
from typing import List, Dict, Any, Tuple
from math import isfinite
from arbitrage_scanner import config, notifier
from arbitrage_scanner.adapters import fonbet, winline, olimpbet, betcity

logger = logging.getLogger("detector")

# anti-spam cache for alerts
_last_alert: Dict[str, float] = {}

def _pair_key(home: str, away: str) -> str:
    def n(s: str) -> str:
        return " ".join(s.lower().replace(".", " ").replace("-", " ").split())
    a, b = sorted([n(home), n(away)])
    return f"{a}__vs__{b}"

def _calc_surebet(best: Dict[str, Tuple[float, str]]) -> Tuple[float, float]:
    s = 0.0
    for k in ("1","X","2"):
        if k not in best:
            return (999.0, -999.0)
        v = best[k][0]
        if not isfinite(v) or v <= 1e-9:
            return (999.0, -999.0)
        s += 1.0 / v
    return s, 1.0 - s

def _stake_split(bank: float, best: Dict[str, Tuple[float, str]]) -> Dict[str, float]:
    inv = {k: 1.0 / best[k][0] for k in ("1","X","2")}
    s = sum(inv.values())
    stakes = {k: bank * inv[k] / s for k in inv}
    return stakes  # суммы на каждый исход

async def scan_once() -> List[Dict[str, Any]]:
    events_all: List[Dict[str, Any]] = []
    for fetcher in (fonbet.fetch, winline.fetch, olimpbet.fetch, betcity.fetch):
        try:
            events_all += await fetcher()
        except Exception as e:
            logger.debug("Adapter error: %s", e)

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events_all:
        key = _pair_key(ev["home"], ev["away"])
        groups.setdefault(key, []).append(ev)

    arbs: List[Dict[str, Any]] = []
    now = time.time()
    suppress_sec = config.ALERT_SUPPRESS_MIN * 60.0

    for key, evs in groups.items():
        best: Dict[str, Tuple[float, str]] = {}
        league = None
        names = None

        for ev in evs:
            league = league or ev.get("league")
            names = names or (ev["home"], ev["away"])
            bk = ev["bookmaker"]
            odds = ev.get("odds", {})
            for outc in ("1","X","2"):
                v = odds.get(outc)
                if v is None:
                    continue
                cur = best.get(outc)
                if cur is None or v > cur[0]:
                    best[outc] = (float(v), bk)

        if len(best) == 3:
            s, roi = _calc_surebet(best)
            if roi >= config.ROI_THRESHOLD:
                stakes = _stake_split(config.BANK_SIZE, best)
                # anti-spam throttle
                last = _last_alert.get(key, 0.0)
                if now - last >= suppress_sec:
                    _last_alert[key] = now
                    home, away = names or ("?", "?")
                    profit = min(stakes["1"]*best["1"][0], stakes["X"]*best["X"][0], stakes["2"]*best["2"][0]) - sum(stakes.values())
                    msg = (
                        f"🚨 Вилка (ROI {roi*100:.2f}%)\\n"
                        f"Матч: {home} — {away}\\n"
                        f"Лига: {league or 'N/A'}\\n"
                        f"Рынок: 1X2\\n\\n"
                        f"1 (П1): {best['1'][0]:.2f} @ {best['1'][1]} — ставка: {stakes['1']:.2f}\\n"
                        f"X (ничья): {best['X'][0]:.2f} @ {best['X'][1]} — ставка: {stakes['X']:.2f}\\n"
                        f"2 (П2): {best['2'][0]:.2f} @ {best['2'][1]} — ставка: {stakes['2']:.2f}\\n\\n"
                        f"Банк: {config.BANK_SIZE:.2f} | Потенц. прибыль: {profit:.2f}"
                    )
                    try:
                        await notifier.send_telegram_message(msg)
                    except Exception as e:
                        logger.warning("Telegram send failed: %s", e)
                arbs.append({
                    "match": f"{names[0]} — {names[1]}",
                    "league": league,
                    "market": "1X2",
                    "roi": roi,
                    "legs": {
                        "1": {"bookmaker": best["1"][1], "odds": best["1"][0], "stake": stakes["1"]},
                        "X": {"bookmaker": best["X"][1], "odds": best["X"][0], "stake": stakes["X"]},
                        "2": {"bookmaker": best["2"][1], "odds": best["2"][0], "stake": stakes["2"]},
                    },
                    "sum_inverse": 1.0 / best["1"][0] + 1.0 / best["X"][0] + 1.0 / best["2"][0],
                    "bank": config.BANK_SIZE,
                })
    return arbs
