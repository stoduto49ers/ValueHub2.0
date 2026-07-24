"""
test_extension_parser.py — Testes do tradutor dos dados da extensão.

Os dados abaixo foram capturados do DOM REAL da Betano (Atlético-MG x Bahia),
depois de clicar na aba "Todos" e expandir os blocos de mercado.

Rodar:  python -m tests.test_extension_parser
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from valuehub.sources.extension_parser import (classify_market, parse_selection,
                                               parse_snapshot)

falhas = []


def check(cond, msg):
    print(("  ok   " if cond else "  FALHA ") + msg)
    if not cond:
        falhas.append(msg)


print("== classificação de mercados (nomes reais da tela) ==")
casos = [
    ("Handicap Asiático (Resultado atual 0 - 0)", "Spread", "hcp"),
    ("Handicap Asiático - Primeiro Tempo  (Resultado atual 0 - 0)", "Spread HT", "hcp"),
    ("Asiático (Mais/Menos) Total de Gols", "Totals", "ou"),
    ("Asiático (Mais/Menos) - Total de gols - 1° Tempo", "Totals HT", "ou"),
    ("Asiático (Mais/Menos) Escanteios totais", "Corners Totals", "ou"),
    ("Escanteios Handicap", "Corners Spread", "hcp"),
    ("Resultado Final", "ML", "3way"),
    ("Total de Gols", "Totals", "ou"),
    ("Escanteios", "Corners Totals", "ou"),
    ("Resultado do 1° Tempo", "ML HT", "3way"),
]
for nome, esperado, forma_esp in casos:
    r = classify_market(nome)
    check(r == (esperado, forma_esp), f"{nome[:44]!r} -> {r}")

print("\n== mercados que NÃO devem entrar (sem referência sharp) ==")
for nome in ["Resultado Correto", "Intervalo/Final", "Marcador",
             "Total de Cartões", "Resultado Final ou Ambas equipes Marcam",
             "Total de gols Mais/Menos (00:00 - 14:59)"]:
    check(classify_market(nome) is None, f"{nome[:42]!r} ignorado")

print("\n== seleções de handicap asiático ==")
HOME, AWAY = "Atlético-MG", "Bahia"
check(parse_selection("hcp", "Atlético-MG -0.25", 1.83, HOME, AWAY) == ("home", -0.25),
      "'Atlético-MG -0.25' -> home @ -0.25")
check(parse_selection("hcp", "Bahia +0.25", 2.02, HOME, AWAY) == ("away", 0.25),
      "'Bahia +0.25' -> away @ +0.25")
check(parse_selection("hcp", "Bahia 0.0", 2.52, HOME, AWAY) == ("away", 0.0),
      "linha zero preservada")
check(parse_selection("hcp", "Outro Time -1.5", 2.0, HOME, AWAY) is None,
      "time desconhecido -> None (não inventa lado)")

print("\n== seleções over/under (incluindo linhas de quarto) ==")
check(parse_selection("ou", "Mais de 2.25", 1.72, HOME, AWAY) == ("over", 2.25),
      "'Mais de 2.25' -> over @ 2.25 (linha de quarto)")
check(parse_selection("ou", "Menos de 10.0", 1.98, HOME, AWAY) == ("under", 10.0),
      "'Menos de 10.0' -> under @ 10")

print("\n== snapshot completo (DOM real) ==")
snap = {
    "source": "betano", "event_id": "86631407",
    "url": "https://www.betano.bet.br/odds/atletico-mg-bahia/86631407/",
    "markets": [
        {"market": "Resultado Final", "selections": [
            {"sel": "1", "odd": 2.15}, {"sel": "X", "odd": 3.4}, {"sel": "2", "odd": 3.5}]},
        {"market": "Handicap Asiático (Resultado atual 0 - 0)", "selections": [
            {"sel": "Atlético-MG -0.25", "odd": 1.83},
            {"sel": "Bahia +0.25", "odd": 2.02},
            {"sel": "Atlético-MG -0.5", "odd": 2.12},
            {"sel": "Bahia +0.5", "odd": 1.75},
            {"sel": "Atlético-MG 0.0", "odd": 1.55},
            {"sel": "Bahia 0.0", "odd": 2.52}]},
        {"market": "Asiático (Mais/Menos) Total de Gols", "selections": [
            {"sel": "Mais de 2.5", "odd": 1.95}, {"sel": "Menos de 2.5", "odd": 1.9},
            {"sel": "Mais de 2.25", "odd": 1.72}, {"sel": "Menos de 2.25", "odd": 2.18}]},
        {"market": "Escanteios", "selections": [
            {"sel": "Mais de 10.5", "odd": 2.02}, {"sel": "Menos de 10.5", "odd": 1.75}]},
        # ruído que deve ser ignorado
        {"market": "Resultado Correto", "selections": [{"sel": "1 - 0", "odd": 7.5}]},
        {"market": "Total de Cartões", "selections": [{"sel": "Mais de 5.5", "odd": 1.87}]},
    ],
}
linhas = parse_snapshot(snap, "Atlético-MG", "Bahia")
por_mercado = {}
for l in linhas:
    por_mercado.setdefault(l["market"], []).append(l)

check(set(por_mercado) == {"ML", "Spread", "Totals", "Corners Totals"},
      f"mercados extraídos: {sorted(por_mercado)}")
check(len(por_mercado["Spread"]) == 6, f"6 linhas de handicap asiático: {len(por_mercado.get('Spread', []))}")

sp = {(l["side"], l["line"]): l["odd"] for l in por_mercado["Spread"]}
check(sp.get(("home", -0.25)) == 1.83, "Spread home -0.25 = 1.83")
check(sp.get(("away", 0.25)) == 2.02, "Spread away +0.25 = 2.02")

tot = {(l["side"], l["line"]): l["odd"] for l in por_mercado["Totals"]}
check(tot.get(("over", 2.25)) == 1.72, "Totals over 2.25 = 1.72 (linha de quarto)")

check(all(l["book"] == "Betano" for l in linhas), "casa marcada como Betano")
check(all(l["event_id"] == "86631407" for l in linhas), "event_id preservado")

print("\n== Bet365: nomes de mercado e seleções em INGLÊS (DOM real) ==")
b365 = {"source": "bet365", "event": "Corinthians v Remo", "url": "u", "markets": [
    {"market": "Full Time Result", "selections": [
        {"sel": "Corinthians", "odd": 1.5}, {"sel": "Draw", "odd": 4.1}, {"sel": "Remo", "odd": 7.0}]},
    {"market": "Goals Over/Under", "selections": [
        {"sel": "Over 2.5", "odd": 1.95}, {"sel": "Under 2.5", "odd": 1.85}]},
    {"market": "Alternative Total Goals", "selections": [
        {"sel": "Under 3.5", "odd": 1.3}, {"sel": "Over 3.5", "odd": 3.5}]},
    # "Corners" de 3 vias (Over/Exactly/Under 11): NÃO é total asiático de 2
    # vias — "Over 11" ganha só com 12+, o 11 é resultado à parte (sem push).
    # Deve ser REJEITADO (comparar com fair de 2 vias inventaria valor).
    {"market": "Corners", "selections": [
        {"sel": "Over 11", "odd": 2.2}, {"sel": "Under 11", "odd": 2.0}, {"sel": "Exactly 11", "odd": 8.5}]},
    # "Escanteios Asiáticos" (2 vias, com push): É o correto para comparar.
    {"market": "Escanteios Asiáticos", "selections": [
        {"sel": "Mais de 10.5", "odd": 1.9}, {"sel": "Menos de 10.5", "odd": 1.9}]},
    {"market": "Asian Handicap", "selections": [
        {"sel": "Corinthians -1.0", "odd": 1.9}, {"sel": "Remo +1.0", "odd": 1.9}]},
    # sem par sharp -> devem ser ignorados
    {"market": "Draw No Bet", "selections": [{"sel": "Corinthians", "odd": 1.16}]},
    {"market": "Both Teams to Score", "selections": [{"sel": "Yes", "odd": 2.05}]},
    {"market": "Both Teams to Receive Cards", "selections": [{"sel": "Yes 2+ Cards", "odd": 1.8}]},
]}
bl = parse_snapshot(b365, "Corinthians", "Remo")
bm = {}
for l in bl:
    bm.setdefault(l["market"], []).append(l)
check(set(bm) == {"ML", "Totals", "Corners Totals", "Spread"},
      f"mercados Bet365 (inglês) extraídos: {sorted(bm)}")
check({(l["side"], l["line"]) for l in bm.get("ML", [])} == {("home", None), ("draw", None), ("away", None)},
      "Full Time Result -> ML home/draw/away (Draw reconhecido)")
check(any(l["line"] == 2.5 and l["side"] == "over" for l in bm.get("Totals", [])),
      "'Over 2.5' -> Totals over 2.5 (inglês)")
ct = {(l["side"], l["line"]) for l in bm.get("Corners Totals", [])}
check(not any(ln == 11.0 for (_side, ln) in ct),
      "Corners 3-vias (Exactly 11) REJEITADO — não vira total de 2 vias")
check(("over", 10.5) in ct and ("under", 10.5) in ct,
      "Escanteios Asiáticos 10.5 (2 vias) é o mantido")
bsp = {(l["side"], l["line"]): l["odd"] for l in bm.get("Spread", [])}
check(bsp.get(("home", -1.0)) == 1.9 and bsp.get(("away", 1.0)) == 1.9,
      "Asian Handicap -> Spread home -1.0 / away +1.0")
check(all(l["book"] == "Bet365" for l in bl), "casa marcada como Bet365")

print("\n== dedupe: o mesmo mercado aparece em várias abas ==")
snap_dup = {"source": "betano", "event_id": "1", "url": "u", "markets": [
    {"market": "Total de Gols", "selections": [{"sel": "Mais de 2.5", "odd": 1.93}]},
    {"market": "Total de Gols", "selections": [{"sel": "Mais de 2.5", "odd": 1.93}]},
]}
check(len(parse_snapshot(snap_dup, "A", "B")) == 1, "linha repetida enviada uma vez só")

print("\n" + "=" * 58)
if falhas:
    print(f"{len(falhas)} FALHA(S):")
    for f in falhas:
        print("   -", f)
    sys.exit(1)
print("TODOS OS TESTES PASSARAM")
