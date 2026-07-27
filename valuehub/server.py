"""
server.py — Cérebro central do Value HUB 2.0 (FastAPI).

Sobe o poller automático da odds-api + serve o painel + recebe a extensão.
Endpoints da extensão (/boost, /odds) mantêm o contrato da v1: a extensão
antiga continua funcionando sem alteração.
"""
from __future__ import annotations
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, core, db
from .poller import Poller

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
# o coletor faz centenas de requests por varredura — só erros interessam
logging.getLogger("httpx").setLevel(logging.WARNING)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")

poller = Poller()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    poller.start()
    yield
    await poller.stop()


app = FastAPI(title="Value HUB 2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


# ------------------------------------------------------------------- painel

# desabilita cache do navegador: como o painel muda com frequência, o Chrome
# guardava um app.js/css antigo e o usuário via comportamento velho (ex.: botão
# W/L/P sem efeito). Estes headers forçam sempre a versão mais nova.
_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
             "Pragma": "no-cache", "Expires": "0"}


@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"), headers=_NO_CACHE)


class NoCacheStatic(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        for k, v in _NO_CACHE.items():
            resp.headers[k] = v
        return resp


app.mount("/static", NoCacheStatic(directory=WEB_DIR), name="static")


# ---------------------------------------------------------------------- api

@app.get("/api/status")
def api_status():
    return {
        "poller": poller.status(),
        "bankroll": config.BANKROLL,
        "unit_value": config.BANKROLL * config.UNIT_PCT,
        "summary": db.bets_summary(),
    }


@app.get("/api/opportunities")
def api_opportunities(tab: str = "value", min_edge: float = 0.0,
                      sport: str = "", search: str = "", active: int = 1,
                      limit: int = 300, collapse: int = 1, hide_bet: int = 1,
                      min_limit: float = 0.0, book: str = ""):
    # piso de exibição: edge < MIN_DISPLAY_EDGE_PCT nunca aparece, mesmo que o
    # filtro do painel peça menos (o usuário não tem interesse abaixo disso)
    min_edge = max(min_edge, config.MIN_DISPLAY_EDGE_PCT)
    rows = db.list_opportunities(tab=tab, active_only=bool(active),
                                 min_edge=min_edge, sport=sport, search=search,
                                 limit=limit, collapse=bool(collapse),
                                 hide_bet=bool(hide_bet), min_limit=min_limit,
                                 book=book)
    return {"count": len(rows), "rows": rows}


@app.post("/api/restake")
async def restake(req: Request):
    """Mini calculadora inline: a casa 'derreteu' a odd indicada — dada a NOVA
    odd, recalcula edge e stake contra a MESMA fair odds da oportunidade.
    Usa exatamente o mesmo motor (edge + quarter Kelly com amortecimento/teto)
    do botão Apostar, então os números batem."""
    d = await req.json()
    opp = db.get_opportunity(str(d.get("opportunity_id", "")))
    if not opp:
        raise HTTPException(404, "oportunidade não encontrada")
    try:
        odd = float(d["odd"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "odd inválida")
    if odd <= 1.0:
        raise HTTPException(400, "odd deve ser > 1.0")
    fair_prob = opp["fair_prob"]
    is_prop = opp.get("tab") == "props"
    edge = core.edge_percent(fair_prob, odd)
    sizing = core.kelly_stake_cfg(fair_prob, odd, config, is_prop=is_prop)
    return {"edge_pct": round(edge, 2), "stake_units": sizing["stake_units"],
            "stake_amount": sizing["stake_amount"], "fair_odd": opp["fair_odd"],
            "is_value": edge > 0}


@app.post("/api/parlay")
async def parlay(req: Request):
    """Calcula uma DUPLA (ou múltipla) a partir de N oportunidades selecionadas.
    Útil para odds baixas (1.2–1.4) em ML: combinar 2 pernas +EV compõe o edge
    multiplicativamente. fair_prob = produto das probs justas; odd = produto das
    odds da casa. Kelly sobre a combinada. AVISA se as pernas são do mesmo jogo
    (aí NÃO são independentes e a prob do produto não vale)."""
    d = await req.json()
    ids = d.get("ids") or []
    opps = [db.get_opportunity(str(i)) for i in ids]
    opps = [o for o in opps if o]
    if len(opps) < 2:
        raise HTTPException(400, "selecione ao menos 2 apostas")
    prob, odd = 1.0, 1.0
    for o in opps:
        prob *= o["fair_prob"]
        odd *= o["offered_odd"]
    edge = core.edge_percent(prob, odd)
    sizing = core.kelly_stake_cfg(prob, odd, config)
    eventos = {(o["event_home"], o["event_away"]) for o in opps}
    return {
        "combined_odd": round(odd, 3), "fair_prob": prob,
        "fair_odd": round(core.prob_to_odd(prob), 3) if 0 < prob < 1 else None,
        "edge_pct": round(edge, 2), "is_value": edge > 0,
        "stake_units": sizing["stake_units"], "stake_amount": sizing["stake_amount"],
        "same_event": len(eventos) < len(opps),   # pernas do mesmo jogo => não independentes
        "legs": [{"event": f"{o['event_home']} x {o['event_away']}",
                  "market": o["market"], "side": o["side"], "hdp": o["hdp"],
                  "odd": o["offered_odd"], "book": o["book"]} for o in opps],
    }


@app.get("/api/opportunity_family")
def api_opportunity_family(id: str):
    """Linhas correlacionadas (mesmo jogo+mercado+lado) de uma oportunidade,
    para o painel expandir e deixar escolher uma linha alternativa."""
    return {"rows": db.list_family(id)}


from .sources.theoddsapi import TheOddsApiSource
_theoddsapi = TheOddsApiSource()


@app.get("/api/theoddsapi_sports")
async def theoddsapi_sports():
    """Lista de esportes da the-odds-api (NÃO consome créditos). Para o dropdown
    do botão 'Puxar Bet365'."""
    sports = await _theoddsapi.list_sports()
    return {"sports": sports, "error": _theoddsapi.last_error,
            "remaining": _theoddsapi.requests_remaining,
            "configured": bool(config.THE_ODDS_API_KEY)}


@app.post("/api/pull_bet365")
async def pull_bet365(req: Request):
    """SOB DEMANDA: puxa as odds da Bet365 (the-odds-api) de UM esporte e cruza
    contra a Pinnacle. Consome ~1 crédito por mercado — por isso é só no clique."""
    d = await req.json()
    sport_key = (d.get("sport_key") or "").strip()
    if not sport_key:
        raise HTTPException(400, "escolha um esporte (sport_key)")
    events = await _theoddsapi.fetch_bet365(sport_key)
    if _theoddsapi.last_error:
        raise HTTPException(400, _theoddsapi.last_error)
    result = await poller.finder.cross_theoddsapi(events)
    return {"ok": True, **result,
            "raw_events": _theoddsapi.last_raw_count,   # jogos brutos da API
            "book": config.THE_ODDS_API_BOOKMAKER,      # casa que procuramos
            "com_book": len(events),                    # jogos com a casa alvo
            "available_books": _theoddsapi.last_available_books,
            "region": config.THE_ODDS_API_REGIONS,
            "remaining": _theoddsapi.requests_remaining,
            "used": _theoddsapi.requests_used}


@app.get("/api/near_misses")
def api_near_misses():
    """Melhores linhas do último ciclo, mesmo abaixo do corte de edge.
    Serve para confirmar que o scanner está vivo quando não há valor, e
    para calibrar os LIQUIDITY_TIERS com dados reais."""
    return {"rows": poller.finder.near_misses}


@app.get("/api/fair_lines")
def api_fair_lines(sport: str = "", market: str = "", search: str = "",
                   source: str = "", props: int = -1, limit: int = 500):
    """Referência sharp própria (de-vigada). source='pinnacle' (game lines) ou
    'fanduel' (player props). props=1 só props, props=0 só game lines."""
    is_prop = None if props < 0 else bool(props)
    rows = db.list_fair_lines(sport=sport, market=market, search=search,
                              source=source, is_prop=is_prop, limit=limit)
    groups: dict[str, dict] = {}
    for r in rows:
        key = f"{r['matchup_id']}|{r['market_key'] or r['market']}"
        g = groups.setdefault(key, {
            "matchup_id": r["matchup_id"], "sport": r["sport"], "source": r["source"],
            "league": r["league"], "event_home": r["event_home"],
            "event_away": r["event_away"], "event_date": r["event_date"],
            "market": r["market"], "line": r["line"], "player": r["player"],
            "max_limit": r["max_limit"], "updated_at": r["updated_at"],
            "sides": {},
        })
        g["sides"][r["side"]] = {"raw_odd": r["raw_odd"], "fair_odd": r["fair_odd"],
                                 "fair_prob": r["fair_prob"], "line": r["line"]}
    return {"count": len(groups), "stats": db.fair_lines_stats(),
            "rows": list(groups.values())}


@app.post("/bet")
async def place_bet(req: Request):
    """1 clique: registra aposta a partir de uma oportunidade.
    Aceita override de stake (em unidades) e da odd realmente pega."""
    d = await req.json()
    opp = db.get_opportunity(str(d.get("opportunity_id", "")))
    if not opp:
        raise HTTPException(404, "oportunidade não encontrada")
    stake_units = float(d.get("stake_units") or opp["stake_units"])
    odd_taken = float(d.get("odd_taken") or opp["offered_odd"])
    unit_value = config.BANKROLL * config.UNIT_PCT
    stake_amount = round(stake_units * unit_value, 2)
    bet_id = db.register_bet(opp, stake_units, stake_amount, odd_taken)
    return {"ok": True, "bet_id": bet_id, "stake_amount": stake_amount,
            "paper": config.PAPER_TRADING}


@app.post("/bet_parlay")
async def place_parlay(req: Request):
    """Registra a DUPLA selecionada como uma aposta única. Recalcula tudo do
    zero a partir dos ids (não confia nos números do cliente)."""
    d = await req.json()
    ids = d.get("ids") or []
    opps = [db.get_opportunity(str(i)) for i in ids]
    opps = [o for o in opps if o]
    if len(opps) < 2:
        raise HTTPException(400, "selecione ao menos 2 apostas")
    prob, odd = 1.0, 1.0
    for o in opps:
        prob *= o["fair_prob"]
        odd *= o["offered_odd"]
    edge = core.edge_percent(prob, odd)
    stake_units = float(d.get("stake_units") or
                        core.kelly_stake_cfg(prob, odd, config)["stake_units"])
    unit_value = config.BANKROLL * config.UNIT_PCT
    stake_amount = round(stake_units * unit_value, 2)
    fair_odd = round(core.prob_to_odd(prob), 3) if 0 < prob < 1 else 0.0
    bet_id = db.register_parlay(opps, round(odd, 3), fair_odd, round(edge, 2),
                                stake_units, stake_amount)
    return {"ok": True, "bet_id": bet_id, "stake_amount": stake_amount,
            "combined_odd": round(odd, 3), "edge_pct": round(edge, 2)}


@app.post("/settle")
async def settle(req: Request):
    d = await req.json()
    try:
        profit = db.settle_bet(int(d["bet_id"]), d["result"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "profit": profit}


@app.post("/settle_parlay")
async def settle_parlay(req: Request):
    """Liquida uma dupla POR PERNA: legs = [{odd, result}] (um por perna,
    result win|half_win|push|half_loss|loss). Payout = stake × Π(fator)."""
    d = await req.json()
    try:
        profit = db.settle_parlay(int(d["bet_id"]), list(d.get("legs") or []))
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "profit": profit}


@app.post("/reopen_bet")
async def reopen_bet(req: Request):
    """Reabre uma aposta liquidada (corrigir resultado marcado errado)."""
    d = await req.json()
    try:
        db.reopen_bet(int(d["bet_id"]))
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/bets")
def get_bets(pending: int = 0):
    return {"rows": db.list_bets(only_pending=bool(pending))}


@app.get("/bets.csv")
def bets_csv():
    """Exporta as apostas em CSV para você planilhar/backtestar."""
    import csv
    import io as _io
    from fastapi.responses import Response
    rows = db.list_bets()
    cols = ["id", "ts_placed", "event", "event_date", "sport", "league",
            "market", "hdp", "selection", "player", "book", "fair_odd",
            "odd_taken", "edge_pct", "stake_units", "stake_amount",
            "clv_pct", "odd_close", "result", "profit", "settled"]
    buf = _io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=valuehub_bets.csv"})


@app.get("/summary")
def get_summary():
    return db.bets_summary()


# ------------------------------------------------- extensão (contrato v1)

@app.post("/boost")
async def receive_boosts(req: Request):
    d = await req.json()
    added = db.add_boosts(d.get("boosts", []))
    return {"ok": True, "added": added}


@app.get("/boosts")
def get_boosts():
    rows = db.list_boosts()
    return {"simple": [b for b in rows if b["is_simple"]],
            "combined": [b for b in rows if not b["is_simple"]],
            "total": len(rows)}


# memória do que a extensão mandou por último, por casa — só para diagnóstico
# ("a Bet365 está capturando?"). Fica no /api/extension_status.
_ext_last: dict[str, dict] = {}


@app.post("/odds")
async def receive_odds(req: Request):
    """Snapshot da extensão. Arquiva E cruza com a referência sharp.

    É por aqui que entram os mercados que a API pública não entrega —
    Handicap Asiático à frente de todos, e as odds da Bet365."""
    import time as _t
    d = await req.json()
    n = db.add_odds_snapshot(d)
    cruzamento = None
    try:
        cruzamento = await poller.finder.cross_snapshot(d)
    except Exception:
        logging.getLogger("valuehub.server").exception("erro cruzando snapshot")
    # registra a atividade da extensão (para o indicador ao vivo no painel)
    src = d.get("source", "?")
    _ext_last[src] = {
        "at": _t.time(), "event": d.get("event", ""),
        "markets": sum(len(m.get("selections", [])) for m in d.get("markets", [])),
        "cruzamento": cruzamento,
    }
    return {"ok": True, "archived": n, "event": d.get("event", ""),
            "cruzamento": cruzamento}


@app.get("/api/extracted_odds")
def extracted_odds(source: str = "", search: str = "", limit: int = 400):
    """Odds cruas capturadas pela extensão, agrupadas por evento -> mercado.
    É a prova visível de que a extensão está lendo (Bet365 & cia)."""
    rows = db.list_odds_snapshots(source=source, search=search, limit=limit)
    eventos: dict[str, dict] = {}
    for r in rows:
        key = f"{r['source']}|{r['event']}"
        ev = eventos.setdefault(key, {
            "source": r["source"], "event": r["event"], "url": r["url"],
            "event_dt": r["event_dt"], "ts": r["ts"], "markets": {},
        })
        m = ev["markets"].setdefault(r["market"], [])
        m.append({"selection": r["selection"], "line": r["line"], "odd": r["odd"]})
    out = sorted(eventos.values(), key=lambda e: e["ts"], reverse=True)
    return {"count": len(out), "events": out}


@app.get("/api/extension_status")
def extension_status():
    """O que cada casa (Betano/Bet365) mandou pela extensão por último.
    Usado para diagnosticar 'a Bet365 está capturando?' em tempo real."""
    import time as _t
    out = {}
    for src, info in _ext_last.items():
        out[src] = {**info, "secs_ago": round(_t.time() - info["at"], 1)}
    return {"sources": out}


# ------------------------------------------------- calculadora de boosts

@app.post("/api/boost_eval")
async def boost_eval(req: Request):
    """Calculadora da aba Aumentadas.
    Simples:   {"boost_odd": 2.5, "ref_side": 2.2, "ref_opposite": 1.75}
    Combinada: {"boost_odd": 3.96, "legs": [[1.85, 2.0], [1.60, 2.35]]}
               ou {"boost_odd": 3.96, "ref_parlay_odd": 3.40}
    """
    d = await req.json()
    kw = dict(bankroll=config.BANKROLL, kelly_frac=config.KELLY_FRACTION,
              cap_pct=config.KELLY_CAP_PCT, unit_pct=config.UNIT_PCT,
              step_u=config.STAKE_STEP_U, min_u=config.STAKE_MIN_U)
    try:
        boost_odd = float(d["boost_odd"])
        if d.get("legs"):
            legs = [[float(a), float(b)] for a, b in d["legs"]]
            res = core.validate_combined_boost(boost_odd, legs_two_way=legs,
                                               devig=config.DEVIG_METHOD, **kw)
        elif d.get("ref_parlay_odd"):
            res = core.validate_combined_boost(
                boost_odd, ref_parlay_odd=float(d["ref_parlay_odd"]), **kw)
        elif d.get("ref_side") and d.get("ref_opposite"):
            res = core.validate_simple_boost(
                boost_odd, [float(d["ref_side"]), float(d["ref_opposite"])],
                devig=config.DEVIG_METHOD, **kw)
        else:
            raise HTTPException(400, "informe ref_side+ref_opposite, legs ou ref_parlay_odd")
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(400, f"payload inválido: {e}")
    if res is None:
        raise HTTPException(400, "não foi possível avaliar")
    return res


def main():
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
