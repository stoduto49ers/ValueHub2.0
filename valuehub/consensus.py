"""
consensus.py — Consenso de várias casas sharp numa única fair odds.

A ideia (pedida pelo usuário): não depender de uma sharp só. Cada fonte sharp
(Pinnacle, Betfair Exchange, FanDuel…) de-viga sua própria linha e produz uma
probabilidade justa. O consenso combina essas probabilidades PONDERADAS pela
confiabilidade de cada casa naquele tipo de mercado.

Por que ponderado e não média simples: a Pinnacle é referência para linhas
regulares; o FanDuel/Circa para props americanos. Uma média simples deixaria
uma casa fraca degradar uma forte. Os pesos vivem em config.SHARP_WEIGHTS.

Depois de combinar, renormaliza os dois lados do mercado para somarem 1 (mantém
a coerência de uma fair odds sem vig).

Tudo aqui é função PURA — testável sem rede.
"""
from __future__ import annotations

from . import config, core


def source_weight(source: str, is_prop: bool) -> float:
    """Peso da fonte, dependente do tipo de mercado (props vs regular)."""
    tabela = config.SHARP_WEIGHTS_PROPS if is_prop else config.SHARP_WEIGHTS
    return tabela.get(source, tabela.get("_default", 1.0))


def esports_weights(sources_present) -> dict:
    """Pesos do consenso de E-SPORTS: Pinnacle 50% e TODAS as outras casas
    presentes (Polymarket + Betano + EstrelaBet + …) DIVIDINDO os outros 50%
    igualmente. A própria casa da aposta entra na conta (sem leave-one-out) — só
    amortece o edge p/ baixo. Sem Pinnacle no jogo, as casas dividem tudo
    (combine_side renormaliza) = média das casas."""
    srcs = set(sources_present)
    outras = [s for s in srcs if s != "pinnacle"]
    por_outra = ((1.0 - config.ESPORTS_WEIGHT_PINNACLE) / len(outras)) if outras else 0.0
    return {s: (config.ESPORTS_WEIGHT_PINNACLE if s == "pinnacle" else por_outra)
            for s in srcs}


def combine_side(entries: list[dict], is_prop: bool, weights: dict | None = None) -> dict | None:
    """Combina as fair probs de um MESMO lado vindas de várias fontes.

    entries: [{source, fair_prob, fair_odd, raw_odd, max_limit}, ...]
    weights: tabela {fonte: peso} explícita (ex.: e-sports). Se None, usa os
             pesos padrão de sharp (SHARP_WEIGHTS / SHARP_WEIGHTS_PROPS).
    Retorna a entrada de consenso (média ponderada da prob) ou None.
    """
    num = den = 0.0
    fontes = []
    limites = []
    for e in entries:
        p = e.get("fair_prob")
        if not p or not (0.0 < p < 1.0):
            continue
        src = e.get("source", "")
        w = weights.get(src, 0.0) if weights is not None else source_weight(src, is_prop)
        # POLYMARKET por LIQUIDEZ: mercado raso pesa menos (o preço é ruidoso).
        # max_limit da entrada Poly guarda a liquidez ($) do book naquele mercado.
        if src == "polymarket" and e.get("max_limit") is not None:
            liq = e.get("max_limit") or 0.0
            w *= min(1.0, liq / config.POLY_FULL_LIQUIDITY)
        if w <= 0:
            continue
        num += w * p
        den += w
        fontes.append(src)
        if e.get("max_limit"):
            limites.append(e["max_limit"])
    if den <= 0:
        return None
    prob = num / den
    return {
        "fair_prob": prob,
        "sources": sorted(set(fontes)),
        "n_sources": len(set(fontes)),
        # liquidez do consenso: o MAIOR limite entre as fontes (mais confiável)
        "max_limit": max(limites) if limites else None,
    }


def consensus_market(sides: dict[str, list[dict]], is_prop: bool) -> dict:
    """Consenso de um mercado inteiro (ex.: over+under de uma linha).

    sides: {'over': [entradas...], 'under': [entradas...]} ou home/away/draw.
    Renormaliza para as probabilidades dos lados somarem 1.
    Retorna {lado: {fair_prob, fair_odd, sources, n_sources, max_limit}}.
    """
    combinado = {}
    for lado, entries in sides.items():
        c = combine_side(entries, is_prop)
        if c:
            combinado[lado] = c
    # de-vig exige o mercado completo: com um lado só não há fair (a prob
    # renormalizaria para 1.0). Dado incompleto -> descarta o mercado.
    if len(combinado) < 2:
        return {}
    total = sum(c["fair_prob"] for c in combinado.values())
    if total <= 0:
        return {}
    for lado, c in combinado.items():
        p = min(max(c["fair_prob"] / total, 1e-6), 1.0 - 1e-6)
        c["fair_prob"] = round(p, 6)
        c["fair_odd"] = round(core.prob_to_odd(p), 4)
    return combinado
