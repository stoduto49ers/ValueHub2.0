"""
test_thunderpick.py — Parsing do WebSocket da Thunderpick (e-sports).

Cobre: ML (Match Winner), Spread (handicap de MAPAS), Totals (total de MAPAS) e
a DEFESA da armadilha — mercados por-mapa/round (period != None) NÃO entram.

Rodar:  python -m tests.test_thunderpick
"""
from __future__ import annotations
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from valuehub.sources.thunderpick import (parse_signalr_frames, build_offered_lines,
                                          classify_market)

falhas = []
def check(cond, msg):
    print(("  ok   " if cond else "  FALHA ") + msg)
    if not cond:
        falhas.append(msg)

RS = chr(0x1e)
def frame(kind, items):
    return json.dumps({"type": 1, "target": "PublicEvent",
                       "arguments": [{"type": kind, "data": {"list": items}}]})

# --- um jogo de CS2 (gameId 6) via matchesShown, com Match Winner no `market`
match = {
    "id": 2400001, "gameId": 6, "startTime": "2026-08-02T18:00:00+00:00",
    "isLive": False, "bestOfMaps": 3,
    "teams": {"home": {"name": "FaZe Clan"}, "away": {"name": "Team Vitality"}},
    "name": "FaZe Clan vs Team Vitality",
    "market": {"name": "Match Winner",
               "home": {"name": "FaZe Clan", "odds": 1.68},
               "away": {"name": "Team Vitality", "odds": 2.05}, "draw": None},
}
# --- mercados (marketsShown): Map Handicap + Total Maps (nível PARTIDA) e duas
#     ARMADILHAS por-mapa (period != None) que devem ser descartadas.
mkts = [
    {"eventId": 2400001, "name": "Map Handicap", "period": None, "selections": [
        {"name": "FaZe Clan", "type": "home", "odds": 1.90, "handicap": -1.5, "total": "-1.5"},
        {"name": "Team Vitality", "type": "away", "odds": 1.85, "handicap": 1.5, "total": "1.5"}]},
    {"eventId": 2400001, "name": "Total Maps Played Over/Under", "period": None, "selections": [
        {"name": "Over", "type": "other", "odds": 1.80, "total": "2.5"},
        {"name": "Under", "type": "other", "odds": 1.95, "total": "2.5"}]},
    # ARMADILHA 1: handicap de ROUNDS do Mapa 1 (period.type=map) — NÃO é de mapas
    {"eventId": 2400001, "name": "Round Handicap", "period": {"type": "map", "number": 1},
     "selections": [{"name": "FaZe Clan", "type": "home", "odds": 1.90, "handicap": -1.5},
                    {"name": "Team Vitality", "type": "away", "odds": 1.85, "handicap": 1.5}]},
    # ARMADILHA 2: total de ROUNDS do Mapa 1 (period.type=map)
    {"eventId": 2400001, "name": "Total Rounds", "period": {"type": "map", "number": 1},
     "selections": [{"name": "Over", "type": "other", "odds": 1.9, "total": "24.5"},
                    {"name": "Under", "type": "other", "odds": 1.9, "total": "24.5"}]},
]

frames = [frame("matchesShown", [match]), frame("marketsShown", mkts)]
# junta dois frames num só (SignalR concatena com RS) p/ testar o split também
frames = [frames[0], RS.join([frames[1], json.dumps({"type": 6})])]

matches, markets = parse_signalr_frames(frames)
print("== parse dos frames ==")
check(2400001 in matches, "matchesShown parseado (jogo presente)")
check(len(markets.get(2400001, [])) == 4, f"marketsShown parseado (4 mercados); veio {len(markets.get(2400001, []))}")

print("\n== classify_market (defesa map/round) ==")
by_name = {m["name"]: m for m in markets[2400001]}
check(classify_market(by_name["Map Handicap"]) == "Spread", "'Map Handicap' -> Spread")
check(classify_market(by_name["Total Maps Played Over/Under"]) == "Totals", "'Total Maps...' -> Totals")
check(classify_market(by_name["Round Handicap"]) is None, "'Round Handicap' (por-mapa) DESCARTADO")
check(classify_market(by_name["Total Rounds"]) is None, "'Total Rounds' (por-mapa) DESCARTADO")

print("\n== build_offered_lines ==")
lines = build_offered_lines(match, markets[2400001], "CS2")
S = {(o["market"], o["side"], o["line"]): o["odd"] for o in lines}
check(S.get(("ML", "home", None)) == 1.68, "ML home @1.68 (do Match Winner)")
check(S.get(("ML", "away", None)) == 2.05, "ML away @2.05")
check(S.get(("Spread", "home", -1.5)) == 1.90, "Spread home -1.5 (handicap de mapas)")
check(S.get(("Spread", "away", 1.5)) == 1.85, "Spread away +1.5")
check(S.get(("Totals", "over", 2.5)) == 1.80, "Totals over 2.5 (total de mapas)")
check(S.get(("Totals", "under", 2.5)) == 1.95, "Totals under 2.5")
mercados = {o["market"] for o in lines}
check(mercados == {"ML", "Spread", "Totals"}, f"só ML/Spread/Totals (sem rounds); veio {mercados}")
check(all(o["book"] == "Thunderpick" and o["event_home"] == "FaZe Clan" for o in lines),
      "book/casa e times preenchidos")

# jogo sem gameId de e-sports que casamos -> build ainda funciona, mas o coletor
# filtra por THUNDERPICK_GAMES (testado no fluxo do coletor, não aqui).
print("\n" + "=" * 58)
if falhas:
    print(f"{len(falhas)} FALHA(S)")
    sys.exit(1)
print("TODOS OS TESTES PASSARAM")
