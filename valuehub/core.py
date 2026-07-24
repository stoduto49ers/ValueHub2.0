"""
core.py — Núcleo matemático (portado da v1, agnóstico de API).

  1. Conversão odds <-> probabilidade implícita
  2. De-vig por 3 métodos: multiplicativo, aditivo, Shin
  3. EV / edge de uma aposta
  4. CLV realizado e esperado
  5. Kelly fracionado com arredondamento em unidades
  6. Avaliação de boosts (simples e combinados)
"""
from __future__ import annotations
from typing import Sequence
import math


# ---------------------------------------------------------------- conversões

def odd_to_prob(odd: float) -> float:
    if odd <= 1.0:
        raise ValueError(f"Odd decimal deve ser > 1.0, recebido: {odd}")
    return 1.0 / odd


def prob_to_odd(prob: float) -> float:
    if not (0.0 < prob < 1.0):
        raise ValueError(f"Probabilidade deve estar entre 0 e 1, recebido: {prob}")
    return 1.0 / prob


def american_to_decimal(american: float) -> float:
    """Converte odd americana (+124, -122) para decimal. A Pinnacle publica
    preços em formato americano no endpoint arcadia."""
    a = float(american)
    if a > 0:
        return 1.0 + a / 100.0
    return 1.0 + 100.0 / abs(a)


# ------------------------------------------------------------------- de-vig

def devig_multiplicative(odds: Sequence[float]) -> list[float]:
    probs = [odd_to_prob(o) for o in odds]
    total = sum(probs)
    return [p / total for p in probs]


def devig_additive(odds: Sequence[float]) -> list[float]:
    probs = [odd_to_prob(o) for o in odds]
    n = len(probs)
    excess = sum(probs) - 1.0
    return [p - excess / n for p in probs]


def devig_shin(odds: Sequence[float], max_iter: int = 100, tol: float = 1e-10) -> list[float]:
    """De-vig de Shin (1992): modela o favorito-longshot bias. Preferido
    por traders sharp; converge rápido em mercados de 2 vias."""
    probs = [odd_to_prob(o) for o in odds]
    total = sum(probs)

    z = 0.0
    for _ in range(max_iter):
        denom = 2.0 * (1.0 - z) if (1.0 - z) != 0 else 1e-12
        fair = [(math.sqrt(z * z + 4.0 * (1.0 - z) * (p * p) / total) - z) / denom
                for p in probs]
        s = sum(fair)
        new_z = z + (s - 1.0)
        if abs(new_z - z) < tol:
            z = new_z
            break
        z = max(0.0, min(0.99, new_z))

    denom = 2.0 * (1.0 - z) if (1.0 - z) != 0 else 1e-12
    fair = [(math.sqrt(z * z + 4.0 * (1.0 - z) * (p * p) / total) - z) / denom
            for p in probs]
    s = sum(fair)
    return [f / s for f in fair]


def fair_probabilities(odds: Sequence[float], method: str = "multiplicative") -> list[float]:
    methods = {
        "multiplicative": devig_multiplicative,
        "additive": devig_additive,
        "shin": devig_shin,
    }
    if method not in methods:
        raise ValueError(f"Método '{method}' inválido. Use: {list(methods)}")
    return methods[method](odds)


# ----------------------------------------------------------------- EV / CLV

def expected_value(fair_prob: float, offered_odd: float) -> float:
    b = offered_odd - 1.0
    return fair_prob * b - (1.0 - fair_prob)


def edge_percent(fair_prob: float, offered_odd: float) -> float:
    return expected_value(fair_prob, offered_odd) * 100.0


def clv_percent(odd_taken: float, odd_close: float) -> float:
    """CLV% = (odd_taken / odd_close - 1) * 100. Positivo = bateu o fechamento."""
    return (odd_taken / odd_close - 1.0) * 100.0


def clv_expected(fair_prob: float, odd_taken: float) -> float:
    fair_odd = prob_to_odd(fair_prob)
    return clv_percent(odd_taken, fair_odd)


# -------------------------------------------------------------------- Kelly

def kelly_fraction(fair_prob: float, offered_odd: float) -> float:
    b = offered_odd - 1.0
    if b <= 0:
        return 0.0
    return max(0.0, (b * fair_prob - (1.0 - fair_prob)) / b)


def round_to_unit_step(stake_pct: float, unit_pct: float, step_u: float = 0.25,
                       min_u: float = 0.25) -> float:
    if stake_pct <= 0 or unit_pct <= 0:
        return 0.0
    units = stake_pct / unit_pct
    rounded = round(units / step_u) * step_u
    if rounded < min_u:
        return 0.0 if units < (min_u / 2) else min_u
    return round(rounded, 2)


def odds_dampen(offered_odd: float, start: float = 2.5, end: float = 12.0,
                floor: float = 0.4) -> float:
    """Fator de amortecimento da stake por odd (floor a 1). Odd alta = mais
    variância/incerteza -> stake menor, ALÉM do que o Kelly já reduz. NÃO zera
    (não há teto de odd): odds altas ainda apostam, só que com stake reduzida.
      odd <= start   -> 1.0 (sem amortecimento)
      start<odd<end  -> decai linearmente até `floor`
      odd >= end      -> `floor`
    """
    if offered_odd <= start:
        return 1.0
    if offered_odd >= end:
        return floor
    frac = (offered_odd - start) / (end - start)     # 0 no start, 1 no end
    return 1.0 - frac * (1.0 - floor)


def kelly_stake(fair_prob: float, offered_odd: float, bankroll: float,
                fraction: float = 0.25, cap_pct: float = 0.03,
                unit_pct: float = 0.01, step_u: float = 0.25,
                min_u: float = 0.25, max_odds=None,
                dampen_start: float = 2.5, dampen_end: float = 12.0,
                dampen_floor: float = 0.4) -> dict:
    # teto de odd opcional (padrão: sem teto). Se definido e a odd passar, pula.
    if max_odds is not None and offered_odd >= max_odds:
        return {"kelly_full": 0.0, "kelly_fractional": 0.0, "odds_dampen": 0.0,
                "stake_pct": 0.0, "stake_units": 0.0, "stake_amount": 0.0,
                "was_capped": False}
    full = kelly_fraction(fair_prob, offered_odd)
    fractional = full * fraction
    # amortecimento por odd (segurança extra para odds altas)
    damp = odds_dampen(offered_odd, dampen_start, dampen_end, dampen_floor)
    dampened = fractional * damp
    capped = min(dampened, cap_pct)

    stake_units = round_to_unit_step(capped, unit_pct, step_u, min_u)
    stake_pct_rounded = stake_units * unit_pct
    if stake_pct_rounded > cap_pct:
        stake_units = max(min_u, stake_units - step_u)
        stake_pct_rounded = stake_units * unit_pct

    return {
        "kelly_full": full,
        "kelly_fractional": fractional,
        "odds_dampen": round(damp, 3),
        "stake_pct": stake_pct_rounded,
        "stake_units": stake_units,
        "stake_amount": round(stake_pct_rounded * bankroll, 2),
        "was_capped": dampened > cap_pct,
    }


def kelly_stake_cfg(fair_prob: float, offered_odd: float, config,
                    is_prop: bool = False) -> dict:
    """kelly_stake usando os parâmetros da config. Props usam um teto menor
    (mais incertos). Usado por todo o pipeline."""
    cap = getattr(config, "PROPS_KELLY_CAP_PCT", config.KELLY_CAP_PCT) if is_prop \
        else config.KELLY_CAP_PCT
    return kelly_stake(
        fair_prob, offered_odd, config.BANKROLL,
        fraction=config.KELLY_FRACTION, cap_pct=cap,
        unit_pct=config.UNIT_PCT, step_u=config.STAKE_STEP_U,
        min_u=config.STAKE_MIN_U, max_odds=config.MAX_ODDS,
        dampen_start=config.ODDS_DAMPEN_START, dampen_end=config.ODDS_DAMPEN_END,
        dampen_floor=config.ODDS_DAMPEN_FLOOR)


# ------------------------------------------------------------------- boosts

def validate_simple_boost(boost_odd: float, ref_two_way: Sequence[float],
                          devig: str = "shin", bankroll: float = 10_000.0,
                          kelly_frac: float = 0.25, cap_pct: float = 0.03,
                          unit_pct: float = 0.01, step_u: float = 0.25,
                          min_u: float = 0.25) -> dict:
    """Boost simples: ref_two_way = [odd do lado apostado, odd do lado oposto]
    numa casa de referência. De-viga e calcula o edge real."""
    fair_prob = fair_probabilities(ref_two_way, method=devig)[0]
    fair_odd = prob_to_odd(fair_prob)
    edge = edge_percent(fair_prob, boost_odd)
    sizing = kelly_stake(fair_prob, boost_odd, bankroll, fraction=kelly_frac,
                         cap_pct=cap_pct, unit_pct=unit_pct, step_u=step_u, min_u=min_u)
    return {
        "type": "simple",
        "boost_odd": boost_odd,
        "fair_odd": round(fair_odd, 3),
        "edge_pct": round(edge, 2),
        "is_value": edge > 0,
        "stake_units": sizing["stake_units"],
        "stake_amount": sizing["stake_amount"],
    }


def parlay_fair_odd(legs_two_way, method: str = "multiplicative") -> dict:
    """Odd justa de uma múltipla: de-viga cada perna e multiplica as probs."""
    combined = 1.0
    leg_probs = []
    for two_way in legs_two_way:
        p = fair_probabilities(two_way, method=method)[0]
        leg_probs.append(p)
        combined *= p
    return {
        "leg_probs": leg_probs,
        "combined_prob": combined,
        "fair_odd": prob_to_odd(combined) if combined > 0 else None,
    }


def validate_combined_boost(boost_odd: float, legs_two_way=None,
                            ref_parlay_odd: float | None = None,
                            devig: str = "multiplicative",
                            bankroll: float = 10_000.0,
                            kelly_frac: float = 0.25, cap_pct: float = 0.03,
                            unit_pct: float = 0.01, step_u: float = 0.25,
                            min_u: float = 0.25) -> dict | None:
    """Boost combinado por dois métodos:
    A (preciso): legs_two_way -> de-vig por perna e multiplica.
    B (rápido): ref_parlay_odd = mesma múltipla noutra casa como proxy do justo."""
    if legs_two_way:
        res = parlay_fair_odd(legs_two_way, method=devig)
        fair_odd, fair_prob, modo = res["fair_odd"], res["combined_prob"], "devig_pernas"
    elif ref_parlay_odd:
        fair_odd, fair_prob, modo = ref_parlay_odd, 1.0 / ref_parlay_odd, "ref_outra_casa"
    else:
        return None

    edge = edge_percent(fair_prob, boost_odd)
    sizing = kelly_stake(fair_prob, boost_odd, bankroll, fraction=kelly_frac,
                         cap_pct=cap_pct, unit_pct=unit_pct, step_u=step_u, min_u=min_u)
    return {
        "type": "combined",
        "method": modo,
        "boost_odd": boost_odd,
        "fair_odd": round(fair_odd, 3) if fair_odd else None,
        "edge_pct": round(edge, 2),
        "is_value": edge > 0,
        "stake_units": sizing["stake_units"],
        "stake_amount": sizing["stake_amount"],
    }
