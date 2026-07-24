"""
oddsapi.py — Cliente assíncrono da odds-api.io com controle de erros.

Comportamento por status:
  403 numa casa -> a casa é desativada no runtime (plano não cobre) e o HUB
                   segue com as demais, avisando no painel.
  429           -> backoff temporário global.
"""
from __future__ import annotations
import time
import httpx
from . import config


class OddsApiClient:
    def __init__(self):
        self.http = httpx.AsyncClient(base_url=config.API_BASE, timeout=25.0)
        self.disabled_books: dict[str, str] = {}   # book -> motivo
        self.backoff_until: float = 0.0
        self.last_error: str = ""
        self.requests_made: int = 0
        self.ratelimit_remaining: str = ""

    @property
    def active_books(self) -> list[str]:
        return [b for b in config.TARGET_BOOKS if b not in self.disabled_books]

    async def value_bets(self, bookmaker: str) -> list[dict] | None:
        """Retorna a lista de value bets da casa, ou None se pulou/falhou."""
        if not config.API_KEY:
            self.last_error = "ODDS_API_KEY não configurada (crie o arquivo .env)"
            return None
        if time.time() < self.backoff_until:
            return None
        try:
            r = await self.http.get("/value-bets", params={
                "apiKey": config.API_KEY,
                "bookmaker": bookmaker,
                "includeEventDetails": "true",
            })
        except httpx.HTTPError as e:
            self.last_error = f"rede: {e.__class__.__name__}"
            return None

        self.requests_made += 1
        self.ratelimit_remaining = r.headers.get("x-ratelimit-remaining", "")

        if r.status_code == 200:
            self.last_error = ""
            try:
                data = r.json()
                return data if isinstance(data, list) else []
            except ValueError:
                self.last_error = "resposta não-JSON"
                return None
        if r.status_code == 403:
            self.disabled_books[bookmaker] = "plano não cobre esta casa (403)"
            self.last_error = f"{bookmaker}: 403 — desativada (upgrade em odds-api.io)"
            return None
        if r.status_code == 429:
            self.backoff_until = time.time() + config.BACKOFF_ON_429_SEC
            self.last_error = f"rate limit (429) — pausa de {config.BACKOFF_ON_429_SEC}s"
            return None
        if r.status_code == 401:
            self.last_error = "chave inválida (401) — confira ODDS_API_KEY"
            return None
        self.last_error = f"HTTP {r.status_code}"
        return None

    async def close(self):
        await self.http.aclose()
