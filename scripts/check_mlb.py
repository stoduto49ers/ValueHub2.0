import requests
import json
import socket

_org_getaddrinfo = socket.getaddrinfo
def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host == 'gamma-api.polymarket.com':
        return _org_getaddrinfo('104.18.34.205', port, family, type, proto, flags)
    return _org_getaddrinfo(host, port, family, type, proto, flags)
socket.getaddrinfo = patched_getaddrinfo


def fetch():
    events = []
    limit = 100
    offset = 0
    # Pegar apenas 1000 eventos pra não demorar
    while offset < 1000:
        r = requests.get(f'https://gamma-api.polymarket.com/events?closed=false&limit={limit}&offset={offset}', timeout=20)
        data = r.json()
        if not data: break
        events.extend(data)
        offset += limit
        
    mlb_events = []
    for ev in events:
        title = ev.get('title', '').lower()
        if 'mlb' in title or 'baseball' in title or 'sox' in title or 'dodgers' in title or 'yankees' in title:
            mlb_events.append(ev)
            
    print(f"Found {len(mlb_events)} MLB related events in the first 1000 events.")
    for ev in mlb_events[:5]:
        print(f"Title: {ev.get('title')}")
        for mkt in ev.get('markets', []):
            if mkt.get('active') and not mkt.get('closed'):
                outcomes = mkt.get('outcomes', '[]')
                prices = mkt.get('outcomePrices', '[]')
                print(f"  Market: {mkt.get('question')} | Outcomes: {outcomes} | Prices: {prices}")
        print("-" * 50)

if __name__ == "__main__":
    fetch()
