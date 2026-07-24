# Value HUB 2.0

Sistema pessoal de detecção de value bets — **infra própria, custo de dados R$ 0/mês**.

## A ideia central

Todo value betting depende de uma **referência sharp**: a probabilidade justa.
É a única coisa que os serviços pagos realmente vendem (RebelBetting,
OddsNotifier, odds-api…). Aqui ela é **calculada por nós**:

```
odds cruas da Pinnacle  →  de-vig (Shin)  →  FAIR ODDS  (nossa referência)
                                                  ↓
odds da Betano (raspagem própria) → casamento de jogo+mercado → edge → Kelly
```

A Pinnacle é a casa sharp por definição (margem baixa, aceita ganhador, move
com dinheiro informado). De-vigar a linha dela é o método clássico de estimar
a probabilidade verdadeira. O endpoint público `guest` do front-end dela
devolve tudo em JSON, **sem login e sem custo** — validado do Brasil.

Cada mercado ainda traz `limits[].amount` (limite de risco da Pinnacle), que
é o melhor proxy de liquidez existente e alimenta os `LIQUIDITY_TIERS`.

> A arquitetura trata "fonte sharp" como **plugável**. Hoje Pinnacle (linhas)
> + FanDuel (props); adicionar outra = criar o coletor e pôr o peso em
> `config.SHARP_WEIGHTS`.

## Consenso de sharps

Quando duas ou mais sharps cobrem o mesmo jogo/mercado/lado, a fair odds vira
a **média ponderada** das probabilidades justas de cada uma
([consensus.py](valuehub/consensus.py)). Pesos por tipo de mercado
(`config.SHARP_WEIGHTS` para linhas, `SHARP_WEIGHTS_PROPS` para props) — a
Pinnacle domina as linhas regulares; FanDuel/Circa os props. O consenso é
**por (mercado, linha, lado) exato**, nunca renormalizando entre lados
diferentes (num spread, "Time A -2.5" e "Time B -2.5" são apostas distintas,
não os dois lados de um mercado). Cada aposta mostra de quantas sharps o
consenso saiu.

Sharps acessíveis do Brasil para adicionar: **Betfair Exchange** (bolsa, 200 OK
— ótimo sinal). Circa/DraftKings são geo-bloqueadas (EUA) — só via upgrade da
odds-api.

## O casamento de jogos — a peça mais delicada

A Pinnacle fala inglês ("Atletico Mineiro"), a Betano fala português
("Atlético-MG"). Casar errado inventa uma oportunidade que não existe e faz
apostar contra a referência de outro jogo — **o erro mais caro que este
sistema pode cometer**. Por isso [matching.py](valuehub/matching.py) exige
três condições simultâneas:

1. **horário compatível** (na prática batem exato nos dois lados)
2. **os dois times casando** (mandante com mandante)
3. **ausência de ambiguidade** — dois candidatos empatados? rejeita ambos

Casos reais resolvidos: acentos (`São Paulo`↔`Sao Paulo`), siglas
(`Athletico-PR`↔`Athletico Paranaense`), ruído (`Botafogo FR RJ`), e o
armadilhoso `Botafogo-RJ` **≠** `Botafogo-SP` — a sigla do estado é
preservada justamente porque distingue clubes homônimos.

Toda aposta guarda o `match_score`; abaixo de 100% o painel sinaliza.

## Como rodar

1. Dê dois cliques em `run.bat` (ou `python -m valuehub`).
2. Abra http://localhost:8000

**Não precisa de chave nem de assinatura** — o motor sharp (Pinnacle) roda
sozinho. O `.env` com `ODDS_API_KEY` é opcional: se existir, liga também o
motor legado da odds-api como conferência cruzada; se não, o HUB roda 100%
com infra própria.

Dependências (uma vez): `pip install -r requirements.txt` no ambiente
`nfl_env`.

## Abas do painel

- **Value Bets** — oportunidades de game lines (ML/Spread/Totals/Corners,
  incl. Handicap Asiático) da casa-alvo vs. Pinnacle.
- **Value Props** — oportunidades de player props da casa-alvo vs. FanDuel.
- **Sharp** — referência de game lines: linha crua da Pinnacle → fair odds
  de-vigada, com o limite de risco por mercado.
- **Sharp Props** — referência de player props: linha do FanDuel de-vigada.
- **Aumentadas / Apostas / Outros** — boosts, registro/CLV, e o resto.

### (detalhe) Value Bets — mercados
- **Value Bets** — ML / Spread / Totals de Futebol e Basquete em ligas
  maiores (whitelist em `config.MAJOR_LEAGUES`). Edge mínimo escalonado pela
  liquidez do mercado (`config.LIQUIDITY_TIERS`).
- **Player Props** — props de NFL, NBA, NHL (+ WNBA/MLB no verão americano),
  com edge mínimo próprio (`PROPS_MIN_EDGE_PCT`, padrão 4%).
- **Aumentadas** — boosts capturados pela extensão Chrome + calculadora de
  de-vig (simples e combinada, método Shin).
- **Apostas** — registro em 1 clique, stake por quarter-Kelly em degraus de
  0.25u, liquidação W/L/P, ROI e **CLV automático** (a fair odd do consenso é
  congelada quando o jogo começa e vira a linha de fechamento).
- **Outros** — tudo que tem edge mas está fora dos filtros principais
  (ligas menores, mercados HT, córners…).

## Casas

`config.TARGET_BOOKS`. Free tier da odds-api = máx. 2 casas (hoje Bet365 +
FanDuel). Com upgrade, basta adicionar os nomes (ex.: `"Betano BR"`, `"KTO"`,
`"Superbet"`) — casas não cobertas pelo plano são desativadas sozinhas (403)
sem derrubar o resto.

## Extensão Chrome

A pasta `extension/` é a mesma da v1 (aponta para `localhost:8000`, contrato
`/boost` e `/odds` mantido). Instalação: `chrome://extensions` → modo
desenvolvedor → "Carregar sem compactação".

## Estrutura

```
valuehub/
├── config.py         # EDITE AQUI: ligas, tiers, Kelly, banca, fontes
├── core.py           # matemática: de-vig (Shin), EV, CLV, Kelly
├── matching.py       # casamento de jogos PT<->EN (regra de ouro: na dúvida, não casa)
├── valuefinder.py    # cruza sharp x casa -> edge -> oportunidade
├── sources/
│   ├── pinnacle.py   # COLETOR SHARP — raspa, de-viga, gera as fair lines
│   └── betano.py     # COLETOR ALVO — API pública da Betano (Kaizen)
├── engine.py         # normaliza /value-bets -> oportunidades (legado)
├── oddsapi.py        # cliente odds-api (opcional/legado)
├── poller.py         # 3 motores: sharp + alvos + odds-api
├── db.py             # SQLite (hub2.db) — fair_lines, opportunities, bets
└── server.py         # FastAPI: painel + API + endpoints da extensão
tests/                # testes das partes críticas (matching e coletores)
web/                  # dashboard (dark, 6 abas)
extension/            # extensão Chrome (compatível com a v1)
```

Testes: `python -m tests.test_matching` e `python -m tests.test_sources`.

## Roteiro

- [x] **Fase 0** — validar acesso à Pinnacle do Brasil (HTTP 200, sem proxy)
- [x] **Fase 1** — coletor da Pinnacle: ML/Spread/Totals das ligas grandes,
      de-vig automático, ~3.600 linhas justas por varredura
- [x] **Fase 2** — Betano raspada (ML + Totals), casamento de jogos validado
      em dados reais (99 jogos casados) e cruzamento gerando edge
- [x] **Fase 2.1** — escanteios (Pinnacle `units='Corners'` ↔ Betano `CNOU`)
      e mercados de 1º tempo (período 1 ↔ `OUH1`/`H1RS`/`COU1`)
- [ ] **Fase 2.2** — Handicap Asiático da Betano e Bet365: ambos exigem a
      extensão Chrome (ver abaixo)
- [ ] **Fase 2.3** — KTO como segunda casa-alvo (responde 200, é raspável)
- [ ] **Fase 3** — player props americanos (NFL/NBA/NHL), com FanDuel como
      referência sharp; consenso multi-fonte

### Mercados cobertos

| Mercado | Pinnacle (sharp) | Betano (alvo) |
|---|---|---|
| Resultado (ML) | `moneyline` p0 | `MRES` |
| Total de gols | `total` p0 | `HCTG` |
| Handicap asiático | `spread` p0 | via **extensão** ✅ |
| Escanteios | `units='Corners'` | `CNOU`, `COU1` |
| 1º tempo | período 1 | `OUH1`, `H1RS` |
| Cartões | ❌ não oferece | `TCOU` |

Onde falta um dos lados não há como medir valor — é melhor não ter o mercado
do que ter uma comparação inventada.

### Mercados profundos da Betano — HTTP puro, sem navegador

A API pública da Betano entrega 19 dos ~950 mercados. O resto **não** exige
navegador nem extensão: a aba "Todos" apenas dispara o XHR
`/api/odds/{slug}/{id}/?bt=13`. Adicionar `?bt=13` traz os 512 mercados de uma
vez — incluindo o **Handicap Asiático** (`AHRF`, = `spread` da Pinnacle, com
linhas de quarto -0.25/-0.75) e os totais asiáticos. Tudo server-side, sem
login, ~1 request por jogo. É o que o `BetanoSource.event_markets(deep=True)`
faz.

> Descoberta importante: o parâmetro é `bt` (não `tab`/`marketTab`, que
> testei antes e davam 404). `?bt=11` traz só o handicap asiático; `?bt=13`
> traz tudo.

### FanDuel — referência sharp de player props

A Pinnacle cobre pouco player prop. Para NBA/NFL/NHL/MLB/WNBA, o FanDuel é a
sharp reconhecida. [fanduel.py](valuehub/sources/fanduel.py) pega os props do
`/value-bets` (que traz a odd de 2 lados do FanDuel), **de-viga a linha do
próprio FanDuel** e grava como `fair_lines` com `source='fanduel'`. Requer
`ODDS_API_KEY`. No verão americano testa com MLB (strikeouts) e WNBA.

### Bet365 — via extensão, e depois sozinha

`bet365.bet.br` devolve **403** com `Cf-Mitigated: challenge` (Cloudflare).
Contornar detecção de bot não é algo que este projeto faz. O caminho é a
**extensão Chrome** ([extension/](extension/)): **você abre a Bet365 e passa o
"não sou robô" uma vez**.

**Sem entrar jogo a jogo**: abra a lista de uma liga/competição e clique
"Varrer" no popup da extensão. Como a Bet365 é SPA (jogos não têm URL própria),
a extensão **clica em cada jogo sozinha**, deixa o auto-captura enviar, e volta
para a lista (`b365_crawl` em [content.js](extension/content.js)).

Tudo que a extensão captura aparece na aba **👁 Odds Extraídas** do painel —
cru, antes de qualquer cruzamento. É a prova de que a leitura funcionou (o
value bet só surge quando bate contra uma sharp).

O servidor traduz o DOM ([extension_parser.py](valuehub/sources/extension_parser.py))
e cruza com a Pinnacle. Como a Bet365 não tem API pública, o casamento usa os
**nomes dos times lidos do DOM** — o casador exige pontuação mais alta e zero
ambiguidade quando não há horário confiável (a regra de ouro continua: na
dúvida, não casa).

### Player props — FanDuel como sharp, casador de jogador

A referência de props é o **FanDuel via odds-api** (site dá 403 do Brasil +
PerimeterX — raspar direto é inviável). O cruzamento casa **evento** (times) e
depois **jogador** ([matching.py](valuehub/matching.py) `match_player`), que
resolve "Bryce Elder" ↔ "B. Elder" ↔ "Elder, Bryce". Só props over/under de
2 lados entram (de-vigáveis).

> Cobertura hoje: os props do FanDuel são de esportes americanos (MLB/WNBA no
> verão); a Betano BR tem props de futebol. A sobreposição real aparece quando
> a casa-alvo (ex.: Bet365) oferecer props dos mesmos jogos americanos. A
> infraestrutura está pronta e testada para quando isso ocorrer.

## Disciplina (herdada da v1)

- CLV é o juiz: meta de 50–100 apostas antes de escalar stake.
- Quarter Kelly, teto 3% da banca, degraus de 0.25u.
- Edge > 20% é marcado como **suspeito** (linha podre / palp) — não aposte
  sem conferir manualmente.
