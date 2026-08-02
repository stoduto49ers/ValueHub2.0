"""
betnacional.py — Coletor da Betnacional (NSX / bet6).

A Betnacional usa a plataforma NSX (via bet6.com.br). Duas fases:

  1) LISTA  (`odds/1/events-by-seasons?sport_id=X`) — só o mercado principal,
     mas traz todos os jogos do esporte (home/away/horário). 1 chamada/esporte.
  2) DETALHE (`event-odds/{event_id}`) — TODOS os mercados do jogo (formato
     achatado: `events` com os times + `odds` com as cotações). Só roda para
     jogos já casados com a Pinnacle (ver valuefinder.run).

Mercados canônicos (jogo inteiro):
  - ML      = Resultado da partida (`|Win-Draw-Win|`, `|Match Betting 3-Way|`)
  - Totals  = Total de gols/pontos over/under (`Total`, `|Total Goals|`,
              `|Alternate Total Goals|`)
  - Spread  = handicap de 2 VIAS (`|2-Way Handicap Betting|`, asiático/linha de
              corrida) — casa com o Spread da Pinnacle. O handicap EUROPEU de 3
              vias (`|Handicap Betting|`, com empate) é IGNORADO de propósito:
              não é o mesmo mercado da Pinnacle (regras de push diferentes).

Nas odds do detalhe os times vêm NO array `events` (não em cada odd), e a linha
vem no `outcome_name`/`specifier` (decimal com vírgula ou ponto).
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from .. import config

log = logging.getLogger("valuehub.betnacional")

_BFF = "https://prod-global-bff-events.bet6.com.br/api"

# A NSX/bet6 devolve o horário em LOCAL do Brasil (UTC-3), SEM timezone
# ('2026-07-29 19:30:00'). Se não convertermos, o casamento por horário erra em
# 3h e NENHUM jogo casa com a Pinnacle (que vem em UTC). Convertemos p/ UTC ISO.
_BR_TZ = timezone(timedelta(hours=-3))


def _to_utc_iso(date_start) -> str | None:
    """'2026-07-29 19:30:00' (local BR) -> '2026-07-29T22:30:00Z' (UTC)."""
    if not date_start:
        return date_start
    s = str(date_start)
    try:
        if "T" not in s and " " in s:          # naive local BR
            dt = datetime.fromisoformat(s).replace(tzinfo=_BR_TZ)
        else:                                   # já ISO (com ou sem tz)
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_BR_TZ)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return s

# nosso nome de esporte -> sport_id da NSX
# tênis=5: habilita a coleta de jogos de tênis. Só o Vencedor (ML, sem linha)
# casa contra a Pinnacle — os handicaps/totais genéricos ('Spread'/'Totals') NÃO
# casam, porque no tênis a Pinnacle usa nomes de SET ('Set Handicap'/'Total Sets')
# e a Betnacional não expõe (aqui) os códigos de set p/ mapearmos com segurança.
_SPORT_IDS = {"futebol": 1, "basquete": 2, "beisebol": 3, "mma": 117, "tenis": 5}

# códigos de mercado (jogo inteiro)
_ML_CODES = {"|Win-Draw-Win|", "|Match Betting 3-Way|"}
_ML_NAMES = {"resultado da partida", "vencedor da partida", "aposta 3-way na partida"}
_TOTAL_CODES = {"Total", "|Total Goals|", "|Alternate Total Goals|"}

_DRAW_WORDS = {"empate", "draw", "x", "tie"}
# número assinado em parênteses no fim: 'Time (-1.5)'
_PAREN_NUM = re.compile(r"\(\s*([-+]?\d+(?:[.,]\d+)?)\s*\)\s*$")
_TRAIL_PAREN = re.compile(r"\s*\([-+]?\d+(?:[.,]\d+)?\)\s*$")
# linha assinada no specifier: 'hc=A:-1.5' / 'nhc=H:2'
_SPEC_SIGNED = re.compile(r":\s*([-+]?\d+(?:[.,]\d+)?)")
_MAG = re.compile(r"(\d+(?:[.,]\d+)?)")   # magnitude (totals)


def _f(x):
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def _match_side(oc: str, hn: str, an: str) -> str | None:
    """Lado pelo nome do time (retira a linha em parêntese antes de comparar)."""
    t = _norm(_TRAIL_PAREN.sub("", oc))
    if not t:
        return None
    if hn and (t == hn or t.startswith(hn) or hn.startswith(t)):
        return "home"
    if an and (t == an or t.startswith(an) or an.startswith(t)):
        return "away"
    return None


def _is_2way_hcp(code: str) -> bool:
    c = code.lower()
    if "handicap" in c and ("2-way" in c or "asian" in c):
        return True
    # point spread / run line (basquete, beisebol) — 2 vias sem empate
    return code in {"|Point Spread|", "|Run Line|", "|Spread|"}


def _classify(code: str, name: str, oc: str, spec: str, hn: str, an: str):
    """Devolve (mercado, lado, linha) — (None,_,_) descarta."""
    ocl = _norm(oc)

    # ---- ML (resultado) ----
    if code in _ML_CODES or _norm(name) in _ML_NAMES:
        if ocl in _DRAW_WORDS:
            return "ML", "draw", None
        side = _match_side(oc, hn, an)
        return ("ML", side, None) if side else (None, None, None)

    # ---- Spread (handicap 2 vias) ----
    if _is_2way_hcp(code):
        side = _match_side(oc, hn, an)
        if not side:
            return None, None, None
        m = _PAREN_NUM.search(oc)          # linha assinada no nome
        line = _f(m.group(1)) if m else None
        if line is None and (ms := _SPEC_SIGNED.search(spec)):
            line = _f(ms.group(1))
        return ("Spread", side, line) if line is not None else (None, None, None)

    # ---- Totals (over/under) ----
    if code in _TOTAL_CODES:
        if "mais" in ocl or "over" in ocl or "acima" in ocl:
            side = "over"
        elif "menos" in ocl or "under" in ocl or "abaixo" in ocl:
            side = "under"
        else:
            return None, None, None
        line = None
        if (ms := _MAG.search(oc)):        # magnitude no nome ('Mais de 2,5 gols')
            line = _f(ms.group(1))
        if line is None and (mm := _MAG.search(spec)):
            line = _f(mm.group(1))
        return ("Totals", side, line) if line is not None else (None, None, None)

    return None, None, None


def parse_event_odds(det: dict) -> list[dict]:
    """Converte a resposta de `event-odds/{id}` (events + odds) em linhas
    canônicas. Deduplica (evento, mercado, linha, lado)."""
    ev_map: dict[str, dict] = {}
    for e in det.get("events", []) or []:
        eid = str(e.get("event_id") or e.get("id") or "")
        if eid:
            ev_map[eid] = e

    out, seen = [], set()
    for item in det.get("odds", []) or []:
        odd = _f(item.get("odd"))
        if not odd or odd <= 1.0:
            continue
        eid = str(item.get("event_id") or "")
        ev = ev_map.get(eid)
        if not ev:
            continue
        home = str(ev.get("home") or "")
        away = str(ev.get("away") or "")
        if not home or not away:
            continue
        hn, an = _norm(home), _norm(away)

        code = str(item.get("market_code") or "")
        name = str(item.get("market_name") or "")
        oc = str(item.get("outcome_name") or "")
        spec = str(item.get("specifier") or "")

        market, side, line = _classify(code, name, oc, spec, hn, an)
        if not (market and side):
            continue
        key = (eid, market, round(line, 2) if line is not None else None, side)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "market": market, "line": line, "side": side, "odd": odd,
            "sel_id": item.get("outcome_id"),
            "source": "betnacional", "book": "Betnacional",
            "event_home": home, "event_away": away, "event_id": eid,
            "event_date": _to_utc_iso(ev.get("date_start")),
            "league": ev.get("tournament_name") or "",
            "url": config.BETNACIONAL_BASE,
        })
    return out


class BetnacionalSource:
    name = "betnacional"
    book = "Betnacional"

    def __init__(self):
        import requests
        self.http = requests.Session()
        self.http.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json",
            "Origin": config.BETNACIONAL_BASE,
            "Referer": config.BETNACIONAL_BASE + "/",
        })
        self.last_error = ""
        self.requests_made = 0

    async def _get(self, path: str):
        try:
            url = f"{_BFF}/{path}"
            r = await asyncio.to_thread(self.http.get, url, timeout=15.0)
        except Exception as e:
            self.last_error = f"rede: {e.__class__.__name__}"
            return None
        self.requests_made += 1
        if r.status_code != 200 or not r.text:
            self.last_error = f"Betnacional: HTTP {r.status_code}"
            return None
        try:
            self.last_error = ""
            return r.json()
        except ValueError:
            self.last_error = "Betnacional: JSON inválido"
            return None

    async def collect_listings(self, sport: str = "") -> list[dict]:
        """Fase 1 (barata): lista os jogos do esporte (só participantes +
        horário). Os mercados vêm no detalhe."""
        sid = _SPORT_IDS.get(sport)
        if sid is None:
            return []                      # esporte não mapeado (ex.: esports)
        # filter_time_event = JANELA EM HORAS. Vazio traz só uma janela curta
        # (~113 jogos); com 72h vêm ~991 (todas as ligas/qualifiers) — sem isso
        # a maioria dos jogos que a Pinnacle cobre nem era buscada.
        horizon = getattr(config, "BETANO_HORIZON_HOURS", 72)
        d = await self._get(
            f"odds/1/events-by-seasons?sport_id={sid}&category_id=0"
            f"&tournament_id=&markets=1&filter_time_event={horizon}&provider=ramp")
        if not d:
            return []
        events: dict[str, dict] = {}
        for o in d.get("odds", []) or []:
            eid = str(o.get("event_id") or "")
            home = o.get("home") or ""
            away = o.get("away") or ""
            if not eid or not home or not away or eid in events:
                continue
            events[eid] = {
                "id": eid,
                "participants": [{"name": home}, {"name": away}],
                "startTime": _to_utc_iso(o.get("date_start")),
                "leagueName": o.get("tournament_name") or "",
                "url": config.BETNACIONAL_BASE, "lines": [],
            }
        return list(events.values())

    async def fetch_event_lines(self, event: dict) -> list[dict]:
        """Fase 2 (cara, só p/ casados): todos os mercados de UM jogo."""
        eid = event.get("id")
        if not eid:
            return []
        det = await self._get(
            f"event-odds/{eid}?languageId=1&marketIds=&outcomeIds="
            f"&statusId=0&provider=ramp")
        if not det:
            return []
        return parse_event_odds(det)

    async def close(self):
        self.http.close()
