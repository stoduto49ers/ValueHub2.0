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
            offset = 0
            limit = 100
            while True:
                r = requests.get(f'https://gamma-api.polymarket.com/events?closed=false&limit={limit}&offset={offset}', timeout=20)
                self.requests_made += 1
                if r.status_code == 200:
                    self.last_error = ""
                    data = r.json()
                    if not data:
                        break
                    all_events.extend(data)
                    offset += limit
                else:
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
            
            # Vamos usar o primeiro mercado ativo para inferir os participantes
            main_mkt = next((m for m in mkts if m.get("active") and not m.get("closed")), None)
            if not main_mkt:
                continue

            try:
                outcomes = json.loads(main_mkt.get('outcomes', '[]'))
            except Exception:
                continue
            
            if len(outcomes) == 2:
                if "Yes" in outcomes or "No" in outcomes:
                    continue
                home_name, away_name = outcomes[0], outcomes[1]
                sides = ["home", "away"]
            elif len(outcomes) == 3:
                # 3-way (ex: Futebol: Team A, Draw, Team B)
                draw_synonyms = {"draw", "tie", "empate"}
                draw_idx = -1
                for i, o in enumerate(outcomes):
                    if o.lower() in draw_synonyms:
                        draw_idx = i
                        break
                
                if draw_idx == -1:
                    continue # Não é um mercado 3-way de esportes reconhecido
                
                other_indices = [i for i in range(3) if i != draw_idx]
                home_name, away_name = outcomes[other_indices[0]], outcomes[other_indices[1]]
                
                sides = ["", "", ""]
                sides[draw_idx] = "draw"
                sides[other_indices[0]] = "home"
                sides[other_indices[1]] = "away"
            else:
                continue
                
            alvo = {
                "home": home_name, 
                "away": away_name,
                "start": ev.get("startDateIso") or ev.get("endDateIso")
            }
            
            # Tenta casar o evento
            m = matching.match_event(
                alvo, candidates,
                max_minutes=24*60, # Polymarket as vezes tem datas imprecisas
                min_score=0.80, 
                min_side_score=0.80
            )
            
            if not m:
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
                    
                if len(mkt_outcomes) != len(sides) or len(prices) != len(sides):
                    continue
                    
                for idx, side_name in enumerate(mkt_outcomes):
                    price_str = prices[idx]
                    if not price_str:
                        continue
                    try:
                        price = float(price_str)
                    except ValueError:
                        continue
                        
                    if price <= 0.01 or price >= 0.99:
                        continue
                        
                    # Odd = 1 / probabilidade implícita
                    odd = 1.0 / price
                    
                    # Definir o mercado canônico
                    market = "ML" 
                    side = sides[idx]

                    
                    offered.append({
                        "book": self.book,
                        "event_id": str(ev["id"]),
                        "market": market,
                        "line": None,
                        "side": side,
                        "odd": odd,
                        "url": f"https://polymarket.com/event/{ev.get('slug')}"
                    })
            
            if offered:
                fair_event = fair_events[m["event"]["matchup_id"]]
                opps = evaluate_event(alvo, offered, fair_event, m["score"], near_out=None, comparadas=None)
                opps_found.extend(opps)
                
        return opps_found
