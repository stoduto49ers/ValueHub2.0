"""
pinnacle.py — Coletor da Pinnacle (nossa referência sharp, infra própria).

A Pinnacle é a casa sharp por definição: margem baixa, aceita apostador
vencedor e move a linha com dinheiro informado. De-vigar a linha dela é a
forma clássica de estimar a probabilidade justa — é exatamente o que serviços
pagos vendem como "fair odds"/consenso. Aqui isso passa a ser nosso.

Fonte: endpoint "guest" do front-end da própria Pinnacle (JSON, sem login).
Preços vêm em formato AMERICANO e são convertidos para decimal.

Cada mercado traz `limits[].amount` (maxRiskStake) — o limite de aposta da
Pinnacle naquele mercado. É o melhor proxy de liquidez que existe: quanto
maior o limite, mais confiável a linha. Alimenta os LIQUIDITY_TIERS.

Saída: "fair lines" — dicts já de-vigados prontos para o motor cruzar contra
as odds das casas-alvo.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

from .. import config, core

log = logging.getLogger("valuehub.pinnacle")

# designações que formam um mercado de 2/3 vias
_SIDES = {
    "moneyline": ("home", "draw", "away"),
    "spread": ("home", "away"),
    "total": ("over", "under"),
}
# nome amigável do mercado (alinhado com o vocabulário do resto do HUB)
_MARKET_NAME = {"moneyline": "ML", "spread": "Spread", "total": "Totals"}


def _max_limit(market: dict) -> float | None:
    """Maior limite de risco publicado para o mercado (proxy de liquidez)."""
    amounts = [l.get("amount") for l in (market.get("limits") or [])
               if l.get("amount") is not None]
    return float(max(amounts)) if amounts else None


def _market_name(mtype: str, period, prefix: str = "") -> str:
    """'total' + período 1 + prefixo 'Corners' -> 'Corners Totals HT'."""
    base = _MARKET_NAME[mtype]
    if prefix:
        base = f"{prefix} {base}"
    return f"{base} HT" if period == 1 else base


def extract_fair_lines(matchup: dict, markets: list[dict],
                       sub_labels: dict[str, str] | None = None) -> list[dict]:
    """Converte um matchup + seus mercados em fair lines de-vigadas.

    sub_labels: {matchupId do sub-jogo: rótulo}, ex. {'163...': 'Corners'}.
      O endpoint devolve os mercados do jogo E dos sub-jogos misturados. Sem
      rótulo, um sub-jogo é DESCARTADO — é o que impede um total de escanteios
      (linha 10.5) de entrar como total de gols.

    Função PURA (sem rede) — é o coração testável do coletor.
    """
    sub_labels = sub_labels or {}
    parts = {p.get("alignment"): p.get("name")
             for p in (matchup.get("participants") or [])}
    home, away = parts.get("home") or "", parts.get("away") or ""
    league = (matchup.get("league") or {})
    sport = ((league.get("sport") or {}).get("name")) or ""
    league_name = league.get("name") or ""
    start = matchup.get("startTime") or ""
    matchup_id = str(matchup.get("id") or "")
    now = datetime.now(timezone.utc).isoformat()

    out: list[dict] = []
    for mk in markets:
        # O endpoint "related/straight" mistura os mercados do jogo com os dos
        # sub-jogos (escanteios, props), cada um com o seu matchupId. O jogo
        # principal vira gols; sub-jogo conhecido ganha rótulo; o resto é
        # descartado (senão um total de escanteios entraria como total de gols).
        mid = str(mk.get("matchupId") or "")
        if mid == matchup_id:
            prefix = ""
        elif mid in sub_labels:
            prefix = sub_labels[mid]
        else:
            continue
        mtype = mk.get("type")
        if mtype not in _SIDES:
            continue
        if mk.get("period") not in config.PINNACLE_PERIODS:
            continue
        if mk.get("status") != "open":
            continue
        if mk.get("isAlternate") and not config.PINNACLE_INCLUDE_ALTERNATES:
            continue

        prices = mk.get("prices") or []
        # mantém só os lados válidos, na ordem canônica do tipo de mercado
        wanted = _SIDES[mtype]
        picked = []
        for side in wanted:
            p = next((x for x in prices
                      if x.get("designation") == side and x.get("price") is not None), None)
            if p is not None:
                picked.append((side, p))
        if len(picked) < 2:
            continue

        try:
            decimals = [core.american_to_decimal(p["price"]) for _, p in picked]
        except (TypeError, ValueError):
            continue
        if any(d <= 1.0 for d in decimals):
            continue

        try:
            fair_probs = core.fair_probabilities(decimals, method=config.DEVIG_METHOD)
        except (ValueError, ZeroDivisionError):
            continue

        limit = _max_limit(mk)
        market_name = _market_name(mtype, mk.get("period"), prefix)
        # TÊNIS: no jogo (period 0), o total da Pinnacle é de SETS (linha 2.5) e o
        # spread é handicap de SETS (±1.5) — NÃO de games. Renomeamos para nomes
        # explícitos ('Total Sets' / 'Set Handicap') para que uma linha genérica
        # de GAMES de uma casa (mesma linha ±1.5) NÃO case por engano com o de
        # sets. Só o mercado de set explícito da casa casa com estes.
        if sport == "Tennis" and mk.get("period") == 0 and not prefix:
            if mtype == "total":
                market_name = "Total Sets"
            elif mtype == "spread":
                market_name = "Set Handicap"

        for (side, price), raw_odd, fair_prob in zip(picked, decimals, fair_probs):
            if not (0.0 < fair_prob < 1.0):
                continue
            line = price.get("points")
            line = float(line) if line is not None else None
            out.append({
                "id": f"pinnacle|{matchup_id}|{market_name}|{line}|{side}|{mk.get('period')}",
                "source": "pinnacle",
                "sport": sport,
                "league": league_name,
                "event_home": home,
                "event_away": away,
                "event_date": start,
                "matchup_id": matchup_id,
                # chave do mercado na Pinnacle (ex.: "s;0;ou;2.5") — identifica
                # o PAR de lados, mantendo home/away e over/under juntos
                "market_key": mk.get("key") or f"{market_name}|{mk.get('period')}",
                "market": market_name,
                "line": line,
                "side": side,
                "period": mk.get("period"),
                "raw_odd": round(raw_odd, 4),
                "fair_odd": round(core.prob_to_odd(fair_prob), 4),
                "fair_prob": round(fair_prob, 6),
                "max_limit": limit,
                "updated_at": now,
            })
    return out


class PinnacleSource:
    """Coletor assíncrono. Reaproveita conexão e cacheia a lista de ligas."""

    name = "pinnacle"

    def __init__(self):
        self.http = httpx.AsyncClient(
            base_url=config.PINNACLE_BASE,
            headers={
                "x-api-key": config.PINNACLE_KEY,
                "Referer": config.PINNACLE_REFERER,
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"),
                "Accept": "application/json",
            },
            timeout=25.0,
        )
        self._leagues_cache: dict[int, tuple[float, list[dict]]] = {}
        self.last_error: str = ""
        self.requests_made: int = 0
        # busca ativa por mercados novos: conta os `units` de sub-jogo que
        # ainda não sabemos rotular (é assim que os CARTÕES vão aparecer)
        self.unknown_units: dict[str, int] = {}

    async def _get(self, path: str):
        try:
            r = await self.http.get(path)
        except httpx.HTTPError as e:
            self.last_error = f"rede: {e.__class__.__name__}"
            return None
        self.requests_made += 1
        if r.status_code == 200:
            self.last_error = ""
            try:
                return r.json()
            except ValueError:
                self.last_error = f"JSON inválido em {path}"
                return None
        if r.status_code == 429:
            self.last_error = "Pinnacle: rate limit (429)"
        elif r.status_code in (401, 403):
            self.last_error = f"Pinnacle: bloqueio {r.status_code} (chave/geo)"
        else:
            self.last_error = f"Pinnacle: HTTP {r.status_code} em {path}"
        return None

    async def leagues(self, sport_id: int) -> list[dict]:
        hit = self._leagues_cache.get(sport_id)
        if hit and time.time() - hit[0] < config.PINNACLE_LEAGUES_CACHE_SEC:
            return hit[1]
        data = await self._get(f"/sports/{sport_id}/leagues") or []
        self._leagues_cache[sport_id] = (time.time(), data)
        return data

    async def matchups(self, league_id: int) -> list[dict]:
        return await self._get(f"/leagues/{league_id}/matchups") or []

    async def markets(self, matchup_id: str | int) -> list[dict]:
        return await self._get(f"/matchups/{matchup_id}/markets/related/straight") or []

    def _wanted_leagues(self, leagues: list[dict]) -> list[dict]:
        """Só as ligas grandes com jogos abertos (config.is_major_league)."""
        return [lg for lg in leagues
                if lg.get("matchupCount") and config.is_major_league(lg.get("name"))]

    def _in_horizon(self, matchup: dict) -> bool:
        start = matchup.get("startTime")
        if not start:
            return False
        try:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            return False
        now = datetime.now(timezone.utc)
        return now - timedelta(hours=4) <= dt <= now + timedelta(
            hours=config.PINNACLE_HORIZON_HOURS)

    async def collect(self) -> list[dict]:
        """Varredura completa: ligas grandes -> jogos na janela -> fair lines."""
        fair_lines: list[dict] = []
        for sport_name, sport_id in config.PINNACLE_SPORTS.items():
            leagues = self._wanted_leagues(await self.leagues(sport_id))
            for lg in leagues:
                matchups = await self.matchups(lg["id"])
                mains = [m for m in matchups
                         if m.get("type") == "matchup"
                         and not m.get("parentId")
                         and m.get("hasMarkets")
                         and self._in_horizon(m)]
                # sub-jogos rotuláveis (hoje: escanteios), indexados pelo pai
                subs: dict[str, dict[str, str]] = {}
                for m in matchups:
                    pid = str(m.get("parentId") or "")
                    if not pid:
                        continue
                    units = m.get("units") or ""
                    rotulo = config.PINNACLE_SUB_UNITS.get(units)
                    if rotulo:
                        subs.setdefault(pid, {})[str(m["id"])] = rotulo
                    elif config.PINNACLE_REPORT_UNKNOWN_UNITS and units:
                        # não coletamos, mas registramos para descobrir mercados
                        # novos (é por aqui que os cartões vão surgir)
                        self.unknown_units[units] = self.unknown_units.get(units, 0) + 1
                for m in mains:
                    mkts = await self.markets(m["id"])
                    if mkts:
                        fair_lines.extend(
                            extract_fair_lines(m, mkts, subs.get(str(m["id"]))))
                    await asyncio.sleep(config.PINNACLE_REQUEST_DELAY)
        return fair_lines

    async def close(self):
        await self.http.aclose()
