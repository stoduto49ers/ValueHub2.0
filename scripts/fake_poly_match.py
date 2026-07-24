import asyncio
from valuehub.db import init, upsert_fair_lines
from valuehub.sources.polymarket import PolymarketSource
import json
import datetime

async def main():
    print("Inicializando banco de dados...")
    init()
    
    print("Buscando um evento aleatório da Polymarket...")
    poly = PolymarketSource()
    events = await asyncio.to_thread(poly._fetch_events)
    
    target_event = None
    target_home = None
    target_away = None
    
    for ev in events:
        mkts = ev.get('markets') or []
        if not mkts: continue
        main_mkt = next((m for m in mkts if m.get("active") and not m.get("closed")), None)
        if not main_mkt: continue
        
        try:
            outcomes = json.loads(main_mkt.get('outcomes', '[]'))
        except: continue
        
        if len(outcomes) == 2 and "Yes" not in outcomes and "No" not in outcomes:
            target_event = ev
            target_home = outcomes[0]
            target_away = outcomes[1]
            break
            
    if not target_event:
        print("Não achei evento 2-way pra teste!")
        return
        
    print(f"Evento escolhido: {target_home} x {target_away}")
    
    start = target_event.get("startDateIso") or target_event.get("endDateIso")
    
    # Criar uma linha sharp falsa da Pinnacle
    fake_line = {
        "id": f"pinnacle|fake_1|ML|None|home",
        "source": "pinnacle",
        "sport": "Test",
        "league": "Polymarket Test League",
        "event_home": target_home,
        "event_away": target_away,
        "event_date": start,
        "matchup_id": "fake_matchup_1",
        "market": "ML",
        "line": None,
        "side": "home",
        "period": 0,
        "fair_prob": 0.95, 
        "raw_odd": 1.05,
        "fair_odd": 1.05,
        "max_limit": 5000, 
        "updated_at": datetime.datetime.utcnow().isoformat()
    }
    
    upsert_fair_lines([fake_line])
    print("\nLinha 'sharp' (Pinnacle) falsa inserida no banco com sucesso!")
    print("Deixe o 'run.bat' rodando. No próximo ciclo de varredura (1-2 minutos),")
    print("uma oportunidade irreal da Polymarket deve pipocar na sua tela inicial!")

if __name__ == '__main__':
    asyncio.run(main())
