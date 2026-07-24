"""
test_sources.py — Testes dos coletores (funções puras, sem rede).

Cobre os dois bugs de correção que já custaram caro em depuração:
  1. Pinnacle: mercados de SUB-JOGOS (escanteios/cartões/props) vazando para
     dentro dos totais de GOLS.
  2. Betano: parsing de 'Mais de X' / 'Menos de X' e do 1X2.

Rodar:  python -m tests.test_sources
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from valuehub.sources.pinnacle import extract_fair_lines
from valuehub.sources.betano import parse_markets

falhas = []


def check(cond, msg):
    print(("  ok   " if cond else "  FALHA ") + msg)
    if not cond:
        falhas.append(msg)


# ---------------------------------------------------------------- Pinnacle
matchup = {
    "id": 100,
    "startTime": "2026-07-21T22:30:00Z",
    "participants": [{"alignment": "home", "name": "Atletico Mineiro"},
                     {"alignment": "away", "name": "Bahia"}],
    "league": {"name": "Brazil - Serie A", "sport": {"name": "Soccer"}},
}
markets = [
    # --- do jogo principal (matchupId 100)
    {"matchupId": 100, "type": "moneyline", "period": 0, "status": "open",
     "key": "s;0;m", "limits": [{"amount": 500, "type": "maxRiskStake"}],
     "prices": [{"designation": "home", "price": 124},
                {"designation": "draw", "price": 213},
                {"designation": "away", "price": 238}]},
    {"matchupId": 100, "type": "total", "period": 0, "status": "open",
     "key": "s;0;ou;2.5", "limits": [{"amount": 300, "type": "maxRiskStake"}],
     "prices": [{"designation": "over", "points": 2.5, "price": -101},
                {"designation": "under", "points": 2.5, "price": -122}]},
    # --- SUB-JOGO de ESCANTEIOS (rotulado): vira 'Corners Totals'
    {"matchupId": 999, "type": "total", "period": 0, "status": "open",
     "key": "s;0;ou;10.5", "limits": [{"amount": 200, "type": "maxRiskStake"}],
     "prices": [{"designation": "over", "points": 10.5, "price": -110},
                {"designation": "under", "points": 10.5, "price": -110}]},
    # --- SUB-JOGO NÃO rotulado (props): deve ser descartado por inteiro
    {"matchupId": 777, "type": "total", "period": 0, "status": "open",
     "key": "s;0;ou;7.5", "limits": [],
     "prices": [{"designation": "over", "points": 7.5, "price": -110},
                {"designation": "under", "points": 7.5, "price": -110}]},
    # --- mercado fechado: deve ser ignorado
    {"matchupId": 100, "type": "total", "period": 0, "status": "closed",
     "key": "s;0;ou;3.5", "limits": [],
     "prices": [{"designation": "over", "points": 3.5, "price": 150},
                {"designation": "under", "points": 3.5, "price": -180}]},
    # --- período 1 (primeiro tempo): fora do escopo atual
    {"matchupId": 100, "type": "moneyline", "period": 1, "status": "open",
     "key": "s;1;m", "limits": [],
     "prices": [{"designation": "home", "price": 150},
                {"designation": "away", "price": 200}]},
]

print("== Pinnacle: extração de fair lines ==")
# 999 = escanteios (rotulado); 777 = sub-jogo desconhecido (descartado)
lines = extract_fair_lines(matchup, markets, {"999": "Corners"})
linhas_totals = sorted({l["line"] for l in lines if l["market"] == "Totals"})
check(10.5 not in linhas_totals,
      "total de ESCANTEIOS (10.5) NÃO entrou como total de gols")
check(linhas_totals == [2.5], f"só o total de gols do jogo principal: {linhas_totals}")
check(3.5 not in linhas_totals, "mercado 'closed' ignorado")

corners = [l for l in lines if l["market"] == "Corners Totals"]
check(len(corners) == 2, f"escanteios coletados com rótulo próprio: {len(corners)}")
check(all(l["line"] == 10.5 for l in corners), "linha de escanteios preservada (10.5)")
check(not any(l["line"] == 7.5 for l in lines),
      "sub-jogo SEM rótulo (props) descartado por inteiro")

ht = [l for l in lines if l["market"].endswith(" HT")]
check(len(ht) == 2, f"período 1 coletado com sufixo HT: {[l['market'] for l in ht]}")
check(all(l["period"] == 1 for l in ht), "linhas HT marcadas com período 1")

ml = [l for l in lines if l["market"] == "ML"]
check(len(ml) == 3, f"ML com 3 vias (home/draw/away): {len(ml)}")
soma = sum(l["fair_prob"] for l in ml)
check(abs(soma - 1.0) < 1e-6, f"probabilidades justas somam 1: {soma:.6f}")
home = next(l for l in ml if l["side"] == "home")
check(abs(home["raw_odd"] - 2.24) < 0.01, f"+124 americana -> 2.24 decimal: {home['raw_odd']}")
check(home["fair_odd"] > home["raw_odd"], "fair odd > odd crua (vig removido)")
check(home["max_limit"] == 500, "limite de risco capturado")

tot = [l for l in lines if l["market"] == "Totals"]
check({l["side"] for l in tot} == {"over", "under"}, "Totals com over e under")

# ------------------------------------------------------------------ Betano
print("\n== Betano: parsing de mercados ==")
evento = {
    "id": "86631407",
    "startTime": 1784673000000,
    "url": "/odds/atletico-mg-bahia/86631407/",
    "leagueName": "Brasileirão - Série A Betano",
    "participants": [{"name": "Atlético-MG"}, {"name": "Bahia"}],
}
mkts = [
    {"type": "MRES", "name": "Resultado Final", "selections": [
        {"id": "1", "name": "1", "price": 2.15, "handicap": 0.0},
        {"id": "2", "name": "X", "price": 3.4, "handicap": 0.0},
        {"id": "3", "name": "2", "price": 3.5, "handicap": 0.0}]},
    {"type": "HCTG", "name": "Total de Gols", "selections": [
        {"id": "4", "name": "Mais de 2.5", "price": 1.93, "handicap": 2.5},
        {"id": "5", "name": "Menos de 2.5", "price": 1.88, "handicap": 2.5},
        {"id": "6", "name": "Mais de 1.5", "price": 1.31, "handicap": 1.5}]},
    # escanteios
    {"type": "CNOU", "name": "Escanteios", "selections": [
        {"id": "8", "name": "Mais de 10.5", "price": 2.02, "handicap": 10.5},
        {"id": "9", "name": "Menos de 10.5", "price": 1.75, "handicap": 10.5}]},
    # resultado do 1º tempo: os rótulos são NOMES DE TIMES, não 1/X/2
    {"type": "H1RS", "name": "Resultado do 1° Tempo", "selections": [
        {"id": "10", "name": "Atlético-MG", "price": 2.77, "columnIndex": 0},
        {"id": "11", "name": "Empate", "price": 2.10, "columnIndex": 1},
        {"id": "12", "name": "Bahia", "price": 3.95, "columnIndex": 2}]},
    # mercado que não mapeamos ainda — deve ser ignorado sem quebrar
    {"type": "BTSC", "name": "Ambas Marcam", "selections": [
        {"id": "7", "name": "Sim", "price": 1.72}]},
]
off = parse_markets(evento, mkts)
por_mercado = {}
for o in off:
    por_mercado.setdefault(o["market"], []).append(o)

check(len(por_mercado.get("ML", [])) == 3, "1X2 -> 3 linhas de ML")
sides = {o["side"] for o in por_mercado.get("ML", [])}
check(sides == {"home", "draw", "away"}, f"1/X/2 -> home/draw/away: {sides}")
h = next(o for o in por_mercado["ML"] if o["side"] == "home")
check(h["odd"] == 2.15 and h["line"] is None, "ML mandante 2.15, sem linha")

tots = por_mercado.get("Totals", [])
check(len(tots) == 3, f"3 linhas de Totals: {len(tots)}")
over25 = [o for o in tots if o["side"] == "over" and o["line"] == 2.5]
check(len(over25) == 1 and over25[0]["odd"] == 1.93, "'Mais de 2.5' -> over @2.5 = 1.93")
under25 = [o for o in tots if o["side"] == "under" and o["line"] == 2.5]
check(len(under25) == 1 and under25[0]["odd"] == 1.88, "'Menos de 2.5' -> under @2.5 = 1.88")
corners_b = por_mercado.get("Corners Totals", [])
check(len(corners_b) == 2, f"escanteios da Betano -> Corners Totals: {len(corners_b)}")
co = [o for o in corners_b if o["side"] == "over"][0]
check(co["line"] == 10.5 and co["odd"] == 2.02, "'Mais de 10.5' -> over @10.5 = 2.02")

ht_b = por_mercado.get("ML HT", [])
check(len(ht_b) == 3, f"resultado do 1º tempo -> ML HT: {len(ht_b)}")
lados_ht = {o["side"]: o["odd"] for o in ht_b}
check(lados_ht.get("home") == 2.77 and lados_ht.get("away") == 3.95,
      f"nomes de time mapeados por coluna -> home/away: {lados_ht}")

check(all(o["book"] == "Betano" for o in off), "casa marcada como Betano")
check(off[0]["url"].startswith("https://"), "link direto absoluto")
check(not any(o["market"] == "BTSC" for o in off), "mercado não mapeado ignorado sem erro")

print("\n== Betano: HANDICAP ASIÁTICO (mercados profundos, ?bt=13) ==")
mkts_deep = [
    # AHRF = Handicap Asiático, seleções com nome do time + linha de quarto
    {"type": "AHRF", "name": "Handicap Asiático (Resultado atual 0 - 0)", "selections": [
        {"id": "20", "name": "Atlético-MG -0.25", "price": 1.83, "handicap": -0.25},
        {"id": "21", "name": "Bahia +0.25", "price": 2.02, "handicap": 0.25},
        {"id": "22", "name": "Atlético-MG -0.75", "price": 2.30, "handicap": -0.75},
        {"id": "23", "name": "Bahia +0.75", "price": 1.63, "handicap": 0.75}]},
    # ASOU = Asiático Total de Gols (over/under com linha de quarto)
    {"type": "ASOU", "name": "Asiático (Mais/Menos) Total de Gols", "selections": [
        {"id": "24", "name": "Mais de 2.25", "price": 1.72, "handicap": 2.25},
        {"id": "25", "name": "Menos de 2.25", "price": 2.18, "handicap": 2.25}]},
]
deep = parse_markets(evento, mkts_deep)
pd = {}
for o in deep:
    pd.setdefault(o["market"], []).append(o)

check(len(pd.get("Spread", [])) == 4, f"handicap asiático -> 4 linhas de Spread: {len(pd.get('Spread', []))}")
sp = {(o["side"], o["line"]): o["odd"] for o in pd["Spread"]}
check(sp.get(("home", -0.25)) == 1.83, "Atlético-MG -0.25 -> home @ -0.25 = 1.83")
check(sp.get(("away", 0.75)) == 1.63, "Bahia +0.75 -> away @ +0.75 = 1.63 (linha de quarto)")
tot_a = {(o["side"], o["line"]): o["odd"] for o in pd.get("Totals", [])}
check(tot_a.get(("over", 2.25)) == 1.72, "Asiático over 2.25 = 1.72")

print("\n" + "=" * 58)
if falhas:
    print(f"{len(falhas)} FALHA(S):")
    for f in falhas:
        print("   -", f)
    sys.exit(1)
print("TODOS OS TESTES PASSARAM")
