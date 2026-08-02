"""
thunderpick.py — Coletor da Thunderpick (casa-alvo de E-SPORTS).

A Thunderpick é uma SPA React atrás de Cloudflare. A lista de jogos e as odds
NÃO saem por REST simples (o `/api/matches` só responde com IDs explícitos, e a
lista de IDs só vem pelo socket). Então um Chromium headless (Playwright) abre
as páginas de e-sports — passando o Cloudflare e deixando a própria SPA assinar
os jogos — e interceptamos os frames do WebSocket SignalR
(wss://thunderpick.io/ws/websockets):

  - `matchesShown`  -> objetos de JOGO: {id, gameId, startTime, teams:{home,away},
                       name, bestOfMaps, market (o "Match Winner" principal)}.
  - `marketsShown`  -> objetos de MERCADO: {eventId, name, category, period,
                       selections:[{name, type, odds, handicap, total}]}.

As odds vêm em DECIMAL. Mapeamos para o vocabulário do HUB (só o que a Pinnacle
cobre em e-sports, p/ casar):
    Match Winner            -> ML       (2 vias, sem empate)
    Map Handicap            -> Spread   (handicap de MAPAS; ±1.5, ±2.5)
    Total Maps (Over/Under) -> Totals   (total de MAPAS; 2.5, 3.5)

CUIDADO (mesma armadilha do tênis): a Thunderpick também tem handicap/total de
ROUNDS e por-MAPA. Só pegamos os de nível de PARTIDA — identificados por
`period is None` (os por-mapa/round trazem period.type) E pelo nome conter
"map/maps". Assim um "Round Handicap" nunca casa com o "Spread" de mapas sharp.

As funções de parsing são PURAS (sem browser) — o coração testável do coletor.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from .. import config

log = logging.getLogger("valuehub.thunderpick")

_RS = chr(0x1e)                    # separador de registro do SignalR
_MAP_RE = re.compile(r"(?i)\bmaps?\b")
_HCP_RE = re.compile(r"(?i)handicap")
_TOT_RE = re.compile(r"(?i)total|over\s*/\s*under")
_WINNER_RE = re.compile(r"(?i)^(match|map)\s+winner$")


def _f(x):
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- parsing puro
def parse_signalr_frames(frames: list) -> tuple[dict, dict]:
    """Separa os frames SignalR em (matches, markets).

    matches: {eventId: match_dict}  — do `matchesShown`
    markets: {eventId: [market_dict, ...]}  — do `marketsShown`
    Função PURA (recebe a lista de frames-texto já capturados)."""
    matches: dict = {}
    markets: dict = {}
    for frame in frames or []:
        if not isinstance(frame, str):
            continue
        for part in frame.split(_RS):
            part = part.strip()
            if not part or part[0] not in "{[":
                continue
            try:
                msg = json.loads(part)
            except (ValueError, TypeError):
                continue
            if not (isinstance(msg, dict) and msg.get("target") == "PublicEvent"):
                continue
            a0 = (msg.get("arguments") or [{}])[0]
            if not isinstance(a0, dict):
                continue
            kind = a0.get("type")
            lst = (a0.get("data") or {}).get("list") or []
            for it in lst:
                if not isinstance(it, dict):
                    continue
                if kind == "matchesShown" and it.get("id") is not None:
                    matches[it["id"]] = it
                elif kind == "marketsShown" and it.get("eventId") is not None:
                    markets.setdefault(it["eventId"], []).append(it)
    return matches, markets


def _ml_from_main(market: dict) -> list[tuple]:
    """O campo `market` do jogo é o Match Winner principal:
    {name, home:{name,odds}, away:{name,odds}, draw}. -> [(side, odd, None)]."""
    if not isinstance(market, dict):
        return []
    if not _WINNER_RE.match(str(market.get("name") or "")):
        return []
    out = []
    for side in ("home", "away"):
        sel = market.get(side) or {}
        odd = _f(sel.get("odds"))
        if odd and odd > 1.0:
            out.append((side, odd, None))
    return out


def _is_match_level(mkt: dict) -> bool:
    """Só mercados de PARTIDA inteira (period None). Os por-mapa/round trazem
    period.type ('map'/'set'/'round') e são descartados — evita a armadilha."""
    return mkt.get("period") in (None, "", {})


def classify_market(mkt: dict) -> str | None:
    """Nome canônico do HUB p/ um marketsShown, ou None p/ descartar.
    Só ML/Spread(map)/Totals(map) de nível de partida."""
    name = str(mkt.get("name") or "")
    if _WINNER_RE.match(name):
        return "ML"
    if not _is_match_level(mkt):
        return None                     # per-mapa/round -> fora
    if not _MAP_RE.search(name):
        return None                     # sem "map/maps" -> não é o de mapas
    if _HCP_RE.search(name):
        return "Spread"
    if _TOT_RE.search(name):
        return "Totals"
    return None


def _sides_from_market(canon: str, mkt: dict) -> list[tuple]:
    """(side, odd, line) de cada seleção, conforme o mercado canônico."""
    out = []
    for s in mkt.get("selections") or []:
        odd = _f(s.get("odds"))
        if not odd or odd <= 1.0:
            continue
        stype = str(s.get("type") or "").lower()
        if canon == "ML":
            if stype in ("home", "away"):
                out.append((stype, odd, None))
        elif canon == "Spread":
            line = _f(s.get("handicap"))
            if stype in ("home", "away") and line is not None:
                out.append((stype, odd, line))
        elif canon == "Totals":
            line = _f(s.get("total")) if s.get("total") is not None else _f(s.get("handicap"))
            nm = str(s.get("name") or "").lower()
            if "over" in nm or stype == "over" or nm.startswith("mais"):
                side = "over"
            elif "under" in nm or stype == "under" or nm.startswith("menos"):
                side = "under"
            else:
                continue
            if line is not None:
                out.append((side, odd, line))
    return out


def build_offered_lines(match: dict, market_list: list, game_name: str) -> list[dict]:
    """Converte um jogo (matchesShown) + seus mercados (marketsShown) em offered
    lines canônicas (ML/Spread/Totals de mapas). Deduplica (mercado, linha, lado).
    Função PURA."""
    teams = match.get("teams") or {}
    home = str((teams.get("home") or {}).get("name") or "")
    away = str((teams.get("away") or {}).get("name") or "")
    if not home or not away:
        return []
    eid = str(match.get("id") or "")
    collected: list[tuple] = []                      # (market, side, odd, line)
    # 1) ML do campo principal do jogo
    for side, odd, line in _ml_from_main(match.get("market") or {}):
        collected.append(("ML", side, odd, line))
    # 2) demais mercados (ML/Spread/Totals de mapas) do marketsShown
    for mkt in market_list or []:
        canon = classify_market(mkt)
        if not canon:
            continue
        for side, odd, line in _sides_from_market(canon, mkt):
            collected.append((canon, side, odd, line))

    out: list[dict] = []
    seen: set = set()
    for market, side, odd, line in collected:
        chave = (market, round(line, 2) if line is not None else None, side)
        if chave in seen:
            continue
        seen.add(chave)
        out.append({
            "market": market, "line": line, "side": side, "odd": odd,
            "source": "thunderpick", "book": "Thunderpick",
            "event_home": home, "event_away": away, "event_id": eid,
            "event_date": match.get("startTime"), "league": game_name,
            "url": f"https://thunderpick.io/match/{eid}",
        })
    return out


# ---------------------------------------------------- varredura com Playwright
def _sweep_sync(pages: list, headless: bool, wait_sec: float) -> dict:
    """Abre as páginas de e-sports num Chromium e devolve:
      - frames: frames-texto do WebSocket (matchesShown/marketsShown = jogos AO
                VIVO + mercados completos: handicap/total de mapas).
      - rest_matches: {id: match_obj} das respostas REST /api/matches?...
                includeMarkets — os jogos PRÉ-JOGO (metadados + Match Winner),
                que o browser busca ao ROLAR a página (por isso rolamos).
    SÍNCRONO (roda numa thread via asyncio.to_thread)."""
    from playwright.sync_api import sync_playwright
    import re as _re
    frames: list = []
    match_ids: set = set()          # ids que o browser (ou o WS) revelou

    def on_ws(ws):
        ws.on("framereceived",
              lambda p: frames.append(p) if isinstance(p, str) and len(frames) < 5000 else None)

    def on_req(req):
        # o browser pede /api/matches?matchesIds=A&matchesIds=B... (sem odds).
        # Colhemos os IDs p/ re-buscar COM includeMarkets (times + Match Winner).
        try:
            if "/api/matches?" in req.url and "matchesIds=" in req.url:
                for m in _re.findall(r"matchesIds=(\d+)", req.url):
                    match_ids.add(m)
        except Exception:
            pass

    rest_matches: dict = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless, args=["--disable-blink-features=AutomationControlled"])
        try:
            ctx = browser.new_context(
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
                viewport={"width": 1366, "height": 1000}, locale="pt-BR")
            page = ctx.new_page()
            page.on("websocket", on_ws)
            page.on("request", on_req)
            for url in pages:
                try:
                    page.goto(url, wait_until="networkidle", timeout=45000)
                    page.wait_for_timeout(int(wait_sec * 1000))
                    # ROLA a página: dispara o carregamento dos jogos mais abaixo
                    # (a SPA busca /api/matches?matchesIds à medida que aparecem).
                    for _ in range(6):
                        page.mouse.wheel(0, 2500)
                        page.wait_for_timeout(1500)
                except Exception:
                    log.exception("thunderpick: erro abrindo %s", url)
            # também os jogos AO VIVO vindos do WS matchesShown
            for f in frames:
                for mo in _re.findall(r'"id":(\d+),"gameId"', f if isinstance(f, str) else ""):
                    match_ids.add(mo)
            # re-busca EM LOTE com includeMarkets (contexto já passou o Cloudflare)
            ids = list(match_ids)
            for i in range(0, len(ids), 40):
                lote = ids[i:i + 40]
                qs = "&".join(f"matchesIds={x}" for x in lote)
                try:
                    resp = page.request.get(
                        f"https://thunderpick.io/api/matches?{qs}&includeMarkets=true",
                        timeout=20000)
                    if resp.ok:
                        for m in (resp.json().get("data") or {}).get("matches") or []:
                            if isinstance(m, dict) and m.get("id") is not None:
                                rest_matches[m["id"]] = m
                except Exception:
                    log.exception("thunderpick: erro no lote includeMarkets")
        finally:
            browser.close()
    return {"frames": frames, "rest_matches": rest_matches}


class ThunderpickSource:
    name = "thunderpick"
    book = "Thunderpick"

    def __init__(self):
        self.last_error = ""
        self.requests_made = 0
        self._cache: dict[str, list] = {}     # event_id -> offered lines
        self._listings: list[dict] = []
        self._last_sweep = 0.0

    async def collect_listings(self, sport: str = "") -> list[dict]:
        """Só age em e-sports. Faz UMA varredura de browser por intervalo
        (THUNDERPICK_SWEEP_INTERVAL_SEC) e cacheia; devolve as listagens."""
        if sport != "esports":
            return []
        agora = time.time()
        if self._listings and agora - self._last_sweep < config.THUNDERPICK_SWEEP_INTERVAL_SEC:
            return self._listings          # ainda fresco: reusa o cache
        try:
            res = await asyncio.to_thread(
                _sweep_sync, config.THUNDERPICK_PAGES,
                config.THUNDERPICK_HEADLESS, config.THUNDERPICK_PAGE_WAIT_SEC)
        except Exception as e:
            self.last_error = f"playwright: {e.__class__.__name__}: {str(e)[:80]}"
            return self._listings
        self.requests_made += 1
        frames = res.get("frames") or []
        matches, markets = parse_signalr_frames(frames)
        # funde os jogos PRÉ-JOGO vindos do REST (mesmo schema: id/gameId/teams/
        # startTime/market). O REST tem prioridade nos metadados; os mercados
        # completos (handicap/total) só existem no WS p/ jogos ao vivo.
        for mid, mobj in (res.get("rest_matches") or {}).items():
            matches.setdefault(mid, mobj)
        cache: dict[str, list] = {}
        listings: list[dict] = []
        for eid, mt in matches.items():
            game = config.THUNDERPICK_GAMES.get(mt.get("gameId"))
            if not game:                   # não é um e-sport que casamos
                continue
            lines = build_offered_lines(mt, markets.get(eid, []), game)
            if not lines:
                continue
            cache[str(eid)] = lines
            teams = mt.get("teams") or {}
            listings.append({
                "id": str(eid),
                "participants": [{"name": (teams.get("home") or {}).get("name")},
                                 {"name": (teams.get("away") or {}).get("name")}],
                "startTime": mt.get("startTime"),
                "leagueName": game,
                "url": f"https://thunderpick.io/match/{eid}",
            })
        self._cache, self._listings, self._last_sweep = cache, listings, agora
        self.last_error = ""
        log.info("thunderpick: %d jogos de e-sports (%d frames)", len(listings), len(frames))
        return listings

    async def fetch_event_lines(self, event: dict) -> list[dict]:
        """As linhas já foram parseadas na varredura — devolve do cache."""
        return self._cache.get(str(event.get("id") or ""), [])

    async def close(self):
        pass
