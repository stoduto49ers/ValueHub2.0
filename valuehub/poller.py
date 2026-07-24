"""
poller.py — Loops de fundo do HUB.

Dois motores independentes, cada um no seu ritmo:

  1. SHARP (infra própria)  — varre a Pinnacle, de-viga e grava as fair lines.
     É a nossa referência de probabilidade justa. Sem custo, sem dependência.

  2. VALUE-BETS (odds-api)  — opcional/legado. Só roda se houver ODDS_API_KEY.
     Serve como conferência cruzada enquanto migramos 100% para infra própria.

Ambos sobem junto com o servidor (lifespan do FastAPI).
"""
from __future__ import annotations

import asyncio
import logging
import time

from . import config, db, engine
from .oddsapi import OddsApiClient
from .sources.betano import BetanoSource
from .sources.fanduel import extract_prop_fair_lines
from .sources.pinnacle import PinnacleSource
from .sources.polymarket import PolymarketSource
from .valuefinder import ValueFinder

log = logging.getLogger("valuehub.poller")


class Poller:
    def __init__(self):
        self.client = OddsApiClient()
        self.pinnacle = PinnacleSource()
        self.betano = BetanoSource()
        self.polymarket = PolymarketSource()
        self.finder = ValueFinder(self.betano)
        self.running = False

        # casas-alvo
        self.tgt_last_at: float = 0.0
        self.tgt_last_ms: int = 0
        self.tgt_runs: int = 0
        self.tgt_stats: dict = {}

        # FanDuel (props sharp)
        self.fd_last_at: float = 0.0
        self.fd_sweeps: int = 0
        self.fd_props: int = 0

        # odds-api
        self.last_cycle_at: float = 0.0
        self.last_cycle_ms: int = 0
        self.cycles: int = 0
        self.new_since_start: int = 0
        self.raw_counts: dict[str, int] = {}
        self.kept_counts: dict[str, int] = {}

        # pinnacle
        self.pinn_last_at: float = 0.0
        self.pinn_last_ms: int = 0
        self.pinn_sweeps: int = 0
        self.pinn_lines: int = 0
        self.pinn_new: int = 0

        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------- status
    def status(self) -> dict:
        return {
            "running": self.running,
            # fonte sharp própria
            "pinnacle": {
                "enabled": config.PINNACLE_ENABLED,
                "last_sweep_at": self.pinn_last_at,
                "last_sweep_ms": self.pinn_last_ms,
                "sweeps": self.pinn_sweeps,
                "lines_last_sweep": self.pinn_lines,
                "new_last_sweep": self.pinn_new,
                "requests_made": self.pinnacle.requests_made,
                "last_error": self.pinnacle.last_error,
                "interval_sec": config.PINNACLE_SWEEP_INTERVAL_SEC,
                "stats": db.fair_lines_stats(),
                # mercados que a Pinnacle publica e ainda não sabemos rotular.
                # Quando CARTÕES aparecerem, o rótulo deles surge aqui.
                "unknown_units": dict(sorted(self.pinnacle.unknown_units.items(),
                                             key=lambda x: -x[1])[:10]),
            },
            # FanDuel: referência sharp de player props
            "fanduel": {
                "enabled": config.FANDUEL_PROPS_ENABLED and bool(config.API_KEY),
                "sweeps": self.fd_sweeps,
                "props_last_sweep": self.fd_props,
                "last_sweep_at": self.fd_last_at,
            },
            # casas-alvo (Betano) + cruzamento
            "targets": {
                "enabled": config.BETANO_ENABLED,
                "books": [self.betano.book, self.polymarket.book],
                "last_run_at": self.tgt_last_at,
                "last_run_ms": self.tgt_last_ms,
                "runs": self.tgt_runs,
                "requests_made": self.betano.requests_made,
                "last_error": self.betano.last_error,
                "interval_sec": config.BETANO_SWEEP_INTERVAL_SEC,
                "stats": self.tgt_stats,
            },
            # odds-api (legado/conferência)
            "books_configured": config.TARGET_BOOKS,
            "books_active": self.client.active_books,
            "books_disabled": self.client.disabled_books,
            "last_cycle_at": self.last_cycle_at,
            "last_cycle_ms": self.last_cycle_ms,
            "cycles": self.cycles,
            "poll_interval_sec": config.POLL_INTERVAL_SEC,
            "requests_made": self.client.requests_made,
            "ratelimit_remaining": self.client.ratelimit_remaining,
            "last_error": self.client.last_error,
            "raw_counts": self.raw_counts,
            "kept_counts": self.kept_counts,
            "api_key_set": bool(config.API_KEY),
            "paper_trading": config.PAPER_TRADING,
        }

    # ---------------------------------------------------- motor 1: sharp
    async def pinnacle_sweep(self):
        t0 = time.time()
        lines = await self.pinnacle.collect()
        self.pinn_lines = len(lines)
        self.pinn_new = db.upsert_fair_lines(lines)
        self.pinn_sweeps += 1
        self.pinn_last_at = time.time()
        self.pinn_last_ms = int((self.pinn_last_at - t0) * 1000)
        log.info("pinnacle: %d linhas (%d novas) em %.1fs",
                 self.pinn_lines, self.pinn_new, self.pinn_last_ms / 1000)

    async def run_pinnacle(self):
        while self.running:
            try:
                await self.pinnacle_sweep()
                db.purge_fair_lines()
            except Exception:
                log.exception("erro na varredura da Pinnacle")
            await asyncio.sleep(config.PINNACLE_SWEEP_INTERVAL_SEC)

    # ------------------------------------- motor sharp de props: FanDuel
    async def fanduel_sweep(self):
        items = await self.client.value_bets("FanDuel")
        if items is None:
            return
        props = [it for it in items
                 if str((it.get("market") or {}).get("name", "")).startswith("Player Props")]
        fair = extract_prop_fair_lines(props, devig=config.DEVIG_METHOD)
        db.upsert_fair_lines(fair)
        self.fd_props = len(fair)
        self.fd_sweeps += 1
        self.fd_last_at = time.time()
        log.info("fanduel props: %d linhas justas de %d props", len(fair), len(props))

    async def run_fanduel(self):
        while self.running:
            try:
                await self.fanduel_sweep()
            except Exception:
                log.exception("erro na varredura do FanDuel")
            await asyncio.sleep(config.FANDUEL_SWEEP_INTERVAL_SEC)

    # --------------------------------------- motor 2: casas-alvo + valor
    async def targets_run(self):
        t0 = time.time()
        self.tgt_stats = await self.finder.run()
        
        # Polymarket cross
        fair_events, candidates = self.finder._fair_index()
        poly_opps = await self.polymarket.collect_opportunities(fair_events, candidates, self.tgt_stats)
        novas = sum(1 for opp in poly_opps if db.upsert_opportunity(opp))
        self.tgt_stats["poly_novas"] = novas
        
        self.tgt_runs += 1
        self.tgt_last_at = time.time()
        self.tgt_last_ms = int((self.tgt_last_at - t0) * 1000)
        log.info("alvos: %s", self.tgt_stats)

    async def run_targets(self):
        # espera a primeira varredura sharp: sem referência não há o que cruzar
        while self.running and self.pinn_sweeps == 0:
            await asyncio.sleep(5)
        while self.running:
            try:
                await self.targets_run()
            except Exception:
                log.exception("erro na varredura das casas-alvo")
            await asyncio.sleep(config.BETANO_SWEEP_INTERVAL_SEC)

    # ------------------------------------------------- motor 3: odds-api
    async def cycle(self):
        t0 = time.time()
        for book in self.client.active_books:
            items = await self.client.value_bets(book)
            if items is None:
                continue
            self.raw_counts[book] = len(items)
            kept = 0
            for item in items:
                try:
                    opp = engine.normalize(item)
                except Exception:
                    log.exception("erro normalizando item")
                    continue
                if opp is None:
                    continue
                kept += 1
                if db.upsert_opportunity(opp):
                    self.new_since_start += 1
            self.kept_counts[book] = kept
            db.deactivate_stale(book, config.STALE_AFTER_SEC)
        self.cycles += 1
        self.last_cycle_at = time.time()
        self.last_cycle_ms = int((self.last_cycle_at - t0) * 1000)

    async def run_valuebets(self):
        purge_counter = 0
        while self.running:
            try:
                await self.cycle()
            except Exception:
                log.exception("erro no ciclo do poller")
            purge_counter += 1
            if purge_counter >= 80:
                purge_counter = 0
                try:
                    db.purge_old()
                except Exception:
                    log.exception("erro na limpeza")
            await asyncio.sleep(config.POLL_INTERVAL_SEC)

    # ------------------------------------ motor de CLV (fechamento pré-jogo)
    async def run_closings(self):
        """Congela o fechamento (CLV) perto do início dos jogos — INDEPENDENTE
        da odds-api, roda mesmo sem chave. Na subida, também pega jogos que
        começaram enquanto o servidor esteve offline (best-effort: usa a linha
        mais fresca disponível e marca clv_stale se estiver velha)."""
        try:
            db.freeze_closings()                 # freeze imediato na subida
            db.deactivate_stale("Bet365", config.BET365_STALE_SEC)
        except Exception:
            log.exception("erro no freeze inicial de CLV")
        while self.running:
            await asyncio.sleep(config.CLV_FREEZE_INTERVAL_SEC)
            try:
                db.freeze_closings()
                # rede de segurança: some com linhas antigas da Bet365 (extensão)
                db.deactivate_stale("Bet365", config.BET365_STALE_SEC)
            except Exception:
                log.exception("erro congelando CLV / limpando Bet365")

    # ------------------------------------------------------------- ciclo
    def start(self):
        self.running = True
        db.init()
        loop = asyncio.get_event_loop()
        # motor de CLV: sempre ligado (não depende de chave nem de casa)
        self._tasks.append(loop.create_task(self.run_closings()))
        log.info("motor de CLV (fechamento pré-jogo) iniciado")
        if config.PINNACLE_ENABLED:
            self._tasks.append(loop.create_task(self.run_pinnacle()))
            log.info("motor SHARP (Pinnacle) iniciado — infra própria")
        if config.BETANO_ENABLED:
            self._tasks.append(loop.create_task(self.run_targets()))
            log.info("motor ALVOS (Betano) iniciado — cruzamento de valor")
        if config.API_KEY and config.FANDUEL_PROPS_ENABLED:
            self._tasks.append(loop.create_task(self.run_fanduel()))
            log.info("motor FANDUEL (props sharp) iniciado")
        if config.API_KEY:
            self._tasks.append(loop.create_task(self.run_valuebets()))
            log.info("motor odds-api iniciado — casas: %s", config.TARGET_BOOKS)
        else:
            log.info("odds-api sem chave — FanDuel/odds-api inativos, infra própria só")

    async def stop(self):
        self.running = False
        for t in self._tasks:
            t.cancel()
        await self.client.close()
        await self.pinnacle.close()
        await self.betano.close()
