"""
valuefinder.py — Cruza a referência sharp com as casas-alvo e acha o valor.

Pipeline:
    fair_lines (Pinnacle, de-vigadas)      offered lines (Betano)
                    \\                       /
                     +--- casamento de EVENTO (matching.py)
                                |
                     +--- casamento de MERCADO (mercado + linha + lado)
                                |
                     +--- edge = EV(fair_prob, odd_da_casa)
                                |
                     +--- filtro por liquidez -> Kelly -> oportunidade

Só entra no painel o que passa no edge mínimo da faixa de liquidez do
mercado (limite de risco da Pinnacle). Casamento duvidoso é descartado
antes de chegar aqui — ver a REGRA DE OURO em matching.py.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from . import config, consensus, core, db, matching

log = logging.getLogger("valuehub.valuefinder")

# esporte (como a Pinnacle nomeia) -> aba do painel
_TAB_BY_SPORT = {"E Sports": "esports", "Tennis": "tenis"}


def _event_sig(row: dict) -> str:
    """Assinatura canônica de um jogo, para juntar a MESMA partida vinda de
    fontes sharp diferentes (Pinnacle, FanDuel, Betfair). Times normalizados +
    dia. Só junta quando os nomes normalizados batem — nunca junta errado."""
    h = matching.normalize_team(row.get("event_home", ""))
    a = matching.normalize_team(row.get("event_away", ""))
    dia = (row.get("event_date") or "")[:10]
    return f"{h}|{a}|{dia}"


def group_fair_by_event(fair_rows: list[dict]) -> dict[str, dict]:
    """Agrupa as linhas justas por JOGO REAL (todas as fontes sharp) e aplica
    o CONSENSO ponderado. Saída no formato antigo — ev['lines'][(market,line,side)].

    CHAVE: o consenso é por (mercado, linha, LADO) EXATO. Nunca renormalizamos
    entre lados, porque num spread 'Seattle -2.5' e 'Cincinnati -2.5' são
    apostas distintas (não os dois lados de um mercado). Cada fonte já entrega a
    prob de-vigada; a média ponderada de valores já de-vigados segue de-vigada.
    """
    raw: dict[str, dict] = {}
    for r in fair_rows:
        sig = _event_sig(r)
        ev = raw.setdefault(sig, {
            "home": r["event_home"], "away": r["event_away"],
            "start": r["event_date"], "league": r["league"], "sport": r["sport"],
            "keys": {}, "sources": set(),
        })
        ev["sources"].add(r["source"])
        if r.get("event_date") and len(r["event_date"]) > len(ev["start"] or ""):
            ev["start"] = r["event_date"]
        key = (r["market"], r["line"], r["side"])
        ev["keys"].setdefault(key, []).append({
            "source": r["source"], "fair_prob": r["fair_prob"],
            "max_limit": r["max_limit"],
        })

    events: dict[str, dict] = {}
    for sig, ev in raw.items():
        # E-SPORTS: fair = média ponderada de várias casas (Pinnacle 50% /
        # Polymarket 25% / casas-alvo dividem 25%). Nos demais esportes, None ->
        # o consenso usa os pesos sharp padrão (Pinnacle domina).
        ew = None
        if config.ESPORTS_CONSENSUS_ENABLED and ev.get("sport") == config.ESPORTS_SPORT_NAME:
            ew = consensus.esports_weights(ev["sources"])
        lines = {}
        for (market, line, side), entries in ev["keys"].items():
            is_prop = market.startswith("Prop:")
            c = consensus.combine_side(entries, is_prop, weights=ew)
            if not c:
                continue
            lines[(market, line, side)] = {
                "fair_prob": c["fair_prob"],
                "fair_odd": round(core.prob_to_odd(c["fair_prob"]), 4),
                "max_limit": c["max_limit"], "event_date": ev["start"],
                "sources": c["sources"], "n_sources": c["n_sources"],
                "league": ev["league"], "sport": ev["sport"],
                "event_home": ev["home"], "event_away": ev["away"],
            }
        events[sig] = {
            "matchup_id": sig, "home": ev["home"], "away": ev["away"],
            "start": ev["start"], "league": ev["league"], "sport": ev["sport"],
            "sources": sorted(ev["sources"]), "lines": lines,
        }
    return events


def contribute_book_to_consensus(offered: list[dict], fair_event: dict, source: str) -> int:
    """E-SPORTS: de-viga (Shin) as linhas oferecidas de UMA casa e grava como
    fair_lines (source=<casa>), usando os NOMES CANÔNICOS do evento sharp já
    casado — assim se agrupam com a Pinnacle no consenso ponderado (Pinnacle 50%
    / demais casas 50%). TODAS as casas entram (incl. a própria onde se aposta);
    sem leave-one-out. Roda SÓ p/ e-sports. Precisa dos DOIS lados p/ de-vigar."""
    if not (config.ESPORTS_CONSENSUS_ENABLED
            and fair_event.get("sport") == config.ESPORTS_SPORT_NAME):
        return 0
    home = fair_event.get("home") or fair_event.get("event_home") or ""
    away = fair_event.get("away") or fair_event.get("event_away") or ""
    start = fair_event.get("start") or ""
    league = fair_event.get("league") or ""
    groups: dict[tuple, dict] = defaultdict(dict)
    liqs: dict[tuple, float] = {}          # liquidez por mercado (p/ ponderar Poly)
    for o in offered:
        odd = o.get("odd")
        if not odd or odd <= 1.0:
            continue
        market, line, side = o["market"], o.get("line"), o["side"]
        # SPREAD: os dois lados complementares são home@-L e away@+L (não @-L).
        # Agrupamos pela linha na PERSPECTIVA DO MANDANTE — senão de-vigaríamos
        # 'home 2-0' contra 'away 2-0' (que não somam 1) e inflaria os DOIS
        # lados, criando falso valor em ambos. É como a Pinnacle pareia.
        gline = (-line if (side == "away" and market.startswith("Spread")
                           and line is not None) else line)
        key = (market, gline)
        groups[key][side] = odd
        if o.get("liq") is not None:
            liqs[key] = o["liq"]
    sig = (f"{matching.normalize_team(home)}|{matching.normalize_team(away)}"
           f"|{(start or '')[:10]}")
    now = db.utcnow()
    lines = []
    for (market, gline), sides in groups.items():
        odds = list(sides.values())
        if len(odds) < 2:            # sem os dois lados não há como de-vigar
            continue
        try:
            probs = core.fair_probabilities(odds, method=config.DEVIG_METHOD)
        except Exception:
            continue
        for (n, raw), p in zip(sides.items(), probs):
            if not (0.0 < p < 1.0):
                continue
            # linha REAL do lado (away é o espelho da linha do mandante)
            side_line = (-gline if (n == "away" and market.startswith("Spread")
                                    and gline is not None) else gline)
            lines.append({
                "id": f"{source}|{sig}|{market}|{side_line}|{n}", "source": source,
                "sport": config.ESPORTS_SPORT_NAME, "league": league,
                "event_home": home, "event_away": away, "event_date": start,
                "matchup_id": sig, "market_key": None, "market": market,
                "line": side_line, "side": n, "period": 0, "player": None,
                "raw_odd": raw, "fair_odd": round(core.prob_to_odd(p), 4),
                "fair_prob": round(p, 6),
                # liquidez da casa neste mercado -> pondera o peso no consenso
                "max_limit": liqs.get((market, gline)), "updated_at": now,
            })
    if lines:
        db.upsert_fair_lines(lines)
    return len(lines)


def _line_key(market: str, line, side: str):
    """Normaliza a linha para casar Totals 2.5 == 2.50. Mercados de resultado
    (ML, ML HT) não têm linha."""
    if market.startswith("ML"):
        return (market, None, side)
    return (market, round(float(line), 2) if line is not None else None, side)


def evaluate_event(target_event: dict, offered: list[dict],
                   fair_event: dict, match_score: float,
                   near_out: list | None = None,
                   comparadas: list | None = None) -> list[dict]:
    """Gera oportunidades para UM jogo já casado.

    target_event: evento da casa-alvo (com home/away/start/url)
    offered: linhas oferecidas pela casa nesse jogo
    fair_event: evento da sharp com as linhas justas
    """
    out: list[dict] = []
    comparadas = comparadas if comparadas is not None else [0]
    for off in offered:
        odd = off.get("odd")
        if not odd or odd <= 1.0:
            continue
        key = _line_key(off["market"], off.get("line"), off["side"])
        fair = fair_event["lines"].get(key)
        if fair is None:
            continue                      # a sharp não cobre esta linha

        fair_prob = fair["fair_prob"]
        if not (0.0 < fair_prob < 1.0):
            continue

        eval_odd = off.get("net_odd", odd)
        edge = core.edge_percent(fair_prob, eval_odd)
        max_limit = fair.get("max_limit")
        required = _min_edge_for(max_limit)

        comparadas[0] += 1
        if near_out is not None and edge > -2.0:
            near_out.append({
                "event": f"{fair_event['home']} × {fair_event['away']}",
                "league": fair_event.get("league") or "",
                "event_date": fair["event_date"],
                "market": off["market"], "line": off.get("line"),
                "side": off["side"], "book": off["book"],
                "offered_odd": round(odd, 4), "fair_odd": fair["fair_odd"],
                "edge_pct": round(edge, 2), "min_edge_required": required,
                "max_limit": max_limit, "direct_link": off.get("url") or "",
            })

        if edge < required:
            continue

        # stake também usa a odd LÍQUIDA (net_odd) — a que você realmente recebe
        # após a taxa da casa (ex.: 2% da Polymarket). offered_odd (exibido)
        # segue a odd cheia; edge e stake usam a líquida.
        sizing = core.kelly_stake_cfg(fair_prob, eval_odd, config)
        if sizing["stake_units"] <= 0:
            continue

        line = off.get("line")
        opp_id = (f"{off['book']}|{off['event_id']}|{off['market']}|"
                  f"{line}|{off['side']}")
        out.append({
            "id": opp_id,
            "matchup_id": fair_event.get("matchup_id"),
            "tab": _TAB_BY_SPORT.get(fair_event.get("sport"), "value"),
            "suspicious": 1 if edge > config.EDGE_SANITY_MAX_PCT else 0,
            "sport": fair_event.get("sport") or "",
            "league": fair_event.get("league") or off.get("league") or "",
            "event_home": off.get("event_home") or target_event.get("home", ""),
            "event_away": off.get("event_away") or target_event.get("away", ""),
            "event_date": fair["event_date"],
            "event_id": off.get("event_id"),
            "market": off["market"],
            "hdp": line,
            "side": off["side"],
            "player": "",
            "book": off["book"],
            "offered_odd": round(odd, 4),
            "fair_odd": fair["fair_odd"],
            "fair_prob": fair_prob,
            "edge_pct": round(edge, 2),
            "min_edge_required": required,
            "max_limit": max_limit,
            "direct_link": off.get("url") or "",
            "stake_units": sizing["stake_units"],
            "stake_amount": sizing["stake_amount"],
            "match_score": match_score,
            "sharp_sources": ",".join(fair.get("sources") or []),
            "n_sharps": fair.get("n_sources", 1),
        })
    return out


def _min_edge_for(max_limit) -> float:
    if max_limit is None:
        return config.DEFAULT_MIN_EDGE_PCT
    for floor, edge in config.LIQUIDITY_TIERS:
        if max_limit >= floor:
            return edge
    return config.LIQUIDITY_TIERS[-1][1]


class ValueFinder:
    """Orquestra: listagens da casa -> casamento -> detalhe -> valor."""

    def __init__(self, targets: list):
        self.targets = targets
        self.last_stats: dict = {}
        # melhores linhas do ciclo, mesmo abaixo do corte. Servem para (a) provar
        # que o sistema está vivo quando não há valor e (b) calibrar os cortes.
        self.near_misses: list[dict] = []

    def _fair_index(self):
        """Índice dos eventos sharp + lista de candidatos para o casador."""
        fair_rows = db.list_fair_lines(limit=100_000)
        fair_events = group_fair_by_event(fair_rows)
        candidates = [{"home": e["home"], "away": e["away"], "start": e["start"],
                       "matchup_id": mid} for mid, e in fair_events.items()]
        return fair_events, candidates

    async def _resolver_evento(self, snapshot: dict) -> dict | None:
        """Descobre mandante/visitante/horário do snapshot.

        Betano: resolve pelo event_id na API da casa (ordem confiável).
        Bet365: sem API pública -> usa o que a extensão leu do DOM
                (home/away/start ou o nome 'Time A v Time B')."""
        source = snapshot.get("source")
        event_id = str(snapshot.get("event_id") or "")

        if source == "betano" and event_id:
            # acha a fonte Betano na lista de alvos (self.targets é lista agora)
            betano = next((t for t in self.targets
                           if getattr(t, "name", "") == "betano"), None)
            if betano and hasattr(betano, "event_by_id"):
                meta = await betano.event_by_id(event_id)
                if meta:
                    return meta

        # caminho do DOM (Bet365 e fallback)
        home = snapshot.get("home") or ""
        away = snapshot.get("away") or ""
        if not (home and away):
            nome = snapshot.get("event") or ""
            for sep in (" v ", " vs ", " x ", " - ", " @ "):
                if sep in nome:
                    p = nome.split(sep, 1)
                    home, away = p[0].strip(), p[1].strip()
                    break
        if home and away:
            return {"home": home, "away": away,
                    "start": snapshot.get("event_datetime") or snapshot.get("start"),
                    "league": "", "url": snapshot.get("url", "")}
        return None

    async def cross_snapshot(self, snapshot: dict) -> dict:
        """Cruza um snapshot da extensão (Bet365) ou coletor (Betano) com a
        referência sharp. Trata game lines E player props."""
        from .sources.extension_parser import parse_snapshot

        t_start = db.utcnow()          # marca o instante ANTES dos upserts
        meta = await self._resolver_evento(snapshot)
        if not meta:
            return {"ok": False, "motivo": "não deu para identificar o jogo"}

        offered = parse_snapshot(snapshot, meta["home"], meta["away"])
        props = self._parse_props(snapshot, meta)
        if not offered and not props:
            return {"ok": False, "motivo": "nenhum mercado reconhecido",
                    "evento": f"{meta['home']} x {meta['away']}"}

        fair_events, candidates = self._fair_index()
        m = matching.match_event(
            {"home": meta["home"], "away": meta["away"], "start": meta.get("start")},
            candidates, max_minutes=config.MATCH_MAX_MINUTES,
            min_score=config.MATCH_MIN_SCORE,
            min_side_score=config.MATCH_MIN_SIDE_SCORE)

        near: list[dict] = []
        comparadas = [0]
        novas = 0
        # game lines: contra a Pinnacle (evento casado)
        if m and offered:
            fair_event = fair_events[m["event"]["matchup_id"]]
            opps = evaluate_event({"home": meta["home"], "away": meta["away"]},
                                  offered, fair_event, m["score"],
                                  near_out=near, comparadas=comparadas)
            novas += sum(1 for o in opps if db.upsert_opportunity(o))
            # LIMPEZA: desativa linhas ANTIGAS deste jogo, só nos mercados que
            # acabamos de reler — a linha errada (ex.: Spread +1.0) some quando a
            # certa (+0.75) entra. Um cupom (só ML) não apaga o Spread lido antes.
            book = "Bet365" if snapshot.get("source") == "bet365" else "Betano"
            for mkt in {o["market"] for o in offered}:
                db.deactivate_event_market_stale(book, meta["home"], meta["away"],
                                                 mkt, t_start)
        # player props: contra o FanDuel (mesmo evento, casador de jogador)
        novas_props = self._cross_props(props, meta, m["score"] if m else 0.0, near)
        novas += novas_props

        if near:
            juntas = (self.near_misses + near)
            juntas.sort(key=lambda x: -x["edge_pct"])
            self.near_misses = juntas[:25]
        return {"ok": True, "evento": f"{meta['home']} x {meta['away']}",
                "casou_evento": bool(m), "book": snapshot.get("source"),
                "linhas_ofertadas": len(offered), "props_ofertados": len(props),
                "linhas_comparadas": comparadas[0], "novas": novas}

    # ---------------------------------------------------------- player props
    def _parse_props(self, snapshot: dict, meta: dict) -> list[dict]:
        """Extrai player props do snapshot (mercados cujo nome indica jogador)."""
        from .sources.extension_parser import parse_props_snapshot
        return parse_props_snapshot(snapshot)

    def _cross_props(self, props: list[dict], meta: dict, match_score: float,
                     near: list) -> int:
        """Compara props da casa contra a referência sharp de props (FanDuel).
        Casa por evento (times) e por jogador."""
        if not props:
            return 0
        # referência de props do MESMO jogo (source fanduel)
        fair_props = db.list_fair_lines(source="fanduel", limit=100_000,
                                        stale_min=config.PROPS_FAIR_STALE_MIN)
        if not fair_props:
            return 0
        # agrupa a referência por evento sharp, e casa o evento da casa contra eles
        by_event: dict[str, list[dict]] = {}
        for r in fair_props:
            by_event.setdefault(r["matchup_id"], []).append(r)
        cand = [{"home": v[0]["event_home"], "away": v[0]["event_away"],
                 "start": v[0]["event_date"], "matchup_id": k}
                for k, v in by_event.items()]
        me = matching.match_event(
            {"home": meta["home"], "away": meta["away"], "start": meta.get("start")},
            cand, max_minutes=config.MATCH_MAX_MINUTES,
            min_score=config.MATCH_MIN_SCORE, min_side_score=config.MATCH_MIN_SIDE_SCORE)
        if not me:
            return 0
        ref = by_event[me["event"]["matchup_id"]]
        jogadores = sorted({r["player"] for r in ref if r.get("player")})
        # índice (jogador_norm, stat, linha, lado) -> fair
        idx = {}
        for r in ref:
            idx[(r["player"], r["market"], r["line"], r["side"])] = r

        novas = 0
        for off in props:
            jog = matching.match_player(off["player"], jogadores)
            if not jog:
                continue
            fair = idx.get((jog, off["market"], off["line"], off["side"]))
            if not fair:
                continue
            odd = off["odd"]
            fair_prob = fair["fair_prob"]
            edge = core.edge_percent(fair_prob, odd)
            required = max(_min_edge_for(fair.get("max_limit")), config.PROPS_MIN_EDGE_PCT)
            near.append({"event": f"{meta['home']} × {meta['away']}",
                         "league": fair.get("league") or "", "event_date": fair["event_date"],
                         "market": f"{jog} — {fair['market'].replace('Prop: ','')}",
                         "line": off["line"], "side": off["side"], "book": off["book"],
                         "offered_odd": round(odd, 4), "fair_odd": fair["fair_odd"],
                         "edge_pct": round(edge, 2), "min_edge_required": required,
                         "max_limit": fair.get("max_limit"), "direct_link": off.get("url", "")})
            if edge < required:
                continue
            sizing = core.kelly_stake_cfg(fair_prob, odd, config, is_prop=True)
            if sizing["stake_units"] <= 0:
                continue
            opp = {
                "id": f"{off['book']}|prop|{me['event']['matchup_id']}|{jog}|{fair['market']}|{off['line']}|{off['side']}",
                "matchup_id": me["event"]["matchup_id"],
                "tab": "props", "suspicious": 1 if edge > config.EDGE_SANITY_MAX_PCT else 0,
                "sport": fair.get("sport") or "", "league": fair.get("league") or "",
                "event_home": meta["home"], "event_away": meta["away"],
                "event_date": fair["event_date"], "event_id": off.get("event_id"),
                "market": fair["market"], "hdp": off["line"], "side": off["side"],
                "player": jog, "book": off["book"], "offered_odd": round(odd, 4),
                "fair_odd": fair["fair_odd"], "fair_prob": fair_prob,
                "edge_pct": round(edge, 2), "min_edge_required": required,
                "max_limit": fair.get("max_limit"), "direct_link": off.get("url", ""),
                "stake_units": sizing["stake_units"], "stake_amount": sizing["stake_amount"],
                "match_score": match_score,
            }
            if db.upsert_opportunity(opp):
                novas += 1
        return novas

    async def cross_book_props(self, target, sports: list[str]) -> int:
        """Cruza as PLAYER PROPS de uma casa (Betano) contra a referência sharp
        de props (FanDuel). Casa o evento primeiro (barato) e só então busca as
        props do jogo (caro) — evita puxar props de jogos que a FanDuel não
        cobre. Reaproveita _cross_props (jogador + linha + lado)."""
        fair_props = db.list_fair_lines(source="fanduel", limit=100_000,
                                        stale_min=config.PROPS_FAIR_STALE_MIN)
        if not fair_props:
            return 0
        by_event: dict[str, list[dict]] = {}
        for r in fair_props:
            by_event.setdefault(r["matchup_id"], []).append(r)
        cand = [{"home": v[0]["event_home"], "away": v[0]["event_away"],
                 "start": v[0]["event_date"], "matchup_id": k}
                for k, v in by_event.items()]
        get_listings = getattr(target, "prop_listings", None) or target.collect_listings
        near: list[dict] = []
        novas = 0
        for sport in sports:
            try:
                listings = await get_listings(sport)
            except Exception:
                log.exception("erro listando jogos de props (%s)", sport)
                continue
            for ev in listings:
                parts = [p.get("name") for p in (ev.get("participants") or [])]
                if len(parts) < 2:
                    continue
                meta = {"home": parts[0], "away": parts[1], "start": ev.get("startTime")}
                m = matching.match_event(
                    meta, cand, max_minutes=config.MATCH_MAX_MINUTES,
                    min_score=config.MATCH_MIN_SCORE,
                    min_side_score=config.MATCH_MIN_SIDE_SCORE)
                if not m:
                    continue
                try:
                    props = await target.fetch_event_props(ev)
                except Exception:
                    log.exception("erro buscando props do jogo")
                    continue
                if props:
                    novas += self._cross_props(props, meta, m["score"], near)
        if near:
            near.sort(key=lambda x: -x["edge_pct"])
            self.near_misses = (near + self.near_misses)[:25]
        return novas

    async def run(self) -> dict:
        fair_rows = db.list_fair_lines(limit=100_000)
        if not fair_rows:
            self.last_stats = {"erro": "sem linhas sharp — aguarde a Pinnacle"}
            return self.last_stats

        fair_events = group_fair_by_event(fair_rows)
        candidates = [{"home": e["home"], "away": e["away"], "start": e["start"],
                       "matchup_id": mid}
                      for mid, e in fair_events.items()]

        stats = defaultdict(int)
        por_casa: dict[str, dict] = {}     # resumo da última varredura por casa
        novas = 0
        near: list[dict] = []
        comparadas = [0]      # linhas que encontraram par na referência sharp
        for target in self.targets:
            c = {"book": target.book, "jogos": 0, "casados": 0, "opps": 0,
                 "erro": ""}
            for sport in config.BETANO_SPORTS:
                listings = await target.collect_listings(sport)
                stats["eventos_casa"] += len(listings)
                c["jogos"] += len(listings)

                for ev in listings:
                    parts = [p.get("name") for p in (ev.get("participants") or [])]
                    if len(parts) < 2:
                        continue
                    alvo = {"home": parts[0], "away": parts[1],
                            "start": ev.get("startTime")}
                    m = matching.match_event(
                        alvo, candidates,
                        max_minutes=config.MATCH_MAX_MINUTES,
                        min_score=config.MATCH_MIN_SCORE,
                        min_side_score=config.MATCH_MIN_SIDE_SCORE)
                    if not m:
                        stats["sem_casamento"] += 1
                        continue
                    stats["casados"] += 1
                    c["casados"] += 1
                    fair_event = fair_events[m["event"]["matchup_id"]]

                    # detalhe do jogo (traz Total de Gols) — só para casados
                    try:
                        offered = await target.fetch_event_lines(ev)
                    except Exception:
                        log.exception("erro no detalhe do evento da casa")
                        continue
                    stats["linhas_ofertadas"] += len(offered)

                    # e-sports: alimenta o consenso com a casa de-vigada
                    contribute_book_to_consensus(offered, fair_event, target.name)

                    opps = evaluate_event(alvo, offered, fair_event, m["score"],
                                          near_out=near, comparadas=comparadas)
                    stats["oportunidades"] += len(opps)
                    c["opps"] += len(opps)
                    for o in opps:
                        if db.upsert_opportunity(o):
                            novas += 1

            c["erro"] = getattr(target, "last_error", "") or ""
            por_casa[target.name] = c
            db.deactivate_stale(target.book, config.STALE_AFTER_SEC)
        near.sort(key=lambda x: -x["edge_pct"])
        self.near_misses = near[:20]
        stats["novas"] = novas
        stats["linhas_comparadas"] = comparadas[0]
        stats["por_casa"] = por_casa
        self.last_stats = dict(stats)
        return self.last_stats
