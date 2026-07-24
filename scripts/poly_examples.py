import asyncio
from valuehub.sources.polymarket import PolymarketSource
from valuehub import core
import json

async def main():
    poly = PolymarketSource()
    events = await asyncio.to_thread(poly._fetch_events)
    
    examples_found = []
    
    for ev in events:
        mkts = ev.get('markets') or []
        if not mkts: continue
        
        main_mkt = next((m for m in mkts if m.get("active") and not m.get("closed")), None)
        if not main_mkt: continue
            
        try:
            outcomes = json.loads(main_mkt.get('outcomes', '[]'))
            prices = json.loads(main_mkt.get('outcomePrices', '[]'))
        except: continue
            
        if len(outcomes) == 2 and "Yes" not in outcomes and "No" not in outcomes:
            poly_h, poly_a = outcomes[0], outcomes[1]
            try:
                price = float(prices[0])
                if price > 0 and price < 1:
                    poly_odd = 1.0 / price
                    
                    # Simulando que a Pinnacle tem a mesma probabilidade implícita do mercado,
                    # para mostrar que a odd oferecida sempre tem EV negativo sem uma grande discrepância
                    fair_prob = price
                    fair_odd = 1.0 / fair_prob
                    
                    # E se a Pinnacle discordasse só um pouquinho?
                    # Digamos que Pinnacle diz que é 1% menos provável
                    fair_prob_slightly_worse = fair_prob - 0.01
                    edge = core.edge_percent(fair_prob_slightly_worse, poly_odd)
                    
                    examples_found.append({
                        "event": f"{poly_h} x {poly_a}",
                        "price": price,
                        "poly_odd": round(poly_odd, 2),
                        "fair_odd": round(1.0/fair_prob_slightly_worse, 2),
                        "edge": round(edge, 2)
                    })
            except: pass
            
        if len(examples_found) >= 3:
            break
            
    print("=== EXEMPLOS REAIS (SIMULANDO O CRUZAMENTO) ===\n")
    print("Como no momento não temos jogos de esportes cruzando com a Pinnacle,")
    print("peguei 3 eventos aleatórios reais que estão rolando AGORA na Polymarket.")
    print("Vamos simular que a Pinnacle abriu uma linha para eles, para você ver como a filtragem descarta apostas ruins:\n")
    
    for i, ex in enumerate(examples_found):
        print(f"[{i+1}] {ex['event']}")
        print(f"   Ação na Polymarket está custando: ${ex['price']}")
        print(f"   Odd Implícita (Oferecida): {ex['poly_odd']}")
        print(f"   Simulando a Pinnacle (Fair Odd): {ex['fair_odd']}")
        print(f"   Edge calculado: {ex['edge']}%")
        if ex['edge'] < 2.8:
            print("   -> DESCARTADA: Edge menor que 2.8% ou negativo!\n")
        else:
            print("   -> APROVADA: Apareceria no painel!\n")

if __name__ == '__main__':
    asyncio.run(main())
