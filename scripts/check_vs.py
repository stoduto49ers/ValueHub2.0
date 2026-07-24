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
    # Pegar apenas 1000 eventos
    while offset < 500:
        r = requests.get(f'https://gamma-api.polymarket.com/events?closed=false&limit={limit}&offset={offset}', timeout=20)
        data = r.json()
        if not data: break
        events.extend(data)
        offset += limit
        
    vs_events = []
    for ev in events:
        title = ev.get('title', '').lower()
        if ' vs ' in title or ' vs. ' in title:
            vs_events.append(ev)
            
    print(f"Found {len(vs_events)} 'VS' related events.")
    for ev in vs_events[:10]:
        print(f"Title: {ev.get('title')}")
        print("-" * 50)

if __name__ == "__main__":
    fetch()
