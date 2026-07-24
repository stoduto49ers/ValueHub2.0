"""
test_matching.py — Testes do casador de eventos.

Os pares vêm de dados REAIS de produção (Pinnacle x Betano, Brasileirão).
Inclui casos negativos: o matcher precisa REJEITAR o que não tem certeza.

Rodar:  python -m tests.test_matching
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from valuehub.matching import (normalize_team, team_similarity, match_event,
                               parse_time, player_similarity, match_player)

falhas = []


def check(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FALHA {msg}")
        falhas.append(msg)


print("== normalização ==")
check(normalize_team("São Paulo") == "sao paulo", "acento removido: São Paulo")
check(normalize_team("Atlético-MG") == "atletico mineiro", "Atlético-MG -> alias canônico")
check(normalize_team("Botafogo FR RJ") == "botafogo rj",
      "ruído FR removido, estado RJ PRESERVADO (distingue do Botafogo-SP)")
check(normalize_team("Athletico-PR") == "athletico paranaense", "Athletico-PR -> alias")
check(normalize_team("Grêmio") == "gremio", "Grêmio sem acento")

print("\n== semelhança entre nomes (pares reais) ==")
pares_iguais = [
    ("Atlético-MG", "Atletico Mineiro"),
    ("Athletico-PR", "Athletico Paranaense"),
    ("Botafogo-RJ", "Botafogo FR RJ"),
    ("São Paulo", "Sao Paulo"),
    ("Vitória", "Vitoria"),
    ("Grêmio", "Gremio"),
    ("Vasco da Gama", "Vasco da Gama"),
    ("Bahia", "Bahia"),
    ("Chapecoense", "Chapecoense"),
]
for a, b in pares_iguais:
    s = team_similarity(a, b)
    check(s >= 0.85, f"{a!r} ~ {b!r} = {s:.3f}")

print("\n== times DIFERENTES devem pontuar baixo ==")
pares_diferentes = [
    ("Flamengo", "Fluminense"),
    ("Botafogo-RJ", "Botafogo-SP"),
    ("Atlético-MG", "Atletico Goianiense"),
    ("Internacional", "Inter Limeira"),
    ("Corinthians", "Coritiba"),
]
for a, b in pares_diferentes:
    s = team_similarity(a, b)
    check(s < 0.85, f"{a!r} != {b!r} = {s:.3f}")

print("\n== casamento de eventos (com horário) ==")
# candidatos = jogos da Pinnacle (nomes em inglês)
pinn = [
    {"home": "Atletico Mineiro", "away": "Bahia", "start": "2026-07-21T22:30:00Z", "id": "P1"},
    {"home": "Sao Paulo", "away": "Athletico Paranaense", "start": "2026-07-23T00:30:00Z", "id": "P2"},
    {"home": "Botafogo FR RJ", "away": "Vitoria", "start": "2026-07-23T22:30:00Z", "id": "P3"},
    {"home": "Corinthians", "away": "Remo", "start": "2026-07-23T22:30:00Z", "id": "P4"},
    {"home": "Chapecoense", "away": "Flamengo", "start": "2026-07-23T00:30:00Z", "id": "P5"},
]
# alvos = jogos da Betano (epoch ms, nomes em português)
casos = [
    ({"home": "Atlético-MG", "away": "Bahia", "start": 1784673000000}, "P1"),
    ({"home": "São Paulo", "away": "Athletico-PR", "start": 1784766600000}, "P2"),
    ({"home": "Botafogo-RJ", "away": "Vitória", "start": 1784845800000}, "P3"),
    ({"home": "Corinthians", "away": "Remo", "start": 1784845800000}, "P4"),
]
for alvo, esperado in casos:
    r = match_event(alvo, pinn)
    got = r["event"]["id"] if r else None
    check(got == esperado,
          f"{alvo['home']} x {alvo['away']} -> {got} (esperado {esperado})"
          + (f" score={r['score']}" if r else ""))

print("\n== segurança: casos que devem ser REJEITADOS ==")
# jogo que não existe na sharp
r = match_event({"home": "Santos", "away": "Chapecoense", "start": 1784847600000}, pinn)
check(r is None, "jogo ausente na sharp -> None")

# times certos mas horário muito distante (outro jogo do mesmo confronto)
r = match_event({"home": "Atlético-MG", "away": "Bahia", "start": 1785000000000}, pinn)
check(r is None, "horário fora da janela -> None")

# invertido (mandante/visitante trocados) não deve casar
r = match_event({"home": "Bahia", "away": "Atlético-MG", "start": 1784673000000}, pinn)
check(r is None, "mandante/visitante invertidos -> None")

# ambiguidade: dois candidatos idênticos no mesmo horário
ambiguo = [
    {"home": "Coritiba", "away": "Palmeiras", "start": "2026-07-22T22:30:00Z", "id": "A1"},
    {"home": "Coritiba", "away": "Palmeiras", "start": "2026-07-22T22:40:00Z", "id": "A2"},
]
r = match_event({"home": "Coritiba", "away": "Palmeiras", "start": 1784759400000}, ambiguo)
check(r is None, "dois candidatos empatados -> None (ambíguo)")

print("\n== casador de jogadores (player props) ==")
for a, b in [("Bryce Elder", "B. Elder"), ("Bryce Elder", "Elder, Bryce"),
             ("Shohei Ohtani", "S. Ohtani"), ("Ronald Acuna Jr", "Acuña Jr, Ronald")]:
    check(player_similarity(a, b) >= 0.9, f"{a!r} ~ {b!r} = {player_similarity(a, b):.2f}")
for a, b in [("Bryce Elder", "Bryce Harper"), ("Bryce Elder", "Corbin Elder")]:
    check(player_similarity(a, b) < 0.9, f"{a!r} != {b!r} = {player_similarity(a, b):.2f}")
cand = ["Bryce Elder", "Mitch Bratt", "Aaron Judge"]
check(match_player("B. Elder", cand) == "Bryce Elder", "match_player resolve inicial")
check(match_player("Jose Silva", cand) is None, "jogador ausente -> None")

print("\n== casamento de evento SEM horário (Bet365 via DOM) ==")
r = match_event({"home": "Atlético-MG", "away": "Bahia", "start": None}, pinn)
check(r is not None and r["event"]["id"] == "P1", "casa por times quando não há horário")
# ambiguidade sem horário: dois jogos iguais -> rejeita
amb = [{"home": "Coritiba", "away": "Palmeiras", "start": None, "id": "Z1"},
       {"home": "Coritiba", "away": "Palmeiras", "start": None, "id": "Z2"}]
check(match_event({"home": "Coritiba", "away": "Palmeiras", "start": None}, amb) is None,
      "sem horário + ambíguo -> None")

print("\n== parse de horário ==")
check(parse_time(1784673000000).isoformat() == "2026-07-21T22:30:00+00:00",
      "epoch ms -> UTC")
check(parse_time("2026-07-21T22:30:00Z") == parse_time(1784673000000),
      "ISO e epoch representam o mesmo instante")

print("\n" + "=" * 58)
if falhas:
    print(f"{len(falhas)} FALHA(S):")
    for f in falhas:
        print("   -", f)
    sys.exit(1)
print("TODOS OS TESTES PASSARAM")
