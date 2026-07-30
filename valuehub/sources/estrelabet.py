"""
estrelabet.py — Coletor da EstrelaBet (Altenar).

A EstrelaBet usa o provedor Altenar. Duas fases:

  1) LISTA  (`Widget/GetEvents?sportId=X`) — devolve os jogos do esporte em
     formato achatado (events / competitors). Barato: 1 chamada por esporte.
  2) DETALHE (`Sportsbook/GetEventDetails?eventId=Y`) — traz TODOS os mercados
     do jogo (MarketGroups → Items[mercado] → Items[odd]), incluindo a escada
     completa de Handicap Asiático e Total. Só roda para jogos já casados com a
     Pinnacle (ver valuefinder.run) — por isso o volume de chamadas é baixo.

Mercados canônicos extraídos (jogo inteiro; períodos/quartos/props ignorados):
  - ML      = vencedor do jogo
  - Spread  = Handicap Asiático (linha assinada por lado, igual à Pinnacle)
  - Totals  = Total de gols/pontos (over/under)

Os MarketTypeId do jogo inteiro por esporte (whitelist — evita mercados
derivados):
  Futebol(66)   ML=1     Spread=16  Totals=18
  Basquete(67)  ML=219   Spread=223 Totals=225
  Beisebol(76)  ML=251   Spread=256 Totals=258
  E-sports(145) ML=30001 Spread=327(Hcp de Mapa) Totals=328(Total mapas)
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata

from .. import config

log = logging.getLogger("valuehub.estrelabet")

_ALT_BASE = "https://sb2frontend-altenar2.biahosted.com/api"
_ALT_COMMON = ("culture=pt-BR&timezoneOffset=180&integration=estrelabet"
               "&deviceType=1&numFormat=en-GB&countryCode=BR")

# nome do nosso esporte -> sportId da Altenar. Só estes são buscados; os demais
# (ex.: mma, que a EstrelaBet não lista aqui) devolvem [] no loop do finder.
_SPORT_IDS = {"futebol": 66, "basquete": 67, "beisebol": 76, "esports": 145,
              "tenis": 68}

# whitelist de MarketTypeId do JOGO INTEIRO -> mercado canônico. São universais
# na Altenar (um typeId = o mesmo mercado em todos os esportes), então incluir
# os do tênis não colide com os das outras modalidades.
#   Tênis: 186 Vencedor · 189 Total de jogos. NÃO incluímos o handicap do tênis:
#   o 187 é handicap de GAMES e o 188 de SETS, mas a Pinnacle marca ambos como
#   "Spread" com a mesma linha (±1.5) — casaria games contra sets e daria edge
#   falso. ML (sem linha) e Total de games (mesma unidade) são inequívocos.
_ML_TYPES = {1, 219, 251, 30001, 186}
_SPREAD_TYPES = {16, 223, 256, 327}
_TOTALS_TYPES = {18, 225, 258, 328, 189}

_DRAW_WORDS = {"empate", "draw", "x", "tie"}
# número assinado dentro de parênteses no fim do nome: 'Juventude (+0.5)'
_PAREN_NUM = re.compile(r"\(\s*([-+]?\d+(?:\.\d+)?)\s*\)\s*$")
# parênteses numérico no fim (para retirar a linha e sobrar só o time)
_TRAIL_PAREN = re.compile(r"\s*\([-+]?\d+(?:\.\d+)?\)\s*$")
_NUM = re.compile(r"([-+]?\d+(?:\.\d+)?)")


def _f(x):
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _norm(s: str) -> str:
    """minúsculas, sem acento, espaços colapsados — para casar o time do nome da
    seleção com o nome do competidor (mesma fonte, então basta normalizar)."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def _match_side(team_raw: str, hn: str, an: str) -> str | None:
    """Resolve o lado pelo nome do time (já sem a linha). Nunca inventa lado."""
    t = _norm(team_raw)
    if not t:
        return None
    if hn and (t == hn or t.startswith(hn) or hn.startswith(t)):
        return "home"
    if an and (t == an or t.startswith(an) or an.startswith(t)):
        return "away"
    return None


def _side_line(canon: str, name: str, odd: dict, hn: str, an: str):
    """Devolve (lado, linha) para uma odd do detalhe. (None, _) => descartar."""
    if canon == "ML":
        nn = _norm(name)
        if hn and (nn == hn or nn.startswith(hn) or hn.startswith(nn)):
            return "home", None
        if an and (nn == an or nn.startswith(an) or an.startswith(nn)):
            return "away", None
        if nn in _DRAW_WORDS:
            return "draw", None
        return None, None

    if canon == "Totals":
        low = name.lower()
        if "mais" in low or "over" in low or "acima" in low:
            side = "over"
        elif "menos" in low or "under" in low or "abaixo" in low:
            side = "under"
        else:
            return None, None
        line = _f(odd.get("SPOV"))
        if line is None and (m := _NUM.search(name)):
            line = _f(m.group(1))
        return (side, line) if line is not None else (None, None)

    if canon == "Spread":
        # a linha assinada de CADA lado está no parêntese do nome: 'Time (-1.5)'
        m = _PAREN_NUM.search(name)
        line = _f(m.group(1)) if m else None
        team = _TRAIL_PAREN.sub("", name).strip()
        side = _match_side(team, hn, an)
        if line is None and side:        # fallback: SPOV (ref. do mandante)
            sp = _f(odd.get("SPOV"))
            if sp is not None:
                line = sp if side == "home" else -sp
        return (side, line) if (side and line is not None) else (None, None)

    return None, None


def _classify(mkt: dict) -> str | None:
    tid = int(mkt.get("MarketTypeId") or 0)
    if tid in _ML_TYPES:
        return "ML"
    if tid in _SPREAD_TYPES:
        return "Spread"
    if tid in _TOTALS_TYPES:
        return "Totals"
    return None


def parse_event_details(res: dict) -> list[dict]:
    """Converte o `Result` de GetEventDetails em linhas canônicas (ML/Spread/
    Totals do jogo inteiro). Deduplica (mercado, linha, lado)."""
    comps = res.get("Competitors") or []
    if len(comps) < 2:
        return []
    home = str(comps[0].get("Name") or "")
    away = str(comps[1].get("Name") or "")
    if not home or not away:
        return []
    hn, an = _norm(home), _norm(away)
    ev_id = str(res.get("Id") or "")
    start = res.get("EventDate")
    league = str(res.get("ChampName") or res.get("ChampId") or "")

    out, seen = [], set()
    for g in res.get("MarketGroups") or []:
        for mkt in (g.get("Items") or []):
            canon = _classify(mkt)
            if not canon:
                continue
            for o in (mkt.get("Items") or []):
                if o.get("IsActive") is False:
                    continue
                price = _f(o.get("Price"))
                if not price or price <= 1.0:
                    continue
                side, line = _side_line(canon, str(o.get("Name") or ""), o, hn, an)
                if not side:
                    continue
                key = (canon, round(line, 2) if line is not None else None, side)
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "market": canon, "line": line, "side": side, "odd": price,
                    "source": "estrelabet", "book": "EstrelaBet",
                    "event_home": home, "event_away": away, "event_id": ev_id,
                    "event_date": start, "league": league,
                    "url": config.ESTRELABET_BASE,
                })
    return out


class EstrelaBetSource:
    name = "estrelabet"
    book = "EstrelaBet"

    def __init__(self):
        import requests
        self.http = requests.Session()
        self.http.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json",
        })
        self.last_error = ""
        self.requests_made = 0

    async def _get(self, url: str):
        try:
            r = await asyncio.to_thread(self.http.get, url, timeout=15.0)
        except Exception as e:
            self.last_error = f"rede: {e.__class__.__name__}"
            return None
        self.requests_made += 1
        if r.status_code != 200:
            self.last_error = f"EstrelaBet: HTTP {r.status_code}"
            return None
        try:
            self.last_error = ""
            return r.json()
        except ValueError:
            self.last_error = "EstrelaBet: JSON inválido"
            return None

    async def collect_listings(self, sport: str = "") -> list[dict]:
        """Fase 1 (barata): lista os jogos do esporte. Só participantes +
        horário — os mercados vêm no detalhe (fetch_event_lines)."""
        sid = _SPORT_IDS.get(sport)
        if sid is None:
            return []                      # esporte não coberto pela EstrelaBet
        events: dict[str, dict] = {}
        page = 1
        while page <= 5:
            url = (f"{_ALT_BASE}/Widget/GetEvents?{_ALT_COMMON}"
                   f"&sportId={sid}&period=1&page={page}&eventCount=100")
            data = await self._get(url)
            if not data:
                break
            comp_map = {c["id"]: str(c.get("name") or "")
                        for c in data.get("competitors", [])}
            for ev in data.get("events", []):
                cids = ev.get("competitorIds") or []
                if len(cids) < 2:
                    continue
                home = comp_map.get(cids[0], "")
                away = comp_map.get(cids[1], "")
                if not home or not away:
                    continue
                eid = str(ev.get("id"))
                events[eid] = {
                    "id": eid,
                    "participants": [{"name": home}, {"name": away}],
                    "startTime": ev.get("startDate") or ev.get("startTime"),
                    "leagueName": str(ev.get("champId") or ""),
                    "url": config.ESTRELABET_BASE, "lines": [],
                }
            page_count = int(data.get("pageCount") or 1)
            if page >= page_count:
                break
            page += 1
        return list(events.values())

    async def fetch_event_lines(self, event: dict) -> list[dict]:
        """Fase 2 (cara, só p/ casados): mercados completos de UM jogo."""
        eid = event.get("id")
        if not eid:
            return []
        url = f"{_ALT_BASE}/Sportsbook/GetEventDetails?{_ALT_COMMON}&eventId={eid}"
        data = await self._get(url)
        if not data:
            return []
        res = data.get("Result") if isinstance(data, dict) else None
        if not isinstance(res, dict):
            return []
        return parse_event_details(res)

    async def close(self):
        self.http.close()
