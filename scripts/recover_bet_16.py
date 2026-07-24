import json
import sqlite3

def recover_bet():
    print("Conectando ao banco de dados...")
    conn = sqlite3.connect("hub2.db")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # 1. Fetch bet 16
    c.execute("SELECT * FROM bets WHERE id = 16")
    bet = c.fetchone()
    
    if not bet:
        print("Aposta #16 não encontrada!")
        return
        
    date_prefix = bet['ts_placed'][:10]
    print(f"Buscando oportunidades do dia {date_prefix}...")
    
    c.execute(f"SELECT * FROM opportunities WHERE first_seen LIKE '{date_prefix}%' AND market='Totals HT'")
    opps = c.fetchall()
    
    # Achar as pernas que casam com "Coritiba", "Palmeiras", "Internacional", "Cruzeiro"
    # e "over 1.0", "over 0.75"
    
    leg1 = None
    leg2 = None
    
    for opp in opps:
        ev_str = f"{opp['event_home']} x {opp['event_away']}"
        if "Coritiba" in ev_str or "Palmeiras" in ev_str:
            if opp['hdp'] == 1.0 and opp['side'] == 'over':
                leg1 = opp
        if "Internacional" in ev_str or "Cruzeiro" in ev_str:
            if opp['hdp'] == 0.75 and opp['side'] == 'over':
                leg2 = opp
                
    if leg1 and leg2:
        print(f"\nEncontrei as pernas exatas na tabela de oportunidades!")
        print(f"Leg 1: {leg1['event_home']} x {leg1['event_away']} | {leg1['market']} {leg1['hdp']} {leg1['side']} @ {leg1['offered_odd']}")
        print(f"Leg 2: {leg2['event_home']} x {leg2['event_away']} | {leg2['market']} {leg2['hdp']} {leg2['side']} @ {leg2['offered_odd']}")
        
        mult = float(leg1['offered_odd']) * float(leg2['offered_odd'])
        print(f"Multiplicação = {mult:.3f} (Odd registrada: {bet['odd_taken']})")
        
        legs = [
            {"market": leg1['market'], "line": leg1['hdp'], "side": leg1['side'], "odd": leg1['offered_odd']},
            {"market": leg2['market'], "line": leg2['hdp'], "side": leg2['side'], "odd": leg2['offered_odd']}
        ]
        
        legs_json_str = json.dumps(legs)
        c.execute("UPDATE bets SET legs_json = ? WHERE id = 16", (legs_json_str,))
        conn.commit()
        print(f"Aposta 16 atualizada com sucesso com: {legs_json_str}")
    else:
        print("Não foi possível encontrar as pernas originais!")
        
if __name__ == "__main__":
    recover_bet()
