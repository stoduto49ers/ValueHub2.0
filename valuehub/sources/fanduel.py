"""
fanduel.py — FanDuel como referência SHARP para player props (NBA/NFL/NHL/MLB/WNBA).

A Pinnacle cobre pouco player prop; o FanDuel é a referência sharp reconhecida
para props americanos (é de onde serviços como o OddsNotifier tiram os deles).

Fonte: endpoint /value-bets da odds-api, filtrado para "Player Props". Cada
item traz DOIS conjuntos de odds:
  - market.home/away   -> consenso do mercado (já de-vigado)
  - bookmakerOdds.*    -> a odd REAL de 2 lados do FanDuel

Aqui de-vigamos a linha do PRÓPRIO FanDuel (bookmakerOdds), que é o pedido:
FanDuel como sharp, não o consenso genérico. O resultado vira uma "fair line"
com source='fanduel', igual às da Pinnacle, e alimenta a mesma tabela.

O nome do jogador e a estatística saem de "Player Props - Fulano (Estatística)".
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .. import config, core

_PROP_RE = re.compile(r"Player Props?\s*-\s*(?P<player>.+?)\s*\((?P<stat>[^)]+)\)\s*$", re.I)
# rótulo da seleção no endpoint /odds: 'Will Warren (Total Strikeouts)'
# (sem o prefixo 'Player Props - ', que só existe no /value-bets)
_LABEL_RE = re.compile(r"^(?P<player>.+?)\s*\((?P<stat>[^)]+)\)\s*$")


def _f(x):
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def parse_prop(market_name: str) -> tuple[str, str] | None:
    """'Player Props - Bryce Elder (Total Strikeouts)' -> ('Bryce Elder', 'Total Strikeouts')."""
    m = _PROP_RE.search(market_name or "")
    if not m:
        return None
    return m.group("player").strip(), m.group("stat").strip()


def parse_prop_label(label: str) -> tuple[str, str] | None:
    """'Will Warren (Total Strikeouts)' -> ('Will Warren', 'Total Strikeouts')."""
    m = _LABEL_RE.search(label or "")
    if not m:
        return None
    return m.group("player").strip(), m.group("stat").strip()


def extract_prop_fair_lines_from_odds(od: dict, devig: str = "shin") -> list[dict]:
    """Converte a resposta de /odds?eventId&bookmakers=FanDuel em fair lines de
    player props. É o CATÁLOGO COMPLETO do FanDuel para o jogo (não o filtrado
    de /value-bets). Só entram props com over E under válidos (senão não há como
    de-vigar). Função PURA (sem rede), espelha o schema de extract_prop_fair_lines.

    Estrutura esperada:
      od['bookmakers']['FanDuel'] = [ {name, odds:[...]}, ... ]
      mercado 'Player Props' -> odds:[ {label:'Nome (Stat)', hdp, over, under}, ...]
    """
    ev = od or {}
    home = ev.get("home") or ""
    away = ev.get("away") or ""
    sport = (ev.get("sport") or {}).get("name") if isinstance(ev.get("sport"), dict) else (ev.get("sport") or "")
    league = (ev.get("league") or {}).get("name") if isinstance(ev.get("league"), dict) else (ev.get("league") or "")
    date = ev.get("date") or ""
    eid = ev.get("id")
    fd = (ev.get("bookmakers") or {}).get("FanDuel") or []
    sels: list[dict] = []
    for m in fd:
        if str(m.get("name") or "").startswith("Player Props"):
            sels.extend(m.get("odds") or [])

    out: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    matchup_id = f"fd-{eid}"
    for s in sels:
        info = parse_prop_label(s.get("label") or "")
        if not info:
            continue
        player, stat = info
        over = _f(s.get("over"))
        under = _f(s.get("under"))
        if not over or not under:
            continue
        try:
            probs = core.fair_probabilities([over, under], method=devig)
        except (ValueError, ZeroDivisionError):
            continue
        line = _f(s.get("hdp"))
        for side, raw, prob in (("over", over, probs[0]), ("under", under, probs[1])):
            if not (0.0 < prob < 1.0):
                continue
            out.append({
                "id": f"fanduel|{eid}|{player}|{stat}|{line}|{side}",
                "source": "fanduel",
                "sport": sport,
                "league": league,
                "event_home": home,
                "event_away": away,
                "event_date": date,
                "matchup_id": matchup_id,
                "market_key": f"prop|{player}|{stat}|{line}",
                "market": f"Prop: {stat}",
                "line": line,
                "side": side,
                "period": 0,
                "player": player,
                "raw_odd": round(raw, 4),
                "fair_odd": round(core.prob_to_odd(prob), 4),
                "fair_prob": round(prob, 6),
                "max_limit": None,
                "updated_at": now,
            })
    return out


def extract_prop_fair_lines(items: list[dict], devig: str = "shin") -> list[dict]:
    """Converte itens de /value-bets do FanDuel em fair lines de player props.

    Só entram props com os DOIS lados (over/under) presentes — sem o par não há
    como de-vigar. Função PURA, testável sem rede.
    """
    out: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    for it in items:
        market = it.get("market") or {}
        info = parse_prop(market.get("name") or "")
        if not info:
            continue
        player, stat = info
        bo = it.get("bookmakerOdds") or {}
        over = _f(bo.get("home"))          # na odds-api, home=over / away=under nos props O/U
        under = _f(bo.get("away"))
        if not over or not under:
            continue
        try:
            probs = core.fair_probabilities([over, under], method=devig)
        except (ValueError, ZeroDivisionError):
            continue

        ev = it.get("event") or {}
        line = _f(market.get("hdp"))
        matchup_id = f"fd-{it.get('eventId')}"
        for side, raw, prob in (("over", over, probs[0]), ("under", under, probs[1])):
            if not (0.0 < prob < 1.0):
                continue
            out.append({
                "id": f"fanduel|{it.get('eventId')}|{player}|{stat}|{line}|{side}",
                "source": "fanduel",
                "sport": ev.get("sport") or "",
                "league": ev.get("league") or "",
                "event_home": ev.get("home") or "",
                "event_away": ev.get("away") or "",
                "event_date": ev.get("date") or "",
                "matchup_id": matchup_id,
                "market_key": f"prop|{player}|{stat}|{line}",
                "market": f"Prop: {stat}",
                "line": line,
                "side": side,
                "period": 0,
                "player": player,
                "raw_odd": round(raw, 4),
                "fair_odd": round(core.prob_to_odd(prob), 4),
                "fair_prob": round(prob, 6),
                "max_limit": _f(market.get("max")),
                "updated_at": now,
            })
    return out
