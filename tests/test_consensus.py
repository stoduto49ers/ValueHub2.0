"""
test_consensus.py — Testes do consenso multi-sharp.

Rodar:  python -m tests.test_consensus
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from valuehub import consensus, config
from valuehub.valuefinder import group_fair_by_event

falhas = []


def check(cond, msg):
    print(("  ok   " if cond else "  FALHA ") + msg)
    if not cond:
        falhas.append(msg)


print("== pesos por tipo de mercado ==")
check(consensus.source_weight("pinnacle", is_prop=False) == 1.0, "Pinnacle pesa 1.0 em linha regular")
check(consensus.source_weight("fanduel", is_prop=True) == 1.0, "FanDuel pesa 1.0 em props")
check(consensus.source_weight("fanduel", is_prop=False) < 0.5, "FanDuel pesa pouco em linha regular")
check(consensus.source_weight("desconhecida", is_prop=False) == config.SHARP_WEIGHTS["_default"],
      "fonte desconhecida usa o peso padrão")

print("\n== combinação ponderada de um mercado (2 sharps) ==")
# Pinnacle: home 55% / away 45%.  Fonte fraca: home 45% / away 55%.
sides = {
    "home": [{"source": "pinnacle", "fair_prob": 0.55, "max_limit": 3000},
             {"source": "fanduel", "fair_prob": 0.45, "max_limit": 500}],
    "away": [{"source": "pinnacle", "fair_prob": 0.45, "max_limit": 3000},
             {"source": "fanduel", "fair_prob": 0.55, "max_limit": 500}],
}
c = consensus.consensus_market(sides, is_prop=False)
# peso pinnacle 1.0, fanduel 0.4 -> home = (1*.55 + .4*.45)/1.4 = .5214
check(abs(c["home"]["fair_prob"] - 0.5214) < 0.001, f"home puxado p/ a Pinnacle: {c['home']['fair_prob']}")
check(abs(c["home"]["fair_prob"] + c["away"]["fair_prob"] - 1.0) < 1e-6, "lados somam 1 (renormalizado)")
check(c["home"]["n_sources"] == 2, "registra 2 fontes")
check(c["home"]["max_limit"] == 3000, "liquidez = maior limite entre as fontes")

print("\n== consenso de 1 fonte = a própria fonte ==")
solo = {"over": [{"source": "pinnacle", "fair_prob": 0.52, "max_limit": 500}],
        "under": [{"source": "pinnacle", "fair_prob": 0.48, "max_limit": 500}]}
c1 = consensus.consensus_market(solo, is_prop=False)
check(abs(c1["over"]["fair_prob"] - 0.52) < 1e-6, "1 fonte passa direto")
check(c1["over"]["n_sources"] == 1, "n_sources = 1")

print("\n== props usam a tabela de pesos de props ==")
sides_p = {
    "over": [{"source": "fanduel", "fair_prob": 0.60, "max_limit": 500},
             {"source": "pinnacle", "fair_prob": 0.50, "max_limit": 500}],
    "under": [{"source": "fanduel", "fair_prob": 0.40, "max_limit": 500},
              {"source": "pinnacle", "fair_prob": 0.50, "max_limit": 500}],
}
cp = consensus.consensus_market(sides_p, is_prop=True)
# fanduel 1.0, pinnacle 0.5 -> over = (1*.6 + .5*.5)/1.5 = .5667
check(abs(cp["over"]["fair_prob"] - 0.5667) < 0.001, f"props puxam p/ FanDuel: {cp['over']['fair_prob']}")

print("\n== integração: mesmo jogo de 2 fontes vira consenso ==")
# Pinnacle e FanDuel, MESMO jogo (nomes idênticos), ML
rows = [
    {"source": "pinnacle", "event_home": "New York Yankees", "event_away": "Boston Red Sox",
     "event_date": "2026-07-22T23:00:00Z", "league": "USA - MLB", "sport": "Baseball",
     "market": "ML", "line": None, "side": "home", "fair_prob": 0.60, "fair_odd": 1.667, "max_limit": 3000},
    {"source": "pinnacle", "event_home": "New York Yankees", "event_away": "Boston Red Sox",
     "event_date": "2026-07-22T23:00:00Z", "league": "USA - MLB", "sport": "Baseball",
     "market": "ML", "line": None, "side": "away", "fair_prob": 0.40, "fair_odd": 2.5, "max_limit": 3000},
    {"source": "fanduel", "event_home": "New York Yankees", "event_away": "Boston Red Sox",
     "event_date": "2026-07-22T23:00:00Z", "league": "USA - MLB", "sport": "Baseball",
     "market": "ML", "line": None, "side": "home", "fair_prob": 0.50, "fair_odd": 2.0, "max_limit": 500},
    {"source": "fanduel", "event_home": "New York Yankees", "event_away": "Boston Red Sox",
     "event_date": "2026-07-22T23:00:00Z", "league": "USA - MLB", "sport": "Baseball",
     "market": "ML", "line": None, "side": "away", "fair_prob": 0.50, "fair_odd": 2.0, "max_limit": 500},
]
events = group_fair_by_event(rows)
check(len(events) == 1, f"as duas fontes juntaram num jogo só: {len(events)}")
ev = list(events.values())[0]
check(ev["sources"] == ["fanduel", "pinnacle"], f"registra as duas fontes: {ev['sources']}")
fair_home = ev["lines"][("ML", None, "home")]
check(fair_home["n_sources"] == 2, "consenso de 2 fontes no mercado")
# home consenso = (1*.6 + .4*.5)/1.4 = .5714 (renormaliza com away)
check(0.56 < fair_home["fair_prob"] < 0.58, f"fair de consenso puxado p/ Pinnacle: {fair_home['fair_prob']}")

print("\n== REGRESSÃO: spread de beisebol não cruza apostas distintas ==")
# Bug real: 'Time A -2.5' e 'Time B -2.5' são apostas diferentes (cada um vencer
# por 3+), NÃO os dois lados de um mercado. O consenso não pode renormalizá-las.
bb = [
    # linha padrão -1.5: home lay 1.5 (prob baixa) / away +1.5 (prob alta)
    {"source": "pinnacle", "event_home": "Colorado Rockies", "event_away": "Washington Nationals",
     "event_date": "2026-07-22T23:00:00Z", "league": "USA - MLB", "sport": "Baseball",
     "market": "Spread", "line": -1.5, "side": "home", "fair_prob": 0.35, "fair_odd": 2.857, "max_limit": 500},
    {"source": "pinnacle", "event_home": "Colorado Rockies", "event_away": "Washington Nationals",
     "event_date": "2026-07-22T23:00:00Z", "league": "USA - MLB", "sport": "Baseball",
     "market": "Spread", "line": 1.5, "side": "away", "fair_prob": 0.65, "fair_odd": 1.538, "max_limit": 500},
    # linha reversa: away lay 1.5 (prob baixa) — MESMO 'line=-1.5' mas lado away
    {"source": "pinnacle", "event_home": "Colorado Rockies", "event_away": "Washington Nationals",
     "event_date": "2026-07-22T23:00:00Z", "league": "USA - MLB", "sport": "Baseball",
     "market": "Spread", "line": -1.5, "side": "away", "fair_prob": 0.38, "fair_odd": 2.632, "max_limit": 500},
]
ebb = list(group_fair_by_event(bb).values())[0]["lines"]
check(abs(ebb[("Spread", -1.5, "home")]["fair_prob"] - 0.35) < 1e-6,
      f"home -1.5 preserva prob 0.35: {ebb[('Spread', -1.5, 'home')]['fair_prob']}")
check(abs(ebb[("Spread", -1.5, "away")]["fair_prob"] - 0.38) < 1e-6,
      f"away -1.5 (aposta distinta) preserva prob 0.38: {ebb[('Spread', -1.5, 'away')]['fair_prob']}")
# a prova do bug antigo: essas duas NÃO somam 1 (não são complementares)
soma = ebb[("Spread", -1.5, "home")]["fair_prob"] + ebb[("Spread", -1.5, "away")]["fair_prob"]
check(abs(soma - 0.73) < 1e-6, f"home -1.5 + away -1.5 = {soma:.2f} (NÃO renormalizado p/ 1)")

print("\n== jogos diferentes NÃO se juntam ==")
rows2 = rows[:2] + [
    {"source": "fanduel", "event_home": "Los Angeles Dodgers", "event_away": "San Francisco Giants",
     "event_date": "2026-07-22T23:00:00Z", "league": "USA - MLB", "sport": "Baseball",
     "market": "ML", "line": None, "side": "home", "fair_prob": 0.7, "fair_odd": 1.43, "max_limit": 500}]
check(len(group_fair_by_event(rows2)) == 2, "times diferentes = eventos separados")

print("\n" + "=" * 58)
if falhas:
    print(f"{len(falhas)} FALHA(S):")
    for f in falhas:
        print("   -", f)
    sys.exit(1)
print("TODOS OS TESTES PASSARAM")
