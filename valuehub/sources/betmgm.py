"""
betmgm.py — Coletor da BetMGM Brasil (plataforma Kambi).

A BetMGM roda no motor da Kambi, que expõe uma API JSON REST limpa e fácil de
mapear. Duas fases:

  1) LISTA  (`listView/{sport}.json`) — jogos do esporte (id, times, horário).
  2) DETALHE (`betoffer/event/{id}.json`) — TODOS os mercados do jogo. Só roda
     para jogos já casados com a Pinnacle (ver valuefinder.run).

Formato Kambi: odds e linhas vêm como INTEIRO ×1000 (odds 6750 = 6.75; line
2500 = 2.5). Cada betOffer tem `criterion.label` (nome), `betOfferType.name`
(tipo) e `outcomes[]` com `{label, line, odds, type, participant}`. O lado sai
do `type`: OT_ONE=casa, OT_TWO=fora, OT_CROSS=empate, OT_OVER/OT_UNDER.

Mercados canônicos (jogo inteiro), por betOfferType:
  - "Match"       -> ML
  - "Over/Under"  -> Totals (over/under)
  - "Handicap"    -> Spread (handicap ASIÁTICO 2 vias — casa com a Pinnacle)
  O "3-Way Handicap" (europeu, com empate) é IGNORADO: não é o mesmo mercado.

CONFIG (config.py): o host e o offering do cluster Kambi são parametrizáveis —
o cluster do BRASIL (offering `betmgmbr`) fica atrás de residência de dados e
não responde no host público. Ajuste KAMBI_HOST/KAMBI_OFFERING quando tiver o
host da BR. O coletor funciona idêntico contra qualquer offering Kambi.
"""
from __future__ import annotations

import asyncio
import logging
import unicodedata

from .. import config

log = logging.getLogger("valuehub.betmgm")

# nosso nome de esporte -> termKey de esporte da Kambi
_SPORT_PATHS = {"futebol": "football", "basquete": "basketball",
                "beisebol": "baseball", "esports": "e_sports"}

# betOfferType.name -> mercado canônico
_TYPE_ML = {"match", "moneyline", "1x2"}
_TYPE_TOTAL = {"over/under", "total", "total goals"}
_TYPE_SPREAD = {"handicap", "asian handicap"}

_DRAW_WORDS = {"empate", "draw", "x", "tie"}


def _f_odds(x):
    """Kambi manda odds/linha como inteiro ×1000."""
    try:
        return round(float(x) / 1000.0, 4)
    except (TypeError, ValueError):
        return None


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def _classify(bo: dict) -> str | None:
    t = _norm(bo.get("betOfferType", {}).get("name"))
    lbl = _norm(bo.get("criterion", {}).get("label"))
    if "3-way" in t or "3 way" in t:
        return None                        # handicap europeu — ignorado
    if t in _TYPE_ML:
        return "ML"
    if t in _TYPE_TOTAL:
        return "Totals"
    if t in _TYPE_SPREAD:
        return "Spread"
    # fallback pelo label do critério (alguns offerings variam o type)
    if "over/under" in lbl or "total" in lbl:
        return "Totals"
    if "asian handicap" in lbl or lbl == "handicap":
        return "Spread"
    return None


def _side_line(canon: str, oc: dict, home: str, away: str):
    """Devolve (lado, linha) de um outcome. (None,_) => descarta."""
    typ = str(oc.get("type") or "").upper()
    line = _f_odds(oc.get("line")) if oc.get("line") is not None else None
    if canon == "ML":
        if typ == "OT_ONE":
            return "home", None
        if typ == "OT_TWO":
            return "away", None
        if typ == "OT_CROSS" or _norm(oc.get("label")) in _DRAW_WORDS:
            return "draw", None
        return None, None
    if canon == "Totals":
        if typ == "OT_OVER":
            return ("over", line) if line is not None else (None, None)
        if typ == "OT_UNDER":
            return ("under", line) if line is not None else (None, None)
        return None, None
    if canon == "Spread":
        # handicap 2 vias: linha assinada por lado (já no outcome.line)
        if typ == "OT_ONE" and line is not None:
            return "home", line
        if typ == "OT_TWO" and line is not None:
            return "away", line
        return None, None
    return None, None


def parse_betoffers(det: dict) -> list[dict]:
    """Converte a resposta de `betoffer/event/{id}.json` em linhas canônicas."""
    events = {str(e.get("id")): e for e in det.get("events", []) or []}
    out, seen = [], set()
    for bo in det.get("betOffers", []) or []:
        canon = _classify(bo)
        if not canon:
            continue
        eid = str(bo.get("eventId") or "")
        ev = events.get(eid) or (next(iter(events.values())) if events else None)
        if not ev:
            continue
        home = str(ev.get("homeName") or "")
        away = str(ev.get("awayName") or "")
        if not home or not away:
            continue
        for oc in bo.get("outcomes", []) or []:
            odd = _f_odds(oc.get("odds"))
            if not odd or odd <= 1.0:
                continue
            side, line = _side_line(canon, oc, home, away)
            if not side:
                continue
            key = (canon, round(line, 2) if line is not None else None, side)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "market": canon, "line": line, "side": side, "odd": odd,
                "sel_id": oc.get("id"),
                "source": "betmgm", "book": "BetMGM",
                "event_home": home, "event_away": away, "event_id": eid,
                "event_date": ev.get("start"),
                "league": ev.get("group") or "",
                "url": config.BETMGM_BASE,
            })
    return out


class BetMGMSource:
    name = "betmgm"
    book = "BetMGM"

    def __init__(self):
        import requests
        self.http = requests.Session()
        self.http.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json",
            "Referer": config.BETMGM_BASE + "/",
        })
        self.last_error = ""
        self.requests_made = 0

    def _base(self) -> str:
        return f"{config.KAMBI_HOST}/offering/v2018/{config.KAMBI_OFFERING}"

    def _q(self) -> str:
        return (f"lang={config.KAMBI_LANG}&market={config.KAMBI_MARKET}"
                f"&client_id={config.KAMBI_CLIENT_ID}")

    async def _get(self, url: str):
        try:
            r = await asyncio.to_thread(self.http.get, url, timeout=15.0)
        except Exception as e:
            self.last_error = f"rede: {e.__class__.__name__}"
            return None
        self.requests_made += 1
        if r.status_code != 200 or not r.text:
            self.last_error = f"BetMGM: HTTP {r.status_code}"
            return None
        try:
            self.last_error = ""
            return r.json()
        except ValueError:
            self.last_error = "BetMGM: JSON inválido"
            return None

    async def collect_listings(self, sport: str = "") -> list[dict]:
        """Fase 1 (barata): lista os jogos do esporte."""
        path = _SPORT_PATHS.get(sport)
        if not path:
            return []
        data = await self._get(f"{self._base()}/listView/{path}.json?{self._q()}")
        if not data:
            return []
        events: dict[str, dict] = {}
        for wrap in data.get("events", []) or []:
            ev = wrap.get("event") or wrap
            eid = str(ev.get("id") or "")
            home = ev.get("homeName") or ""
            away = ev.get("awayName") or ""
            if not eid or not home or not away or eid in events:
                continue
            events[eid] = {
                "id": eid,
                "participants": [{"name": home}, {"name": away}],
                "startTime": ev.get("start"),
                "leagueName": ev.get("group") or "",
                "url": config.BETMGM_BASE, "lines": [],
            }
        return list(events.values())

    async def fetch_event_lines(self, event: dict) -> list[dict]:
        """Fase 2 (cara, só p/ casados): todos os mercados de UM jogo."""
        eid = event.get("id")
        if not eid:
            return []
        det = await self._get(
            f"{self._base()}/betoffer/event/{eid}.json?{self._q()}")
        if not det:
            return []
        return parse_betoffers(det)

    async def close(self):
        self.http.close()
