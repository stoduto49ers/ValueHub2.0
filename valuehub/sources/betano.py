"""
betano.py — Coletor da Betano BR (casa-alvo, onde as apostas são feitas).

A Betano roda na plataforma Kaizen Gaming, que expõe a API do site em
`/api/...` devolvendo JSON. Sem login, sem chave — os mesmos dados que a
página mostra. Preços já vêm em DECIMAL.

Dois níveis de coleta, por eficiência:
  1. listagem da liga  -> todos os jogos + mercado principal (1X2 = ML)
     custo: 1 request por liga
  2. detalhe do evento -> demais mercados (Total de Gols = Totals)
     custo: 1 request por jogo — só vale a pena para jogos que CASARAM
     com a referência sharp, então quem chama decide.

Mapeamento para o vocabulário canônico do HUB:
    MRES  '1' / 'X' / '2'                  -> ML     home / draw / away
    HCTG  'Mais de 2.5' / 'Menos de 2.5'   -> Totals over / under  (linha 2.5)
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

import httpx

from .. import config

log = logging.getLogger("valuehub.betano")

# 'Mais de 2.5' / 'Menos de 2.5' (a linha também vem no campo handicap)
_OVER_RE = re.compile(r"^\s*mais\s+de\s+([\d.,-]+)", re.I)
_UNDER_RE = re.compile(r"^\s*menos\s+de\s+([\d.,-]+)", re.I)
# 'Atlético-MG -0.25' / 'Bahia +0.25' — nome do time seguido da linha
_HCP_RE = re.compile(r"^(?P<time>.+?)\s*(?P<linha>[-+]?\d+(?:\.\d+)?)\s*$")


def _norm_team(s: str) -> str:
    import unicodedata
    nfkd = unicodedata.normalize("NFD", str(s or "").lower())
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn").strip()


def _f(x):
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return None


# Mapa dos mercados da Betano para o vocabulário canônico do HUB.
# 'ou'  = over/under  ('Mais de X' / 'Menos de X')
# '3way'= resultado   (colunas 1 / X / 2, na ordem home / draw / away)
# 'hcp' = handicap    (nome do time + linha: 'Atlético-MG -0.25')
#
# Os mercados marcados "profundo" só chegam com ?bt=13 na URL (a aba "Todos").
# O HANDICAP ASIÁTICO (AHRF) é o equivalente exato do `spread` da Pinnacle, com
# linhas de quarto (-0.25, -0.75) — o que a odds-api não entregava.
MARKET_MAP = {
    "MRES": ("ML", "3way"),               # Resultado Final
    "H1RS": ("ML HT", "3way"),            # Resultado do 1º Tempo
    "HCTG": ("Totals", "ou"),             # Total de Gols
    "OUH1": ("Totals HT", "ou"),          # Total de Gols 1º Tempo
    "CNOU": ("Corners Totals", "ou"),     # Escanteios
    "COU1": ("Corners Totals HT", "ou"),  # Escanteios 1º Tempo
    # --- profundos (só com ?bt=13) ---
    "AHRF": ("Spread", "hcp"),            # Handicap Asiático
    "AHRH": ("Spread HT", "hcp"),         # Handicap Asiático 1º Tempo
    "ASOU": ("Totals", "ou"),             # Asiático (Mais/Menos) Total de Gols
    "AOH1": ("Totals HT", "ou"),          # Asiático Total de Gols 1º Tempo
    "FCH2": ("Corners Spread", "hcp"),    # Handicap Escanteios
    "1000617": ("Corners Totals", "ou"),  # Asiático Escanteios totais
    "1000619": ("Corners Totals HT", "ou"),  # Asiático Escanteios 1º Tempo
    # --- basquete (NBA/WNBA) ---
    "H2HT": ("ML", "ml2"),                # Vencedor (2 vias, sem empate)
    "FHOT": ("Spread", "hcp"),            # Handicap
    "FTPO": ("Totals", "ou"),             # Total de pontos / Total de Runs
    # --- beisebol (MLB) ---
    "HCAP": ("Spread", "hcp"),            # Handicap (run line)
    # --- e-sports (LOL/DOTA/CS/Valorant): tudo em nível de PARTIDA, unidade=MAPAS.
    #     H2HT já é o "Vencedor" (2 vias). FAHC "Handicap do Jogo" ("KRU -1.5") e
    #     TSMF "Total de Mapas" ("Mais de 2.5") são os que a Pinnacle também cobre.
    #     (Mercados de mapa/round específicos — TMPW/3642/TNXR/MXRH — ficam de fora.)
    "FAHC": ("Spread", "hcp"),            # Handicap de mapas do jogo
    "TSMF": ("Totals", "ou"),             # Total de mapas
}


def _parse_3way(sels: list[dict]) -> list[tuple[str, float, None]]:
    """1/X/2 -> home/draw/away. Usa columnIndex quando existe (no 1º tempo os
    rótulos são os nomes dos times, não '1'/'X'/'2')."""
    mapa_nome = {"1": "home", "X": "draw", "x": "draw", "2": "away"}
    por_col = {0: "home", 1: "draw", 2: "away"}
    out = []
    for i, s in enumerate(sels):
        odd = _f(s.get("price"))
        if not odd or odd <= 1.0:
            continue
        side = mapa_nome.get(str(s.get("name") or "").strip())
        if side is None:
            col = s.get("columnIndex")
            side = por_col.get(col if col is not None else i)
        if side:
            out.append((side, odd, None))
    return out


def _parse_ml2(sels: list[dict]) -> list[tuple[str, float, None]]:
    """Vencedor de 2 vias (basquete): coluna 0 = mandante, 1 = visitante.
    As seleções trazem o nome do time, não 1/2."""
    out = []
    por_col = {0: "home", 1: "away"}
    for i, s in enumerate(sels[:2]):
        odd = _f(s.get("price"))
        if not odd or odd <= 1.0:
            continue
        col = s.get("columnIndex")
        side = por_col.get(col if col is not None else i)
        if side:
            out.append((side, odd, None))
    return out


def _parse_over_under(sels: list[dict]) -> list[tuple[str, float, float]]:
    """'Mais de 2.5' / 'Menos de 2.5' -> over/under com a linha."""
    out = []
    for s in sels:
        odd = _f(s.get("price"))
        if not odd or odd <= 1.0:
            continue
        nome = str(s.get("name") or "")
        line = _f(s.get("handicap"))
        if (mo := _OVER_RE.match(nome)):
            side, line = "over", (line if line is not None else _f(mo.group(1)))
        elif (mu := _UNDER_RE.match(nome)):
            side, line = "under", (line if line is not None else _f(mu.group(1)))
        else:
            continue
        if line is not None:
            out.append((side, odd, line))
    return out


def _parse_hcp(sels: list[dict], home: str, away: str) -> list[tuple[str, float, float]]:
    """'Atlético-MG -0.25' / 'Bahia +0.25' -> (home,-0.25) / (away,+0.25).
    Resolve o lado pelo nome do time; o campo handicap confirma a linha."""
    h, a = _norm_team(home), _norm_team(away)
    out = []
    for s in sels:
        odd = _f(s.get("price"))
        if not odd or odd <= 1.0:
            continue
        nome = str(s.get("name") or "")
        m = _HCP_RE.match(nome)
        if not m:
            continue
        rot = _norm_team(m.group("time"))
        if h and (rot == h or rot.startswith(h) or h.startswith(rot)):
            side = "home"
        elif a and (rot == a or rot.startswith(a) or a.startswith(rot)):
            side = "away"
        else:
            continue                       # time desconhecido: não inventa lado
        line = _f(s.get("handicap"))
        if line is None:
            line = _f(m.group("linha"))
        if line is not None:
            out.append((side, odd, line))
    return out


def parse_markets(event: dict, markets: list[dict]) -> list[dict]:
    """Converte os mercados de um evento da Betano em 'offered lines'.

    Função PURA — o coração testável do coletor.
    """
    parts = [p.get("name") for p in (event.get("participants") or [])]
    home = parts[0] if parts else ""
    away = parts[1] if len(parts) > 1 else ""
    out: list[dict] = []
    vistos: set[tuple] = set()

    for m in markets:
        alvo = MARKET_MAP.get(m.get("type"))
        if not alvo:
            continue                      # mercado que ainda não mapeamos
        nome, forma = alvo
        sels = m.get("selections") or []
        if forma == "3way":
            linhas = _parse_3way(sels)
        elif forma == "ml2":
            linhas = _parse_ml2(sels)
        elif forma == "hcp":
            linhas = _parse_hcp(sels, home, away)
        else:
            linhas = _parse_over_under(sels)
        for side, odd, line in linhas:
            # o mesmo mercado canônico pode vir de dois códigos (ex.: Totals de
            # HCTG e de ASOU): mantém a primeira linha vista de cada (mercado,linha,lado)
            chave = (nome, round(line, 2) if line is not None else None, side)
            if chave in vistos:
                continue
            vistos.add(chave)
            out.append({"market": nome, "line": line, "side": side,
                        "odd": odd, "sel_id": m.get("id")})

    for o in out:
        o.update({
            "source": "betano", "book": "Betano",
            "event_home": home, "event_away": away,
            "event_id": str(event.get("id") or ""),
            "event_date": event.get("startTime"),
            "league": event.get("leagueName") or "",
            "url": config.BETANO_BASE + (event.get("url") or ""),
        })
    return out


class BetanoSource:
    name = "betano"
    book = "Betano"

    def __init__(self):
        import requests
        self.http = requests.Session()
        self.http.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
            "Accept": "application/json",
            "Referer": config.BETANO_BASE + "/",
        })
        self._leagues_cache: dict[str, tuple[float, list[dict]]] = {}
        self.last_error = ""
        self.requests_made = 0

    async def _get(self, path: str):
        try:
            url = config.BETANO_BASE + path if path.startswith("/") else path
            r = await asyncio.to_thread(self.http.get, url, timeout=25.0)
        except Exception as e:
            self.last_error = f"rede: {e.__class__.__name__}"
            return None
        self.requests_made += 1
        if r.status_code != 200:
            self.last_error = f"Betano: HTTP {r.status_code} em {path}"
            return None
        try:
            self.last_error = ""
            return (r.json() or {}).get("data")
        except ValueError:
            self.last_error = f"Betano: JSON inválido em {path}"
            return None

    async def leagues(self, sport: str = "futebol") -> list[dict]:
        """Todas as ligas do esporte, achatadas de regionGroups->regions."""
        hit = self._leagues_cache.get(sport)
        if hit and time.time() - hit[0] < 1800:
            return hit[1]
        d = await self._get(f"/api/sport/{sport}/")
        out: list[dict] = []
        if d:
            for g in d.get("regionGroups") or []:
                for reg in g.get("regions") or []:
                    for lg in reg.get("leagues") or []:
                        out.append({"id": str(lg.get("id")), "name": lg.get("name"),
                                    "url": lg.get("url") or "", "region": reg.get("name")})
        self._leagues_cache[sport] = (time.time(), out)
        return out

    def wanted_leagues(self, leagues: list[dict]) -> list[dict]:
        """Filtra por trecho de URL (país incluso) — evita o falso positivo
        de 'Premier League' do Líbano/Butão/Esoccer."""
        return [lg for lg in leagues
                if any(pat in (lg.get("url") or "") for pat in config.BETANO_LEAGUE_URLS)]

    async def league_events(self, league_url: str) -> list[dict]:
        """Jogos da liga já com o mercado principal (1X2)."""
        d = await self._get("/api" + league_url)
        if not d:
            return []
        evs: list[dict] = []
        for block in d.get("blocks") or []:
            evs.extend(block.get("events") or [])
        return evs

    async def event_by_id(self, event_id: str | int) -> dict | None:
        """Metadados do jogo pelo id (rota que dispensa o slug da URL).

        É a fonte de verdade de quem é mandante e visitante quando os dados
        vêm da extensão — ler nomes do DOM seria ambíguo."""
        d = await self._get(f"/api/odds/event/{event_id}")
        if not d:
            return None
        ev = d.get("event") or {}
        nomes = [p.get("name") for p in (ev.get("participants") or [])]
        if len(nomes) < 2:
            return None
        return {"home": nomes[0], "away": nomes[1],
                "start": ev.get("startTime"), "league": ev.get("leagueName") or "",
                "url": config.BETANO_BASE + (ev.get("url") or "")}

    async def event_markets(self, event_url: str, deep: bool = True) -> list[dict]:
        """Mercados de um jogo. Com deep=True usa ?bt=13 (aba "Todos"), que traz
        os ~512 mercados incluindo o Handicap Asiático — o mesmo XHR que a
        interface dispara ao clicar em "Todos". Sem navegador."""
        sep = "&" if "?" in event_url else "?"
        path = "/api" + event_url + (f"{sep}bt=13" if deep else "")
        d = await self._get(path)
        if not d:
            return []
        ev = d.get("event") or {}
        return ev.get("markets") or []

    async def collect_listings(self, sport: str = "futebol") -> list[dict]:
        """Passo 1 (barato): todos os jogos das ligas-alvo + odds de ML."""
        lgs = self.wanted_leagues(await self.leagues(sport))
        events: list[dict] = []
        for lg in lgs:
            for ev in await self.league_events(lg["url"]):
                ev.setdefault("leagueName", lg.get("name"))
                events.append(ev)
            await asyncio.sleep(config.BETANO_REQUEST_DELAY)
        return events

    async def fetch_event_lines(self, event: dict) -> list[dict]:
        """Passo 2 (caro): mercados completos de UM jogo -> offered lines."""
        mkts = await self.event_markets(event.get("url") or "")
        await asyncio.sleep(config.BETANO_REQUEST_DELAY)
        return parse_markets(event, mkts)

    async def close(self):
        self.http.close()
