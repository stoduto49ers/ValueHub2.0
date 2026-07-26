"""
db.py — Persistência SQLite do HUB 2.0 (hub2.db na raiz do projeto).

Tabelas:
  opportunities  — value bets vivas/históricas vindas da odds-api
  bets           — apostas registradas (paper ou real) + CLV automático
  boosts         — odds aumentadas capturadas pela extensão
  odds_snapshots — snapshots brutos da extensão (compatível com a v1)
"""
from __future__ import annotations
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hub2.db")
_lock = threading.Lock()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _con():
    """Abre, commita e FECHA a conexão (o context manager nativo do sqlite3
    não fecha — vazaria uma conexão por chamada no poller)."""
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init():
    with _lock, _con() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS opportunities (
            id TEXT PRIMARY KEY,
            tab TEXT, suspicious INTEGER DEFAULT 0,
            sport TEXT, league TEXT,
            event_home TEXT, event_away TEXT, event_date TEXT, event_id INTEGER,
            market TEXT, hdp REAL, side TEXT, player TEXT,
            book TEXT, offered_odd REAL, fair_odd REAL, fair_prob REAL,
            edge_pct REAL, best_edge_pct REAL, min_edge_required REAL,
            max_limit REAL, direct_link TEXT,
            stake_units REAL, stake_amount REAL,
            first_seen TEXT, last_seen TEXT, active INTEGER DEFAULT 1,
            closing_fair_odd REAL, closed INTEGER DEFAULT 0,
            match_score REAL, sharp_sources TEXT, n_sharps INTEGER DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_opp_active ON opportunities(active, tab);
        CREATE INDEX IF NOT EXISTS idx_opp_event_date ON opportunities(event_date);

        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id TEXT,
            ts_placed TEXT, event TEXT, event_date TEXT, sport TEXT, league TEXT,
            market TEXT, hdp REAL, selection TEXT, player TEXT,
            book TEXT, fair_odd REAL, odd_taken REAL, edge_pct REAL,
            stake_units REAL, stake_amount REAL,
            clv_pct REAL, odd_close REAL,
            result TEXT, profit REAL, settled INTEGER DEFAULT 0, ts_settled TEXT,
            source_tab TEXT
        );

        CREATE TABLE IF NOT EXISTS boosts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT, source TEXT, selnid TEXT, is_simple INTEGER,
            title TEXT, odd_old REAL, odd_new REAL, raw_json TEXT,
            UNIQUE(selnid, odd_new)
        );

        CREATE TABLE IF NOT EXISTS odds_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, source TEXT, event TEXT, event_dt TEXT, url TEXT,
            market TEXT, market_id TEXT,
            selection TEXT, line REAL, odd REAL, selnid TEXT
        );

        -- Referência sharp própria: linhas já de-vigadas (Pinnacle e futuras
        -- fontes). É o que substitui o consenso pago.
        CREATE TABLE IF NOT EXISTS fair_lines (
            id TEXT PRIMARY KEY,
            source TEXT, sport TEXT, league TEXT,
            event_home TEXT, event_away TEXT, event_date TEXT,
            matchup_id TEXT, market_key TEXT,
            market TEXT, line REAL, side TEXT, period INTEGER, player TEXT,
            raw_odd REAL, fair_odd REAL, fair_prob REAL, max_limit REAL,
            first_seen TEXT, updated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_fair_event ON fair_lines(event_date);
        CREATE INDEX IF NOT EXISTS idx_fair_match ON fair_lines(matchup_id, market);
        CREATE INDEX IF NOT EXISTS idx_fair_source ON fair_lines(source, updated_at);
        """)
        # migração leve: bancos criados antes da coluna market_key
        cols = [r["name"] for r in con.execute("PRAGMA table_info(fair_lines)").fetchall()]
        if "market_key" not in cols:
            con.execute("ALTER TABLE fair_lines ADD COLUMN market_key TEXT")
        if "player" not in cols:
            con.execute("ALTER TABLE fair_lines ADD COLUMN player TEXT")
        ocols = [r["name"] for r in con.execute("PRAGMA table_info(opportunities)").fetchall()]
        if "match_score" not in ocols:
            con.execute("ALTER TABLE opportunities ADD COLUMN match_score REAL")
        if "sharp_sources" not in ocols:
            con.execute("ALTER TABLE opportunities ADD COLUMN sharp_sources TEXT")
            con.execute("ALTER TABLE opportunities ADD COLUMN n_sharps INTEGER DEFAULT 1")
        bcols = [r["name"] for r in con.execute("PRAGMA table_info(bets)").fetchall()]
        if "clv_stale" not in bcols:   # 1 = fechamento aproximado (offline no kickoff)
            con.execute("ALTER TABLE bets ADD COLUMN clv_stale INTEGER DEFAULT 0")
        if "legs_ids" not in bcols:    # ids das pernas de uma dupla (p/ CLV combinado)
            con.execute("ALTER TABLE bets ADD COLUMN legs_ids TEXT")
        if "legs_json" not in bcols:   # pernas [{id,odd,label}] p/ liquidar por perna
            con.execute("ALTER TABLE bets ADD COLUMN legs_json TEXT")
        if "source_tab" not in bcols:
            con.execute("ALTER TABLE bets ADD COLUMN source_tab TEXT")
        # backfill: duplas antigas (têm legs_ids, mas não legs_json) — reconstrói
        # os dados das pernas pelas oportunidades, se ainda existirem no banco
        for r in con.execute("SELECT id, legs_ids FROM bets WHERE "
                             "(legs_json IS NULL OR legs_json='') AND "
                             "legs_ids IS NOT NULL AND legs_ids != ''").fetchall():
            legs, ok = [], True
            for oid in (r["legs_ids"] or "").split(","):
                o = con.execute("SELECT offered_odd, event_home, event_away, "
                                "market, side, hdp FROM opportunities WHERE id=?",
                                (oid,)).fetchone()
                if not o:
                    ok = False
                    break
                legs.append({"id": oid, "odd": o["offered_odd"],
                             "label": f"{o['event_home']}×{o['event_away']} · "
                                      f"{o['market']} {o['side']}"
                                      + (f" {o['hdp']}" if o["hdp"] is not None else "")})
            if ok and legs:
                con.execute("UPDATE bets SET legs_json=? WHERE id=?",
                            (json.dumps(legs, ensure_ascii=False), r["id"]))


# ------------------------------------------------------------ opportunities

def upsert_opportunity(o: dict) -> bool:
    """Insere ou atualiza uma oportunidade. Retorna True se é NOVA."""
    now = utcnow()
    with _lock, _con() as con:
        row = con.execute("SELECT id, best_edge_pct FROM opportunities WHERE id=?",
                          (o["id"],)).fetchone()
        if row is None:
            con.execute("""
                INSERT INTO opportunities
                (id, tab, suspicious, sport, league, event_home, event_away,
                 event_date, event_id, market, hdp, side, player, book,
                 offered_odd, fair_odd, fair_prob, edge_pct, best_edge_pct,
                 min_edge_required, max_limit, direct_link, stake_units,
                 stake_amount, first_seen, last_seen, active, match_score,
                 sharp_sources, n_sharps)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)
            """, (o["id"], o["tab"], o["suspicious"], o["sport"], o["league"],
                  o["event_home"], o["event_away"], o["event_date"], o["event_id"],
                  o["market"], o["hdp"], o["side"], o["player"], o["book"],
                  o["offered_odd"], o["fair_odd"], o["fair_prob"], o["edge_pct"],
                  o["edge_pct"], o["min_edge_required"], o["max_limit"],
                  o["direct_link"], o["stake_units"], o["stake_amount"], now, now,
                  o.get("match_score"), o.get("sharp_sources"), o.get("n_sharps", 1)))
            return True
        best = max(row["best_edge_pct"] or 0.0, o["edge_pct"])
        con.execute("""
            UPDATE opportunities SET offered_odd=?, fair_odd=?, fair_prob=?,
                edge_pct=?, best_edge_pct=?, min_edge_required=?, max_limit=?,
                direct_link=?, stake_units=?, stake_amount=?, suspicious=?,
                last_seen=?, active=1, match_score=?, tab=?
            WHERE id=?
        """, (o["offered_odd"], o["fair_odd"], o["fair_prob"], o["edge_pct"],
              best, o["min_edge_required"], o["max_limit"], o["direct_link"],
              o["stake_units"], o["stake_amount"], o["suspicious"], now, 
              o.get("match_score"), o["tab"], o["id"]))
        return False


def deactivate_stale(book: str, stale_after_sec: int):
    """Oportunidades da casa que sumiram do feed viram inativas."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_after_sec)).isoformat()
    with _lock, _con() as con:
        con.execute("UPDATE opportunities SET active=0 WHERE book=? AND active=1 AND last_seen < ?",
                    (book, cutoff))


def deactivate_event_market_stale(book: str, home: str, away: str,
                                  market: str, before: str) -> int:
    """Desativa linhas ATIVAS do MESMO jogo+mercado+casa que NÃO foram
    revistas nesta leitura (last_seen anterior a `before`). É como a linha
    ERRADA antiga (ex.: Spread +1.0) some quando relemos o jogo e a linha certa
    (+0.75) entra: a nova tem last_seen=agora (>before) e sobrevive; a velha,
    não. Só mexe no mercado relido — um cupom (só ML) não apaga o Spread lido
    antes."""
    with _lock, _con() as con:
        cur = con.execute("""
            UPDATE opportunities SET active=0
            WHERE book=? AND event_home=? AND event_away=? AND market=?
              AND active=1 AND last_seen < ?
        """, (book, home, away, market, before))
        return cur.rowcount


def _closing_fair(con, opp, matching) -> tuple:
    """Fechamento = a fair_line MAIS RECENTE da Pinnacle para o mesmo
    jogo/mercado/linha/lado. A fair_line é atualizada a cada varredura,
    INDEPENDENTE de ainda haver valor (a oportunidade some quando o edge acaba,
    mas a linha justa continua sendo publicada). Retorna (fair_odd, updated_at)
    ou (None, None) se a Pinnacle não cobre mais aquela linha."""
    rows = con.execute(
        "SELECT event_home, event_away, line, fair_odd, updated_at "
        "FROM fair_lines WHERE market=? AND side=?",
        (opp["market"], opp["side"])).fetchall()
    if not rows:
        return (None, None)
    h = matching.normalize_team(opp["event_home"])
    a = matching.normalize_team(opp["event_away"])
    hdp = opp["hdp"]
    best = None
    for r in rows:
        if matching.normalize_team(r["event_home"]) != h or \
           matching.normalize_team(r["event_away"]) != a:
            continue
        if hdp is not None and r["line"] is not None and \
           round(r["line"], 2) != round(hdp, 2):
            continue
        if best is None or (r["updated_at"] or "") > (best["updated_at"] or ""):
            best = r
    if best is None:
        return (None, None)
    return (best["fair_odd"], best["updated_at"])


def freeze_closings():
    """Congela a fair odd de FECHAMENTO (última linha da Pinnacle pré-jogo) e
    preenche o CLV das apostas quando o jogo começa.

    Fonte do fechamento, em ordem: (1) a fair_line mais recente da Pinnacle para
    o mercado/linha/lado exatos (correto mesmo se a oportunidade já sumiu); (2)
    fallback: a última fair_odd guardada na oportunidade. Marca clv_stale=1 se o
    fechamento for velho (> CLV_STALE_MINUTES antes do início) — ex.: servidor
    ficou offline no kickoff —, para o painel sinalizar CLV aproximado."""
    from . import config, matching
    now = utcnow()
    with _lock, _con() as con:
        rows = con.execute("""
            SELECT id, fair_odd, event_home, event_away, market, hdp, side,
                   event_date, last_seen
            FROM opportunities
            WHERE closed=0 AND event_date IS NOT NULL AND event_date <= ?
        """, (now,)).fetchall()
        for r in rows:
            closing, closing_at = _closing_fair(con, r, matching)
            if closing is None:
                closing, closing_at = r["fair_odd"], r["last_seen"]
            con.execute("UPDATE opportunities SET closed=1, closing_fair_odd=?, active=0 WHERE id=?",
                        (closing, r["id"]))
            # quão velho é o fechamento em relação ao início do jogo?
            stale = 0
            kick = matching.parse_time(r["event_date"])
            cap = matching.parse_time(closing_at)
            if kick and cap and (kick - cap).total_seconds() > config.CLV_STALE_MINUTES * 60:
                stale = 1
            bets = con.execute("""
                SELECT id, odd_taken FROM bets
                WHERE opportunity_id=? AND odd_close IS NULL
            """, (r["id"],)).fetchall()
            for b in bets:
                if closing and closing > 1.0:
                    clv = (b["odd_taken"] / closing - 1.0) * 100.0
                    con.execute("UPDATE bets SET odd_close=?, clv_pct=?, clv_stale=? WHERE id=?",
                                (closing, round(clv, 2), stale, b["id"]))

        # CLV das DUPLAS: quando TODAS as pernas fecharam, o fechamento combinado
        # é o produto dos fechamentos individuais (odd justa da múltipla).
        parlays = con.execute("""
            SELECT id, odd_taken, legs_ids FROM bets
            WHERE odd_close IS NULL AND legs_ids IS NOT NULL AND legs_ids != ''
        """).fetchall()
        for p in parlays:
            ids = [x for x in (p["legs_ids"] or "").split(",") if x]
            if not ids:
                continue
            comb, ok, stale = 1.0, True, 0
            for oid in ids:
                leg = con.execute(
                    "SELECT closing_fair_odd, closed FROM opportunities WHERE id=?",
                    (oid,)).fetchone()
                if not leg or not leg["closed"] or not leg["closing_fair_odd"] \
                        or leg["closing_fair_odd"] <= 1.0:
                    ok = False
                    break
                comb *= leg["closing_fair_odd"]
            if ok and comb > 1.0:
                clv = (p["odd_taken"] / comb - 1.0) * 100.0
                con.execute("UPDATE bets SET odd_close=?, clv_pct=?, clv_stale=? WHERE id=?",
                            (round(comb, 3), round(clv, 2), stale, p["id"]))


def bet_families() -> set:
    """Conjunto de (evento, mercado) em que JÁ HÁ aposta registrada. Serve para
    silenciar as demais linhas do mesmo jogo+mercado (evita duplicar/superexpor).
    'evento' é 'mandante vs visitante', igual ao gravado em bets."""
    with _con() as con:
        rows = con.execute("SELECT event, market FROM bets").fetchall()
    return {(r["event"], r["market"]) for r in rows}


def _family(o: dict) -> tuple:
    """Chave de correlação: mesmo jogo + mesmo mercado + mesmo lado. Todas as
    linhas de Spread home da Chapecoense caem na mesma família."""
    return (o["event_home"], o["event_away"], o["market"], o["side"])


def list_opportunities(tab: str = "", active_only: bool = True,
                       min_edge: float = 0.0, sport: str = "",
                       search: str = "", limit: int = 300,
                       collapse: bool = True, hide_bet: bool = True,
                       min_limit: float = 0.0, book: str = "") -> list[dict]:
    q = "SELECT * FROM opportunities WHERE 1=1"
    args: list = []
    if tab:
        q += " AND tab=?"; args.append(tab)
    if active_only:
        q += " AND active=1"
    if min_edge > 0:
        q += " AND edge_pct >= ?"; args.append(min_edge)
    if min_limit > 0:      # esconde mercados de liquidez baixa (ex.: € < 100)
        q += " AND max_limit >= ?"; args.append(min_limit)
    if sport:
        q += " AND sport=?"; args.append(sport)
    if book:
        q += " AND book=?"; args.append(book)
    if search:
        q += " AND (event_home LIKE ? OR event_away LIKE ? OR league LIKE ? OR player LIKE ?)"
        args += [f"%{search}%"] * 4
    q += " ORDER BY edge_pct DESC"
    with _con() as con:
        rows = [dict(r) for r in con.execute(q, args).fetchall()]

    # silencia jogos+mercados em que você já apostou (não superexpõe)
    if hide_bet:
        fam = bet_families()
        rows = [r for r in rows
                if (f"{r['event_home']} vs {r['event_away']}", r["market"]) not in fam]

    # colapsa linhas correlacionadas: 1 por (jogo, mercado, lado) — a de maior
    # edge — e conta quantas outras existem (para o painel mostrar "+N linhas")
    if collapse:
        best: dict = {}
        for r in rows:
            k = _family(r)
            if k not in best:
                r["family_count"] = 1
                best[k] = r
            else:
                best[k]["family_count"] += 1
        rows = sorted(best.values(), key=lambda x: -x["edge_pct"])

    return rows[:limit]


def get_opportunity(opp_id: str) -> dict | None:
    with _con() as con:
        row = con.execute("SELECT * FROM opportunities WHERE id=?", (opp_id,)).fetchone()
        return dict(row) if row else None


def list_family(opp_id: str) -> list[dict]:
    """Todas as linhas ATIVAS correlacionadas à oportunidade (mesmo jogo +
    mercado + lado), da maior para a menor edge. É o que o painel mostra ao
    expandir '+N linhas correlacionadas' — para escolher uma linha alternativa
    (ex.: outra do handicap) mesmo com edge levemente menor."""
    o = get_opportunity(opp_id)
    if not o:
        return []
    with _con() as con:
        rows = con.execute("""
            SELECT * FROM opportunities
            WHERE active=1 AND event_home=? AND event_away=? AND market=? AND side=?
            ORDER BY edge_pct DESC
        """, (o["event_home"], o["event_away"], o["market"], o["side"])).fetchall()
    return [dict(r) for r in rows]


def purge_old(days: int = 7):
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _lock, _con() as con:
        con.execute("DELETE FROM opportunities WHERE last_seen < ? AND active=0", (cutoff,))
        con.execute("DELETE FROM odds_snapshots WHERE ts < ?", (cutoff,))


# --------------------------------------------------------- fair lines (sharp)

def upsert_fair_lines(lines: list[dict]) -> int:
    """Grava/atualiza as linhas de-vigadas da fonte sharp. Retorna quantas
    entraram novas (as demais foram atualizadas)."""
    if not lines:
        return 0
    new = 0
    with _lock, _con() as con:
        for ln in lines:
            cur = con.execute("""
                UPDATE fair_lines SET raw_odd=?, fair_odd=?, fair_prob=?,
                    max_limit=?, event_date=?, updated_at=?
                WHERE id=?
            """, (ln["raw_odd"], ln["fair_odd"], ln["fair_prob"],
                  ln["max_limit"], ln["event_date"], ln["updated_at"], ln["id"]))
            if cur.rowcount == 0:
                con.execute("""
                    INSERT INTO fair_lines
                    (id, source, sport, league, event_home, event_away, event_date,
                     matchup_id, market_key, market, line, side, period, player,
                     raw_odd, fair_odd, fair_prob, max_limit, first_seen, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (ln["id"], ln["source"], ln["sport"], ln["league"],
                      ln["event_home"], ln["event_away"], ln["event_date"],
                      ln["matchup_id"], ln.get("market_key"), ln["market"],
                      ln["line"], ln["side"], ln["period"], ln.get("player"),
                      ln["raw_odd"], ln["fair_odd"], ln["fair_prob"], ln["max_limit"],
                      ln["updated_at"], ln["updated_at"]))
                new += 1
    return new


def list_fair_lines(sport: str = "", market: str = "", search: str = "",
                    source: str = "", is_prop: bool | None = None,
                    active_only: bool = True,
                    limit: int = 500) -> list[dict]:
    q = "SELECT * FROM fair_lines WHERE 1=1"
    args: list = []
    
    if active_only:
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        stale_threshold = (now - timedelta(minutes=15)).isoformat()
        q += " AND event_date > ? AND updated_at > ?"
        args.extend([now_str, stale_threshold])
        
    if source:
        q += " AND source=?"; args.append(source)
    if is_prop is True:
        q += " AND player IS NOT NULL AND player != ''"
    elif is_prop is False:
        q += " AND (player IS NULL OR player = '')"
    if sport:
        q += " AND sport=?"; args.append(sport)
    if market:
        q += " AND market=?"; args.append(market)
    if search:
        q += " AND (event_home LIKE ? OR event_away LIKE ? OR league LIKE ?)"
        args += [f"%{search}%"] * 3
    q += " ORDER BY event_date ASC, matchup_id, market, line LIMIT ?"
    args.append(limit)
    with _con() as con:
        return [dict(r) for r in con.execute(q, args).fetchall()]


def fair_lines_stats() -> dict:
    with _con() as con:
        row = con.execute("""
            SELECT COUNT(*) n, COUNT(DISTINCT matchup_id) events,
                   MAX(updated_at) last, AVG(max_limit) avg_limit
            FROM fair_lines
        """).fetchone()
        by_sport = {r["sport"]: r["n"] for r in con.execute(
            "SELECT sport, COUNT(*) n FROM fair_lines GROUP BY sport").fetchall()}
    return {"lines": row["n"], "events": row["events"], "last_update": row["last"],
            "avg_limit": round(row["avg_limit"], 2) if row["avg_limit"] else None,
            "by_sport": by_sport}


def purge_fair_lines(hours: int = 6):
    """Linhas de jogos que já começaram há mais de X horas não servem mais."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _lock, _con() as con:
        con.execute("DELETE FROM fair_lines WHERE event_date < ?", (cutoff,))


# --------------------------------------------------------------------- bets

def register_bet(opp: dict, stake_units: float, stake_amount: float,
                 odd_taken: float) -> int:
    with _lock, _con() as con:
        cur = con.execute("""
            INSERT INTO bets (opportunity_id, ts_placed, event, event_date,
                sport, league, market, hdp, selection, player, book,
                fair_odd, odd_taken, edge_pct, stake_units, stake_amount,
                source_tab)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (opp["id"], utcnow(),
              f"{opp['event_home']} vs {opp['event_away']}", opp["event_date"],
              opp["sport"], opp["league"], opp["market"], opp["hdp"],
              opp["side"], opp["player"], opp["book"], opp["fair_odd"],
              odd_taken, opp["edge_pct"], stake_units, stake_amount, opp.get("tab", "")))
        return cur.lastrowid


def register_parlay(opps: list[dict], odd_taken: float, fair_odd: float,
                    edge_pct: float, stake_units: float, stake_amount: float) -> int:
    """Registra uma DUPLA/múltipla como UMA aposta (evento e seleção descrevem
    as pernas). opportunity_id vazio: o CLV automático (freeze_closings) não a
    toca — CLV de múltipla precisaria das duas pernas e fica de fora por ora."""
    ev = " + ".join(f"{o['event_home']}×{o['event_away']}" for o in opps)
    sel = " | ".join(
        f"{o['market']} {o['side']}" + (f" {o['hdp']}" if o.get("hdp") is not None else "")
        for o in opps)
    datas = [o.get("event_date") for o in opps if o.get("event_date")]
    event_date = min(datas) if datas else None
    books = {o.get("book") for o in opps if o.get("book")}
    book = next(iter(books)) if len(books) == 1 else "Múltiplas"
    legs_ids = ",".join(str(o["id"]) for o in opps)   # p/ CLV combinado das pernas
    legs_json = json.dumps([{                          # p/ liquidar POR PERNA
        "id": o["id"], "odd": o["offered_odd"],
        "label": f"{o['event_home']}×{o['event_away']} · {o['market']} "
                 f"{o['side']}" + (f" {o['hdp']}" if o.get("hdp") is not None else ""),
    } for o in opps], ensure_ascii=False)
    with _lock, _con() as con:
        cur = con.execute("""
            INSERT INTO bets (opportunity_id, ts_placed, event, event_date,
                sport, league, market, hdp, selection, player, book,
                fair_odd, odd_taken, edge_pct, stake_units, stake_amount,
                legs_ids, legs_json, source_tab)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, ("", utcnow(), f"Dupla: {ev}", event_date, "", "",
              f"Dupla ({len(opps)})", None, sel, "", book,
              fair_odd, odd_taken, edge_pct, stake_units, stake_amount,
              legs_ids, legs_json, "dupla"))
        return cur.lastrowid


def settle_bet(bet_id: int, result: str) -> float:
    """Liquida a aposta. Aceita meio-ganho/meia-perda dos mercados asiáticos
    (linha de quarto): metade da stake resolve, a outra metade é devolvida.
      win        -> stake*(odd-1)
      half_win   -> (stake/2)*(odd-1)   (metade ganha, metade push)
      push       -> 0
      half_loss  -> -stake/2            (metade perde, metade push)
      loss       -> -stake
    """
    result = result.lower()
    if result not in ("win", "loss", "push", "half_win", "half_loss"):
        raise ValueError("result deve ser win | loss | push | half_win | half_loss")
    with _lock, _con() as con:
        row = con.execute("SELECT odd_taken, stake_amount FROM bets WHERE id=?",
                          (bet_id,)).fetchone()
        if not row:
            raise ValueError(f"Aposta {bet_id} não encontrada")
        s, o = row["stake_amount"], row["odd_taken"]
        if result == "win":
            profit = s * (o - 1.0)
        elif result == "half_win":
            profit = (s / 2.0) * (o - 1.0)
        elif result == "loss":
            profit = -s
        elif result == "half_loss":
            profit = -s / 2.0
        else:
            profit = 0.0
        con.execute("""UPDATE bets SET result=?, profit=?, settled=1, ts_settled=?
                       WHERE id=?""", (result, round(profit, 2), utcnow(), bet_id))
        return profit


# fator de retorno de UMA perna, por resultado (asiático de quarto = meio):
#   win = odd | ½win = (odd+1)/2 | push = 1 | ½loss = 0.5 | loss = 0
# O payout da dupla é o PRODUTO dos fatores × stake (é como cada casa ajusta o
# pagamento quando uma perna é meio-ganho/meia-perda — a outra segue cheia).
_LEG_FACTOR = {
    "win": lambda o: o,
    "half_win": lambda o: (o + 1.0) / 2.0,
    "push": lambda o: 1.0,
    "half_loss": lambda o: 0.5,
    "loss": lambda o: 0.0,
}


def settle_parlay(bet_id: int, legs: list[dict]) -> float:
    """Liquida uma DUPLA/múltipla POR PERNA. `legs` = [{'odd': float,
    'result': win|half_win|push|half_loss|loss}, ...] — a odd vem do painel
    (pré-preenchida quando temos, ou digitada p/ duplas antigas sem dados).
    Payout = stake × Π(fator_perna)."""
    if not legs:
        raise ValueError("informe as pernas (odd + resultado de cada uma)")
    with _lock, _con() as con:
        row = con.execute("SELECT stake_amount FROM bets WHERE id=?",
                          (bet_id,)).fetchone()
        if not row:
            raise ValueError(f"Aposta {bet_id} não encontrada")
        fator = 1.0
        resumo_parts = []
        for leg in legs:
            res = str(leg.get("result", "")).lower()
            if res not in _LEG_FACTOR:
                raise ValueError(f"resultado inválido: {res}")
            odd = float(leg.get("odd") or 0.0)
            if res in ("win", "half_win") and odd <= 1.0:
                raise ValueError("odd da perna deve ser > 1.0")
            fator *= _LEG_FACTOR[res](odd)
            resumo_parts.append({"win": "W", "half_win": "½W", "push": "P",
                                 "half_loss": "½L", "loss": "L"}[res])
        payout = row["stake_amount"] * fator
        profit = payout - row["stake_amount"]
        base = "win" if profit > 1e-6 else ("loss" if profit < -1e-6 else "push")
        con.execute("""UPDATE bets SET result=?, profit=?, settled=1, ts_settled=?
                       WHERE id=?""",
                    (f"{base} ({'+'.join(resumo_parts)})", round(profit, 2),
                     utcnow(), bet_id))
        return profit


def reopen_bet(bet_id: int):
    """Reabre uma aposta liquidada (para corrigir um resultado marcado errado):
    volta a pendente e limpa resultado/lucro. Mantém o CLV/fechamento."""
    with _lock, _con() as con:
        cur = con.execute("""UPDATE bets SET settled=0, result=NULL, profit=NULL,
                             ts_settled=NULL WHERE id=?""", (bet_id,))
        if cur.rowcount == 0:
            raise ValueError(f"Aposta {bet_id} não encontrada")


def list_bets(only_pending: bool = False) -> list[dict]:
    q = "SELECT * FROM bets"
    if only_pending:
        q += " WHERE settled=0"
    q += " ORDER BY id DESC"
    with _con() as con:
        return [dict(r) for r in con.execute(q).fetchall()]


def bets_summary() -> dict:
    with _con() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT stake_amount, stake_units, profit, clv_pct, edge_pct, result, settled FROM bets"
        ).fetchall()]
    settled = [r for r in rows if r["settled"] == 1]
    # resultado-base: 'win', 'half_win'... ou 'win (W+½W)' de dupla -> 1º token
    def _base(r):
        return (r["result"] or "").split(" ")[0]
    staked = sum(r["stake_amount"] for r in settled)
    profit = sum(r["profit"] or 0.0 for r in settled)
    wins = sum(1 for r in settled if _base(r) == "win")
    losses = sum(1 for r in settled if _base(r) == "loss")
    half_wins = sum(1 for r in settled if _base(r) == "half_win")
    half_losses = sum(1 for r in settled if _base(r) == "half_loss")
    clvs = [r["clv_pct"] for r in rows if r["clv_pct"] is not None]
    edges = [r["edge_pct"] for r in rows if r["edge_pct"] is not None]
    units = sum((r["profit"] or 0.0) / (r["stake_amount"] / r["stake_units"])
                for r in settled if r["stake_units"] and r["stake_amount"])
    # taxa de acerto conta meio-ganho como 0,5 acerto (mercado asiático)
    win_eq = wins + 0.5 * half_wins
    return {
        "total_bets": len(rows),
        "settled": len(settled),
        "pending": len(rows) - len(settled),
        "wins": wins, "losses": losses,
        "half_wins": half_wins, "half_losses": half_losses,
        "pushes": sum(1 for r in settled if r["result"] == "push"),
        "win_rate": round(win_eq / len(settled) * 100, 1) if settled else 0.0,
        "total_staked": round(staked, 2),
        "total_profit": round(profit, 2),
        "profit_units": round(units, 2),
        "roi_pct": round(profit / staked * 100, 2) if staked else 0.0,
        "avg_clv_pct": round(sum(clvs) / len(clvs), 2) if clvs else None,
        "clv_positive_pct": round(sum(1 for c in clvs if c > 0) / len(clvs) * 100, 1) if clvs else None,
        "avg_edge_pct": round(sum(edges) / len(edges), 2) if edges else None,
    }


# ------------------------------------------------------------------- boosts

def add_boosts(items: list[dict]) -> int:
    added = 0
    with _lock, _con() as con:
        for b in items:
            try:
                con.execute("""
                    INSERT OR IGNORE INTO boosts
                    (captured_at, source, selnid, is_simple, title, odd_old, odd_new, raw_json)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (b.get("captured_at") or utcnow(), b.get("source", "betano"),
                      str(b.get("selnid", "")), 1 if b.get("is_simple") else 0,
                      b.get("title") or b.get("name", ""), b.get("odd_old"),
                      b.get("odd_new"), json.dumps(b, ensure_ascii=False)))
                added += con.execute("SELECT changes()").fetchone()[0]
            except Exception:
                continue
    return added


def list_boosts(limit: int = 200) -> list[dict]:
    with _con() as con:
        rows = con.execute("SELECT * FROM boosts ORDER BY id DESC LIMIT ?",
                           (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["raw"] = json.loads(d.pop("raw_json") or "{}")
        except Exception:
            d["raw"] = {}
        out.append(d)
    return out


# ---------------------------------------------------------- odds da extensão

def add_odds_snapshot(payload: dict) -> int:
    n = 0
    with _lock, _con() as con:
        for mkt in payload.get("markets", []):
            for s in mkt.get("selections", []):
                con.execute("""
                    INSERT INTO odds_snapshots
                    (ts, source, event, event_dt, url, market, market_id, selection, line, odd, selnid)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, (payload.get("captured_at") or utcnow(),
                      payload.get("source", "betano"), payload.get("event", ""),
                      payload.get("event_datetime", ""), payload.get("url", ""),
                      mkt.get("market", ""), mkt.get("market_id", ""),
                      s.get("sel", ""), s.get("line"), s.get("odd"),
                      str(s.get("selnid", ""))))
                n += 1
    return n


def list_odds_snapshots(source: str = "", search: str = "", limit: int = 400) -> list[dict]:
    """Odds cruas capturadas pela extensão (para a aba 'Odds Extraídas').
    Mostra a captura MAIS RECENTE de cada evento+mercado+seleção."""
    q = ("SELECT source, event, event_dt, url, market, selection, line, odd, MAX(ts) ts "
         "FROM odds_snapshots WHERE 1=1")
    args: list = []
    if source:
        q += " AND source=?"; args.append(source)
    if search:
        q += " AND event LIKE ?"; args.append(f"%{search}%")
    q += (" GROUP BY source, event, market, selection, line "
          "ORDER BY ts DESC LIMIT ?")
    args.append(limit)
    with _con() as con:
        return [dict(r) for r in con.execute(q, args).fetchall()]
