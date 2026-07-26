import asyncio
import logging
import time
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from . import extension_parser

log = logging.getLogger("valuehub.bet365")

def _sync_scrape_bet365(url: str, event_id: str, event_home: str, event_away: str) -> list[dict]:
    # Configurações do Undetected ChromeDriver
    options = uc.ChromeOptions()
    # options.add_argument("--headless") # Se Bet365 bloquear, tiramos o headless no código chamador se precisar, mas undetected geralmente passa
    options.add_argument("--disable-popup-blocking")
    
    driver = None
    markets_data = []
    
    try:
        log.info(f"Bet365 (UC): Iniciando Chrome para {url}")
        # Inicializa o navegador com a versão principal do Chrome do sistema
        driver = uc.Chrome(options=options, version_main=150)
        driver.set_page_load_timeout(30)
        
        driver.get(url)
        
        # Espera carregar o painel principal de mercados
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".gl-MarketGroup"))
            )
        except Exception as e:
            log.warning(f"Bet365 (UC): Timeout aguardando mercados. A página pode não ter carregado. URL: {url}")
            driver.save_screenshot("bet365_uc_debug.png")
            return []
            
        time.sleep(2) # Pausa extra para garantir renderização de odds
        
        # Extrair DOM
        groups = driver.find_elements(By.CSS_SELECTOR, ".gl-MarketGroup")
        for g in groups:
            try:
                title_els = g.find_elements(By.CSS_SELECTOR, ".gl-MarketGroupButton_Text")
                if not title_els:
                    continue
                market_name = title_els[0].text.strip()
                
                # Participants
                sels = []
                participants = g.find_elements(By.CSS_SELECTOR, ".gl-Participant")
                for p_node in participants:
                    name_els = p_node.find_elements(By.CSS_SELECTOR, ".gl-Participant_Name")
                    odd_els = p_node.find_elements(By.CSS_SELECTOR, ".gl-Participant_Odds")
                    
                    if name_els and odd_els:
                        sel_name = name_els[0].text.strip()
                        sel_odd = odd_els[0].text.strip()
                        sels.append({"sel": sel_name, "odd": sel_odd})
                        
                if sels:
                    markets_data.append({"market": market_name, "selections": sels})
                    
            except Exception as e:
                log.warning(f"Erro ao extrair grupo de mercado no UC: {e}")
                
    except Exception as e:
        log.error(f"Bet365 (UC): Erro geral ao acessar {url} - {e}")
        if driver:
            driver.save_screenshot("bet365_uc_debug_error.png")
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass
                
    if not markets_data:
        return []
        
    log.info(f"Bet365 (UC): Extraídos {len(markets_data)} mercados. Repassando para parser.")
    
    snapshot = {
        "source": "bet365",
        "event_id": event_id,
        "url": url,
        "markets": markets_data
    }
    
    return extension_parser.parse_snapshot(snapshot, event_home, event_away)


async def scrape_bet365_event(url: str, event_id: str, event_home: str, event_away: str) -> list[dict]:
    """
    Roda a extração síncrona do Undetected ChromeDriver em uma thread separada
    para não bloquear o event loop do asyncio.
    """
    return await asyncio.to_thread(_sync_scrape_bet365, url, event_id, event_home, event_away)
