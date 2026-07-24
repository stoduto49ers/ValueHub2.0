"""
config.py — Configuração central do Value HUB 2.0. Edite SÓ este arquivo.

Arquitetura v2: em vez de scraping do Telegram, o HUB consome direto a
odds-api.io (/value-bets), que compara cada casa contra o CONSENSO do mercado
(fair odds sem vig) a cada 5 segundos, com link direto para a aposta.

O campo `max` de cada mercado (limite de stake do consenso) substitui o
"Limit" da Pinnacle como proxy de liquidez para o edge mínimo escalonado.
"""
import os

# ----------------------------------------------------------------------------
# API — odds-api.io
# A chave é lida do ambiente ODDS_API_KEY ou do arquivo .env na raiz do projeto
# (linha: ODDS_API_KEY=xxxx). NUNCA commite a chave.
# ----------------------------------------------------------------------------
API_BASE = "https://api.odds-api.io/v3"


def _load_api_key() -> str:
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if key:
        return key
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line.startswith("ODDS_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


API_KEY = _load_api_key()

# ----------------------------------------------------------------------------
# CASAS-ALVO — uma chamada de /value-bets por casa por ciclo.
# Free tier: máx 2 casas (hoje: Bet365 + FanDuel). Ao fazer upgrade, adicione
# aqui os nomes EXATOS da odds-api (ex.: "Betano BR", "KTO", "Superbet",
# "Estrela Bet", "BetMGM BR", "Sportingbet BR", "Pixbet", "Betnacional").
# Casas que retornarem 403 são desativadas automaticamente no runtime.
# ----------------------------------------------------------------------------
# VAZIO de propósito: a Bet365 NÃO vem da odds-api (só raspagem própria via
# extensão). Da odds-api usamos SÓ o FanDuel, e apenas para player props
# (motor separado run_fanduel). Nada de game lines de terceiros aqui.
TARGET_BOOKS = [
    # "Bet365",   <- removido: Bet365 é raspada por nós, não pela odds-api
    # "Betano BR",
    # "KTO",
    # "Superbet",
]

# ----------------------------------------------------------------------------
# BANCA E SIZING (quarter Kelly + salvaguardas de segurança)
# ----------------------------------------------------------------------------
BANKROLL = 10_000.0          # banca em R$
UNIT_PCT = 0.01              # 1u = 1% da banca = R$ 100
KELLY_FRACTION = 0.25        # quarter Kelly (baixe p/ 0.125 = mais conservador)
KELLY_CAP_PCT = 0.03         # teto por aposta em game lines (3u)
PROPS_KELLY_CAP_PCT = 0.02   # teto MENOR para player props (2u) — mais incertos
STAKE_STEP_U = 0.25          # arredonda stakes em degraus de 0.25u
STAKE_MIN_U = 0.25           # stake mínima
PAPER_TRADING = True

# --- AMORTECIMENTO DE STAKE POR ODD ------------------------------------------
# Odd alta = mais variância e mais incerteza. NÃO há teto de odd (você aposta
# em odd alta se houver valor real), mas a stake é AMORTECIDA progressivamente
# conforme a odd sobe, além do que o Kelly já reduz. Reflete "quanto maior a
# odd, mais conservador".
#   odd <= START            -> stake cheia
#   START < odd < END        -> amortecimento linear até FLOOR
#   odd >= END               -> stake em FLOOR (não zera; ainda aposta)
MAX_ODDS = None              # sem teto de odd (era 8.0; removido a seu pedido)
ODDS_DAMPEN_START = 2.5      # abaixo disto, sem amortecimento
ODDS_DAMPEN_END = 12.0       # daqui pra cima, amortecimento máximo
ODDS_DAMPEN_FLOOR = 0.4      # nunca reduz abaixo de 40% da stake do Kelly

# ----------------------------------------------------------------------------
# ABA "VALUE BETS" — mercados principais de Futebol e Basquete
# ----------------------------------------------------------------------------
# Esportes e mercados que a odds-api (Bet365) manda para a aba Value.
# Ampliado para bater com o que o caminho Pinnacle/Betano já cobre — senão a
# maioria dos value bets da Bet365 (48/ciclo!) fica escondida em "Outros".
VALUE_SPORTS = {"Football", "Basketball", "Baseball",
                "Ice Hockey", "American Football"}
VALUE_MARKETS = {"ML", "Moneyline", "Spread", "Totals",
                 "Corners Spread", "Corners Totals",
                 "Bookings Spread", "Bookings Totals"}
INCLUDE_HALF_MARKETS = True                         # inclui 1º tempo (HT) no Value

# Ligas "maiores" — mais liquidez e limitação mais lenta.
# Os nomes seguem a convenção "País - Liga" (idêntica na Pinnacle e na
# odds-api). O casamento é por PREFIXO, então "uefa - champions league"
# também pega "...Qualifiers". Prefixo com país evita o falso positivo
# clássico (ex.: "premier league" casando com Líbano).
MAJOR_LEAGUES = [
    # -- Europa (top 5 + copas nacionais grandes)
    "england - premier league", "england - championship", "england - efl cup",
    "england - fa cup",
    "spain - la liga", "spain - copa del rey",
    "italy - serie a", "italy - coppa italia",
    "germany - bundesliga", "germany - dfb pokal",
    "france - ligue 1", "france - coupe de france",
    "netherlands - eredivisie", "portugal - primeira liga",
    "scotland - premiership", "belgium - first division",
    "turkey - super lig", "sweden - allsvenskan", "norway - eliteserien",
    "denmark - superliga", "switzerland - super league",
    "austria - bundesliga", "greece - super league",
    # -- competições continentais
    "uefa - champions league", "uefa - europa league",
    "uefa - conference league", "uefa - nations league",
    "uefa - euro", "fifa - world cup", "fifa - club world cup",
    "conmebol - copa libertadores", "conmebol - copa sudamericana",
    "conmebol - copa america",
    # -- Américas
    "brazil - serie a", "brazil - serie b", "brazil - copa do brasil",
    "argentina - liga pro", "argentina - copa argentina",
    "usa - major league soccer", "mexico - liga mx",
    "colombia - primera a", "chile - primera division",
    # -- basquete
    "nba", "wnba", "ncaa", "euroleague", "eurocup",
    "spain - acb", "brazil - nbb", "italy - lega a", "germany - bbl",
    # -- ligas americanas (props entram por aqui). Duas convenções de nome:
    # a Pinnacle usa "NFL"; a odds-api usa "USA - NFL".
    "nfl", "nhl", "mlb",
    "usa - nfl", "usa - nhl", "usa - mlb", "usa - nba", "usa - wnba",
    "canada - cfl",
    # -- MMA / boxe: só os eventos grandes, que têm liquidez
    "ufc", "bellator", "pfl",
]
ONLY_MAJOR_LEAGUES = True     # False = coleta/mostra todas as ligas


def is_major_league(name: str) -> bool:
    """Casamento por prefixo, caso-insensível. Usado tanto pelo coletor da
    Pinnacle quanto pelo motor da odds-api — uma regra só, sem divergência."""
    if not ONLY_MAJOR_LEAGUES or not MAJOR_LEAGUES:
        return True
    low = (name or "").strip().lower()
    return any(low.startswith(pat) for pat in MAJOR_LEAGUES)

# ----------------------------------------------------------------------------
# ABA "PLAYER PROPS" — NFL, NBA, NHL (+ WNBA/MLB enquanto as outras não voltam)
# ----------------------------------------------------------------------------
PROPS_SPORTS = {"American Football", "Basketball", "Ice Hockey", "Baseball"}
PROPS_MIN_EDGE_PCT = 4.0      # props têm consenso mais fraco -> exige mais edge

# ----------------------------------------------------------------------------
# EDGE MÍNIMO ESCALONADO POR LIQUIDEZ (campo `max` do consenso, em EUR)
# Lido de cima p/ baixo: usa a primeira faixa cujo piso <= max do mercado.
# ----------------------------------------------------------------------------
# Quanto MENOR o limite (liquidez), MAIOR o edge exigido — qualquer valor
# balança um mercado de €50/€100, então lá o corte é alto (>10%). Conforme o
# limite sobe, o corte cai, até o piso de 2,8% que o usuário escolheu.
LIQUIDITY_TIERS = [
    (2500, 2.8),   # >= €2500: muito líquido -> piso 2,8%
    (1500, 3.5),
    (800,  4.5),
    (400,  6.0),
    (200,  8.0),
    (100, 10.5),   # €100 -> exige > 10%
    (0,   13.0),   # < €100 (ex.: €50) -> exige 13%
]
MIN_MAX_ABSOLUTE = 50         # mercados com max < €50 são descartados
DEFAULT_MIN_EDGE_PCT = 4.0    # quando o mercado não informa `max`
EDGE_SANITY_MAX_PCT = 20.0    # acima disso marca como "suspeita" (linha podre/palp)
MIN_DISPLAY_EDGE_PCT = 2.8    # piso de EXIBIÇÃO: edge < 2,8% nunca aparece no painel

# ----------------------------------------------------------------------------
# POLLER
# Free tier = 100 requests/HORA. Com 1 casa, 45s -> 80 req/h (folga p/ extras).
# Com 2 casas, use >= 90. Nos planos pagos (5000 req/h) pode descer p/ 10-20s.
# ----------------------------------------------------------------------------
POLL_INTERVAL_SEC = 45        # 1 request por casa por ciclo
STALE_AFTER_SEC = 120         # oportunidade some do feed -> inativa
BACKOFF_ON_429_SEC = 120

# CLV: congela o fechamento (última linha da Pinnacle pré-jogo) neste intervalo,
# INDEPENDENTE da odds-api. Curto para pegar o início dos jogos com precisão.
CLV_FREEZE_INTERVAL_SEC = 30
# se o fechamento capturado for mais velho que isto (min) antes do início do
# jogo, marca o CLV como aproximado (servidor pode ter ficado offline no kickoff)
CLV_STALE_MINUTES = 20

# Bet365 vem pela extensão (sem feed contínuo): oportunidades não relidas há
# mais que isto viram inativas. Rede de segurança p/ linhas erradas antigas que
# você não vai reler — o cross já desativa por jogo+mercado quando você relê.
BET365_STALE_SEC = 600

# ----------------------------------------------------------------------------
# SERVIDOR
# ----------------------------------------------------------------------------
HOST = "127.0.0.1"
PORT = 8000                   # mesma porta da v1 -> extensão continua funcionando

DEVIG_METHOD = "shin"         # de-vig padrão (boosts e fontes sharp)

# ============================================================================
# CONSENSO DE SHARPS — pesos por fonte (quanto confiamos em cada uma)
# ============================================================================
# Quando duas ou mais sharps cobrem o mesmo mercado, a fair odds vira uma média
# PONDERADA das probabilidades justas de cada uma. Peso maior = mais influência.
# Adicionar uma sharp nova = criar o coletor + pôr o peso aqui. Nada mais muda.
#
# Linhas regulares (ML/Spread/Totals/Corners): a Pinnacle é a referência.
SHARP_WEIGHTS = {
    "pinnacle": 1.0,
    "betfair":  0.9,   # bolsa — sinal sharp forte (coletor a implementar)
    "fanduel":  0.4,   # sharp em props, fraca em linha regular -> peso baixo
    "_default": 0.3,
}
# Player props americanos: FanDuel/Circa são as sharps; Pinnacle cobre pouco.
SHARP_WEIGHTS_PROPS = {
    "fanduel":  1.0,
    "circa":    1.0,   # (coletor a implementar; via upgrade odds-api)
    "pinnacle": 0.5,
    "_default": 0.4,
}

# ============================================================================
# FONTES SHARP PRÓPRIAS (infra nossa — sem custo recorrente)
# ============================================================================
# A referência "fair odds" deixa de ser comprada e passa a ser CALCULADA por
# nós: raspamos a casa sharp, de-vigamos e o resultado é a probabilidade justa.
# Arquitetura plugável: cada fonte é um coletor. Hoje Pinnacle; depois FanDuel
# (referência de props americanos) e outras, formando um consenso sharp.
# ----------------------------------------------------------------------------
PINNACLE_ENABLED = True
PINNACLE_BASE = "https://guest.api.arcadia.pinnacle.com/0.1"
# chave pública embutida no front-end da própria Pinnacle (endpoint "guest")
PINNACLE_KEY = "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R"
PINNACLE_REFERER = "https://www.pinnacle.com/"

# IDs de esporte na Pinnacle (confirmados por sondagem no /sports).
# Só entram esportes de boa liquidez; as ligas ainda passam pela whitelist.
PINNACLE_SPORTS = {
    "Soccer": 29,               # 580 jogos
    "Football": 15,             # futebol americano — NFL
    "Basketball": 4,            # NBA, WNBA
    "Baseball": 3,              # MLB
    "Hockey": 19,               # NHL
    "Mixed Martial Arts": 22,   # UFC
}

# Só colhe jogos que começam dentro desta janela (economiza requests).
PINNACLE_HORIZON_HOURS = 72
# Pausa entre requests de matchup — educado com o servidor deles.
PINNACLE_REQUEST_DELAY = 0.35
# Cache da lista de ligas (elas mudam devagar).
PINNACLE_LEAGUES_CACHE_SEC = 1800
# Intervalo entre varreduras completas da Pinnacle.
PINNACLE_SWEEP_INTERVAL_SEC = 300
# Períodos: 0 = jogo completo, 1 = primeiro tempo (vira sufixo " HT").
PINNACLE_PERIODS = [0, 1]

# Sub-jogos da Pinnacle que queremos coletar, por campo `units`.
# O sub-jogo é identificado por `units` (NÃO por `special`, que vem vazio nos
# escanteios). Sub-jogo sem rótulo aqui é descartado — é o que impede um total
# de escanteios de entrar como total de gols.
# Obs.: a Pinnacle não oferece cartões nas ligas da nossa whitelist; a Betano
# oferece (TCOU), mas sem referência sharp não há como medir valor.
PINNACLE_SUB_UNITS = {
    "Corners": "Corners",
    # CARTÕES: a Pinnacle oferece em algumas ligas, mas nenhuma da whitelist
    # tinha no momento do mapeamento. Os rótulos abaixo ficam pré-cadastrados
    # para que, assim que aparecerem, sejam coletados sem mexer no código.
    "Bookings": "Bookings",
    "Cards": "Bookings",
    "Yellow Cards": "Bookings",
}
# Sub-mercados com `units` desconhecido são contados e expostos no /api/status.
# É a "busca ativa": quando a Pinnacle publicar cartões (ou outro mercado novo)
# com um rótulo diferente, ele aparece aqui e basta adicioná-lo ao mapa acima.
PINNACLE_REPORT_UNKNOWN_UNITS = True
# Ignora linhas alternativas muito distantes (mantém as principais + alt úteis).
PINNACLE_INCLUDE_ALTERNATES = True

# ----------------------------------------------------------------------------
# FANDUEL — referência sharp para PLAYER PROPS (via odds-api /value-bets)
# Só roda se houver ODDS_API_KEY. FanDuel é a sharp reconhecida de props
# americanos; de-vigamos a linha dele para NBA/NFL/NHL/MLB/WNBA.
# ----------------------------------------------------------------------------
FANDUEL_PROPS_ENABLED = True
FANDUEL_SWEEP_INTERVAL_SEC = 120

# ----------------------------------------------------------------------------
# CASAS-ALVO (onde apostamos) — raspagem própria, sem login
# ----------------------------------------------------------------------------
BETANO_ENABLED = True
BETANO_BASE = "https://www.betano.bet.br"
BETANO_SWEEP_INTERVAL_SEC = 180
BETANO_REQUEST_DELAY = 0.3
BETANO_HORIZON_HOURS = 72

# Ligas da Betano por trecho de URL. Usamos a URL (e não o nome) porque
# "Premier League" existe para Inglaterra, Canadá, Butão, Líbano, Somália e
# até Esoccer — o país no caminho elimina a ambiguidade.
BETANO_LEAGUE_URLS = [
    "/brasil/brasileirao-serie-a", "/brasil/brasileirao-serie-b",
    "/brasil/copa-betano-do-brasil",
    "/copa-libertadores/copa-libertadores", "/copa-sul-americana/copa-sul-americana",
    "/liga-dos-campeoes/liga-dos-campeoes", "/liga-europa/liga-europa",
    "/liga-conferencia/liga-conferencia",
    "/inglaterra/premier-league", "/inglaterra/championship",
    "/espanha/laliga", "/italia/serie-a", "/alemanha/bundesliga",
    "/franca/ligue-1", "/portugal/primeira-liga",
    "/paises-baixos/eredivisie", "/eua/mls", "/mexico/liga-mx",
    "/noruega/eliteserien", "/suecia/allsvenskan",
    # -- basquete e beisebol americanos (game lines com sharp na Pinnacle;
    #    a Betano TEM esses jogos e também player props deles)
    "/basquete/eua/nba/", "/basquete/eua/wnba/", "/beisebol/eua/mlb/",
]
# Esportes da Betano a varrer (slug na URL deles). Cada um busca suas ligas e
# filtra por BETANO_LEAGUE_URLS.
BETANO_SPORTS = ["futebol", "basquete", "beisebol"]

# ----------------------------------------------------------------------------
# MERCADOS PROFUNDOS via navegador (Handicap Asiático & linhas de quarto)
# ----------------------------------------------------------------------------
# A API pública devolve 19 dos ~950 mercados. O resto vem por SignalR, mas a
# própria interface resolve com um clique na aba "Todos" — então automatizamos
# o clique com Playwright, no servidor, sem ninguém navegando.
#
# IMPORTANTE: em modo HEADLESS a aba "Todos" não troca (o app Vue da Betano
# ignora o clique sintético). Com janela visível funciona. Por isso o padrão
# é headless=False. A janela abre e fecha sozinha; não exige interação.
BETANO_DEEP_ENABLED = False        # ligue depois de validar na sua máquina
BETANO_DEEP_HEADLESS = False       # True não funciona para a aba "Todos"
BETANO_DEEP_MAX_EVENTS = 25        # jogos por ciclo (os mais próximos primeiro)
BETANO_DEEP_INTERVAL_SEC = 900     # ~10s por jogo -> 25 jogos ≈ 4min
# Confirmação do portão "18+" — autorizada explicitamente pelo usuário.
BETANO_DEEP_ACEITAR_IDADE = True

# ----------------------------------------------------------------------------
# CRUZAMENTO SHARP x ALVO (detecção de valor com infra própria)
# ----------------------------------------------------------------------------
# Tolerância de horário no casamento de eventos (na prática batem exato).
MATCH_MAX_MINUTES = 90
# Pontuação mínima para aceitar um casamento (média dos dois times).
MATCH_MIN_SCORE = 0.85
# Nenhum dos lados pode ficar abaixo disto.
MATCH_MIN_SIDE_SCORE = 0.72
