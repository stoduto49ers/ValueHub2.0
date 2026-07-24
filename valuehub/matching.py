"""
matching.py — Casamento de eventos entre a fonte sharp (Pinnacle, nomes em
inglês) e as casas-alvo (Betano/KTO, nomes em português).

REGRA DE OURO: na dúvida, NÃO casa.
Um casamento errado inventa uma oportunidade que não existe e faz apostar em
cima de uma referência de outro jogo — o erro mais caro que este sistema pode
cometer. É preferível perder uma oportunidade real a criar uma falsa.

Por isso o casamento exige TRÊS condições simultâneas:
  1. horário de início compatível (janela apertada — na prática batem exato)
  2. os DOIS times casando (mandante com mandante, visitante com visitante)
  3. ausência de ambiguidade (se dois candidatos empatam, rejeita os dois)

Desafios reais tratados (medidos em dados de produção):
    Pinnacle                Betano
    Atletico Mineiro   <->  Atlético-MG          acento + sigla de estado
    Athletico Paranaense <-> Athletico-PR        sigla
    Botafogo FR RJ     <->  Botafogo-RJ          ruído "FR" + sigla
    Sao Paulo          <->  São Paulo            acento
    Gremio / Vitoria   <->  Grêmio / Vitória     acento
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher

# ---------------------------------------------------------------------------
# Tokens de ruído: siglas de clube que não distinguem times.
# ---------------------------------------------------------------------------
_CLUB_NOISE = {
    "fc", "ec", "sc", "cf", "ac", "fr", "cr", "ca", "sd", "cd", "ad",
    "afc", "cfc", "rc", "ss", "us", "as", "if", "bk", "fk", "sk", "nk", "ik",
    "clube", "club", "futebol", "football", "esporte", "esportivo", "esportiva",
    "associacao", "sociedade", "regatas", "recreativo",
    "team", "the", "de", "do", "da", "dos", "das", "of",
}
# Siglas de estado (BR). NÃO são ruído: elas DISTINGUEM clubes homônimos
# (Botafogo-RJ x Botafogo-SP são times diferentes). São preservadas no nome
# e usadas como desempate: siglas conflitantes => clubes diferentes.
_BR_STATES = {
    "mg", "pr", "rj", "sp", "rs", "ba", "pe", "ce", "go", "mt", "ms",
    "pa", "am", "rn", "pb", "al", "se", "pi", "ma", "to", "ro", "ap", "df",
}

# ---------------------------------------------------------------------------
# Apelidos explícitos: a rede de segurança para os casos que a heurística
# sozinha resolveria mal. Chave e valor em forma NORMALIZADA.
# Adicione aqui sempre que encontrar um par novo que não casa sozinho.
# ---------------------------------------------------------------------------
ALIASES: dict[str, str] = {
    # --- Brasil (a sigla do estado é resolvida para o nome por extenso)
    "atletico mg": "atletico mineiro",
    "athletico pr": "athletico paranaense",
    "atletico pr": "athletico paranaense",
    "atletico go": "atletico goianiense",
    "america mg": "america mineiro",
    "red bull bragantino": "bragantino",
    "vasco gama": "vasco da gama",
    "vasco": "vasco da gama",
    # sufixo de cidade que uma casa põe e a Pinnacle não
    "bolivar la paz": "bolivar",
    "the strongest la paz": "the strongest",
    "nacional potosi": "nacional potosi",
    # --- Europa (nomes PT <-> EN mais comuns)
    "bayern munique": "bayern munich",
    "bayern munchen": "bayern munich",
    "bayern de munique": "bayern munich",
    "atletico madrid": "atletico madrid",
    "atletico de madrid": "atletico madrid",
    "manchester city": "manchester city",
    "manchester united": "manchester united",
    "inter milao": "inter",
    "internazionale": "inter",
    "milan": "ac milan",
    "ac milan": "ac milan",
    "juventus": "juventus",
    "psg": "paris saint germain",
    "paris saint germain": "paris saint germain",
    "paris sg": "paris saint germain",
    "colonia": "koln",
    "koln": "koln",
    "borussia dortmund": "borussia dortmund",
    "sporting": "sporting cp",
    "sporting cp": "sporting cp",
    "sporting lisboa": "sporting cp",
}


def strip_accents(text: str) -> str:
    """'São Paulo' -> 'Sao Paulo'."""
    nfkd = unicodedata.normalize("NFD", text)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def normalize_team(name: str) -> str:
    """Reduz um nome de time à sua forma comparável, resolvendo apelidos.

    'Atlético-MG'          -> 'atletico mineiro'   (via ALIASES)
    'Atletico Mineiro'     -> 'atletico mineiro'
    'Botafogo FR RJ'       -> 'botafogo rj'        (estado preservado!)
    'Vasco da Gama'        -> 'vasco da gama'
    """
    if not name:
        return ""
    s = strip_accents(str(name)).lower()
    # qualificadores entre parênteses que uma casa põe e a outra não: (F), (W),
    # (Fem), (Res), (Sub-20), (U20)... — removê-los faz o casamento bater 100%
    # (ex.: "New York Liberty (F)" vs "New York Liberty" era 92%).
    s = re.sub(r"\(\s*(?:f|w|fem|women|res|reservas?|b|ii|jr|jun|junior|youth|"
               r"am|amateur|sub[-\s]?\d+|u\s?\d+)\s*\)", " ", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)          # hífen, ponto, etc. viram espaço
    tokens = [t for t in s.split() if t and t not in _CLUB_NOISE]
    # descarta tokens de 1 letra que sobraram (ex.: o "f" de "(F)" sem parênteses)
    if len([t for t in tokens if len(t) > 1]) >= 1:
        tokens = [t for t in tokens if len(t) > 1 or t.isdigit()]
    if not tokens:                               # nome era só ruído — preserva
        tokens = [t for t in s.split() if t]
    base = " ".join(tokens)
    return ALIASES.get(base, base)


def _states_in(normalized: str) -> set[str]:
    return {t for t in normalized.split() if t in _BR_STATES}


def team_similarity(a: str, b: str) -> float:
    """Semelhança entre dois nomes de time, de 0 a 1.

    Sinais, do mais forte para o mais fraco:
      - siglas de estado conflitantes  -> 0.2  (clubes homônimos diferentes)
      - igualdade após normalização    -> 1.0
      - contenção com 2+ tokens        -> 0.92 ('vasco gama' ⊂ 'vasco da gama')
      - contenção de token único       -> 0.82 ('inter' ⊂ 'inter limeira':
                                                plausível, mas exige que o
                                                outro time confirme o jogo)
      - Jaccard de tokens / sequência
    """
    na, nb = normalize_team(a), normalize_team(b)
    if not na or not nb:
        return 0.0

    # Botafogo-RJ x Botafogo-SP: mesmo nome, estados diferentes = clubes
    # diferentes. Este teste vem ANTES de tudo.
    sa, sb = _states_in(na), _states_in(nb)
    if sa and sb and not (sa & sb):
        return 0.2

    if na == nb:
        return 1.0

    ta, tb = set(na.split()), set(nb.split())
    if ta and tb and (ta <= tb or tb <= ta):
        menor = min(len(ta), len(tb))
        return 0.92 if menor >= 2 else 0.82

    jaccard = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    seq = SequenceMatcher(None, na.replace(" ", ""), nb.replace(" ", "")).ratio()
    if not (ta & tb):        # sem token em comum, desconfia da sequência
        seq *= 0.85
    return max(jaccard, seq)


def parse_time(value) -> datetime | None:
    """Aceita ISO8601 ('2026-07-21T22:30:00Z') ou epoch em milissegundos."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, timezone.utc)
    s = str(value)
    if s.isdigit():
        return datetime.fromtimestamp(int(s) / 1000.0, timezone.utc)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def score_pair(target: dict, fair: dict) -> float:
    """Pontuação do casamento entre um evento da casa-alvo e um da sharp.
    Média das semelhanças de mandante e visitante — os dois precisam bater."""
    h = team_similarity(target.get("home", ""), fair.get("home", ""))
    a = team_similarity(target.get("away", ""), fair.get("away", ""))
    return (h + a) / 2.0


def match_event(target: dict, candidates: list[dict],
                max_minutes: int = 90,
                min_score: float = 0.85,
                min_side_score: float = 0.72,
                ambiguity_margin: float = 0.05) -> dict | None:
    """Encontra o evento sharp correspondente, ou None se não for seguro.

    target/candidates: dicts com 'home', 'away' e 'start' (ISO ou epoch ms).

    Rejeita quando:
      - nenhum candidato dentro da janela de horário
      - melhor pontuação abaixo do mínimo, ou um dos lados fraco
      - dois candidatos empatados dentro de `ambiguity_margin` (ambíguo)
    """
    t_start = parse_time(target.get("start"))
    # Sem horário confiável (ex.: Bet365 lido do DOM) casamos só pelos times,
    # mas então exigimos pontuação mais alta e ambiguidade zero — a proteção
    # contra casar o jogo errado passa a depender só disso.
    sem_horario = t_start is None
    if sem_horario:
        min_score = max(min_score, 0.90)

    scored: list[tuple[float, dict]] = []
    for c in candidates:
        if not sem_horario:
            c_start = parse_time(c.get("start"))
            if c_start is None:
                continue
            if abs((c_start - t_start).total_seconds()) > max_minutes * 60:
                continue
        h = team_similarity(target.get("home", ""), c.get("home", ""))
        a = team_similarity(target.get("away", ""), c.get("away", ""))
        if h < min_side_score or a < min_side_score:
            continue          # um lado fraco já invalida o par
        scored.append(((h + a) / 2.0, c))

    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    best_score, best = scored[0]
    if best_score < min_score:
        return None
    # ambiguidade: dois candidatos praticamente iguais -> não arrisca.
    # Sem horário a margem é maior (o horário deixou de desempatar).
    margem = ambiguity_margin * (2 if sem_horario else 1)
    if len(scored) > 1 and (best_score - scored[1][0]) < margem:
        return None
    return {"event": best, "score": round(best_score, 4)}


# ---------------------------------------------------------------------------
# CASAMENTO DE JOGADORES (player props)
# ---------------------------------------------------------------------------
# Nomes de jogador variam mais que os de time: "Bryce Elder" vs "B. Elder"
# vs "Elder, Bryce". A estratégia: comparar o SOBRENOME (âncora) e, se houver,
# a inicial do primeiro nome. Sobrenomes diferentes => jogadores diferentes.

def normalize_player(name: str) -> str:
    """'Bryce Elder' / 'Elder, Bryce' / 'B. Elder' -> forma 'primeiro sobrenome'.
    Nome com vírgula ('Sobrenome, Primeiro') é reordenado para 'Primeiro Sobrenome'."""
    if not name:
        return ""
    s = strip_accents(str(name)).lower()
    if "," in s:                       # 'elder, bryce' -> 'bryce elder'
        partes = [p.strip() for p in s.split(",", 1)]
        s = f"{partes[1]} {partes[0]}" if len(partes) == 2 else partes[0]
    s = re.sub(r"[^a-z\s.]", " ", s)
    return " ".join(t for t in s.split() if t)


def _player_parts(norm: str) -> tuple[str, str]:
    """(sobrenome, inicial_do_primeiro_nome). Assume ordem ocidental
    'Primeiro [Meio] Sobrenome' (a vírgula já foi reordenada em normalize)."""
    toks = [t for t in norm.split() if t]
    if not toks:
        return "", ""
    if len(toks) == 1:
        return toks[0].replace(".", ""), ""
    sobrenome = toks[-1].replace(".", "")      # último token = sobrenome
    inicial = toks[0].replace(".", "")[:1]     # inicial do primeiro nome
    return sobrenome, inicial


def player_similarity(a: str, b: str) -> float:
    """Semelhança entre nomes de jogador, 0 a 1."""
    na, nb = normalize_player(a), normalize_player(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    sa, ia = _player_parts(na)
    sb, ib = _player_parts(nb)
    if not sa or not sb:
        return 0.0
    if sa != sb:
        # sobrenomes diferentes: só aceita se forem quase idênticos (typo/acento)
        if SequenceMatcher(None, sa, sb).ratio() < 0.9:
            return 0.2
    # sobrenome bate; a inicial confirma (se ambas existem)
    if ia and ib and ia != ib:
        return 0.5           # mesmo sobrenome, iniciais diferentes: desconfia
    return 0.95


def match_player(name: str, candidatos: list[str], min_score: float = 0.9) -> str | None:
    """Devolve o nome do candidato que casa, ou None. Rejeita ambiguidade."""
    scored = sorted(((player_similarity(name, c), c) for c in candidatos),
                    key=lambda x: -x[0])
    if not scored or scored[0][0] < min_score:
        return None
    if len(scored) > 1 and (scored[0][0] - scored[1][0]) < 0.05:
        return None
    return scored[0][1]
