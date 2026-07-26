import sys
import asyncio
from valuehub.sources.bet365 import scrape_bet365_event

async def main():
    if len(sys.argv) < 2:
        print("Uso: python teste_bet365.py <URL-DO-JOGO-NA-BET365>")
        return
        
    url = sys.argv[1]
    print(f"Iniciando teste de extração Headless para a URL: {url}")
    print("O navegador invisível vai abrir a página, aguarde (pode levar uns 5 a 10 segundos)...")
    
    # Event ID fictício e times para o teste do parser
    lines = await scrape_bet365_event(url, "teste_id", "Flamengo", "Sao Paulo")
    
    if not lines:
        print("\nNenhuma linha foi retornada. Pode ser que a página não carregou corretamente ou o layout mudou.")
        return
        
    print(f"\nSucesso! {len(lines)} odds (linhas) extraídas e formatadas:")
    for l in lines:
        # Exibe formatado
        print(f"[{l.get('market')}] {l.get('side')} {l.get('line')} @ {l.get('odd')} (Fonte: {l.get('source')})")
        
if __name__ == "__main__":
    asyncio.run(main())
