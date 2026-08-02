"""
test_tennis.py — Tênis: total de SETS e handicap de SETS, e a defesa contra a
armadilha games×sets (handicap de games ±1.5 NÃO pode casar com o de sets ±1.5).

Rodar:  python -m tests.test_tennis
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from valuehub.sources.pinnacle import extract_fair_lines
from valuehub.sources.betano import parse_markets
from valuehub.sources.estrelabet import parse_event_details
from valuehub.valuefinder import group_fair_by_event, evaluate_event

falhas = []


def check(cond, msg):
    print(("  ok   " if cond else "  FALHA ") + msg)
    if not cond:
        falhas.append(msg)


# ---------------------------------------------------------------- Pinnacle
# Um jogo de tênis: ML, Total de SETS (2.5) e Handicap de SETS (±1.5).
matchup = {
    "id": 500, "startTime": "2026-07-31T20:00:00Z",
    "participants": [{"alignment": "home", "name": "Taylah Preston"},
                     {"alignment": "away", "name": "Mananchaya Sawangkaew"}],
    "league": {"name": "WTA 125K Vancouver", "sport": {"name": "Tennis"}},
}
markets = [
    {"matchupId": 500, "type": "moneyline", "period": 0, "status": "open",
     "key": "s;0;m", "limits": [{"amount": 500}],
     "prices": [{"designation": "home", "price": -140},
                {"designation": "away", "price": 120}]},
    {"matchupId": 500, "type": "total", "period": 0, "status": "open",
     "key": "s;0;ou;2.5", "limits": [{"amount": 300}],
     "prices": [{"designation": "over", "points": 2.5, "price": 120},
                {"designation": "under", "points": 2.5, "price": -150}]},
    {"matchupId": 500, "type": "spread", "period": 0, "status": "open",
     "key": "s;0;s;-1.5", "limits": [{"amount": 250}],
     "prices": [{"designation": "home", "points": -1.5, "price": 130},
                {"designation": "away", "points": 1.5, "price": -160}]},
]
fair = extract_fair_lines(matchup, markets)
mkts = {(r["market"], r["side"], r["line"]) for r in fair}
print("== Pinnacle renomeia mercados de tênis ==")
check(("Total Sets", "over", 2.5) in mkts, "total de sets vira 'Total Sets'")
check(("Set Handicap", "home", -1.5) in mkts, "spread de sets vira 'Set Handicap'")
check(all(m[0] not in ("Totals", "Spread") for m in mkts),
      "NÃO sobra 'Totals'/'Spread' genérico no tênis (evita colisão games×sets)")

# monta o fair_event p/ o evaluate
fair_event = group_fair_by_event(fair)[list(group_fair_by_event(fair))[0]]

# ---------------------------------------------------------------- Betano
betano_ev = {"id": 999, "startTime": "2026-07-31T20:00:00Z",
             "participants": [{"name": "Taylah Preston"},
                              {"name": "Mananchaya Sawangkaew"}]}
betano_markets = [
    {"type": "HTOH", "selections": [
        {"name": "Taylah Preston", "price": 1.70},
        {"name": "Mananchaya Sawangkaew", "price": 2.10}]},
    # Handicap de SETS (o que queremos) — ±1.5
    {"type": "MSSH", "selections": [
        {"name": "Taylah Preston -1.5", "handicap": -1.5, "price": 2.40},
        {"name": "Mananchaya Sawangkaew +1.5", "handicap": 1.5, "price": 1.55}]},
    # Número de sets (2/3) -> Total de Sets 2.5
    {"type": "NMST", "selections": [
        {"name": "2", "price": 1.55}, {"name": "3", "price": 2.35}]},
    # GAMES (armadilha!): handicap de games ±1.5 e total de games 22.5
    {"type": "TGHC", "selections": [
        {"name": "Taylah Preston -1.5", "handicap": -1.5, "price": 1.80},
        {"name": "Mananchaya Sawangkaew +1.5", "handicap": 1.5, "price": 1.90}]},
    {"type": "FTGO", "selections": [
        {"name": "Mais de 22.5", "handicap": 22.5, "price": 1.75},
        {"name": "Menos de 22.5", "handicap": 22.5, "price": 1.95}]},
]
offered = parse_markets(betano_ev, betano_markets)
omk = {(o["market"], o["side"], o["line"]) for o in offered}
print("\n== Betano tênis: nomes de mercado ==")
check(("Set Handicap", "home", -1.5) in omk, "MSSH -> Set Handicap (-1.5 no mandante)")
check(("Total Sets", "under", 2.5) in omk, "NMST '2' -> Total Sets under 2.5")
check(("Total Sets", "over", 2.5) in omk, "NMST '3' -> Total Sets over 2.5")
check(("Games Handicap", "home", -1.5) in omk, "TGHC -> Games Handicap (nome próprio)")
check(("Total Games", "over", 22.5) in omk, "FTGO -> Total Games (nome próprio)")
check(not any(m[0] in ("Spread",) for m in omk),
      "NÃO existe 'Spread' genérico no tênis da Betano")

# ---------------------------------------------------------------- a defesa
# evaluate_event: o handicap de GAMES ±1.5 NÃO acha par sharp; o de SETS acha.
# quais mercados do offered acharam par sharp (fair)?
from valuehub.valuefinder import _line_key
achou = set()
for o in offered:
    if fair_event["lines"].get(_line_key(o["market"], o.get("line"), o["side"])):
        achou.add(o["market"])
print("\n== defesa games×sets (evaluate) ==")
check("Set Handicap" in achou, "Set Handicap CASA com a Pinnacle")
check("Total Sets" in achou, "Total Sets CASA com a Pinnacle")
check("Games Handicap" not in achou, "Games Handicap NÃO casa (sem ref sharp de games)")
check("Total Games" not in achou, "Total Games NÃO casa (sem ref sharp de games)")
check("ML" in achou, "ML (vencedor) CASA")

# ---------------------------------------------------------------- EstrelaBet
estrela_res = {
    "Id": 17248850, "EventDate": "2026-07-31T20:00:00Z", "ChampName": "ATP",
    "Competitors": [{"Name": "Alex de Minaur"}, {"Name": "Brandon Nakashime"}],
    "MarketGroups": [{"Items": [
        {"MarketTypeId": 186, "Name": "Vencedor", "Items": [
            {"Name": "Alex de Minaur", "Price": 1.53},
            {"Name": "Brandon Nakashime", "Price": 2.50}]},
        {"MarketTypeId": 188, "Name": "Handicap de sets", "Items": [
            {"Name": "Alex de Minaur (-1.5)", "SPOV": "-1.5", "Price": 2.30},
            {"Name": "Brandon Nakashime (+1.5)", "SPOV": "+1.5", "Price": 1.52}]},
        {"MarketTypeId": 196, "Name": "Número exato de sets", "Items": [
            {"Name": "2", "Price": 1.62}, {"Name": "3", "Price": 2.16}]},
        {"MarketTypeId": 187, "Name": "Handicap de jogos", "Items": [
            {"Name": "Alex de Minaur (-1.5)", "SPOV": "-1.5", "Price": 1.83}]},
        {"MarketTypeId": 189, "Name": "Total jogos", "Items": [
            {"Name": "Mais de 22.5", "SPOV": "22.5", "Price": 1.80},
            {"Name": "Menos de 22.5", "SPOV": "22.5", "Price": 1.90}]},
    ]}],
}
est = parse_event_details(estrela_res)
emk = {(o["market"], o["side"], o["line"]) for o in est}
print("\n== EstrelaBet tênis: typeIds ==")
check(("Set Handicap", "home", -1.5) in emk, "188 -> Set Handicap (-1.5 no mandante)")
check(("Total Sets", "under", 2.5) in emk, "196 '2' -> Total Sets under 2.5")
check(("Total Sets", "over", 2.5) in emk, "196 '3' -> Total Sets over 2.5")
check(("Total Games", "over", 22.5) in emk, "189 -> Total Games (nome próprio)")
check(not any(m[0] in ("Spread", "Totals") for m in emk),
      "187 (games hcp) descartado; nada de 'Spread'/'Totals' genérico no tênis")

print("\n" + "=" * 58)
if falhas:
    print(f"{len(falhas)} FALHA(S)")
    sys.exit(1)
print("TODOS OS TESTES PASSARAM")
