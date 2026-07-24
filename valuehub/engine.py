"""
engine.py — Normaliza itens do /value-bets em oportunidades classificadas.

Cada item da API traz:
  market.name        — "ML", "Spread", "Totals", "Player Props - Fulano (Pontos)"...
  market.home/away   — fair odds do CONSENSO (já sem vig; probs somam ~1)
  market.hdp         — linha (handicap / total)
  market.max         — limite de stake do consenso em EUR (proxy de liquidez)
  bookmakerOdds      — odds da casa-alvo + href (link direto)
  expectedValue      — índice (106.3 = 6.3% de edge) ou fração/percentual

Classificação em abas:
  props — market.name começa com "Player Props" e o esporte está em PROPS_SPORTS
  value — Futebol/Basquete, ML/Spread/Totals, liga major (configurável)
  other — todo o resto que passa nos filtros mínimos (aba "Outros")
"""
from __future__ import annotations
import re
from . import config, core

_PROPS_RE = re.compile(r"^Player (?:Props?|\w[\w ]*) - (?P<player>[^(]+?)\s*\((?P<stat>[^)]+)\)\s*$")
_HALF_RE = re.compile(r"\b(HT|1Q|1H|1st Half)\b", re.I)


def normalize_edge(ev) -> float | None:
    """expectedValue vem em formatos diferentes conforme a rota/versão:
    índice (106.3), percentual (6.3) ou fração (0.063). Normaliza p/ %."""
    try:
        ev = float(ev)
    except (TypeError, ValueError):
        return None
    if ev >= 50.0:
        return ev - 100.0
    if 0.0 < ev < 1.0:
        return ev * 100.0
    return ev


def min_edge_for_limit(max_limit: float | None) -> float:
    if max_limit is None:
        return config.DEFAULT_MIN_EDGE_PCT
    for floor, edge in config.LIQUIDITY_TIERS:
        if max_limit >= floor:
            return edge
    return config.LIQUIDITY_TIERS[-1][1]


def _is_major_league(league: str) -> bool:
    """Delegado ao config para que coletor e motor usem a MESMA regra."""
    return config.is_major_league(league)


def classify(sport: str, market_name: str, league: str) -> str | None:
    """Devolve a aba ('props' | 'value' | 'other') ou None para descartar."""
    m = _PROPS_RE.match(market_name or "")
    if market_name.startswith("Player") and m:
        return "props" if sport in config.PROPS_SPORTS else "other"
    if sport in config.VALUE_SPORTS:
        base = market_name
        if _HALF_RE.search(base):
            if not config.INCLUDE_HALF_MARKETS:
                return "other"
            base = _HALF_RE.sub("", base).strip()
        # o mercado-base (sem o " HT") precisa estar na lista de value
        if base in config.VALUE_MARKETS:
            return "value" if _is_major_league(league) else "other"
    return "other"


def parse_player(market_name: str) -> tuple[str, str]:
    """('Fulano', 'Pontos') a partir de 'Player Props - Fulano (Pontos)'."""
    m = _PROPS_RE.match(market_name or "")
    if not m:
        return "", ""
    return m.group("player").strip(), m.group("stat").strip()


def _f(x) -> float | None:
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _hdp(x) -> float | None:
    """Linha do mercado: pode ser negativa (spread -1.5) ou zero."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def normalize(item: dict) -> dict | None:
    """Converte um item cru do /value-bets numa oportunidade, ou None se
    não passa nos filtros. Mantém a decisão de edge mínimo por liquidez."""
    ev_info = item.get("event") or {}
    market = item.get("market") or {}
    book_odds = item.get("bookmakerOdds") or {}
    side = (item.get("betSide") or "").lower()
    sport = ev_info.get("sport") or ""
    league = ev_info.get("league") or ""
    market_name = market.get("name") or ""

    offered = _f(book_odds.get(side))
    fair = _f(market.get(side))
    if not offered or not fair:
        return None
    fair_prob = 1.0 / fair

    edge = normalize_edge(item.get("expectedValue"))
    if edge is None:
        edge = core.edge_percent(fair_prob, offered)

    max_limit = _f(market.get("max"))
    if max_limit is not None and max_limit < config.MIN_MAX_ABSOLUTE:
        return None

    tab = classify(sport, market_name, league)
    if tab is None:
        return None

    required = min_edge_for_limit(max_limit)
    if tab == "props":
        required = max(required, config.PROPS_MIN_EDGE_PCT)
    if edge < required:
        return None

    suspicious = 1 if edge > config.EDGE_SANITY_MAX_PCT else 0

    sizing = core.kelly_stake_cfg(fair_prob, offered, config, is_prop=(tab == "props"))
    if sizing["stake_units"] <= 0:
        return None

    player, stat = parse_player(market_name)
    link = (book_odds.get("href")
            or book_odds.get(f"{side}DirectLink")
            or book_odds.get("directLink") or "")

    return {
        "id": str(item.get("id") or f"{item.get('eventId')}-{market_name}-{side}-{item.get('bookmaker')}"),
        "tab": tab,
        "suspicious": suspicious,
        "sport": sport,
        "league": league,
        "event_home": ev_info.get("home") or "",
        "event_away": ev_info.get("away") or "",
        "event_date": ev_info.get("date") or "",
        "event_id": item.get("eventId"),
        "market": stat if player else market_name,
        "hdp": _hdp(market.get("hdp")),
        "side": side,
        "player": player,
        "book": item.get("bookmaker") or "",
        "offered_odd": offered,
        "fair_odd": fair,
        "fair_prob": round(fair_prob, 5),
        "edge_pct": round(edge, 2),
        "min_edge_required": required,
        "max_limit": max_limit,
        "direct_link": link,
        "stake_units": sizing["stake_units"],
        "stake_amount": sizing["stake_amount"],
    }
