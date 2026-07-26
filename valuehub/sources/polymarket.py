"""
polymarket.py — Coletor da Polymarket (exchange de cripto, alvo).

A Polymarket usa shares (ações) que variam de $0 a $1.
Um preço de $0.50 implica 50% de probabilidade, o que equivale a odd 2.00.
Contornamos o bloqueio de DNS forçando a resolução para o IP direto.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import time
import json
import re
import requests

from .. import core, matching
from ..valuefinder import evaluate_event

log = logging.getLogger("valuehub.polymarket")

# DNS Patch para bypass de geoblock / ISP block no Windows
_org_getaddrinfo = socket.getaddrinfo
def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host == 'gamma-api.polymarket.com':
        return _org_getaddrinfo('104.18.34.205', port, family, type, proto, flags)
    return _org_getaddrinfo(host, port, family, type, proto, flags)
socket.getaddrinfo = patched_getaddrinfo


class PolymarketSource:
    name = "polymarket"
    book = "Polymarket"

    def __init__(self):
        self.requests_made = 0
        self.last_error = ""

    def _fetch_events(self) -> list[dict]:
        try:
            all_events = []
            seen_ids = set()
            tags = ['mlb', 'nba', 'nfl', 'nhl', 'ncaa-football', 'ncaa-basketball', 'soccer', 'tennis', 'mma', 'boxing', 'motorsports', 'esports']
            for tag in tags:
                offset = 0
                limit = 100
                while True:
                    r = requests.get(f'https://gamma-api.polymarket.com/events?closed=false&tag_slug={tag}&limit={limit}&offset={offset}', timeout=20)
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
            return all_events
        except Exception as e:
            self.last_error = str(e)
            return []

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
            
            from datetime import datetime, timezone
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
                        import math
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
                            import math
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
                            
                            # Precisamos saber quem é este side_name (home ou away)
                            h_score = matching.team_similarity(side_name, alvo["home"])
                            a_score = matching.team_similarity(side_name, alvo["away"])
                            if h_score > a_score and h_score > 0.15:
                                side = "home"
                            elif a_score > h_score and a_score > 0.15:
                                side = "away"
                                
                            if not side: continue
                            
                            line = None
                            
                            # USAR A PROPRIEDADE LINE NATIVA DA API SEMPRE QUE POSSÍVEL!
                            m_line = m.get("line")
                            if m_line is not None:
                                try:
                                    if idx == 0:
                                        line = float(m_line)
                                    elif idx == 1:
                                        line = -float(m_line)
                                except (ValueError, TypeError):
                                    pass
                            
                            if line is None:
                                # Fallback: procurar no próprio nome da seleção (ex: "Spirit -1.5")
                                match_side = re.search(r"([+-][0-9.]+)", side_name)
                                if match_side:
                                    line = float(match_side.group(1))
                                else:
                                    # Dividir a string por 'vs' e analisar cada parte
                                    parts = re.split(r'\bvs\b', text_lower)
                                    for part in parts:
                                        if matching.team_similarity(side_name.lower(), part) > 0.5:
                                            match_part = re.search(r"\(([+-][0-9.]+)\)", part) or re.search(r"([+-][0-9.]+)", part)
                                            if match_part:
                                                line = float(match_part.group(1))
                                                break
                            
                            if line is None:
                                # Fallback original se não achou especificamente para o time
                                match_obj = re.search(r"\(([+-][0-9.]+)\)", question) or re.search(r"\(([+-][0-9.]+)\)", group)
                                if not match_obj: continue
                                q_line = float(match_obj.group(1))
                                # Adivinhar se a linha genérica pertence a este lado
                                if side == "home":
                                    line = q_line
                                else:
                                    line = -q_line
                                
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
                            h_score = matching.team_similarity(side_name, alvo["home"])
                            a_score = matching.team_similarity(side_name, alvo["away"])
                            if h_score > a_score and h_score > 0.15:
                                side = "home"
                            elif a_score > h_score and a_score > 0.15:
                                side = "away"
                            elif side_name_lower in {"draw", "tie", "empate"}:
                                side = "draw"
                                
                        if side:
                            offered.append({
                                "book": self.book, "event_id": str(ev["id"]),
                                "market": market, "line": line, "side": side, "odd": odd, "net_odd": net_odd,
                                "url": f"https://polymarket.com/event/{ev.get('slug')}"
                            })
            
            if offered:
                fair_event = fair_events[m["event"]["matchup_id"]]
                opps = evaluate_event(alvo, offered, fair_event, m["score"], near_out=None, comparadas=None)
                opps_found.extend(opps)
                
        return opps_found
