// popup.js — mostra a contagem de boosts e o status do servidor local.

const HUB = "http://localhost:8000";

chrome.storage.local.get(["lastCount"], (r) => {
  document.getElementById("count").textContent = r.lastCount || 0;
});

// testa se o servidor está no ar
fetch(HUB + "/summary")
  .then((res) => {
    const el = document.getElementById("status");
    if (res.ok) {
      el.className = "status ok";
      el.textContent = "Servidor conectado ✓";
    } else {
      throw new Error();
    }
  })
  .catch(() => {
    const el = document.getElementById("status");
    el.className = "status off";
    el.textContent = "Servidor offline — rode python server.py";
  });

// botão "Escanear agora" — força o content script a reler a página atual
document.getElementById("scan").addEventListener("click", () => {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (!tabs[0]) return;
    chrome.tabs.sendMessage(tabs[0].id, { action: "scan_now" }, (resp) => {
      if (chrome.runtime.lastError) {
        // content script não está nesta página
        const el = document.getElementById("status");
        el.className = "status off";
        el.textContent = "Abra/atualize uma página da Betano (F5)";
        return;
      }
      // atualiza a contagem após o scan
      setTimeout(() => {
        chrome.storage.local.get(["lastCount"], (r) => {
          document.getElementById("count").textContent = r.lastCount || 0;
        });
      }, 500);
    });
  });
});

// extrair odds da página atual (um jogo)
document.getElementById("extract").addEventListener("click", () => {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (!tabs[0]) return;
    chrome.tabs.sendMessage(tabs[0].id, { action: "extract_odds" }, (resp) => {
      const el = document.getElementById("status");
      if (chrome.runtime.lastError || !resp) {
        el.className = "status off";
        el.textContent = "Abra um jogo (Betano ou Bet365) e dê F5";
        return;
      }
      el.className = "status ok";
      el.textContent = `${resp.markets} mercados extraídos de ${resp.event}`;
    });
  });
});

// carrega/salva as URLs de liga da Bet365 que o usuário colou
chrome.storage.local.get(["leagueUrls"], (r) => {
  if (r.leagueUrls) document.getElementById("leagueUrls").value = r.leagueUrls;
});
document.getElementById("leagueUrls").addEventListener("input", (e) => {
  chrome.storage.local.set({ leagueUrls: e.target.value });
});

// VARRER LIGAS COLADAS (Bet365): navega por cada URL de liga, coleta os jogos
// e visita cada um pela URL — automático, você só cola os links das ligas.
document.getElementById("crawlLeagues").addEventListener("click", () => {
  const raw = (document.getElementById("leagueUrls").value || "").trim();
  // junta linhas QUEBRADAS: uma URL começa com http; linhas seguintes que não
  // começam com http são continuação da URL anterior (a caixa quebra URLs longas)
  const urls = [];
  raw.split("\n").forEach((line) => {
    const t = line.trim();
    if (!t) return;
    if (/^https?:\/\//i.test(t) || urls.length === 0) urls.push(t);
    else urls[urls.length - 1] += t;      // continuação: concatena
  });
  const urlsValidas = urls.filter((s) => s.length > 8);
  const el = document.getElementById("status");
  if (!urlsValidas.length) {
    el.className = "status off";
    el.textContent = "Cole pelo menos 1 URL de liga da Bet365";
    return;
  }
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (!tabs[0]) return;
    if (!(tabs[0].url || "").includes("bet365")) {
      el.className = "status off";
      el.textContent = "Abra a Bet365 numa aba primeiro";
      return;
    }
    chrome.tabs.sendMessage(tabs[0].id, { action: "b365_crawl_leagues", urls: urlsValidas }, (resp) => {
      if (chrome.runtime.lastError || !resp) {
        el.className = "status off";
        el.textContent = "Recarregue a Bet365 (F5) e tente de novo";
        return;
      }
      el.className = "status ok";
      el.textContent = `Varrendo ${resp.ligas} ligas sozinho...`;
    });
  });
});

// varrer: na Betano navega pelos links da liga; na Bet365 ativa o modo
// troca-de-jogo (você alterna no seletor, ela extrai a cada troca)
document.getElementById("crawl").addEventListener("click", () => {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (!tabs[0]) return;
    const el = document.getElementById("status");
    if ((tabs[0].url || "").includes("bet365")) {
      // varredura AUTOMÁTICA: clica por cada jogo da lista sozinha
      chrome.tabs.sendMessage(tabs[0].id, { action: "b365_crawl" }, (resp) => {
        if (chrome.runtime.lastError || !resp) {
          el.className = "status off";
          el.textContent = "Abra a lista de uma LIGA/competição na Bet365 e dê F5";
          return;
        }
        el.className = "status ok";
        el.textContent = resp.fixtures
          ? `Varrendo ${resp.fixtures} jogos da Bet365 sozinho...`
          : "Nenhum jogo visível — abra a lista de uma liga";
      });
      return;
    }
    chrome.tabs.sendMessage(tabs[0].id, { action: "collect_event_links" }, (resp) => {
      if (chrome.runtime.lastError || !resp || !resp.links || !resp.links.length) {
        el.className = "status off";
        el.textContent = "Abra a página da LIGA (lista de jogos)";
        return;
      }
      chrome.runtime.sendMessage({ action: "start_crawl", links: resp.links, tabId: tabs[0].id }, (r) => {
        el.className = "status ok";
        el.textContent = `Varrendo ${r.total} jogos... (~${Math.ceil(r.total * 6 / 60)} min)`;
      });
    });
  });
});

// PAUSAR/RETOMAR a varredura da Bet365. O estado é persistido em storage; o
// content script lê o flag e para o auto-start (e a varredura em andamento).
function renderPauseBtn(paused) {
  const b = document.getElementById("pauseCrawl");
  if (!b) return;
  if (paused) {
    b.textContent = "▶ Retomar varredura (Bet365)";
    b.style.background = "#1f3a2a"; b.style.color = "#3fb950";
  } else {
    b.textContent = "⏸ Pausar varredura (Bet365)";
    b.style.background = "#3a1f1f"; b.style.color = "#f85149";
  }
}
chrome.storage.local.get(["b365Paused"], (r) => renderPauseBtn(!!r.b365Paused));
document.getElementById("pauseCrawl").addEventListener("click", () => {
  chrome.storage.local.get(["b365Paused"], (r) => {
    const novo = !r.b365Paused;
    // persiste já (o content script também escuta storage) e avisa a aba ativa
    chrome.storage.local.set({ b365Paused: novo });
    renderPauseBtn(novo);
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs[0]) chrome.tabs.sendMessage(tabs[0].id,
        { action: novo ? "b365_pause" : "b365_resume" }, () => void chrome.runtime.lastError);
    });
    const el = document.getElementById("status");
    el.className = "status ok";
    el.textContent = novo ? "Varredura pausada ⏸" : "Varredura retomada ▶";
  });
});

// mostra progresso da varredura e da auto-extração 365
setInterval(() => {
  chrome.storage.local.get(["crawlStatus", "b365CrawlStatus", "lastAuto"], (r) => {
    const el = document.getElementById("crawl-status");
    const parts = [];
    const cs = r.crawlStatus;
    if (cs && (cs.active || cs.done > 0)) {
      parts.push(cs.active ? `Varredura: ${cs.done}/${cs.total} jogos` : `Varredura concluída: ${cs.done}/${cs.total}`);
    }
    const bs = r.b365CrawlStatus;
    if (bs) {
      if (bs.msg) parts.push(bs.msg);
      else if (bs.active || bs.done > 0) parts.push(bs.active ? `Bet365: ${bs.done}/${bs.total} jogos` : `Bet365 concluída: ${bs.done}/${bs.total}`);
    }
    if (r.lastAuto && Date.now() - r.lastAuto.ts < 120000) {
      parts.push(`Última extração: ${r.lastAuto.event} (${r.lastAuto.markets} mercados)`);
    }
    if (parts.length) { el.style.display = "block"; el.textContent = parts.join(" · "); }
  });
}, 1000);

// reenviar boosts que ficaram pendentes (capturados com servidor offline)
document.getElementById("resend").addEventListener("click", () => {
  chrome.runtime.sendMessage({ action: "resend_pending" }, (resp) => {
    if (chrome.runtime.lastError || !resp) {
      alert("Não foi possível reenviar agora.");
      return;
    }
    if (resp.ok) {
      alert(resp.count ? `${resp.count} boosts reenviados.` : "Nenhum boost pendente.");
    } else {
      alert("Servidor ainda offline.");
    }
  });
});
