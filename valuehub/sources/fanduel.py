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
