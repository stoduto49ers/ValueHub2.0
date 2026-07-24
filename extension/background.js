// background.js — service worker da extensão.
//
// O content script (rodando na página HTTPS da Betano) NÃO pode enviar
// direto para http://localhost (bloqueio de "conteúdo misto"). Então ele
// manda os boosts para cá (service worker), e ESTE faz o fetch ao servidor.
// O service worker não sofre a restrição de conteúdo misto.

const HUB_URL = "http://localhost:8000/boost";
const ODDS_URL = "http://localhost:8000/odds";

// ============================================================
// MODO VARREDURA: navega a aba por cada jogo da liga, extrai, arquiva.
// Intervalo proposital entre jogos (parecer navegação humana).
// ============================================================
const CRAWL_DELAY_MS = 5000;   // espera após carregar cada jogo
let crawl = { active: false, queue: [], done: 0, total: 0, tabId: null };

function crawlNext() {
  if (!crawl.active || !crawl.queue.length) {
    crawl.active = false;
    chrome.storage.local.set({ crawlStatus: { active: false, done: crawl.done, total: crawl.total } });
    return;
  }
  const url = crawl.queue.shift();
  chrome.tabs.update(crawl.tabId, { url }, () => {
    // espera a página SPA renderizar, então pede a extração
    setTimeout(() => {
      chrome.tabs.sendMessage(crawl.tabId, { action: "extract_odds" }, () => {
        void chrome.runtime.lastError;   // página pode não ter respondido; segue
        crawl.done++;
        chrome.storage.local.set({ crawlStatus: { active: true, done: crawl.done, total: crawl.total } });
        setTimeout(crawlNext, 800);
      });
    }, CRAWL_DELAY_MS);
  });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // snapshot de odds vindo do content script -> arquiva no servidor
  if (msg && msg.action === "send_odds" && msg.snapshot) {
    fetch(ODDS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(msg.snapshot),
    })
      .then((r) => r.json())
      .then((data) => sendResponse({ ok: true, server: data }))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }

  // inicia varredura: recebe a lista de links da liga e a aba a usar
  if (msg && msg.action === "start_crawl" && Array.isArray(msg.links)) {
    crawl = { active: true, queue: msg.links.slice(), done: 0, total: msg.links.length, tabId: msg.tabId };
    chrome.storage.local.set({ crawlStatus: { active: true, done: 0, total: crawl.total } });
    crawlNext();
    sendResponse({ ok: true, total: crawl.total });
    return true;
  }

  if (msg && msg.action === "stop_crawl") {
    crawl.active = false;
    crawl.queue = [];
    sendResponse({ ok: true });
    return true;
  }

  if (msg && msg.action === "send_boosts" && Array.isArray(msg.boosts)) {
    fetch(HUB_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ boosts: msg.boosts }),
    })
      .then((r) => r.json())
      .then((data) => {
        sendResponse({ ok: true, server: data });
      })
      .catch((err) => {
        // servidor offline: guarda para reenviar
        chrome.storage.local.get(["pending"], (r) => {
          const pending = r.pending || [];
          chrome.storage.local.set({ pending: pending.concat(msg.boosts) });
        });
        sendResponse({ ok: false, error: String(err) });
      });
    return true; // resposta assíncrona
  }

  if (msg && msg.action === "resend_pending") {
    chrome.storage.local.get(["pending"], (r) => {
      const pending = r.pending || [];
      if (!pending.length) {
        sendResponse({ ok: true, count: 0 });
        return;
      }
      fetch(HUB_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ boosts: pending }),
      })
        .then((r) => r.json())
        .then(() => {
          chrome.storage.local.set({ pending: [] });
          sendResponse({ ok: true, count: pending.length });
        })
        .catch((err) => sendResponse({ ok: false, error: String(err) }));
    });
    return true;
  }
});
