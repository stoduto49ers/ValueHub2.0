import requests
import socket

_org_getaddrinfo = socket.getaddrinfo
def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host == 'gamma-api.polymarket.com':
        return _org_getaddrinfo('104.18.34.205', port, family, type, proto, flags)
    return _org_getaddrinfo(host, port, family, type, proto, flags)
socket.getaddrinfo = patched_getaddrinfo

r = requests.get('https://gamma-api.polymarket.com/sports/events?limit=100', timeout=10)
print(r.status_code, r.text)
