import sqlite3
import sys
import os

def merge_dbs(old_db_path, new_db_path):
    if not os.path.exists(old_db_path):
        print(f"Erro: Banco antigo não encontrado em {old_db_path}")
        return
    if not os.path.exists(new_db_path):
        print(f"Erro: Banco novo não encontrado em {new_db_path}")
        return
        
    print(f"Mesclando dados de {old_db_path} para {new_db_path}...")
    con = sqlite3.connect(new_db_path)
    
    # Anexa o banco antigo
    con.execute(f"ATTACH DATABASE '{old_db_path}' AS old_db")
    
    # 1. Mescla Oportunidades (ignora duplicatas pelo ID único)
    cur = con.execute("INSERT OR IGNORE INTO opportunities SELECT * FROM old_db.opportunities")
    print(f"Oportunidades mescladas: {cur.rowcount} novos registros.")
    
    # 2. Mescla Apostas Registradas
    # Inserimos os campos omitindo o ID (que é autoincrement) e filtramos
    # usando o ts_placed (timestamp) para garantir que não duplicaremos apostas.
    cur = con.execute("""
        INSERT INTO bets (
            opportunity_id, ts_placed, event, event_date, sport, league, 
            market, hdp, selection, player, book, fair_odd, odd_taken, 
            edge_pct, stake_units, stake_amount, clv_pct, odd_close, 
            result, profit, settled, ts_settled
        )
        SELECT 
            opportunity_id, ts_placed, event, event_date, sport, league, 
            market, hdp, selection, player, book, fair_odd, odd_taken, 
            edge_pct, stake_units, stake_amount, clv_pct, odd_close, 
            result, profit, settled, ts_settled 
        FROM old_db.bets
        WHERE ts_placed NOT IN (SELECT ts_placed FROM bets)
    """)
    print(f"Apostas mescladas: {cur.rowcount} novos registros.")
    
    con.commit()
    con.close()
    print("Merge concluído com sucesso!")

if __name__ == "__main__":
    print("=== ValueHub Database Merger ===")
    print("Este script junta o histórico do banco de dados antigo com o novo.")
    old_db = input("Digite o nome ou caminho do arquivo do banco ANTIGO (ex: hub2_antigo.db): ")
    new_db = "hub2.db" # O banco principal atual
    
    merge_dbs(old_db, new_db)
