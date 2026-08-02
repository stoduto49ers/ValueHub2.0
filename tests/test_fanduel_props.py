"""
test_fanduel_props.py — Extrai player props do endpoint /odds do FanDuel
(catálogo completo por jogo), o novo caminho que substitui o /value-bets.

Rodar:  python -m tests.test_fanduel_props
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from valuehub.sources.fanduel import (parse_prop_label,
                                       extract_prop_fair_lines_from_odds)

falhas = []


def check(cond, msg):
    print(("  ok   " if cond else "  FALHA ") + msg)
    if not cond:
        falhas.append(msg)


print("== parse do rótulo da seleção ==")
check(parse_prop_label("Will Warren (Total Strikeouts)") == ("Will Warren", "Total Strikeouts"),
      "'Nome (Stat)' separa jogador e estatística")
check(parse_prop_label("Sem parênteses") is None, "sem '(Stat)' -> None")

# resposta /odds?eventId&bookmakers=FanDuel (recortada + realista)
od = {
    "id": 63302395, "home": "Chicago Cubs", "away": "New York Yankees",
    "date": "2026-07-31T18:20:00Z",
    "sport": {"name": "Baseball"}, "league": {"name": "USA - MLB"},
    "bookmakers": {"FanDuel": [
        {"name": "ML", "odds": [{"home": "1.68", "away": "2.26"}]},
        {"name": "Player Props", "odds": [
            # dois lados válidos -> de-viga e vira fair
            {"label": "Shota Imanaga (Total Strikeouts)", "hdp": 5.5,
             "over": "1.72", "under": "2.08"},
            # um lado 'N/A' -> descartado (não dá p/ de-vigar)
            {"label": "Ben Rice (Home Runs)", "hdp": 0.5,
             "over": "4.00", "under": "N/A"},
        ]},
    ]},
}
fair = extract_prop_fair_lines_from_odds(od)
print("\n== extração das fair lines ==")
check(len(fair) == 2, f"1 prop de 2 lados -> 2 fair lines (over+under); veio {len(fair)}")
by_side = {f["side"]: f for f in fair}
check(by_side["over"]["player"] == "Shota Imanaga", "jogador preservado")
check(by_side["over"]["market"] == "Prop: Total Strikeouts", "stat vira 'Prop: <stat>'")
check(by_side["over"]["line"] == 5.5, "linha (hdp) preservada")
check(by_side["over"]["sport"] == "Baseball" and by_side["over"]["league"] == "USA - MLB",
      "esporte/liga vêm do evento")
check(by_side["over"]["matchup_id"] == "fd-63302395", "matchup_id = fd-<eventId>")
p = by_side["over"]["fair_prob"] + by_side["under"]["fair_prob"]
check(abs(p - 1.0) < 1e-6, f"probabilidades de-vigadas somam 1 ({p:.4f})")
check(all("Home Runs" not in f["market"] for f in fair),
      "prop de 1 lado só (N/A) foi descartado")

print("\n" + "=" * 58)
if falhas:
    print(f"{len(falhas)} FALHA(S)")
    sys.exit(1)
print("TODOS OS TESTES PASSARAM")
