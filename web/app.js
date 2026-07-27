/* Value HUB 2.0 — painel */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  tab: "value",
  seen: new Set(),        // ids já vistos (para destacar/apitar novas)
  firstLoad: true,
  betPlaced: new Set(),   // oportunidades já apostadas nesta sessão
  unitValue: 100,         // R$ por unidade (atualizado pelo /api/status)
  parlayLegs: [],         // pernas selecionadas para montar uma dupla/múltipla
  frozen: false,          // congela o auto-refresh enquanto uma expansão está aberta
};

/* ---------------------------------------------------------------- helpers */
const fmtOdd = (o, book) => {
  if (o == null) return "—";
  const num = Number(o);
  let base = num.toFixed(num >= 10 ? 1 : 2);
  if (book === "Polymarket") {
    let prob = (1.0 / num).toFixed(2).replace(".", ",");
    base += ` <small style="color:#888;">(${prob})</small>`;
  }
  return base;
};
const fmtPct = (p) => (p == null ? "—" : `${p > 0 ? "+" : ""}${Number(p).toFixed(2)}%`);
const fmtBRL = (v) => `R$ ${Number(v).toFixed(2)}`;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmtKickoff(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  // sempre a data real (DD/MM HH:MM) — nunca "hoje": assim, quando o dia vira
  // ontem, a data continua correta em vez de virar "hoje" errado.
  const dia = d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
  const hm = d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  return `${dia} ${hm}`;
}

function beep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.connect(g); g.connect(ctx.destination);
    o.frequency.value = 880; g.gain.value = 0.06;
    o.start(); o.stop(ctx.currentTime + 0.18);
  } catch (_) { /* sem áudio, sem drama */ }
}

async function jget(url) { const r = await fetch(url); return r.json(); }
async function jpost(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) });
  if (!r.ok) throw new Error((await r.json()).detail || r.status);
  return r.json();
}

/* ----------------------------------------------------------------- status */
async function refreshStatus() {
  try {
    const s = await jget("/api/status");
    if (s.unit_value) state.unitValue = s.unit_value;
    const p = s.poller;
    const pin = p.pinnacle || {};
    const dot = $("statusDot");
    const parts = [];

    // o motor SHARP (infra própria) é o principal — status manda nele
    if (pin.enabled) {
      if (pin.last_error) {
        dot.className = "dot err";
        parts.push(`Pinnacle: ${pin.last_error}`);
      } else {
        dot.className = "dot ok";
        const ago = pin.last_sweep_at
          ? Math.round(Date.now() / 1000 - pin.last_sweep_at) : null;
        parts.push(ago == null
          ? "Pinnacle: 1ª varredura em andamento…"
          : `Pinnacle: ${pin.stats?.lines ?? 0} linhas justas · ${pin.stats?.events ?? 0} jogos · há ${ago}s`);
      }
    } else {
      dot.className = "dot err";
      parts.push("motor sharp desligado");
    }
    const tg = p.targets || {};
    if (tg.enabled) {
      const s = tg.stats || {};
      if (tg.last_error) parts.push(`Betano: ${tg.last_error}`);
      else if (!tg.runs) parts.push("Betano: aguardando 1ª rodada…");
      else parts.push(`Betano: ${s.casados ?? 0} jogos casados · ${s.oportunidades ?? 0} com valor`);
    }
    if (p.api_key_set) parts.push(`odds-api: ${p.books_active.join(", ") || "—"}`);

    // indicador da extensão (Bet365/Betano) — ajuda a ver se está capturando
    try {
      const ext = await jget("/api/extension_status");
      for (const [src, info] of Object.entries(ext.sources || {})) {
        const quando = info.secs_ago < 90 ? `há ${Math.round(info.secs_ago)}s` : "inativo";
        parts.push(`📡 ${src}: ${info.markets} mercados (${quando})`);
      }
    } catch (_) { /* sem extensão ativa */ }

    $("statusText").textContent = parts.join("  ·  ");
    $("paperBadge").style.display = p.paper_trading ? "" : "none";
    $("footerInfo").textContent =
      `Banca ${fmtBRL(s.bankroll)} · 1u = ${fmtBRL(s.unit_value)} · quarter Kelly, teto 3% · ` +
      `Pinnacle: ${pin.sweeps ?? 0} varreduras / ${pin.requests_made ?? 0} requests · ` +
      `custo mensal de dados: R$ 0`;
  } catch (_) {
    $("statusDot").className = "dot err";
    $("statusText").textContent = "servidor offline";
  }
}

/* ---------------------------------------------------------- oportunidades */
function edgeBadge(o) {
  if (o.suspicious) return `<span class="badge sus" title="edge acima do teto de sanidade — provável linha podre">${fmtPct(o.edge_pct)}</span>`;
  // faixas de cor: 2,8–5% azul · 5–8% âmbar · >8% verde
  const e = o.edge_pct;
  const cls = e >= 8 ? "e-green" : e >= 5 ? "e-amber" : "e-blue";
  return `<span class="badge ${cls}">${fmtPct(e)}</span>`;
}

/* confiança do casamento sharp x casa — 1.0 = nomes idênticos.
   Abaixo de 1 vale conferir o jogo antes de apostar. */
function matchBadge(o) {
  if (o.match_score == null || o.match_score >= 0.999) return "";
  return ` · <span title="confiança do casamento entre o jogo da referência e o da casa"
    style="color:var(--warn)">casamento ${(o.match_score * 100).toFixed(0)}%</span>`;
}

/* quantas casas sharp concordaram na fair odds (consenso). */
function sharpBadge(o) {
  const n = o.n_sharps || 1;
  if (n < 2) return "";
  return ` · <span title="fair odds = consenso de ${esc(o.sharp_sources || "")}"
    style="color:var(--accent)">✓ ${n} sharps</span>`;
}

/* quantas outras linhas correlacionadas (mesmo jogo+mercado+lado) foram
   colapsadas nesta. Alerta de que NÃO se deve apostar em várias. */
function familyNote(o) {
  const n = (o.family_count || 1) - 1;
  if (n < 1) return "";
  // clicável: expande as outras linhas do mesmo mercado para você escolher
  return `<div class="ev-sub fam-toggle" data-id="${esc(o.id)}" role="button" tabindex="0"
    title="clique para ver as outras linhas deste mercado e escolher uma"
    style="color:var(--warn);cursor:pointer;text-decoration:underline dotted">
    +${n} linha${n > 1 ? "s" : ""} correlacionada${n > 1 ? "s" : ""} ▾</div>`;
}

/* mostra na barra de contagem que o auto-refresh está pausado (expansão aberta) */
function updateFreezeHint() {
  const c = $("oppCount");
  if (!c) return;
  const base = c.textContent.replace(/\s*·\s*⏸.*$/, "");
  c.textContent = state.frozen ? `${base} · ⏸ atualização pausada (feche a expansão)` : base;
}

/* expande/colapsa as linhas correlacionadas de uma oportunidade */
function wireFamilyToggles() {
  document.querySelectorAll("#oppBody .fam-toggle").forEach((el) =>
    el.addEventListener("click", () => toggleFamily(el)));
}

let familyLoading = false;
async function toggleFamily(el) {
  const tr = el.closest("tr");
  const next = tr.nextElementSibling;
  // já aberta NESTA linha? então o clique é um "fechar".
  if (next && next.classList.contains("family-expand")) {
    next.remove();
    state.frozen = false;
    updateFreezeHint();
    return;
  }
  if (familyLoading) return;               // ignora cliques enquanto carrega (evita abrir repetido)
  // política de UMA expansão por vez: fecha qualquer outra antes de abrir
  document.querySelectorAll("#oppBody .family-expand").forEach((e) => e.remove());
  state.frozen = true;                      // congela JÁ (ANTES do fetch), senão o refresh duplica/fecha
  updateFreezeHint();
  familyLoading = true;
  let data;
  try { data = await jget(`/api/opportunity_family?id=${encodeURIComponent(el.dataset.id)}`); }
  catch (_) { familyLoading = false; state.frozen = false; updateFreezeHint(); return; }
  familyLoading = false;
  const rows = data.rows || [];
  if (!rows.length) { state.frozen = false; updateFreezeHint(); return; }
  // guarda extra: se um clique-duplo já criou a expansão, não cria outra
  if (tr.nextElementSibling && tr.nextElementSibling.classList.contains("family-expand")) return;
  const cols = tr.children.length;
  const linhas = rows.map((o) => {
    const line = o.hdp != null ? `${o.hdp > 0 && o.market !== "Totals" ? "+" : ""}${o.hdp}` : "—";
    const placed = state.betPlaced.has(o.id);
    return `<div class="fam-line" data-id="${esc(o.id)}" data-side="${esc(o.side)}"
         data-line="${esc(line)}" data-odd="${o.offered_odd}" data-stake="${o.stake_units}">
      <input type="checkbox" class="fam-pick" title="marque para incluir no rateio de stake">
      <span class="mkt-side" style="min-width:90px">${esc(o.side)} ${line}</span>
      <span>odd <b class="odd-cell">${fmtOdd(o.offered_odd, o.book)}</b></span>
      <span class="fair-cell">fair ${fmtOdd(o.fair_odd)}</span>
      ${edgeBadge(o)}
      <span class="stake">${o.stake_units}u <small>(${fmtBRL(o.stake_amount)})</small></span>
      <button class="act bet" data-id="${esc(o.id)}" ${placed ? "disabled" : ""}>${placed ? "✓ registrada" : "Apostar"}</button>
      ${o.direct_link ? `<a class="act link" href="${esc(o.direct_link)}" target="_blank" rel="noopener">Link</a>` : ""}
      <button class="act parlay-add" data-id="${esc(o.id)}" data-odd="${o.offered_odd}"
        data-label="${esc(`${o.event_home} ${o.market} ${o.side} ${line}`)}" type="button"
        title="adicionar/remover esta linha da dupla">🔗</button>
    </div>`;
  }).join("");
  const exp = document.createElement("tr");
  exp.className = "family-expand";
  exp.innerHTML = `<td colspan="${cols}"><div class="fam-box">
    <div class="ev-sub" style="margin-bottom:6px">Linhas de <b>${esc(rows[0].market)}</b> · <span class="mkt-side">${esc(rows[0].side)}</span> — escolha a que preferir (edge maior = melhor, mas a linha importa):</div>
    ${linhas}
    <div class="fam-split">
      <span class="ev-sub">Rateio (2+ linhas): dividir</span>
      <input type="number" class="fam-total" step="0.25" min="0.25" placeholder="auto"> u
      <button class="act win fam-split-btn" type="button">Sugerir divisão</button>
      <span class="ev-sub">— mais peso na linha mais segura (odd menor), para cobrir; as maiores dão o lucro.</span>
      <div class="fam-split-out"></div>
    </div></div></td>`;
  tr.after(exp);
  exp.querySelectorAll(".act.bet").forEach((b) =>
    b.addEventListener("click", () => placeBet(b.dataset.id, b)));
  exp.querySelectorAll(".parlay-add").forEach((b) =>
    b.addEventListener("click", () => toggleParlayLeg(b.dataset.id, b.dataset.label, parseFloat(b.dataset.odd))));
  markParlayButtons();
  const splitBtn = exp.querySelector(".fam-split-btn");
  if (splitBtn) splitBtn.addEventListener("click", () => splitFamily(exp));
  exp.querySelectorAll(".fam-pick").forEach((cb) =>
    cb.addEventListener("change", () => splitFamily(exp)));
}

/* rateio (dutching) entre linhas correlacionadas marcadas: distribui um total
   de unidades proporcionalmente à stake Kelly de cada linha — o que naturalmente
   coloca MAIS na linha mais segura (odd menor, menos amortecida) e menos nas de
   odd alta. Cálculo local (aritmética simples), instantâneo. */
function splitFamily(box) {
  const picks = [...box.querySelectorAll(".fam-line")]
    .filter((l) => l.querySelector(".fam-pick").checked);
  const out = box.querySelector(".fam-split-out");
  if (picks.length < 2) {
    out.innerHTML = `<span class="ev-sub">Marque 2 ou mais linhas para ratear.</span>`;
    return;
  }
  const stakes = picks.map((l) => parseFloat(l.dataset.stake) || 0);
  const maxStake = Math.max(...stakes);
  const wsum = stakes.reduce((a, b) => a + b, 0) || picks.length;
  const totalInput = box.querySelector(".fam-total");
  let total = parseFloat((totalInput.value || "").replace(",", "."));
  if (!total || total <= 0) { total = maxStake; totalInput.value = maxStake; }  // padrão: 1 posição
  const uv = state.unitValue || 100;
  let somaU = 0;
  const plano = [];
  const linhas = picks.map((l, i) => {
    let u = total * (stakes[i] / wsum);
    u = Math.max(0.25, Math.round(u / 0.25) * 0.25);       // passo de 0.25u
    somaU += u;
    plano.push({ id: l.dataset.id, odd: parseFloat(l.dataset.odd), units: u });
    return `<div class="fam-split-row">
      <span class="mkt-side" style="min-width:90px">${esc(l.dataset.side)} ${esc(l.dataset.line)}</span>
      <span>@ ${fmtOdd(parseFloat(l.dataset.odd))}</span>
      <b>${u}u</b> <small>(${fmtBRL(u * uv)})</small></div>`;
  }).join("");
  out.innerHTML = `${linhas}
    <div class="ev-sub" style="margin:4px 0">Total distribuído: <b>${somaU.toFixed(2)}u</b> (${fmtBRL(somaU * uv)}).
    O padrão distribui o tamanho de <b>uma</b> posição entre as linhas (não soma exposição); aumente o total se quiser arriscar mais.</div>
    <button class="act bet fam-split-bet" type="button">Apostar rateio (${plano.length} linhas)</button>`;
  const betBtn = out.querySelector(".fam-split-bet");
  betBtn.addEventListener("click", async () => {
    betBtn.disabled = true;
    try {
      for (const p of plano) {
        await jpost("/bet", { opportunity_id: p.id, stake_units: p.units, odd_taken: p.odd });
        state.betPlaced.add(p.id);
      }
      betBtn.textContent = `✓ ${plano.length} apostas registradas`;
      state.frozen = false;               // libera o refresh após apostar
      setTimeout(() => { refreshOpps(); if (state.tab === "bets") refreshBets(); }, 700);
    } catch (e) { betBtn.disabled = false; alert("Erro ao registrar rateio: " + e.message); }
  });
}

function marketLabel(o) {
  const line = o.hdp != null ? ` ${o.hdp > 0 && o.market !== "Totals" ? "+" : ""}${o.hdp}` : "";
  if (o.player)
    return `<div class="ev-name">${esc(o.player)}</div>
            <div class="ev-sub">${esc(o.market)}${line} · <span class="mkt-side">${esc(o.side)}</span></div>`;
  return `<div class="ev-name">${esc(o.market)}${line}</div>
          <div class="ev-sub"><span class="mkt-side">${esc(o.side)}</span></div>`;
}

async function refreshOpps() {
  if (!["value", "esports", "props", "other"].includes(state.tab)) return;
  const q = new URLSearchParams({
    tab: state.tab,
    min_edge: $("fMinEdge").value || 0,
    min_limit: $("fMinLimit").value || 0,
    sport: $("fSport").value,
    book: $("fBook").value,
    search: $("fSearch").value.trim(),
  });
  let data;
  try { data = await jget(`/api/opportunities?${q}`); } catch (_) { return; }

  let rows = data.rows;
  if (!$("fSuspicious").checked) rows = rows.filter((o) => !o.suspicious);
  $("oppCount").textContent = `${rows.length} oportunidades`;
  $("oppEmpty").hidden = rows.length > 0;

  let hasNew = false;
  const html = rows.map((o) => {
    const isNew = !state.seen.has(o.id);
    if (isNew) { state.seen.add(o.id); if (!state.firstLoad) hasNew = true; }
    const placed = state.betPlaced.has(o.id);
    return `<tr class="${isNew && !state.firstLoad ? "new" : ""}">
      <td>${fmtKickoff(o.event_date)}</td>
      <td><div class="ev-name">${esc(o.event_home)} × ${esc(o.event_away)}</div>
          <div class="ev-sub">${esc(o.sport)} · ${esc(o.league)}${matchBadge(o)}${sharpBadge(o)}</div></td>
      <td>${marketLabel(o)}${familyNote(o)}</td>
      <td>${esc(o.book)}</td>
      <td class="odd-cell">${fmtOdd(o.offered_odd, o.book)}</td>
      <td class="fair-cell">${fmtOdd(o.fair_odd)}</td>
      <td>${edgeBadge(o)}</td>
      <td class="limit">${o.max_limit ? "€" + Math.round(o.max_limit) : "—"}<br>
          <span title="edge mínimo exigido pela faixa de liquidez">≥${o.min_edge_required}%</span></td>
      <td class="stake">${o.stake_units}u <small>(${fmtBRL(o.stake_amount)})</small></td>
      <td>
        <button class="act bet" data-id="${esc(o.id)}" ${placed ? "disabled" : ""}>
          ${placed ? "✓ registrada" : "Apostar"}</button>
        ${o.direct_link ? `<a class="act link" href="${esc(o.direct_link)}" target="_blank" rel="noopener">Link</a>` : ""}
        <button class="act parlay-add" data-id="${esc(o.id)}" data-odd="${o.offered_odd}"
          data-label="${esc(`${o.event_home} ${o.market} ${o.side}`)}" type="button"
          title="adicionar/remover esta aposta da dupla (odds baixas: combinar compõe o valor)">🔗</button>
        <div class="recalc" data-id="${esc(o.id)}"
             title="Se a casa baixou a odd, digite a odd atual para ver o novo edge e a stake recalculada (mesma fair odds).">
          <input type="number" step="0.01" min="1.01" class="rc-odd" placeholder="odd atual">
          <span class="rc-out"></span>
        </div>
      </td>
    </tr>`;
  }).join("");
  $("oppBody").innerHTML = html;

  if (hasNew && $("fSound").checked) beep();
  state.firstLoad = false;
  refreshNearMisses(rows.length === 0 && state.tab === "value");

  document.querySelectorAll("#oppBody .act.bet").forEach((b) =>
    b.addEventListener("click", () => placeBet(b.dataset.id, b)));
  document.querySelectorAll("#oppBody .parlay-add").forEach((b) =>
    b.addEventListener("click", () => toggleParlayLeg(b.dataset.id, b.dataset.label, parseFloat(b.dataset.odd))));
  markParlayButtons();
  wireRecalc();
  wireFamilyToggles();
}

/* ---------------------------------------------------------------- duplas
   Bandeja de MÚLTIPLA: para odds baixas (1.2–1.4), combinar 2 pernas +EV
   compõe o edge multiplicativamente. O cálculo (odd, edge, stake) sai do
   /api/parlay (mesmo motor); "Apostar dupla" salva via /bet_parlay como uma
   aposta única. Avisa se as pernas são do mesmo jogo (não independentes). */
function toggleParlayLeg(id, label, odd) {
  const i = state.parlayLegs.findIndex((l) => l.id === id);
  if (i >= 0) state.parlayLegs.splice(i, 1);
  else {
    if (state.parlayLegs.length >= 4) { alert("Máximo de 4 pernas."); return; }
    state.parlayLegs.push({ id, label: label || id, odd: odd || null });
  }
  renderParlayBar();
  markParlayButtons();
}

function markParlayButtons() {
  const ids = new Set(state.parlayLegs.map((l) => l.id));
  document.querySelectorAll(".parlay-add").forEach((b) =>
    b.classList.toggle("on", ids.has(b.dataset.id)));
}

async function renderParlayBar() {
  const bar = $("parlayBar");
  const legs = state.parlayLegs;
  if (!legs.length) { bar.hidden = true; bar.innerHTML = ""; return; }
  bar.hidden = false;
  const chips = legs.map((l) =>
    `<span class="parlay-chip">${esc(l.label)}${l.odd ? " @" + fmtOdd(l.odd) : ""}
       <b class="parlay-rm" data-id="${esc(l.id)}" title="remover">✕</b></span>`).join("");
  let resumo = `<span class="ev-sub">Marque 2+ pernas para calcular a dupla.</span>`;
  let podeApostar = false, mesmoJogo = false;
  if (legs.length >= 2) {
    try {
      const r = await jpost("/api/parlay", { ids: legs.map((l) => l.id) });
      const cls = r.edge_pct > 0 ? "pos" : "neg";
      mesmoJogo = !!r.same_event;
      podeApostar = !mesmoJogo;
      resumo = `<b>Dupla @${fmtOdd(r.combined_odd)}</b> · fair ${fmtOdd(r.fair_odd)} · ` +
        `<b class="${cls}">${fmtPct(r.edge_pct)}</b> · stake <b>${r.stake_units}u</b> (${fmtBRL(r.stake_amount)})` +
        (mesmoJogo ? ` <span class="neg">⚠ pernas do MESMO jogo — não são independentes, o cálculo não vale</span>` : "");
    } catch (e) { resumo = `<span class="neg">erro ao calcular a dupla</span>`; }
  }
  const btnApostar = podeApostar
    ? `<button class="act bet" id="parlayBet" type="button">Apostar dupla</button>` : "";
  bar.innerHTML = `<div class="parlay-inner">
    <span class="parlay-title">🔗 Dupla</span> ${chips}
    <span class="parlay-res">${resumo}</span>
    ${btnApostar}
    <button class="act push" id="parlayClear" type="button">limpar</button></div>`;
  bar.querySelectorAll(".parlay-rm").forEach((x) =>
    x.addEventListener("click", () => toggleParlayLeg(x.dataset.id)));
  const clr = $("parlayClear");
  if (clr) clr.addEventListener("click", () => {
    state.parlayLegs = []; renderParlayBar(); markParlayButtons();
  });
  const pb = $("parlayBet");
  if (pb) pb.addEventListener("click", async () => {
    pb.disabled = true;
    try {
      await jpost("/bet_parlay", { ids: legs.map((l) => l.id) });
      state.parlayLegs = [];
      renderParlayBar(); markParlayButtons();
      if (state.tab === "bets") refreshBets();
    } catch (e) { pb.disabled = false; alert("Erro ao registrar a dupla: " + e.message); }
  });
}

/* mini calculadora inline: recalcula edge + stake para uma odd "derretida",
   contra a mesma fair odds. Chama /api/restake (mesmo motor do botão Apostar). */
function wireRecalc() {
  document.querySelectorAll("#oppBody .recalc").forEach((box) => {
    const input = box.querySelector(".rc-odd");
    const out = box.querySelector(".rc-out");
    if (!input || !out) return;
    let t;
    input.addEventListener("input", () => {
      clearTimeout(t);
      const odd = parseFloat((input.value || "").replace(",", "."));
      if (!odd || odd <= 1) { out.textContent = ""; return; }
      t = setTimeout(async () => {
        try {
          const r = await jpost("/api/restake", { opportunity_id: box.dataset.id, odd });
          if (r.stake_units > 0 && r.edge_pct > 0) {
            out.innerHTML = `<b class="pos">${fmtPct(r.edge_pct)}</b> · ` +
              `${r.stake_units}u <small>(${fmtBRL(r.stake_amount)})</small>`;
          } else {
            out.innerHTML = `<span class="neg">sem valor (${fmtPct(r.edge_pct)})</span>`;
          }
        } catch (e) { out.textContent = "—"; }
      }, 300);
    });
  });
}

async function placeBet(id, btn) {
  btn.disabled = true;
  try {
    const body = { opportunity_id: id };
    // se você digitou a odd "derretida" na mini calculadora desta linha, a
    // aposta é planilhada COM ela (e a stake recalculada) — não com a original.
    const input = btn.closest("td") ? btn.closest("td").querySelector(".rc-odd") : null;
    const odd = input ? parseFloat((input.value || "").replace(",", ".")) : NaN;
    if (odd && odd > 1) {
      const r = await jpost("/api/restake", { opportunity_id: id, odd });
      if (!(r.stake_units > 0)) {
        btn.disabled = false;
        alert(`Com a odd ${odd.toFixed(2)} não há valor (edge ${r.edge_pct}%, stake 0). Não registrei.`);
        return;
      }
      body.odd_taken = odd;
      body.stake_units = r.stake_units;
    }
    await jpost("/bet", body);
    state.betPlaced.add(id);
    btn.textContent = "✓ registrada";
    // o jogo+mercado é silenciado no servidor; recarrega para sumir as
    // linhas correlacionadas (evita superexposição)
    setTimeout(() => refreshOpps(), 600);
  } catch (e) {
    btn.disabled = false;
    alert("Erro ao registrar: " + e.message);
  }
}

/* ------------------------------------------------------------------ bets */
function statCard(label, value, cls = "") {
  return `<div class="cardstat"><div class="v ${cls}">${value}</div><div class="l">${label}</div></div>`;
}

async function refreshBets() {
  const [sum, bets] = await Promise.all([jget("/summary"), jget("/bets")]);
  const s = sum;
  $("summaryCards").innerHTML =
    statCard("ROI", fmtPct(s.roi_pct), s.roi_pct >= 0 ? "pos" : "neg") +
    statCard("Lucro", `${fmtBRL(s.total_profit)}`, s.total_profit >= 0 ? "pos" : "neg") +
    statCard("Unidades", `${s.profit_units > 0 ? "+" : ""}${s.profit_units}u`, s.profit_units >= 0 ? "pos" : "neg") +
    statCard("CLV médio", s.avg_clv_pct == null ? "—" : fmtPct(s.avg_clv_pct),
             (s.avg_clv_pct || 0) >= 0 ? "pos" : "neg") +
    statCard("CLV positivo", s.clv_positive_pct == null ? "—" : `${s.clv_positive_pct}%`) +
    statCard("Apostas", `${s.settled}/${s.total_bets}`) +
    statCard("Acerto", `${s.win_rate}%`) +
    statCard("Edge médio", s.avg_edge_pct == null ? "—" : `${s.avg_edge_pct}%`) +
    `<div class="cardstat"><a class="act link" href="/bets.csv" download
       style="display:inline-block">⬇ Exportar CSV</a>
       <div class="l">para planilhar</div></div>`;

  let rows = bets.rows;
  const fSport = $("bSport") ? $("bSport").value : "";
  const fTab = $("bTab") ? $("bTab").value : "";
  if (fSport) rows = rows.filter((r) => r.sport === fSport);
  if (fTab) rows = rows.filter((r) => r.source_tab === fTab);
  $("betEmpty").hidden = rows.length > 0;

  // agrupa pela DATA DO JOGO (event_date), não pela data de registro
  const dayKey = (iso) => {
    if (!iso) return null;
    const d = new Date(iso);
    if (isNaN(d)) return null;
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  };
  const hojeKey = dayKey(new Date().toISOString());
  const grupos = new Map();
  for (const b of rows) {
    const k = dayKey(b.event_date) || "sem-data";
    if (!grupos.has(k)) grupos.set(k, []);
    grupos.get(k).push(b);
  }
  const chaves = [...grupos.keys()].sort((a, b) => {
    if (a === "sem-data") return 1;
    if (b === "sem-data") return -1;
    return a < b ? 1 : -1;                 // dia do jogo: mais recente primeiro
  });

  const RES_LABEL = { win: "win", loss: "loss", push: "push", half_win: "½ win", half_loss: "½ loss" };
  const betRow = (b, ehHoje) => {
    const rPos = b.result === "win" || b.result === "half_win";
    const rNeg = b.result === "loss" || b.result === "half_loss";
    const res = b.settled
      ? `<b class="${rPos ? "pos" : rNeg ? "neg" : ""}">${RES_LABEL[b.result] || b.result}</b>`
      : `<span class="ev-sub">pendente</span>`;
    const profit = b.profit == null ? "—"
      : `<b class="${b.profit >= 0 ? "pos" : "neg"}">${fmtBRL(b.profit)}</b>`;
    // detecta DUPLA: legs_json (com odds) OU seleção "A | B" (duplas antigas)
    let legs = null;
    if (b.legs_json) { try { legs = JSON.parse(b.legs_json); } catch (_) {} }
    if (!legs && (b.market || "").startsWith("Dupla") && (b.selection || "").includes(" | ")) {
      legs = b.selection.split(" | ").map((s) => ({ label: s.trim(), odd: "" }));
    }
    let acts = "";
    if (!b.settled && legs && legs.length) {
      // DUPLA: liquida POR PERNA. A odd vem pré-preenchida quando temos; nas
      // duplas antigas (sem dados) você digita a odd de cada perna.
      const opt = (v, t) => `<option value="${v}">${t}</option>`;
      acts = `<div class="parlay-settle" data-id="${b.id}">
        ${legs.map((l) => `<div class="pl-leg">
          <span class="ev-sub">${esc(l.label || "perna")}</span>
          <input type="number" step="0.01" min="1.01" class="pl-odd" placeholder="odd"
            value="${l.odd != null && l.odd !== "" ? l.odd : ""}" title="odd desta perna">
          <select class="pl-res">
            ${opt("win", "W")}${opt("half_win", "½W")}${opt("push", "P")}${opt("half_loss", "½L")}${opt("loss", "L")}
          </select></div>`).join("")}
        <button class="act win pl-confirm" type="button">Liquidar dupla</button></div>`;
    } else if (!b.settled) {
      // W / ½W / P / ½L / L — meio-ganho e meia-perda para linhas asiáticas de quarto
      acts = `
        <button class="act win" data-id="${b.id}" data-r="win" title="Ganhou">W</button>
        <button class="act win" data-id="${b.id}" data-r="half_win" title="Meio-ganho (asiático)">½W</button>
        <button class="act push" data-id="${b.id}" data-r="push" title="Devolvida (push)">P</button>
        <button class="act loss" data-id="${b.id}" data-r="half_loss" title="Meia-perda (asiático)">½L</button>
        <button class="act loss" data-id="${b.id}" data-r="loss" title="Perdeu">L</button>`;
    } else {
      // já liquidada: reabrir para corrigir (ex.: marcou errado)
      acts = `<button class="act push reopen-bet" data-id="${b.id}" type="button"
        title="reabrir esta aposta para corrigir o resultado">↺ reabrir</button>`;
    }
    // "~" = CLV aproximado (fechamento capturado tarde — servidor offline no kickoff?)
    const clv = b.clv_pct == null ? "—"
      : `<b class="${b.clv_pct >= 0 ? "pos" : "neg"}"${b.clv_stale ? ' title="CLV aproximado — o fechamento não foi capturado bem no início do jogo (servidor offline?)"' : ""}>${b.clv_stale ? "~" : ""}${fmtPct(b.clv_pct)}</b>`;
    return `<tr class="${ehHoje ? "bet-today" : ""}">
      <td>${b.id}</td>
      <td>${new Date(b.ts_placed).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</td>
      <td>${fmtKickoff(b.event_date)}</td>
      <td><div class="ev-name">${esc(b.event)}</div><div class="ev-sub">${esc(b.league || b.sport || "")}</div></td>
      <td><div>${esc(b.player ? `${b.player} — ${b.market}` : b.market)}${b.hdp != null ? " " + b.hdp : ""}</div>
          <div class="ev-sub mkt-side">${esc(b.selection)}</div></td>
      <td>${esc(b.book)}</td>
      <td>${esc(b.source_tab || "—")}</td>
      <td class="odd-cell">${fmtOdd(b.odd_taken)}</td>
      <td>${fmtPct(b.edge_pct)}</td>
      <td class="stake">${b.stake_units}u <small>(${fmtBRL(b.stake_amount)})</small></td>
      <td>${clv}</td>
      <td>${res}</td>
      <td>${profit}</td>
      <td>${acts}</td>
    </tr>`;
  };

  let html = "";
  for (const k of chaves) {
    const lista = grupos.get(k).sort((a, b) => b.id - a.id);
    const ehHoje = k === hojeKey;
    let label;
    if (k === "sem-data") label = "Sem data do jogo";
    else label = new Date(lista[0].event_date)
      .toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" });
    html += `<tr class="bet-group${ehHoje ? " today" : ""}"><td colspan="13">` +
      `${ehHoje ? "🔴 HOJE · " : "📅 "}${esc(label)} <span class="ev-sub">(${lista.length})</span></td></tr>`;
    for (const b of lista) html += betRow(b, ehHoje);
  }
  $("betBody").innerHTML = html;

  // singles: botões com data-r
  document.querySelectorAll("#betBody .act[data-r]").forEach((b) =>
    b.addEventListener("click", async () => {
      await jpost("/settle", { bet_id: Number(b.dataset.id), result: b.dataset.r });
      refreshBets();
    }));
  // duplas: lê odd + resultado de cada perna e liquida por perna
  document.querySelectorAll("#betBody .pl-confirm").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const box = btn.closest(".parlay-settle");
      const legs = [...box.querySelectorAll(".pl-leg")].map((el) => ({
        odd: parseFloat((el.querySelector(".pl-odd").value || "").replace(",", ".")),
        result: el.querySelector(".pl-res").value,
      }));
      try {
        await jpost("/settle_parlay", { bet_id: Number(box.dataset.id), legs });
        refreshBets();
      } catch (e) { alert("Erro ao liquidar dupla: " + e.message); }
    }));
  // reabrir aposta liquidada (corrigir resultado errado)
  document.querySelectorAll("#betBody .reopen-bet").forEach((btn) =>
    btn.addEventListener("click", async () => {
      try {
        await jpost("/reopen_bet", { bet_id: Number(btn.dataset.id) });
        refreshBets();
      } catch (e) { alert("Erro ao reabrir: " + e.message); }
    }));
}

/* ---------------------------------------------------------------- boosts */
function renderBoostResult(el, r) {
  const cls = r.edge_pct > 0 ? "pos" : "neg";
  el.innerHTML = `
    <div class="big ${cls}">${r.is_value ? "✅ VALUE" : "❌ sem valor"} — edge ${fmtPct(r.edge_pct)}</div>
    <div>Fair odd: <b>${fmtOdd(r.fair_odd)}</b> · Boost: <b>${fmtOdd(r.boost_odd)}</b></div>
    <div>Stake sugerida: <b>${r.stake_units}u (${fmtBRL(r.stake_amount)})</b>${r.method ? ` · método: ${r.method}` : ""}</div>`;
}

async function refreshBoosts() {
  let data;
  try { data = await jget("/boosts"); } catch (_) { return; }
  const all = [...(data.simple || []), ...(data.combined || [])]
    .sort((a, b) => (b.captured_at || "").localeCompare(a.captured_at || ""));
  $("boostEmpty").hidden = all.length > 0;
  $("boostBody").innerHTML = all.map((b) => {
    const delta = b.odd_old && b.odd_new
      ? (((b.odd_new / b.odd_old) - 1) * 100).toFixed(1) + "%" : "—";
    return `<tr>
      <td>${b.captured_at ? new Date(b.captured_at).toLocaleString("pt-BR") : "—"}</td>
      <td>${esc(b.source)}</td>
      <td>${esc(b.title || (b.raw && (b.raw.title || b.raw.name)) || "—")}</td>
      <td class="fair-cell">${fmtOdd(b.odd_old)}</td>
      <td class="odd-cell">${fmtOdd(b.odd_new)}</td>
      <td class="pos">${delta}</td>
    </tr>`;
  }).join("");
}

$("bsCalc").addEventListener("click", async () => {
  try {
    const r = await jpost("/api/boost_eval", {
      boost_odd: parseFloat($("bsBoost").value),
      ref_side: parseFloat($("bsSide").value),
      ref_opposite: parseFloat($("bsOpp").value),
    });
    renderBoostResult($("bsResult"), r);
  } catch (e) { $("bsResult").innerHTML = `<span class="neg">${esc(e.message)}</span>`; }
});

$("bcCalc").addEventListener("click", async () => {
  try {
    const legsTxt = $("bcLegs").value.trim();
    const body = { boost_odd: parseFloat($("bcBoost").value) };
    if (legsTxt) {
      body.legs = legsTxt.split("\n").map((l) => l.split(",").map((x) => parseFloat(x.trim())));
    } else {
      body.ref_parlay_odd = parseFloat($("bcRef").value);
    }
    const r = await jpost("/api/boost_eval", body);
    renderBoostResult($("bcResult"), r);
  } catch (e) { $("bcResult").innerHTML = `<span class="neg">${esc(e.message)}</span>`; }
});

/* ------------------------------------ melhores linhas (abaixo do corte) */
async function refreshNearMisses(show) {
  const card = $("nearCard");
  if (!show) { card.hidden = true; return; }
  let data;
  try { data = await jget("/api/near_misses"); } catch (_) { return; }
  if (!data.rows.length) { card.hidden = true; return; }
  card.hidden = false;
  $("nearBody").innerHTML = data.rows.map((r) => {
    const ln = r.line == null ? "" : ` ${r.line}`;
    const cls = r.edge_pct >= 0 ? "pos" : "";
    return `<tr>
      <td>${fmtKickoff(r.event_date)}</td>
      <td><div class="ev-name">${esc(r.event)}</div><div class="ev-sub">${esc(r.league)}</div></td>
      <td>${esc(r.market)}${ln} <span class="mkt-side">${esc(r.side)}</span></td>
      <td>${esc(r.book)}</td>
      <td class="odd-cell">${fmtOdd(r.offered_odd, r.book)}</td>
      <td class="fair-cell">${fmtOdd(r.fair_odd)}</td>
      <td class="${cls}"><b>${fmtPct(r.edge_pct)}</b></td>
      <td class="limit">≥${r.min_edge_required}%</td>
    </tr>`;
  }).join("");
}

/* ------------------------------------------------- sharp (fair odds nossas) */
async function refreshSharp() {
  const q = new URLSearchParams({
    sport: $("sSport").value,
    market: $("sMarket").value,
    search: $("sSearch").value.trim(),
    source: "pinnacle",     // aba Sharp = game lines da Pinnacle
    props: 0,
  });
  let data;
  try { data = await jget(`/api/fair_lines?${q}`); } catch (_) { return; }

  const st = data.stats || {};
  $("sharpCards").innerHTML =
    statCard("Linhas justas", st.lines ?? 0) +
    statCard("Eventos", st.events ?? 0) +
    statCard("Limite médio", st.avg_limit ? `€${Math.round(st.avg_limit)}` : "—") +
    statCard("Futebol", st.by_sport?.Soccer ?? 0) +
    statCard("Basquete", st.by_sport?.Basketball ?? 0) +
    statCard("Custo/mês", "R$ 0", "pos");

  $("sharpCount").textContent = `${data.count} mercados`;
  $("sharpEmpty").hidden = data.count > 0;
  $("sharpBody").innerHTML = data.rows.map((g) => {
    const sides = Object.entries(g.sides).map(([side, v]) => {
      // no spread cada lado tem a sua própria linha (home -1.5 / away +1.5)
      const ln = (g.market.includes("Spread") && v.line != null)
        ? ` <small class="ev-sub">${v.line > 0 ? "+" : ""}${v.line}</small>` : "";
      return `<div><span class="mkt-side">${esc(side)}</span>${ln}
         <span class="fair-cell">${fmtOdd(v.raw_odd)}</span> →
         <b class="odd-cell">${fmtOdd(v.fair_odd)}</b>
         <small class="ev-sub">(${(v.fair_prob * 100).toFixed(1)}%)</small></div>`;
    }).join("");
    const ehSpread = g.market.includes("Spread");
    const lineLabel = ehSpread
      ? (g.line == null ? "—" : `±${Math.abs(g.line)}`)
      : (g.line == null ? "—" : g.line);
    return `<tr>
      <td>${fmtKickoff(g.event_date)}</td>
      <td><div class="ev-name">${esc(g.event_home)} × ${esc(g.event_away)}</div>
          <div class="ev-sub">${esc(g.league)}</div></td>
      <td>${esc(g.market)}</td>
      <td>${lineLabel}</td>
      <td>${sides}</td>
      <td class="limit">${g.max_limit ? "€" + Math.round(g.max_limit) : "—"}</td>
    </tr>`;
  }).join("");
}

["sSport", "sMarket", "sSearch"].forEach((id) =>
  $(id).addEventListener("input", () => refreshSharp()));

/* ---------------------------------------- odds extraídas (extensão, cruas) */
async function refreshExtracted() {
  const q = new URLSearchParams({ source: $("exSource").value,
    search: $("exSearch").value.trim() });
  let data;
  try { data = await jget(`/api/extracted_odds?${q}`); } catch (_) { return; }
  $("exCount").textContent = `${data.count} eventos capturados`;
  $("exEmpty").hidden = data.count > 0;
  $("exBody").innerHTML = data.events.map((ev) => {
    const mkts = Object.entries(ev.markets).map(([nome, sels]) => {
      const cells = sels.map((s) =>
        `<span style="display:inline-block;margin:2px 8px 2px 0">
           ${esc(s.selection)}${s.line != null ? " " + s.line : ""}
           <b class="odd-cell">${fmtOdd(s.odd)}</b></span>`).join("");
      return `<div style="margin:4px 0"><span class="ev-sub">${esc(nome)}:</span> ${cells}</div>`;
    }).join("");
    return `<div class="card">
      <h2 style="font-size:14px">${esc(ev.event || "—")}
        <span class="badge mid" style="font-size:11px">${esc(ev.source)}</span>
        <small class="ev-sub">${ev.ts ? "há " + Math.round((Date.now() - new Date(ev.ts)) / 1000) + "s" : ""}</small></h2>
      ${mkts}
      ${ev.url ? `<a class="act link" href="${esc(ev.url)}" target="_blank" rel="noopener">Abrir na casa</a>` : ""}
    </div>`;
  }).join("");
}
["exSource", "exSearch"].forEach((id) =>
  $(id).addEventListener("input", () => refreshExtracted()));

/* ------------------------------------------------- sharp props (FanDuel) */
async function refreshSharpProps() {
  const q = new URLSearchParams({ source: "fanduel", props: 1,
    search: $("spSearch").value.trim() });
  let data;
  try { data = await jget(`/api/fair_lines?${q}`); } catch (_) { return; }
  const st = data.stats || {};
  $("spCards").innerHTML =
    statCard("Props (linhas)", (st.by_sport ? "" : "") + (data.count * 2)) +
    statCard("Jogos", data.count) +
    statCard("Fonte", "FanDuel", "pos") +
    statCard("Custo/mês", "R$ 0", "pos");
  $("spCount").textContent = `${data.count} props`;
  $("spEmpty").hidden = data.count > 0;
  $("spBody").innerHTML = data.rows.map((g) => {
    const sides = Object.entries(g.sides).map(([side, v]) =>
      `<div><span class="mkt-side">${esc(side)}</span>
         <span class="fair-cell">${fmtOdd(v.raw_odd)}</span> →
         <b class="odd-cell">${fmtOdd(v.fair_odd)}</b>
         <small class="ev-sub">(${(v.fair_prob * 100).toFixed(1)}%)</small></div>`).join("");
    return `<tr>
      <td>${fmtKickoff(g.event_date)}</td>
      <td><div class="ev-sub">${esc(g.event_home)} × ${esc(g.event_away)}</div>
          <div class="ev-sub">${esc(g.league)}</div></td>
      <td class="ev-name">${esc(g.player || "—")}</td>
      <td>${esc((g.market || "").replace("Prop: ", ""))}</td>
      <td>${g.line == null ? "—" : g.line}</td>
      <td>${sides}</td>
    </tr>`;
  }).join("");
}
$("spSearch").addEventListener("input", () => refreshSharpProps());

/* ------------------------------------------------------------------ tabs */
document.querySelectorAll(".tab").forEach((t) =>
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    state.tab = t.dataset.tab;
    const opps = ["value", "esports", "props", "other"].includes(state.tab);
    $("pane-opps").hidden = !opps;
    $("filtersBar").style.display = opps ? "" : "none";
    $("pane-boosts").hidden = state.tab !== "boosts";
    $("pane-sharp").hidden = state.tab !== "sharp";
    $("pane-sharpprops").hidden = state.tab !== "sharpprops";
    $("pane-extracted").hidden = state.tab !== "extracted";
    $("pane-bets").hidden = state.tab !== "bets";
    if (opps) { state.firstLoad = true; refreshOpps(); }
    if (state.tab === "boosts") refreshBoosts();
    if (state.tab === "sharp") refreshSharp();
    if (state.tab === "sharpprops") refreshSharpProps();
    if (state.tab === "extracted") refreshExtracted();
    if (state.tab === "bets") refreshBets();
  }));

["fMinEdge", "fMinLimit", "fSport", "fBook", "fSearch", "fSuspicious"].forEach((id) => {
  if ($(id)) {
    const ev = (id === "fSearch" || id === "fMinEdge") ? "input" : "change";
    $(id).addEventListener(ev, () => refreshOpps());
  }
});
if ($("bSport")) $("bSport").addEventListener("change", () => refreshBets());
if ($("bTab")) $("bTab").addEventListener("change", () => refreshBets());

/* -------------------------------------------- Bet365 via The-Odds-API (sob demanda) */
async function initB365Pull() {
  const sel = $("b365Sport"), msg = $("b365PullMsg"), card = $("b365PullCard");
  if (!sel) return;
  let data;
  try { data = await jget("/api/theoddsapi_sports"); } catch (_) { return; }
  if (!data.configured) {
    if (card) card.style.opacity = "0.6";
    msg.innerHTML = `<span class="neg">sem chave — ponha THE_ODDS_API_KEY no .env</span>`;
    return;
  }
  const sports = data.sports || [];
  sel.innerHTML = `<option value="">— escolher esporte —</option>` +
    sports.map((s) => `<option value="${esc(s.key)}">${esc(s.group ? s.group + " · " : "")}${esc(s.title)}</option>`).join("");
  msg.innerHTML = data.error
    ? `<span class="neg">${esc(data.error)}</span>`
    : `${sports.length} esportes` + (data.remaining != null ? ` · <b>${esc(data.remaining)}</b> créditos restantes` : "");
}

if ($("b365Pull")) $("b365Pull").addEventListener("click", async () => {
  const btn = $("b365Pull"), sel = $("b365Sport"), msg = $("b365PullMsg");
  const sport_key = sel.value;
  if (!sport_key) { msg.innerHTML = `<span class="neg">escolha um esporte primeiro</span>`; return; }
  btn.disabled = true; msg.textContent = "puxando (1 crédito por mercado)…";
  try {
    const r = await jpost("/api/pull_bet365", { sport_key });
    let hint = "";
    if (r.com_book === 0) {
      if (r.raw_events > 0) {
        const casas = (r.available_books || []).join(", ") || "(nenhuma)";
        hint = ` <span class="neg">— "${esc(r.book)}" NÃO existe na the-odds-api. ${r.raw_events} jogos com estas casas: ${esc(casas)}. Ajuste THE_ODDS_API_BOOKMAKER para uma delas.</span>`;
      } else {
        hint = ` <span class="ev-sub">— sem jogos nessa liga agora</span>`;
      }
    } else if (r.casados === 0) {
      hint = ` <span class="ev-sub">— ${r.com_book} jogos com "${esc(r.book)}", mas nenhum casou com a Pinnacle</span>`;
    }
    msg.innerHTML = `✅ ${r.com_book} jogos c/ ${esc(r.book)} · ${r.casados} casados · <b class="pos">${r.novas} novas</b>` +
      (r.remaining != null ? ` · <b>${esc(r.remaining)}</b> créditos` : "") + hint;
    if (["value", "props", "other"].includes(state.tab)) refreshOpps();
  } catch (e) { msg.innerHTML = `<span class="neg">erro: ${esc(e.message)}</span>`; }
  finally { btn.disabled = false; }
});

/* ------------------------------------------------------------------ loop */
refreshStatus();
refreshOpps();
initB365Pull();
setInterval(refreshStatus, 10_000);
setInterval(() => {
  // enquanto uma expansão está aberta, NÃO recarrega a lista (senão fecha a
  // expansão e trava você no meio de marcar os boxes)
  if (!state.frozen && ["value", "props", "other"].includes(state.tab)) refreshOpps();
  if (state.tab === "boosts") refreshBoosts();
  if (state.tab === "sharp") refreshSharp();
  if (state.tab === "sharpprops") refreshSharpProps();
  if (state.tab === "extracted") refreshExtracted();
}, 10_000);
