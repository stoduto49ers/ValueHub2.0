"""
extension_parser.py — Traduz o que a extensão lê do DOM para o vocabulário
canônico do HUB.

A extensão entrega o que a API pública NÃO entrega: os mercados profundos,
com destaque para o **Handicap Asiático** (equivalente ao `spread` da
Pinnacle) e os totais asiáticos em linhas de quarto (2.25, 2.75), que são
exatamente as linhas que a Pinnacle publica.

Os nomes chegam em português e as seleções trazem o nome do time:

    "Handicap Asiático (Resultado atual 0 - 0)"
        "Atlético-MG -0.25" @ 1.83   ->  Spread, linha -0.25, lado home
        "Bahia +0.25"       @ 2.02   ->  Spread, linha +0.25, lado away

Quem é mandante e quem é visitante NÃO é adivinhado do DOM: o servidor usa o
`event_id` para consultar a API da Betano e obter a ordem correta. Aqui só
comparamos o nome da seleção com os nomes recebidos.
"""
from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Nome do mercado (PT, como aparece na tela) -> mercado canônico do HUB.
# A ordem importa: o primeiro padrão que casar vence, então os mais
# específicos (1º tempo, escanteios) vêm antes dos genéricos.
# ---------------------------------------------------------------------------
MARKET_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # --- ESCANTEIOS (a ORDEM importa: handicap antes dos totais, senão
    #     "asiático.*escanteios" rouba o handicap e vira total errado)
    # Handicap de escanteios = SPREAD
    (re.compile(r"handicap\s+asi[áa]tico.*escanteios|escanteios.*handicap\s+asi[áa]tico", re.I), "Corners Spread", "hcp"),
    (re.compile(r"^escanteios\s*[-–]?\s*handicap", re.I), "Corners Spread", "hcp"),
    (re.compile(r"escanteios\s+handicap", re.I), "Corners Spread", "hcp"),
    # Totais de escanteios ASIÁTICOS (2 vias, com push) — 1º tempo e jogo todo.
    # "Escanteios Asiáticos" (escanteios ANTES de asiático) estava sendo DESCARTADO.
    (re.compile(r"escanteios\s+asi[áa]tic.*(1[ºo°]|primeiro tempo)|(1[ºo°]\s*tempo|primeiro tempo).*escanteios\s+asi[áa]tic", re.I), "Corners Totals HT", "ou"),
    (re.compile(r"asi[áa]tico.*escanteios.*primeiro tempo", re.I), "Corners Totals HT", "ou"),
    (re.compile(r"^mais/menos 1.*tempo escanteios", re.I), "Corners Totals HT", "ou"),
    (re.compile(r"^total\s+escanteios.*1.*tempo", re.I), "Corners Totals HT", "ou"),
    (re.compile(r"escanteios\s+asi[áa]tic", re.I), "Corners Totals", "ou"),
    (re.compile(r"asi[áa]tico.*escanteios", re.I), "Corners Totals", "ou"),
    # nomes PT genéricos: "Escanteios", "Escanteios - Alternativas", etc.
    # ANCORADOS: NÃO pegam escanteios de TIME ("Escanteios do Time"/"Team Corners").
    (re.compile(r"^escanteios(\s*[-–]\s*(alternativas|2\s*op[çc][õo]es|2\s*vias))?$", re.I), "Corners Totals", "ou"),
    (re.compile(r"^total\s+(de\s+)?escanteios$", re.I), "Corners Totals", "ou"),
    (re.compile(r"^escanteios\s+totais$", re.I), "Corners Totals", "ou"),
    # --- 1º tempo
    (re.compile(r"handicap asi[áa]tico.*primeiro tempo", re.I), "Spread HT", "hcp"),
    (re.compile(r"asi[áa]tico.*total de gols.*1.*tempo", re.I), "Totals HT", "ou"),
    (re.compile(r"^total de gols - 1.*tempo$", re.I), "Totals HT", "ou"),
    (re.compile(r"^resultado do 1.*tempo", re.I), "ML HT", "3way"),
    # --- jogo completo
    (re.compile(r"^handicap asi[áa]tico", re.I), "Spread", "hcp"),
    (re.compile(r"asi[áa]tico \(mais/menos\).*total de gols", re.I), "Totals", "ou"),
    (re.compile(r"^total de gols$", re.I), "Totals", "ou"),
    # variantes PT de total de gols (Bet365/Betano): "Gols Mais/Menos",
    # "Total de Gols - Alternativas" (linhas alternativas do mesmo mercado).
    (re.compile(r"^gols\s+mais\s*/?\s*menos$", re.I), "Totals", "ou"),
    (re.compile(r"^total de gols\s*[-–]\s*alternativas$", re.I), "Totals", "ou"),
    (re.compile(r"^resultado final$", re.I), "ML", "3way"),

    # --- Bet365 usa nomes em INGLÊS (mesmo no site BR). Escanteios e 1º tempo
    #     antes dos genéricos. "Alternative ..." = linhas alternativas do mesmo
    #     mercado (também servem para value).
    (re.compile(r"(1st half|first half).*corners.*handicap", re.I), "Corners Spread HT", "hcp"),
    (re.compile(r"(1st half|first half).*corners", re.I), "Corners Totals HT", "ou"),
    (re.compile(r"^(asian )?corners? handicap", re.I), "Corners Spread", "hcp"),
    # ancorado em ^ para NÃO pegar "Team Corners" (escanteios de time, sem par sharp)
    (re.compile(r"^(alternative |total )?corners", re.I), "Corners Totals", "ou"),
    (re.compile(r"asian handicap.*(1st half|first half)", re.I), "Spread HT", "hcp"),
    (re.compile(r"^asian handicap", re.I), "Spread", "hcp"),
    (re.compile(r"(1st half|first half).*(goals|total).*(over|under|line)", re.I), "Totals HT", "ou"),
    # "Goal Line" = total de gols ASIÁTICO (2 vias, com push) da Bet365
    (re.compile(r"(1st half|first half).*goal\s*line", re.I), "Totals HT", "ou"),
    (re.compile(r"^(asian\s+)?goals?\s*line$|linha\s+de\s+gols", re.I), "Totals", "ou"),
    (re.compile(r"alternative total goals", re.I), "Totals", "ou"),
    (re.compile(r"(goals over/?under|total goals over/?under|^total goals$)", re.I), "Totals", "ou"),
    (re.compile(r"(1st half|first half) result", re.I), "ML HT", "3way"),
    (re.compile(r"^full time result$", re.I), "ML", "3way"),
]

# over/under em PT (Betano: "Mais de 2.5") e EN (Bet365: "Over 2.5")
_OVER_RE = re.compile(r"^\s*(?:mais|over)\s+(?:de\s+)?([\d.,-]+)", re.I)
_UNDER_RE = re.compile(r"^\s*(?:menos|under)\s+(?:de\s+)?([\d.,-]+)", re.I)
# "Atlético-MG -0.25" / "Bahia +0.25" / "Bahia 0.0"
_HCP_RE = re.compile(r"^(?P<time>.+?)\s*(?P<linha>[-+]?\d+(?:\.\d+)?)\s*$")
# handicap DIVIDIDO/quarto da Bet365: "Vitória +0.5, +1.0" = +0.75 ;
# "Botafogo -0.5, -1.0" = -0.75. A linha efetiva é a MÉDIA das duas — pegar só
# a última (+1.0) casava com a fair errada da Pinnacle e inventava valor.
_HCP_SPLIT_RE = re.compile(
    r"^(?P<time>.+?)\s*(?P<l1>[-+]?\d+(?:\.\d+)?)\s*,\s*(?P<l2>[-+]?\d+(?:\.\d+)?)\s*$")
# marca a existência de uma seleção "Exactly N" / "Exatamente" -> mercado de
# 3 vias (over/exato/under), que NÃO pode ser comparado como total de 2 vias.
_EXACT_RE = re.compile(r"\b(exact|exactly|exato|exata|exatamente|precisamente)\b", re.I)


def _norm(s: str) -> str:
    """minúsculas, sem acento — para comparar nomes de time com folga."""
    nfkd = unicodedata.normalize("NFD", str(s or "").lower())
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn").strip()


def _f(x):
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return None


def classify_market(nome: str) -> tuple[str, str] | None:
    """'Handicap Asiático (Resultado atual 0 - 0)' -> ('Spread', 'hcp')."""
    for padrao, canonico, forma in MARKET_PATTERNS:
        if padrao.search(nome or ""):
            return canonico, forma
    return None


def _side_por_time(rotulo: str, home: str, away: str) -> str | None:
    """Descobre se a seleção é do mandante ou do visitante pelo nome."""
    r = _norm(rotulo)
    h, a = _norm(home), _norm(away)
    if h and (r == h or r.startswith(h) or h.startswith(r)):
        return "home"
    if a and (r == a or r.startswith(a) or a.startswith(r)):
        return "away"
    return None


def parse_selection(forma: str, rotulo: str, odd: float,
                    home: str, away: str) -> tuple[str, float | None] | None:
    """(lado, linha) para uma seleção, ou None se não der para interpretar."""
    rotulo = (rotulo or "").strip()

    if forma == "ou":
        if (m := _OVER_RE.match(rotulo)):
            return ("over", _f(m.group(1)))
        if (m := _UNDER_RE.match(rotulo)):
            return ("under", _f(m.group(1)))
        return None

    if forma == "hcp":
        # handicap dividido/quarto ("Vitória +0.5, +1.0" = +0.75) — tenta ANTES
        ms = _HCP_SPLIT_RE.match(rotulo)
        if ms:
            lado = _side_por_time(ms.group("time"), home, away)
            if not lado:
                return None
            l1, l2 = _f(ms.group("l1")), _f(ms.group("l2"))
            if l1 is None or l2 is None:
                return None
            return (lado, round((l1 + l2) / 2.0, 3))
        m = _HCP_RE.match(rotulo)
        if not m:
            return None
        lado = _side_por_time(m.group("time"), home, away)
        if not lado:
            return None
        return (lado, _f(m.group("linha")))

    if forma == "3way":
        mapa = {"1": "home", "x": "draw", "2": "away", "empate": "draw", "draw": "draw"}
        if (lado := mapa.get(_norm(rotulo))):
            return (lado, None)
        if (lado := _side_por_time(rotulo, home, away)):
            return (lado, None)
        return None

    return None


# Player prop over/under, formato "Player Props - Fulano (Estatística)" (casas
# US via extensão) ou seleções "Fulano Mais de X". Só over/under de 2 lados,
# que é o que casa com a referência do FanDuel.
_PROP_MARKET_RE = re.compile(r"player props?\s*-\s*(?P<player>.+?)\s*\((?P<stat>[^)]+)\)", re.I)
_PROP_SEL_RE = re.compile(r"^(?P<player>.+?)\s+(?:mais|menos|over|under|o|u)\s+(?P<line>[\d.,]+)", re.I)


def parse_props_snapshot(snapshot: dict) -> list[dict]:
    """Extrai player props (over/under) do snapshot -> offered lines de prop.

    Formato canônico de saída por linha:
      {player, market='Prop: <Stat>', line, side(over/under), odd, book, ...}

    Conservador: só o que dá para interpretar com confiança. Props de gol de
    futebol (1 via) NÃO entram — não há como de-vigar contra o FanDuel.
    """
    book = "Bet365" if snapshot.get("source") == "bet365" else "Betano"
    out: list[dict] = []
    for mkt in snapshot.get("markets") or []:
        nome = mkt.get("market") or ""
        mm = _PROP_MARKET_RE.search(nome)
        for s in mkt.get("selections") or []:
            odd = _f(s.get("odd"))
            if not odd or odd <= 1.0:
                continue
            rot = str(s.get("sel") or "")
            player = stat = None
            line = None
            side = None
            if mm:                       # mercado já nomeia jogador+stat
                player, stat = mm.group("player").strip(), mm.group("stat").strip()
                if _OVER_RE.match(rot):
                    side, line = "over", _f(_OVER_RE.match(rot).group(1))
                elif _UNDER_RE.match(rot):
                    side, line = "under", _f(_UNDER_RE.match(rot).group(1))
            else:                        # tenta extrair da própria seleção
                sm = _PROP_SEL_RE.match(rot)
                if sm and re.search(r"mais|over|\bo\b", rot, re.I):
                    player, line, side, stat = sm.group("player").strip(), _f(sm.group("line")), "over", nome
                elif sm and re.search(r"menos|under|\bu\b", rot, re.I):
                    player, line, side, stat = sm.group("player").strip(), _f(sm.group("line")), "under", nome
            if not (player and stat and side and line is not None):
                continue
            out.append({
                "player": player, "market": f"Prop: {stat}", "line": line,
                "side": side, "odd": odd, "book": book,
                "event_id": str(snapshot.get("event_id") or ""),
                "url": snapshot.get("url") or "",
            })
    return out


def parse_snapshot(snapshot: dict, home: str, away: str) -> list[dict]:
    """Converte um snapshot da extensão em 'offered lines' canônicas.

    snapshot: {source, event_id, url, markets:[{market, selections:[{sel,odd}]}]}
    home/away: nomes vindos da API da casa (fonte de verdade da ordem).

    Função PURA — testável sem rede nem navegador.
    """
    book = "Bet365" if snapshot.get("source") == "bet365" else "Betano"
    url = snapshot.get("url") or ""
    event_id = str(snapshot.get("event_id") or "")
    out: list[dict] = []
    vistos: set[tuple] = set()

    for mkt in snapshot.get("markets") or []:
        alvo = classify_market(mkt.get("market") or "")
        if not alvo:
            continue
        canonico, forma = alvo
        sels = mkt.get("selections") or []
        # MERCADO DE 3 VIAS (tem seleção "Exactly N"/"Exatamente") NÃO é o total
        # asiático de 2 vias: nele "Over 9" ganha só com 10+ (o 9 é um resultado
        # à parte, não dá push), então a odd é outra. Comparar o over/under dele
        # contra a fair de 2 vias da Pinnacle INVENTA valor. Pula o mercado todo.
        if forma == "ou" and any(_EXACT_RE.search(str(s.get("sel") or "")) for s in sels):
            continue
        for s in sels:
            odd = _f(s.get("odd"))
            if not odd or odd <= 1.0:
                continue
            r = parse_selection(forma, s.get("sel") or "", odd, home, away)
            if not r:
                continue
            lado, linha = r
            chave = (canonico, linha, lado)
            if chave in vistos:      # o mesmo mercado aparece em várias abas
                continue
            vistos.add(chave)
            out.append({
                "market": canonico, "line": linha, "side": lado, "odd": odd,
                "source": snapshot.get("source") or "betano", "book": book,
                "event_home": home, "event_away": away,
                "event_id": event_id, "url": url,
            })
    return out
