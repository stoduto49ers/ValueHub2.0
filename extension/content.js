// content.js — roda dentro das páginas da Betano e lê os boosts (smart-picks).
//
// Estratégia: a Betano usa data-qa="smart-picks-card" para todo card de
// "escolha". Um card é um BOOST de verdade só se tiver odd riscada
// (tw-line-through = odd original) + odd destacada (tw-text-highlight = nova).
// Sem odd riscada, é só uma sugestão "popular", que ignoramos.
//
// O card pode ter 1 perna (boost simples -> value calculável) ou várias
// (combinado -> comparar mesma combinada em outra casa).

(function () {
  "use strict";

  const SEEN = new Set();        // evita reenviar o mesmo boost
  const HUB_URL = "http://localhost:8000/boost";  // endpoint do seu servidor

  function parseOdd(text) {
    if (!text) return null;
    const v = parseFloat(text.replace(",", ".").trim());
    return isNaN(v) ? null : v;
  }

  function extractCard(card) {
    // odds: riscada (antiga) e destacada (nova)
    const oldEl = card.querySelector(".tw-line-through");
    const newEl = card.querySelector(".tw-text-sem-color-text-highlight");
    const oddOld = oldEl ? parseOdd(oldEl.textContent) : null;
    const oddNew = newEl ? parseOdd(newEl.textContent) : null;

    // só é boost se tem aumento real (odd riscada presente e nova > antiga)
    if (oddOld === null || oddNew === null || oddNew <= oddOld) return null;

    // pernas do boost (cada <li data-qa="smart-picks-leg">)
    const legEls = card.querySelectorAll('[data-qa="smart-picks-leg"]');
    const legs = [];
    legEls.forEach((li) => {
      const txt = li.innerText.replace(/\s+/g, " ").trim();
      if (txt) legs.push(txt);
    });

    // se não achou pernas via leg, tenta a Super Odd simples (texto do card)
    if (legs.length === 0) {
      const title = card.querySelector('[data-qa="smart-picks-banner-title"]');
      const main = card.querySelector("main");
      if (main) legs.push(main.innerText.replace(/\s+/g, " ").trim());
    }

    // evento (times), quando houver
    const partEl = card.querySelector('[data-qa="smart-picks-event-participants"]');
    const event = partEl ? partEl.innerText.replace(/\s+/g, " ").trim() : "";

    // data/hora, quando houver
    const dateEl = card.querySelector('[data-qa="smart-picks-event-date"]');
    const date = dateEl ? dateEl.textContent.trim() : "";

    // banner (ex.: "20% SUPER TURBINADA")
    const bannerEl = card.querySelector('[data-qa="smart-picks-banner-title"]');
    const banner = bannerEl ? bannerEl.textContent.trim() : "";

    // ids das seleções (para casar combinada idêntica em outra casa)
    const selEl = card.querySelector("[data-selnid]");
    const selnid = selEl ? selEl.getAttribute("data-selnid") : "";

    const boostPct = oddOld > 0 ? ((oddNew / oddOld - 1) * 100) : null;

    return {
      event,
      date,
      banner,
      legs,
      is_simple: legs.length === 1,
      odd_old: oddOld,
      odd_new: oddNew,
      boost_pct: boostPct ? Math.round(boostPct * 10) / 10 : null,
      selnid,
      captured_at: new Date().toISOString(),
      source: "betano",
    };
  }

  function dedupeKey(b) {
    return `${b.selnid}|${b.odd_new}`;
  }

  function scan() {
    const cards = document.querySelectorAll('[data-qa^="smart-picks-card"]');
    const found = [];
    cards.forEach((card) => {
      const b = extractCard(card);
      if (!b) return;
      const key = dedupeKey(b);
      if (SEEN.has(key)) return;
      SEEN.add(key);
      found.push(b);
    });
    if (found.length) {
      send(found);
      badge(found.length);
    }
  }

  function send(boosts) {
    // envia para o service worker (background.js), que faz o fetch ao servidor.
    // Isso evita o bloqueio de "conteúdo misto" (página HTTPS -> localhost HTTP).
    try {
      chrome.runtime.sendMessage({ action: "send_boosts", boosts }, (resp) => {
        if (chrome.runtime.lastError) {
          // service worker pode estar dormindo; ignora, próximo scan tenta de novo
          return;
        }
      });
    } catch (e) {
      // contexto da extensão invalidado (recarregada): ignora
    }
  }

  let lastCount = 0;
  function badge(n) {
    lastCount += n;
    chrome.storage.local.set({ lastCount });
  }

  // escaneia ao carregar e observa mudanças (boosts carregam dinamicamente)
  const observer = new MutationObserver(() => {
    clearTimeout(window.__vh_t);
    window.__vh_t = setTimeout(() => { scan(); autoCaptureOdds(); }, 900);
  });
  observer.observe(document.body, { childList: true, subtree: true });

  // primeiro scan
  setTimeout(scan, 1500);

  // re-escaneia periodicamente (cobre navegação interna do SPA da Betano)
  setInterval(scan, 5000);

  // ============================================================
  // EXTRATOR DE ODDS — multi-casa (Betano + Bet365)
  // Betano: blocos [data-marketid]; mercados normais e tabelas de jogadores.
  // Bet365: pods .gl-MarketGroupPod com colunas .gl-Market.
  // ============================================================
  const ARIA_RE = /Bet on (.+) with odds (\d+(?:\.\d+)?)/i;
  // linha do mercado. Precisa cobrir o handicap ASIÁTICO, que usa quartos
  // (-0.25, +0.75) e a linha zero (0.0) — não só os .5 do handicap europeu.
  // Ex.: "Atlético-MG -0.25" -> -0.25 ; "Mais de 2.5" -> 2.5 ; "Bahia 0.0" -> 0
  const LINE_RE = /([-+]?\d+(?:\.\d+)?)\s*$/;
  const PLUS_RE = /(\d+)\+/;

  function detectSource() {
    return location.hostname.includes("bet365") ? "bet365" : "betano";
  }

  function eventDateTime() {
    // texto de data/hora visível na página; formatos variados por casa.
    // Betano: "3 Jul 19:00" / "Domingo, 28 Junho 2026 20:00"
    // Bet365: "3 Jul 19:00" no cabeçalho do evento
    const cands = [
      ".mrb-0f39a5",                       // bet365 header time
      '[data-qa="event-date"]',            // betano
      '[class*="event-date"]',
      ".sph-ScoreHeader_TimeStamp, .sph-EventHeader_Time",
    ];
    for (const sel of cands) {
      const el = document.querySelector(sel);
      if (el && el.textContent.trim()) return el.textContent.trim().replace(/\s+/g, " ");
    }
    // fallback: procura um padrão de data no texto do topo
    const head = document.body.innerText.slice(0, 2000);
    const m = head.match(/\d{1,2}[\/. ][A-Za-zç]{3,}[\.\/ ]?\d{0,4}\s*\d{1,2}:\d{2}/);
    return m ? m[0].replace(/\s+/g, " ") : "";
  }

  function eventName() {
    if (detectSource() === "betano") {
      const m = location.pathname.match(/\/odds\/([^/]+)\//);
      return m ? m[1].replace(/-/g, " ") : document.title;
    }
    // bet365: times do cabeçalho do evento (classes hasheadas mudam por deploy;
    // por isso há fallbacks em cadeia)
    const teams = document.querySelectorAll(".mrb-c101f4");
    if (teams.length >= 2) {
      return `${teams[0].textContent.trim()} v ${teams[1].textContent.trim()}`;
    }
    const el = document.querySelector(".sph-EventHeader, .sph-FixturePodHeader, .rcl-MarketHeaderLabel");
    if (el && el.textContent.trim()) return el.textContent.trim();
    return document.title.replace(/\s*[-|–].*$/, "").trim();
  }

  // ============================================================
  // AUTO-CAPTURA CONTÍNUA DE ODDS (Betano E Bet365, sempre ativa)
  // Qualquer mercado que APARECER na tela é capturado e enviado —
  // inclusive ao clicar nas abas internas (Escanteios, Cartões...).
  // Dedupe por mercado+seleção+odd: só envia o que é novo. Ao trocar
  // de jogo, o dedupe zera e recomeça.
  // ============================================================
  const ODDS_SENT = new Set();
  let lastOddsEventKey = "";
  let ultimoJogoExpandido = "";   // evita reexpandir o mesmo jogo a cada ciclo

  function markSnapshotSent(snap) {
    snap.markets.forEach((m) => m.selections.forEach((s) => {
      ODDS_SENT.add(m.market + "|" + s.sel + "|" + s.odd);
    }));
  }

  // envia um jogo do cupom da liga da Bet365 como snapshot próprio (cada jogo
  // tem evento próprio; times separados por " v " / duas linhas de nome)
  function sendBet365CouponGame(g) {
    let home = "", away = "";
    const partes = (g.event || "").split(/\s+v\s+|\s+vs\s+|\n/).map((s) => s.trim()).filter(Boolean);
    if (partes.length >= 2) { home = partes[0]; away = partes[1]; }
    const snap = {
      // event_datetime fica VAZIO de propósito: a data do cupom é só o DIA (sem
      // hora real), e o casador (match_event) usa 'start' numa janela apertada —
      // passar meio-dia como se fosse o horário do jogo faria o casamento com a
      // Pinnacle (que tem a hora exata) falhar. O dia do jogo já é registrado na
      // aposta pela data da Pinnacle. Guardamos o dia só p/ diagnóstico.
      source: "bet365", event: g.event, event_datetime: "", event_day: g.date || "",
      event_id: "",
      home, away, url: location.href, captured_at: new Date().toISOString(),
      markets: [{ market: g.market, market_id: "", selections: g.selections }],
    };
    try { chrome.runtime.sendMessage({ action: "send_odds", snapshot: snap }); } catch (e) {}
  }

  async function autoCaptureOdds() {
    // antes de capturar, abre os mercados profundos (Handicap Asiático & cia).
    // Só age em página de jogo da Betano e só uma vez por jogo.
    if (detectSource() === "betano" && betanoEventId() &&
        betanoEventId() !== ultimoJogoExpandido) {
      ultimoJogoExpandido = betanoEventId();
      try { await abrirMercadosProfundos(); } catch (e) { /* segue com o que houver */ }
    }
    // Bet365 na LISTA da liga (não dentro de um jogo): lê o cupom e manda cada
    // jogo. A decisão é por DOM (b365EstaNumJogo) — a URL não é confiável.
    if (detectSource() === "bet365" && !b365EstaNumJogo()) {
      const jogos = extractBet365LeagueCoupon();
      if (jogos.length) {
        jogos.forEach(sendBet365CouponGame);
        chrome.storage.local.set({ lastAuto: { event: `cupom: ${jogos.length} jogos`, markets: jogos.length, ts: Date.now() } });
        return;
      }
    }
    const snap = extractAllMarkets();
    if (!snap.markets.length) return;
    const evKey = snap.source + "|" + snap.event;
    if (evKey !== lastOddsEventKey) {
      ODDS_SENT.clear();
      lastOddsEventKey = evKey;
    }
    const fresh = [];
    snap.markets.forEach((m) => {
      const novos = m.selections.filter((s) => {
        const k = m.market + "|" + s.sel + "|" + s.odd;
        if (ODDS_SENT.has(k)) return false;
        ODDS_SENT.add(k);
        return true;
      });
      if (novos.length) fresh.push({ market: m.market, market_id: m.market_id, selections: novos });
    });
    if (!fresh.length) return;
    try {
      chrome.runtime.sendMessage({ action: "send_odds", snapshot: { ...snap, markets: fresh } });
      chrome.storage.local.set({ lastAuto: { event: snap.event, markets: fresh.length, ts: Date.now() } });
    } catch (e) { /* contexto invalidado */ }
  }

  setInterval(autoCaptureOdds, 4000);
  window.addEventListener("hashchange", () => setTimeout(autoCaptureOdds, 2500));

  // PAUSA da varredura da Bet365 (persistida): quando ligada, o auto-start não
  // dispara e a varredura ativa para. Serve para você navegar/testar sem a
  // extensão ficar trocando de jogo sozinha.
  let b365Paused = false;
  try {
    chrome.storage.local.get(["b365Paused"], (r) => { b365Paused = !!r.b365Paused; });
    chrome.storage.onChanged.addListener((ch) => {
      if (ch.b365Paused) b365Paused = !!ch.b365Paused.newValue;
    });
  } catch (e) { /* fora do contexto de extensão */ }

  // AUTO-START da varredura da Bet365: quando a extensão detecta a LISTA de
  // uma liga (vários jogos na tela, sem estar dentro de um jogo), começa a
  // varrer sozinha — sem você precisar clicar em "Varrer".
  let b365AutoTried = "";
  setInterval(() => {
    if (b365Paused) return;                     // pausado: não inicia sozinho
    if (detectSource() !== "bet365") return;
    if (typeof b365Crawl !== "undefined" && b365Crawl.active) return;
    // só na lista: há vários jogos e NÃO estamos dentro de um jogo (sem pods)
    const naLista = document.querySelectorAll(".gl-MarketGroupPod").length === 0;
    const fixtures = b365Fixtures();
    const chave = location.href + "|" + fixtures.length;
    if (naLista && fixtures.length >= 2 && chave !== b365AutoTried) {
      b365AutoTried = chave;
      try { b365CrawlStart(); } catch (e) { /* ignora */ }
    }
  }, 6000);

  // ============================================================
  // ABERTURA AUTOMÁTICA DE MERCADOS PROFUNDOS (Betano)
  //
  // A carga inicial da Betano traz ~19 dos ~950 mercados. Clicar na aba
  // "Todos" ([data-qa="all"]) carrega TODOS os blocos — mas eles vêm
  // RECOLHIDOS (altura ~20px, zero seleções). Clicar no cabeçalho
  // ([data-qa^="market-type-id"]) expande e renderiza as odds.
  //
  // Expandir os 512 blocos levaria ~1 min e travaria a página, então só
  // abrimos os mercados que interessam — com destaque para o Handicap
  // Asiático, que é o equivalente ao 'spread' da Pinnacle e é justamente
  // o que a API pública NÃO entrega.
  // ============================================================
  const MERCADOS_ALVO = [
    /^Resultado Final/i,
    /^Total de Gols/i,
    /Handicap Asi[áa]tico/i,
    /Asi[áa]tico \(Mais\/Menos\)/i,
    /^Escanteios/i,
    /Total de Cart[õo]es/i,
    /^Resultado do 1|1[°º] Tempo/i,
  ];

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function ehAlvo(nome) {
    return MERCADOS_ALVO.some((re) => re.test(nome));
  }

  let expandindo = false;

  async function abrirMercadosProfundos() {
    if (expandindo || detectSource() !== "betano") return 0;
    if (!/\/odds\/[^/]+\/\d+/.test(location.pathname)) return 0;  // só página de jogo
    expandindo = true;
    try {
      // 1) aba "Todos" carrega a lista completa de blocos
      const abaTodos = document.querySelector('[data-qa="all"]');
      if (abaTodos) {
        abaTodos.click();
        await sleep(1400);
      }
      // 2) expande só os blocos de interesse que ainda estão recolhidos
      let abertos = 0;
      const blocos = Array.from(document.querySelectorAll("[data-marketid]"));
      for (const bloco of blocos) {
        const head = bloco.querySelector('[data-qa^="market-type-id"]');
        if (!head) continue;
        const nome = head.textContent.trim();
        if (!ehAlvo(nome)) continue;
        if (bloco.querySelector('[data-qa="event-selection"]')) continue; // já aberto
        head.click();
        abertos++;
        await sleep(150);
      }
      if (abertos) await sleep(700);
      return abertos;
    } finally {
      expandindo = false;
    }
  }

  // ID do evento pela URL (/odds/slug/86631407/). O servidor usa esse id na
  // API da Betano para obter times e horário — mais confiável que ler do DOM.
  function betanoEventId() {
    const m = location.pathname.match(/\/odds\/[^/]+\/(\d+)/);
    return m ? m[1] : "";
  }

  function extractBetanoMarkets() {
    const markets = [];
    document.querySelectorAll("[data-marketid]").forEach((block) => {
      const headerEl = block.querySelector('[data-qa^="market-type-id"]');
      if (!headerEl) return;
      const inner = headerEl.querySelector(".tw-self-center");
      const mname = (inner ? inner.textContent : headerEl.textContent).trim();
      if (!mname) return;
      const sels = [];

      if (block.querySelector('[data-qa="player-name-in-table"]')) {
        // TABELA DE JOGADORES (chutes, finalizações, etc.)
        let team = "", player = "";
        block.querySelectorAll('[data-qa="team-name-in-table"], [data-qa="player-name-in-table"], [data-qa="event-selection"]').forEach((el) => {
          const dq = el.getAttribute("data-qa");
          if (dq === "team-name-in-table") team = el.textContent.trim();
          else if (dq === "player-name-in-table") {
            const t = el.querySelector(".row-title__text");
            player = (t ? t.textContent : el.textContent).trim();
          } else {
            const m = ARIA_RE.exec(el.getAttribute("aria-label") || "");
            if (!m || !player) return;
            const plusM = PLUS_RE.exec(m[1]);
            sels.push({ sel: `${player} ${m[1]}`.trim(), player, team,
                        odd: parseFloat(m[2]),
                        line: plusM ? parseFloat(plusM[1]) : null,
                        selnid: el.getAttribute("data-selnid") || "" });
          }
        });
      } else {
        // MERCADOS NORMAIS (1X2, over/under, BTTS, handicaps...)
        block.querySelectorAll('[data-qa="event-selection"]').forEach((el) => {
          const m = ARIA_RE.exec(el.getAttribute("aria-label") || "");
          if (!m) return;
          const lineM = LINE_RE.exec(m[1]);
          sels.push({ sel: m[1], odd: parseFloat(m[2]),
                      line: lineM ? parseFloat(lineM[1]) : null,
                      selnid: el.getAttribute("data-selnid") || "" });
        });
      }
      if (sels.length) markets.push({ market: mname, market_id: block.getAttribute("data-marketid"), selections: sels });
    });
    return markets;
  }

  function extractBet365Markets() {
    const markets = [];
    document.querySelectorAll(".gl-MarketGroupPod").forEach((pod) => {
      // título do mercado: tenta as classes conhecidas e cai em qualquer
      // "MarketGroupButton*_Text" (robusto a prefixo hasheado)
      const titleEl = pod.querySelector('.sc-MarketGroupButtonWithStats_Text, .gl-MarketGroupButton_Text, .cm-MarketGroupWithIconsButton_Text, [class*="MarketGroupButton"][class*="_Text"], [class*="MarketGroupButton_Text"]');
      const mname = titleEl ? titleEl.textContent.trim() : null;
      if (!mname) return;
      let labels = [];
      const oddCols = [];
      pod.querySelectorAll(".gl-Market").forEach((col) => {
        const h = col.querySelector(".gl-MarketColumnHeader");
        const header = h ? h.textContent.replace(/\u00a0/g, "").trim() : "";
        const labs = col.querySelectorAll(".srb-ParticipantLabelCentered_Name, .srb-ParticipantLabel_Name");
        // participantes: QUALQUER classe *Participant* (gl-Participant,
        // srb-ParticipantResponsiveText, etc.). A Bet365 usa prefixos variados
        // (gl-, srb-, cm-) — casar só "Participant" é robusto a isso.
        const parts = [];
        col.querySelectorAll('[class*="Participant"]').forEach((p) => {
          if (/Label/i.test(p.className)) return;   // rótulos de coluna, não seleções
          if (p.parentElement && p.parentElement.closest('[class*="Participant"]:not([class*="Label"])')) return; // filho de outro participante
          const oddsEl = p.querySelector('[class*="_Odds"]');
          if (!oddsEl) return;
          const odd = parseFloat(oddsEl.textContent.replace(",", "."));
          if (isNaN(odd) || odd <= 1) return;
          const nameEl = p.querySelector('[class*="_Name"]');
          let name = nameEl ? nameEl.textContent.trim() : "";
          // handicaps: a linha vive num campo próprio. Junta TODOS os campos de
          // handicap (a Bet365 divide a linha de quarto em dois: "+0.5" e "+1.0"
          // em elementos separados) para não perder uma das pontas.
          const hcpEls = p.querySelectorAll('[class*="_Handicap"]');
          if (hcpEls.length) {
            const hcpTxt = [...hcpEls].map((e) => e.textContent.trim())
              .filter(Boolean).join(", ");
            if (hcpTxt) name = `${name} ${hcpTxt}`.trim();
          }
          parts.push({ name, odd });
        });
        if (labs.length && !parts.length) {
          labels = Array.from(labs).map((l) => l.textContent.trim());
        } else if (parts.length) {
          oddCols.push({ header, parts });
        }
      });
      const sels = [];
      oddCols.forEach((c) => {
        c.parts.forEach((p, i) => {
          const lab = p.name || labels[i] || "";
          let name = `${c.header} ${lab}`.trim();
          if (!name) return;
          // LINHA DE QUARTO dividida da Bet365 ("+0.5, +1.0" = +0.75 ; "0, +0.5"
          // = +0.25 ; "-0.5, -1.0" = -0.75): a casa mostra as DUAS pontas. Ler só
          // a última dava a linha ERRADA. Se a seleção termina com dois números
          // (vírgula/barra), troca pela MÉDIA.
          let line = null;
          const dois = name.match(/([-+]?\d+(?:[.,]\d+)?)\s*[,/]\s*([-+]?\d+(?:[.,]\d+)?)\s*$/);
          if (dois) {
            const a = parseFloat(dois[1].replace(",", "."));
            const b = parseFloat(dois[2].replace(",", "."));
            const media = Math.round(((a + b) / 2) * 100) / 100;
            name = (name.slice(0, dois.index).trim() + " " + (media > 0 ? "+" : "") + media).trim();
            line = media;
          } else {
            const lineM = LINE_RE.exec(name);
            line = lineM ? parseFloat(lineM[1]) : null;
          }
          sels.push({ sel: name, odd: p.odd, line, selnid: "" });
        });
      });
      if (sels.length) markets.push({ market: mname, market_id: "", selections: sels });
    });
    // reforço class-agnóstico: se as classes .gl- mudaram e nada saiu, tenta
    // pelos aria-labels (que a Bet365 mantém para acessibilidade e independem
    // das classes hasheadas). Seguro: se não casar, não emite nada.
    if (!markets.length) {
      return extractBet365ByAria();
    }
    return markets;
  }

  // Extração por aria-label — independente das classes hasheadas.
  // Bet365 rotula os botões de odd tipo "Bet on <seleção> with odds <valor>"
  // (ou variação PT). Agrupa por mercado usando o cabeçalho mais próximo.
  const B365_ARIA_RE = /(?:bet on|aposta em|apostar em|para apostar em)\s+(.+?)\s+(?:with odds|at odds|odds of|com odds|por|@)\s*([\d]{1,3}(?:[.,]\d{1,2})?)/i;
  function extractBet365ByAria() {
    const porMercado = {};
    document.querySelectorAll("[aria-label]").forEach((el) => {
      const m = B365_ARIA_RE.exec(el.getAttribute("aria-label") || "");
      if (!m) return;
      const odd = parseFloat(m[2].replace(",", "."));
      if (!(odd > 1)) return;
      const sel = m[1].replace(/\s+/g, " ").trim();
      // título do mercado: sobe procurando um cabeçalho/heading próximo
      let n = el, titulo = "";
      for (let i = 0; i < 8 && n; i++) {
        n = n.parentElement;
        if (!n) break;
        const h = n.querySelector('[role="heading"],[class*="Header" i],[class*="Title" i]');
        const t = h && h.textContent ? h.textContent.replace(/\s+/g, " ").trim() : "";
        if (t && t.length >= 3 && t.length < 60) { titulo = t; break; }
      }
      titulo = titulo || "Mercado";
      const lineM = LINE_RE.exec(sel);
      (porMercado[titulo] = porMercado[titulo] || []).push({
        sel, odd, line: lineM ? parseFloat(lineM[1]) : null, selnid: "",
      });
    });
    return Object.entries(porMercado).map(([market, selections]) =>
      ({ market, market_id: "", selections }));
  }

  // Detecta as LINHAS DE JOGO do cupom da liga da Bet365, de forma ESTRUTURAL
  // (imune às classes hasheadas umr-/gl-): uma linha de jogo é o MENOR elemento
  // que contém exatamente 3 odds (1/X/2) — ou 2 (2-vias) — e 2 nomes de time.
  // Confirmado no HTML real: div.umr-cb com 2x .umr-e5 (times) + 3x .umr-9b (odds).
  const _ODD_RE = /^\d{1,3}\.\d{1,2}$/;
  const _NAME_RE = /^[A-Za-zÀ-ú][A-Za-zÀ-ú0-9 .'&/-]{2,30}$/;
  const _RUIDO_RE = /respons|placar|em alta|criar aposta|ao.vivo|cassino|promo/i;
  // palavras que NÃO são nome de time (são mercados/seleções da Bet365). Se um
  // "nome" bate nisto, a linha não é um jogo de cupom (evita "Over v Under",
  // "Draw No Bet v Internacional" que apareciam ao ler mercados como jogos).
  const _NAO_TIME_RE = /^(over|under|mais|menos|yes|no|sim|n[ãa]o|draw no bet|both teams|goals? over|total goals?|corners?|escanteios?|handicap|resultado final|full time|draw|empate|exactly|odd|even|par|[íi]mpar|to score|to assist|acum|pagamento|ambas|marcar|1x2|score)/i;

  // janela de datas: só varre jogos que começam de hoje até +N dias (evita
  // ficar com apostas pendentes de 2 semanas). Configurável.
  const B365_HORIZON_DAYS = 2;   // hoje + 2 dias (ex.: 22, 23 e 24 Jul)
  const _MESES = { jan: 0, fev: 1, mar: 2, abr: 3, mai: 4, jun: 5,
                   jul: 6, ago: 7, set: 8, out: 9, nov: 10, dez: 11 };

  // Converte um texto ("Qua 22 Jul", "Hoje", "Amanhã") em Date (dia cheio).
  // Retorna null se não houver data reconhecível.
  function b365ParseDateText(txt) {
    const s = (txt || "").toLowerCase();
    const now = new Date();
    if (/\bhoje\b/.test(s))
      return new Date(now.getFullYear(), now.getMonth(), now.getDate(), 12, 0, 0);
    if (/\bamanh[ãa]\b/.test(s))
      return new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 12, 0, 0);
    const md = s.match(/(\d{1,2})\s*(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)/);
    if (!md) return null;
    const dia = parseInt(md[1], 10), mes = _MESES[md[2]];
    let d = new Date(now.getFullYear(), mes, dia, 12, 0, 0);
    if (d.getTime() < now.getTime() - 36 * 3600 * 1000)     // já passou -> ano seguinte
      d = new Date(now.getFullYear() + 1, mes, dia, 12, 0, 0);
    return d;
  }

  // Cabeçalhos de data do cupom da Bet365. A casa AGRUPA os jogos sob um
  // cabeçalho de dia ("Qua 22 Jul"); a data NÃO fica dentro da linha de cada
  // jogo (que só traz a hora). Sem casar com o cabeçalho, só a 1ª liga (cujos
  // jogos eram do dia) respeitava a janela — as demais liam jogos de semanas à
  // frente. Detecção imune a classe (só texto + posição no documento).
  function b365DateHeaders() {
    const heads = [];
    const re = /\d{1,2}\s*(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)|hoje|amanh[ãa]/i;
    document.querySelectorAll("div,span").forEach((el) => {
      if (el.childElementCount > 4) return;         // cabeçalho é um bloco curto
      const t = (el.textContent || "").trim();
      if (!t || t.length > 40) return;
      if (_ODD_RE.test(t)) return;                  // linha de odd não é cabeçalho
      if (!re.test(t.toLowerCase())) return;
      const d = b365ParseDateText(t);
      if (d) heads.push({ el, d });
    });
    return heads;
  }

  // Data de um jogo do cupom: usa a data no próprio texto, se houver; senão, o
  // cabeçalho de dia mais próximo ACIMA dele no documento.
  function b365RowDate(el, headers) {
    const own = b365ParseDateText(el.textContent || "");
    if (own) return own;
    let melhor = null;
    for (const h of (headers || [])) {
      // h.el precede el no documento? (headers vêm em ordem -> o último vence)
      if (el.compareDocumentPosition(h.el) & Node.DOCUMENT_POSITION_PRECEDING)
        melhor = h.d;
    }
    return melhor;
  }

  function b365DentroDaJanela(d) {
    if (!d) return true;                        // sem data legível -> não descarta
    const now = new Date();
    // limite = fim do dia (hoje + N dias). Ex.: hoje 22 Jul, N=2 -> até 24 Jul 23:59
    const limite = new Date(now.getFullYear(), now.getMonth(),
                            now.getDate() + B365_HORIZON_DAYS, 23, 59, 59);
    return d.getTime() <= limite.getTime();
  }

  // filtrarData: descarta jogos fora da janela de dias.
  // maxOdds: nº máx. de odds por linha (3 = cupom 1X2/2-vias do futebol).
  // exact2Names: exige EXATAMENTE 2 nomes de time (p/ o net largo de MLB/WNBA
  //   não pegar o CONTÊINER de vários jogos como se fosse um jogo só).
  function b365CouponRows(filtrarData = true, maxOdds = 3, exact2Names = false) {
    const headers = filtrarData ? b365DateHeaders() : [];
    const rows = [];
    document.querySelectorAll("div").forEach((el) => {
      const leaves = [...el.querySelectorAll("div,span")].filter((x) => x.childElementCount === 0);
      const oddEls = leaves.filter((x) => _ODD_RE.test((x.textContent || "").trim()));
      if (oddEls.length < 2 || oddEls.length > maxOdds) return;
      const nameEls = leaves.filter((x) => {
        const t = (x.textContent || "").trim();
        return _NAME_RE.test(t) && !_RUIDO_RE.test(t) && !_NAO_TIME_RE.test(t) &&
               !/^\d{1,2}:\d{2}$/.test(t);
      });
      if (exact2Names ? nameEls.length !== 2 : nameEls.length < 2) return;
      const data = b365RowDate(el, headers);
      if (filtrarData && !b365DentroDaJanela(data)) return;    // fora da janela de dias
      rows.push({
        el,
        odds: oddEls.map((o) => parseFloat(o.textContent)),
        names: nameEls.slice(0, 2).map((nm) => nm.textContent.trim()),
        date: data ? data.toISOString() : "",     // data do JOGO (p/ registrar na aposta)
      });
    });
    return rows;
  }

  // Lê o cupom (1/X/2 de TODOS os jogos da liga) sem entrar jogo a jogo.
  function extractBet365LeagueCoupon() {
    const jogos = [];
    b365CouponRows().forEach((r) => {
      const [home, away] = r.names;
      if (!home || !away) return;
      const labels = ["1", "X", "2"];
      const sels = r.odds.filter((v) => v > 1)
        .map((v, j) => ({ sel: labels[j] || String(j), odd: v, line: null, selnid: "" }));
      if (sels.length >= 2) jogos.push({ event: `${home} v ${away}`, market: "Resultado Final", selections: sels, date: r.date || "" });
    });
    return jogos;
  }

  function extractAllMarkets() {
    const source = detectSource();
    const markets = source === "bet365" ? extractBet365Markets() : extractBetanoMarkets();
    return {
      event: eventName(),
      event_datetime: eventDateTime(),
      // id do evento na casa — o servidor cruza com a API para obter times e
      // horário exatos, sem depender de ler nomes do DOM
      event_id: source === "betano" ? betanoEventId() : "",
      url: location.href,
      source,
      captured_at: new Date().toISOString(),
      markets,
    };
  }

  function collectEventLinks() {
    // na página de uma liga, coleta os links dos jogos (/odds/slug/id/)
    const links = new Set();
    document.querySelectorAll('a[href*="/odds/"]').forEach((a) => {
      const href = a.getAttribute("href");
      if (/^\/odds\/[^/]+\/\d+\/?$/.test(href)) links.add(new URL(href, location.origin).href);
    });
    return Array.from(links);
  }

  // ============================================================
  // VARREDURA AUTOMÁTICA DA BET365 (sem entrar jogo a jogo)
  //
  // A Bet365 é SPA: os jogos de uma liga são elementos clicáveis (não links
  // com URL). Então, em vez de navegar por URL, a extensão CLICA em cada jogo
  // da lista, espera renderizar, deixa o auto-captura enviar, e volta. Você só
  // abre a página da liga/competição e aperta "Varrer".
  // ============================================================
  // seletores de "jogo clicável" numa lista/cupom da Bet365. A Bet365 troca
  // as classes; por isso há vários, e um fallback por estrutura no fim.
  const B365_FIXTURE_SEL = [
    ".rcl-ParticipantFixtureDetails_TeamAndPlaypause",
    ".rcl-ParticipantFixtureDetailsHigher_TeamNames",
    ".scb-ParticipantFixtureDetailsLabel",
    ".sl-CouponParticipantWithBookCloses_NameContainer",
    ".sl-CouponParticipantWithBookCloses",
    '[class*="ParticipantFixtureDetails"][class*="Team"]',
    '[class*="CouponParticipant"]',
    '[class*="FixtureDetails"]',
  ];

  function b365Fixtures() {
    // 1) detecção ESTRUTURAL (imune a classe): as linhas do cupom (3 odds + 2
    //    times). É o que funciona no futebol (1X2).
    let rows = b365CouponRows();
    if (rows.length >= 2) return rows.map((r) => r.el);
    // 1b) fallback US (MLB/WNBA/NBA): o cupom mostra vários mercados por jogo
    //     (ML + spread + total = 4-6 odds), então o net de 3 odds não pega.
    //     Aqui alargamos p/ até 10 odds MAS exigindo EXATAMENTE 2 times, para
    //     não confundir o contêiner de vários jogos com um jogo só.
    rows = b365CouponRows(true, 10, true);
    if (rows.length >= 2) return rows.map((r) => r.el);
    // 2) fallback por classes conhecidas
    for (const sel of B365_FIXTURE_SEL) {
      const els = [...document.querySelectorAll(sel)].filter(
        (e) => e.offsetParent !== null && (e.textContent || "").trim().length > 3);
      if (els.length >= 2) return els;
    }
    return [];
  }

  // Estamos DENTRO de um jogo da Bet365? Decisão por DOM (não por URL): as
  // páginas de jogo usam rotas tipo I^21/J^1/K^1 SEM /E<id>/, então testar a
  // URL falhava e o leitor rodava em "modo cupom" dentro do jogo (lia mercados
  // como jogos falsos: "Over v Under"). Um jogo aberto tem pods de mercado
  // e/ou a barra de abas (Resultado/Gols/Escanteios...); a lista da liga tem
  // muitas linhas de cupom (3 odds + 2 times) e nenhum pod.
  function b365EstaNumJogo() {
    if (document.querySelectorAll('[class*="MarketGroupPod"]').length >= 1) return true;
    const abas = [...document.querySelectorAll("[data-content]")]
      .filter((b) => b.closest('[class*="NavBarButton"],[class*="MarketGroupNav"]')).length;
    if (abas >= 2 && b365CouponRows(false).length < 2) return true;
    return false;
  }

  // ============================================================
  // FECHAR O POP-UP DE LOGIN DA BET365
  // O login NÃO é necessário para ler as odds, mas a Bet365 às vezes abre um
  // modal de "Iniciar sessão" que tampa a tela e trava a varredura. Como o
  // usuário pediu, o bot fecha sozinho: botão de fechar -> Escape -> clique
  // FORA do cartão (no backdrop). Imune a classe (detecta pela sobreposição em
  // tela cheia + texto de login).
  // ============================================================
  const _LOGIN_RE = /iniciar sess|fazer login|\blog ?in\b|acessar sua conta|criar conta|nome de usu|\bsenha\b|password|esqueceu sua senha/i;
  function b365FecharLogin() {
    if (detectSource() !== "bet365") return false;
    for (const el of document.querySelectorAll("div")) {
      const r = el.getBoundingClientRect();        // gate barato: só overlays grandes
      if (!(r.width >= window.innerWidth * 0.5 && r.height >= window.innerHeight * 0.4)) continue;
      const st = getComputedStyle(el);
      if (st.position !== "fixed" && st.position !== "absolute") continue;
      if (st.display === "none" || st.visibility === "hidden") continue;
      if ((parseInt(st.zIndex, 10) || 0) < 50) continue;
      if (!_LOGIN_RE.test(el.textContent || "")) continue;

      // 1) botão de fechar dentro do overlay
      const fechar = el.querySelector(
        '[aria-label*="close" i],[aria-label*="fechar" i],[class*="Close" i],[title*="fechar" i],[title*="close" i]');
      if (fechar) { try { fechar.click(); return true; } catch (e) {} }
      // 2) tecla Escape
      try {
        document.dispatchEvent(new KeyboardEvent("keydown",
          { key: "Escape", keyCode: 27, which: 27, bubbles: true }));
      } catch (e) {}
      // 3) clique FORA: num canto do backdrop (fora do cartão central de login)
      const x = Math.max(2, r.left + 4), y = Math.max(2, r.top + 4);
      const alvo = document.elementFromPoint(x, y) || el;
      ["mousedown", "mouseup", "click"].forEach((tipo) => {
        try { alvo.dispatchEvent(new MouseEvent(tipo,
          { bubbles: true, cancelable: true, clientX: x, clientY: y })); } catch (e) {}
      });
      return true;
    }
    return false;
  }

  // vigia contínua: fecha o login sozinho enquanto você navega na Bet365
  setInterval(b365FecharLogin, 3000);

  // diagnóstico: quantos jogos a extensão enxerga na tela atual da Bet365
  function b365Diag() {
    const res = {};
    B365_FIXTURE_SEL.forEach((s) => { res[s] = document.querySelectorAll(s).length; });
    res["_fixtures_detectados"] = b365Fixtures().length;
    res["_pods_de_jogo"] = document.querySelectorAll(".gl-MarketGroupPod").length;
    return res;
  }

  let b365Crawl = { active: false };
  function sleepC(ms) { return new Promise((r) => setTimeout(r, ms)); }

  // ID do evento na rota de hash da Bet365 (ex.: /#/AC/B1/.../E195288878/...)
  function b365EventId() {
    const m = (location.hash || location.href).match(/\/E(\d+)\b/);
    return m ? m[1] : "";
  }

  // DENTRO de um jogo da Bet365: clica cada ABA de mercado (Resultado, Gols,
  // Escanteios, Odds Asiáticas, 1º Tempo/2º Tempo, Cartões) e captura os
  // mercados de cada uma. Sem isso, só pegaríamos a aba "Popular".
  // Abas = div.sph-MarketGroupNavBarButton com [data-content]="<nome>".
  // BILÍNGUE: a Bet365 BR mostra os mercados em INGLÊS ("Asian Handicap",
  // "Full Time Result", "Goals Over/Under"). O regex só-PT "asiátic" NUNCA
  // casava "Asian"/"Handicap", então a aba do Handicap Asiático — o mercado que
  // mais gera valor (Spread) — nunca era aberta. Cobrimos PT e EN.
  const B365_ABAS_ALVO = [
    /resultado|result|full time/i,          // Resultado Final / Full Time Result
    /gols|goals/i,                          // Gols / Goals Over-Under
    /escanteios|corners/i,                  // Escanteios / Corners
    /asi[áa]tic|asian|handicap/i,           // Odds Asiáticas / Asian Handicap / Handicaps
    /tempo|half/i,                          // 1º/2º Tempo / 1st/2nd Half
    /cart|cards/i,                          // Cartões / Cards
  ];
  async function abrirAbasBet365() {
    if (detectSource() !== "bet365") return 0;
    const jaClicadas = new Set();
    let abertas = 0, seguranca = 0;
    // RE-BUSCA os botões a cada volta: clicar numa aba re-renderiza a barra e
    // invalida as referências antigas — por isso antes só UMA aba (a última que
    // sobrava viva, tipo "Cartões") era clicada. Agora clicamos cada aba-alvo
    // uma vez, sempre relendo o DOM. Nome vem de data-content OU do texto
    // visível (a Bet365 mistura PT e EN: "Cartões" mas "Corners"/"Asian Handicap").
    while (seguranca++ < 14) {
      if (b365Paused) break;                     // pausou no meio: para de trocar abas
      const botoes = [...document.querySelectorAll('[data-content]')]
        .filter((b) => b.closest('[class*="NavBarButton"],[class*="MarketGroupNav"]'));
      const alvo = botoes.find((b) => {
        const nome = (b.getAttribute("data-content") || b.textContent || "").trim();
        return nome && !jaClicadas.has(nome) && B365_ABAS_ALVO.some((re) => re.test(nome));
      });
      if (!alvo) break;
      const nome = (alvo.getAttribute("data-content") || alvo.textContent || "").trim();
      jaClicadas.add(nome);
      try {
        (alvo.closest('[class*="NavBarButton"]') || alvo).click();
        abertas++;
        await sleepC(1300);          // a aba carrega os mercados
        b365FecharLogin();
        await autoCaptureOdds();      // captura os mercados desta aba (awaited!)
        await sleepC(200);
      } catch (e) { /* segue para a próxima aba */ }
    }
    return abertas;
  }

  // Coleta as rotas dos JOGOS de uma página de liga da Bet365. Preferimos
  // navegação por URL (hash) — é confiável: voltar à liga é só resetar o hash,
  // sem depender de botão "voltar". Estratégia dupla e class-agnóstica:
  //   1) âncoras <a href> cuja rota tem /E<id>/ (o jeito robusto)
  //   2) fallback: elementos clicáveis que contêm ID de evento em atributos
  function b365GameRoutes() {
    const rotas = new Set();
    document.querySelectorAll('a[href]').forEach((a) => {
      const h = a.getAttribute("href") || "";
      if (/\/E\d+\b/.test(h)) rotas.add(h.startsWith("#") || h.startsWith("/") ? h : "#" + h);
    });
    if (rotas.size === 0) {
      // fallback: procura IDs de evento em qualquer atributo de elementos visíveis
      document.querySelectorAll("[href],[data-uet],[data-fi]").forEach((el) => {
        for (const at of el.attributes) {
          const m = String(at.value).match(/\bE(\d+)\b/);
          if (m) rotas.add("E" + m[1]);
        }
      });
    }
    return [...rotas];
  }

  // Navega na Bet365 (SPA por hash) de forma CONFIÁVEL: além de setar a URL,
  // dispara 'hashchange' (o roteador nem sempre reage só ao location.href — era
  // por isso que, ao trocar de liga, a 2ª não carregava e só a 1ª era lida) e
  // fecha o login. Espera renderizar.
  async function b365Navega(u, esperaMs) {
    const antes = location.href;
    try { location.href = u; } catch (e) {}
    try {
      window.dispatchEvent(new HashChangeEvent("hashchange", { oldURL: antes, newURL: u }));
    } catch (e) {
      try { window.dispatchEvent(new Event("hashchange")); } catch (e2) {}
    }
    await sleepC(esperaMs);
    b365FecharLogin();
  }

  // Varre uma lista de URLs de LIGA (coladas pelo usuário). Para cada liga,
  // coleta os jogos e visita cada um pela URL, capturando as odds.
  async function b365CrawlLeagues(urls) {
    if (b365Crawl.active) return;
    b365Crawl.active = true;
    let totalJogos = 0, capturados = 0, ligas = 0;
    const setStatus = (msg) => chrome.storage.local.set({ b365CrawlStatus: {
      active: b365Crawl.active, ligas, done: capturados, total: totalJogos, msg } });

    for (const ligaUrl of urls) {
      if (!b365Crawl.active) break;
      ligas++;
      const u = ligaUrl.trim();
      // CADA liga isolada num try/catch: um erro numa liga NÃO pode abortar as
      // demais (era o motivo de "leu só a primeira").
      try {
        await b365Navega(u, 4500);

        // PRIORIDADE À LISTA DA LIGA: procura os fixtures ANTES de decidir que é
        // um jogo único. Assim, uma tela "presa" no jogo da liga anterior (a nav
        // não pegou) não é lida como jogo e não faz pular a liga — era o motivo
        // de "parar de trocar de liga". Re-navega até achar os fixtures.
        let nJogos = b365Fixtures().length;
        for (let tent = 0; tent < 3 && nJogos === 0 && b365Crawl.active; tent++) {
          await b365Navega(u, 3000);
          nJogos = b365Fixtures().length;
        }

        // sem fixtures E claramente dentro de um jogo -> a URL colada era de um
        // JOGO único (não uma liga): captura direto e segue.
        if (nJogos === 0 && b365EstaNumJogo()) {
          totalJogos++;
          await abrirAbasBet365();      // troca cada aba (asiático, escanteios...) + captura
          const snap = extractAllMarkets();
          capturados += snap.markets.length ? 1 : 0;
          setStatus(`Jogo ${ligas}/${urls.length}: ${snap.markets.length} mercados`);
          await sleepC(1000);
          continue;
        }

        // 1) SEMPRE captura o cupom da liga (1/X/2 de todos) — garante os
        //    mercados principais mesmo se a entrada no jogo falhar.
        extractBet365LeagueCoupon().forEach(sendBet365CouponGame);

        // 2) ENTRA em cada jogo para pegar TODOS os mercados (asiático, escanteios...)
        totalJogos += nJogos;
        setStatus(`Liga ${ligas}/${urls.length}: ${nJogos} jogos (cupom + entrando um a um)`);

        for (let i = 0; i < nJogos && b365Crawl.active; i++) {
          try {
            await b365Navega(u, 2800);          // volta à liga (confiável, é URL)
            const fx = b365Fixtures();
            if (!fx[i]) continue;
            // clica na linha; se não navegar, tenta os filhos (nome do time etc.)
            const cliqueis = [fx[i], ...fx[i].querySelectorAll("div,span,img")].slice(0, 8);
            for (const alvo of cliqueis) {
              alvo.click();
              await sleepC(1200);
              if (b365EstaNumJogo()) break;        // entrou! (detecta por DOM)
            }
            await sleepC(2600);                   // mercados do jogo renderizam
            b365FecharLogin();                    // garante tela livre antes de capturar
            if (b365EstaNumJogo()) {
              await autoCaptureOdds();            // aba Popular
              await abrirAbasBet365();            // troca cada aba (asiático, escanteios, gols...)
              capturados++;
            }
            setStatus(`Liga ${ligas}/${urls.length}: ${capturados}/${nJogos} jogos completos`);
          } catch (e) { /* segue para o próximo jogo */ }
        }
      } catch (e) {
        setStatus(`Liga ${ligas}/${urls.length}: erro — seguindo para a próxima`);
        continue;                                 // não deixa uma liga quebrar as outras
      }
    }
    b365Crawl.active = false;
    chrome.storage.local.set({ b365CrawlStatus: {
      active: false, ligas, done: capturados, total: totalJogos,
      msg: `Concluído: ${capturados} jogos de ${ligas} ligas` } });
  }

  async function b365CrawlStart() {
    // varredura da liga ATUAL (sem URLs coladas): coleta os jogos da tela
    const rotas = b365GameRoutes();
    if (rotas.length) return b365CrawlLeagues([location.href]);
    // sem rotas: cai no modo clique dos fixtures visíveis
    const fixtures = b365Fixtures();
    if (!fixtures.length) {
      chrome.storage.local.set({ b365CrawlStatus: { active: false, msg: "Abra a lista de uma liga (ou cole as URLs das ligas)" } });
      return;
    }
    return b365CrawlLeagues([location.href]);
  }

  // permite que o popup/background comandem ações
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg && msg.action === "b365_pause") {
      b365Paused = true;
      b365Crawl.active = false;                 // para a varredura em andamento
      try { chrome.storage.local.set({ b365Paused: true,
        b365CrawlStatus: { active: false, msg: "⏸ Varredura pausada" } }); } catch (e) {}
      sendResponse({ ok: true, paused: true });
      return true;
    }
    if (msg && msg.action === "b365_resume") {
      b365Paused = false;
      try { chrome.storage.local.set({ b365Paused: false }); } catch (e) {}
      sendResponse({ ok: true, paused: false });
      return true;
    }
    if (msg && msg.action === "b365_crawl") {
      if (b365Paused) { sendResponse({ ok: false, paused: true }); return true; }
      b365CrawlStart();
      sendResponse({ ok: true, fixtures: b365GameRoutes().length || b365Fixtures().length });
      return true;
    }
    if (msg && msg.action === "b365_crawl_leagues" && Array.isArray(msg.urls)) {
      if (b365Paused) { sendResponse({ ok: false, paused: true }); return true; }
      b365CrawlLeagues(msg.urls);
      sendResponse({ ok: true, ligas: msg.urls.length });
      return true;
    }
    if (msg && msg.action === "b365_crawl_stop") {
      b365Crawl.active = false;
      sendResponse({ ok: true });
      return true;
    }
    if (msg && msg.action === "b365_diag") {
      sendResponse({ ok: true, diag: b365Diag() });
      return true;
    }
    if (msg && msg.action === "scan_now") {
      SEEN.clear();          // limpa o visto para recapturar tudo na tela
      lastCount = 0;
      scan();
      sendResponse({ ok: true, count: lastCount });
    }
    if (msg && msg.action === "extract_odds") {
      const snapshot = extractAllMarkets();
      markSnapshotSent(snapshot);
      chrome.runtime.sendMessage({ action: "send_odds", snapshot });
      sendResponse({ ok: true, markets: snapshot.markets.length, event: snapshot.event });
    }
    if (msg && msg.action === "collect_event_links") {
      sendResponse({ ok: true, links: collectEventLinks() });
    }
    return true;
  });
})();
