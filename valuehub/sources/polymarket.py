"""
polymarket.py — Coletor da Polymarket (exchange de cripto, alvo).

A Polymarket usa shares (ações) que variam de $0 a $1.
Um preço de $0.50 implica 50% de probabilidade, o que equivale a odd 2.00.
Contornamos o bloqueio de DNS forçando a resolução para o IP direto.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import time
import json
import re
import math
from datetime import datetime, timezone

import requests

from .. import core, matching
from ..valuefinder import evaluate_event, contribute_book_to_consensus

log = logging.getLogger("valuehub.polymarket")

# A resolução de DNS da Polymarket às vezes é bloqueada pelo ISP. Em vez de um
# patch GLOBAL e permanente de socket.getaddrinfo (que afetava TODO o processo e
# dependia de um IP fixo que muda), tentamos a resolução NORMAL primeiro e só
# caímos para IPs de fallback da Cloudflare num override TEMPORÁRIO e escopado.
_POLY_HOST = "gamma-api.polymarket.com"
_POLY_FALLBACK_IPS = ["104.18.34.205", "104.18.35.205", "172.64.153.51"]


@contextlib.contextmanager
def _dns_override(host: str, ip: str):
    orig = socket.getaddrinfo
    def _patched(h, port, *a, **k):
        return orig(ip if h == host else h, port, *a, **k)
    socket.getaddrinfo = _patched
    try:
        yield
    finally:
        socket.getaddrinfo = orig


def _side_of(name: str, home: str, away: str,
             floor: float = 0.30, margin: float = 0.12) -> str | None:
    """Decide se `name` é o mandante ou o visitante COM SEGURANÇA: exige superar
    um piso E vencer o outro lado por uma margem clara. Se ficar ambíguo devolve
    None — melhor PULAR do que arriscar inverter o lado (e o sinal do handicap).
    Piso baixo p/ não perder nomes curtos de e-sports ("LEV"); a margem é o que
    protege contra a inversão."""
    h = matching.team_similarity(name, home)
    a = matching.team_similarity(name, away)
    if h >= floor and h >= a + margin:
        return "home"
    if a >= floor and a >= h + margin:
        return "away"
    return None


class PolymarketSource:
    name = "polymarket"
    book = "Polymarket"

    def __init__(self):
        self.requests_made = 0
        self.last_error = ""
        self._cache: list[dict] | None = None
        self._cache_at = 0.0

    def _get(self, url: str):
        """GET com resolução NORMAL de DNS; se o ISP bloquear, tenta os IPs de
        fallback da Cloudflare num override temporário (não global)."""
        try:
            return requests.get(url, timeout=20)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            for ip in _POLY_FALLBACK_IPS:
                try:
                    with _dns_override(_POLY_HOST, ip):
                        return requests.get(url, timeout=20)
                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                    continue
            raise

    def _fetch_events(self) -> list[dict]:
        # cache curto: a varredura é pesada (12 tags paginadas); reusa por 45s
        # p/ não martelar a API a cada ciclo do poller.
        if self._cache is not None and (time.time() - self._cache_at) < 45:
            return self._cache
        try:
            all_events = []
            seen_ids = set()
            tags = ['mlb', 'nba', 'nfl', 'nhl', 'ncaa-football', 'ncaa-basketball', 'soccer', 'tennis', 'mma', 'boxing', 'motorsports', 'esports']
            for tag in tags:
                offset = 0
                limit = 100
                while True:
                    r = self._get(f'https://gamma-api.polymarket.com/events?closed=false&tag_slug={tag}&limit={limit}&offset={offset}')
                    self.requests_made += 1
                    if r.status_code == 200:
                        self.last_error = ""
                        data = r.json()
                        if not data:
                            break
                        for ev in data:
                            if ev.get("id") not in seen_ids:
                                all_events.append(ev)
                                seen_ids.add(ev.get("id"))
                        offset += limit
                    else:
                        if r.status_code != 400:
                            self.last_error = f"HTTP {r.status_code}"
                        break
            self._cache = all_events
            self._cache_at = time.time()
            return all_events
        except Exception as e:
            self.last_error = str(e)
            return self._cache or []      # em erro, reusa o cache anterior se houver

    async def collect_opportunities(self, fair_events: dict, candidates: list[dict], finder_stats: dict) -> list[dict]:
        """
        Busca os eventos na Polymarket, tenta casar com os candidatos sharp,
        extrai as odds oferecidas e usa o evaluate_event para achar valor.
        """
        events = await asyncio.to_thread(self._fetch_events)
        if not events:
            return []

        finder_stats["poly_eventos"] = len(events)
        
        opps_found = []
        for ev in events:
            # Polymarket não tem "home" e "away" claros na raiz.
            # Normalmente o mercado principal de esporte tem 2 outcomes com os nomes dos times.
            mkts = ev.get('markets') or []
            if not mkts:
                continue
            
            title = ev.get('title', '').replace(" - More Markets", "").strip()
            if " vs. " in title:
                parts = title.split(" vs. ")
            elif " vs " in title:
                parts = title.split(" vs ")
            else:
                continue
                
            home_name = parts[0].strip()
            away_name = parts[1].strip()

            start_str = mkts[0].get("endDate") if mkts else None
            
            # Ignora eventos que já começaram (no passado)
            dt = matching.parse_time(start_str)
            if dt and dt < datetime.now(timezone.utc):
                continue
                
            # Extrai a data do slug se existir (ex: brazil-vs-colombia-2026-07-25-...)
            slug = ev.get('slug', '')
            match_date = re.search(r'-(\d{4}-\d{2}-\d{2})-', slug)
            exact_date_str = match_date.group(1) if match_date else None

            alvo = {
                "home": home_name, 
                "away": away_name,
                "start": start_str,
                "exact_date": exact_date_str
            }
            
            # Tenta casar o evento (reduzido para 8 horas para evitar casar o jogo do dia seguinte em MLB/NBA)
            m = matching.match_event(
                alvo, candidates,
                max_minutes=8*60,
                min_score=0.80, 
                min_side_score=0.80
            )
            
            if not m:
                continue
                
            # Pinnacle start time is the true kickoff time. 
            # If the true kickoff time is in the past, the game already started/finished!
            true_start = matching.parse_time(m["event"]["start"])
            if true_start and true_start < datetime.now(timezone.utc):
                continue
                
            finder_stats["poly_casados"] = finder_stats.get("poly_casados", 0) + 1
            
            # Extrair as linhas oferecidas (offered lines)
            offered = []
            for mkt in mkts:
                if not mkt.get("active") or mkt.get("closed"):
                    continue
                try:
                    mkt_outcomes = json.loads(mkt.get('outcomes', '[]'))
                    prices = json.loads(mkt.get('outcomePrices', '[]'))
                except Exception:
                    continue
                
                if len(mkt_outcomes) != len(prices):
                    continue
                    
                question = mkt.get("question", "")
                group = mkt.get("groupItemTitle", "")
                
                # 1. Yes/No Markets (mostly ML)
                if "Yes" in mkt_outcomes and "No" in mkt_outcomes:
                    idx = mkt_outcomes.index("Yes")
                    price_str = prices[idx]
                    if not price_str: continue
                    try:
                        price = float(price_str)
                        # Arredonda para cima (pior cenário) em centavos inteiros (ex: 14.5¢ -> 15¢)
                        # já que a interface da Polymarket só permite ordens limitadas em centavos inteiros
                        price = math.ceil(price * 100) / 100.0
                    except ValueError: continue
                    if price <= 0.01 or price >= 0.99: continue
                    
                    odd = 1.0 / price
                    net_odd = 1.0 + ((1.0 / price) - 1.0) * 0.98
                    
                    text_for_ml = (question + " " + group).lower()
                    if any(w in text_for_ml for w in [
                        "half", "halves", "score", "clean sheet", "card", "corner", 
                        "qualify", "advance", "win by", "to nil", "margin",
                        "game 1", "game 2", "game 3", "game 4", "game 5",
                        "map 1", "map 2", "map 3", "map 4", "map 5",
                        "first blood", "kill", "baron", "dragon", "inhibitor", "tower", "nashor", "blood",
                        "spread", "handicap", "(-", "(+"
                    ]):
                        continue

                    side = None
                    if "draw" in group.lower() or "draw" in question.lower() or "empate" in question.lower():
                        side = "draw"
                    elif matching.team_similarity(group, alvo["home"]) > 0.8:
                        side = "home"
                    elif matching.team_similarity(group, alvo["away"]) > 0.8:
                        side = "away"
                        
                    if side:
                        offered.append({
                            "book": self.book, "event_id": str(ev["id"]),
                            "market": "ML", "line": None, "side": side, "odd": odd, "net_odd": net_odd,
                            "url": f"https://polymarket.com/event/{ev.get('slug')}"
                        })
                    continue
                
                # 2. Team Name / Total Outcomes
                if len(mkt_outcomes) in (2, 3):
                    for idx, side_name in enumerate(mkt_outcomes):
                        price_str = prices[idx]
                        if not price_str: continue
                        try:
                            price = float(price_str)
                            price = math.ceil(price * 100) / 100.0
                        except ValueError: continue
                        if price <= 0.01 or price >= 0.99: continue
                        odd = 1.0 / price
                        net_odd = 1.0 + ((1.0 / price) - 1.0) * 0.98
                        
                        side_name_lower = side_name.lower().strip()
                        market = None
                        side = None
                        line = None
                        
                        # Totals (O/U)
                        if "O/U" in group or "O/U" in question:
                            # Filtro para E-sports: exclui props que não sejam do jogo/mapa inteiro
                            text_lower = (group + " " + question).lower()
                            if any(w in text_lower for w in ["kill", "dragon", "tower", "baron", "round", "inhibitor", "drake", "half", "halves", "map 1", "map 2", "map 3", "map 4", "map 5", "game 1", "game 2", "game 3"]):
                                continue
                                
                            group_lower = group.lower()
                            if not any(group_lower.startswith(p) for p in ["o/u", "total", "match", "game"]):
                                continue
                                
                            market = "Totals"
                            try:
                                # extrai o número que vem depois de O/U
                                match_obj = re.search(r"O/U\s*([0-9.]+)", group) or re.search(r"O/U\s*([0-9.]+)", question)
                                if not match_obj:
                                    continue
                                line = float(match_obj.group(1))
                            except (ValueError, IndexError):
                                continue
                                
                            if "over" in side_name_lower or side_name_lower == "o":
                                side = "over"
                            elif "under" in side_name_lower or side_name_lower == "u":
                                side = "under"
                        
                        # Spreads
                        elif "Spread:" in question or "Handicap" in group or "Handicap" in question:
                            text_lower = (group + " " + question).lower()
                            if any(w in text_lower for w in ["kill", "dragon", "tower", "baron", "round", "inhibitor", "drake", "half", "halves", "map 1", "map 2", "map 3", "map 4", "map 5", "game 1", "game 2", "game 3"]):
                                continue

                            market = "Spread"
                            text_lower = (group + " " + question).lower()

                            # de quem é este handicap? decisão SEGURA (margem clara);
                            # se ficar ambíguo, pula (não arrisca inverter o lado)
                            side = _side_of(side_name, alvo["home"], alvo["away"])
                            if not side: continue

                            line = None
                            # A linha ASSINADA vem no próprio nome do outcome ("Spirit -1.5"):
                            # é a fonte mais confiável e já traz o sinal certo daquele time.
                            # (BUG corrigido: antes lia m.get("line"), mas 'm' é o RESULTADO do
                            # casamento — não tem 'line' —, então era sempre None e caía no
                            # fallback que adivinha o sinal e podia INVERTER o handicap.)
                            if line is None:
                                # 1) preferencial: número assinado no próprio nome ("Spirit -1.5")
                                match_side = re.search(r"([+-][0-9.]+)", side_name)
                                if match_side:
                                    line = float(match_side.group(1))
                                else:
                                    # 2) A linha está na pergunta, ex: "Spread: Tampa Bay Rays (-1.5)"
                                    # e os outcomes são só os nomes dos times ["Tampa Bay Rays", "New York Yankees"]
                                    match_q = re.search(r"(.*?)\s*\(([+-][0-9.]+)\)", question)
                                    if match_q:
                                        # Extrai o nome do time que está na pergunta associado à linha
                                        q_team = match_q.group(1).lower()
                                        for prefix in ["spread:", "1st 5 innings spread:", "puck line:", "run line:", "game handicap:"]:
                                            q_team = q_team.replace(prefix, "")
                                        q_team = q_team.strip()
                                        q_line = float(match_q.group(2))
                                        
                                        # Qual dos dois outcomes se parece mais com o q_team?
                                        sim_this = matching.team_similarity(side_name.lower(), q_team)
                                        # acha o outro outcome:
                                        other_side = mkt_outcomes[0] if mkt_outcomes[0] != side_name else (mkt_outcomes[1] if len(mkt_outcomes) > 1 else "")
                                        sim_other = matching.team_similarity(other_side.lower(), q_team) if other_side else 0
                                        
                                        if sim_this >= sim_other and sim_this > 0:
                                            line = q_line
                                        else:
                                            line = -q_line
                                        # Fallback antigo: Dividir a string por 'vs' e analisar cada parte
                                        parts = re.split(r'\bvs\b', text_lower)
                                        for part in parts:
                                            if matching.team_similarity(side_name.lower(), part) > 0.5:
                                                match_part = re.search(r"\(([+-][0-9.]+)\)", part) or re.search(r"([+-][0-9.]+)", part)
                                                if match_part:
                                                    line = float(match_part.group(1))
                                                    break
                            
                            if line is None:
                                # Não deu para atribuir a linha ASSINADA a ESTE time com
                                # segurança. Em vez de adivinhar o sinal (e arriscar
                                # inverter o handicap), PULA — melhor não apostar.
                                continue
                                
                        # ML direct (3-way or 2-way with team names)
                        else:
                            text_for_ml = (question + " " + group + " " + side_name_lower).lower()
                            if any(w in text_for_ml for w in [
                                "half", "halves", "score", "clean sheet", "card", "corner", 
                                "qualify", "advance", "win by", "to nil", "margin",
                                "game 1", "game 2", "game 3", "game 4", "game 5",
                                "map 1", "map 2", "map 3", "map 4", "map 5",
                                "first blood", "kill", "baron", "dragon", "inhibitor", "tower", "nashor", "blood"
                            ]):
                                continue

                            market = "ML"
                            if side_name_lower in {"draw", "tie", "empate"}:
                                side = "draw"
                            else:
                                side = _side_of(side_name, alvo["home"], alvo["away"])
                                
                        if side:
                            offered.append({
                                "book": self.book, "event_id": str(ev["id"]),
                                "market": market, "line": line, "side": side, "odd": odd, "net_odd": net_odd,
                                "url": f"https://polymarket.com/event/{ev.get('slug')}"
                            })
            
            if offered:
                fair_event = fair_events[m["event"]["matchup_id"]]
                # e-sports: alimenta o consenso com a Polymarket de-vigada
                contribute_book_to_consensus(offered, fair_event, self.name)
                opps = evaluate_event(alvo, offered, fair_event, m["score"], near_out=None, comparadas=None)
                opps_found.extend(opps)
                
        return opps_found
