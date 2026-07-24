"""
test_stake_safety.py — Salvaguardas de stake (amortecimento por odd, teto de
odd) e colapso/silêncio de linhas correlacionadas.

Rodar:  python -m tests.test_stake_safety
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from valuehub import core, db

falhas = []


def check(cond, msg):
    print(("  ok   " if cond else "  FALHA ") + msg)
    if not cond:
        falhas.append(msg)


print("== amortecimento por odd (sem teto de odd; nunca zera) ==")
check(core.odds_dampen(2.0) == 1.0, "odd baixa (2.0) -> sem amortecimento")
check(core.odds_dampen(2.5) == 1.0, "odd 2.5 (start) -> sem amortecimento")
check(abs(core.odds_dampen(20.0) - 0.4) < 1e-9, "odd altíssima (20) -> piso 0.4 (NÃO zera)")
check(core.odds_dampen(50.0) > 0, "odd extrema ainda aposta (sem teto de odd)")
check(core.odds_dampen(4.0) > core.odds_dampen(8.0), "quanto maior a odd, menor o fator")
check(core.odds_dampen(4.0) < 1.0, "odd média (4.0) já é amortecida")

print("\n== stake: odd alta recebe menos que odd baixa (mesmo edge) ==")
s_baixa = core.kelly_stake(0.667, 1.6, 10000)
s_alta = core.kelly_stake(0.25, 4.6, 10000)
check(s_baixa["stake_units"] >= s_alta["stake_units"],
      f"odd baixa {s_baixa['stake_units']}u >= odd alta {s_alta['stake_units']}u")

print("\n== sem teto de odd: odd alta com valor real ainda aposta ==")
s_longshot = core.kelly_stake(0.16, 9.0, 10000)  # +44% edge em odd 9
check(s_longshot["stake_units"] > 0.0, f"odd 9.0 com valor -> ainda aposta: {s_longshot['stake_units']}u")

print("\n== teto absoluto (cap) continua valendo ==")
s_cap = core.kelly_stake(0.80, 1.45, 10000, fraction=0.25, cap_pct=0.03)
check(s_cap["stake_units"] <= 3.0, f"stake respeita o teto de 3u: {s_cap['stake_units']}u")
# teto menor de props
import valuehub.config as _cfg
s_gl = core.kelly_stake_cfg(0.80, 1.45, _cfg, is_prop=False)
s_pr = core.kelly_stake_cfg(0.80, 1.45, _cfg, is_prop=True)
check(s_pr["stake_units"] <= s_gl["stake_units"], f"prop ({s_pr['stake_units']}u) <= game line ({s_gl['stake_units']}u)")
check(s_pr["stake_units"] <= 2.0, f"prop respeita teto menor de 2u: {s_pr['stake_units']}u")

print("\n== colapso + silêncio de linhas correlacionadas ==")
db.DB_PATH = "hub2_stake_test.db"
if os.path.exists(db.DB_PATH):
    os.remove(db.DB_PATH)
db.init()
# 4 handicaps da Chapecoense (mesmo jogo, mesmo mercado, mesmo lado)
base = dict(tab="value", suspicious=0, sport="Soccer", league="Brazil - Serie A",
            event_home="Chapecoense", event_away="Flamengo",
            event_date="2026-07-22T23:00:00Z", event_id="1", side="home",
            player="", book="Bet365", fair_prob=0.5, min_edge_required=2.0,
            max_limit=1200, direct_link="", stake_units=1.5, stake_amount=150.0)
for i, (line, edge) in enumerate([(0.5, 15.1), (1.0, 10.3), (1.5, 7.0), (2.0, 10.4)]):
    o = dict(base, id=f"opp{i}", market="Spread", hdp=line,
             offered_odd=1.8 + i * 0.1, fair_odd=1.6, edge_pct=edge)
    db.upsert_opportunity(o)
# uma linha de OUTRO mercado do mesmo jogo (não deve colapsar junto)
db.upsert_opportunity(dict(base, id="opp_tot", market="Totals", hdp=2.5, side="over",
                           offered_odd=1.95, fair_odd=1.85, edge_pct=5.0))

col = db.list_opportunities(tab="value", collapse=True)
spreads = [r for r in col if r["market"] == "Spread"]
check(len(spreads) == 1, f"4 handicaps colapsam em 1 (o de maior edge): {len(spreads)}")
check(spreads[0]["edge_pct"] == 15.1, "mantém o de MAIOR edge")
check(spreads[0]["family_count"] == 4, f"conta as 4 linhas da família: {spreads[0].get('family_count')}")
check(any(r["market"] == "Totals" for r in col), "outro mercado do mesmo jogo continua")

# registra aposta no Spread -> silencia todo o Spread desse jogo
opp = db.get_opportunity("opp0")
db.register_bet(opp, 1.5, 150.0, 1.8)
depois = db.list_opportunities(tab="value", collapse=True, hide_bet=True)
check(not any(r["market"] == "Spread" for r in depois),
      "após apostar no Spread, TODAS as linhas de Spread do jogo somem")
check(any(r["market"] == "Totals" for r in depois),
      "mas o Totals do mesmo jogo continua (mercado diferente)")

os.remove(db.DB_PATH)

print("\n" + "=" * 58)
if falhas:
    print(f"{len(falhas)} FALHA(S):")
    for f in falhas:
        print("   -", f)
    sys.exit(1)
print("TODOS OS TESTES PASSARAM")
