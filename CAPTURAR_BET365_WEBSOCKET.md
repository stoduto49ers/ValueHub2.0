# Bet365 via WebSocket — capturar frames reais (o caminho robusto)

## Por que isto (a verdade técnica)

A Betano foi fácil porque tem **API REST em JSON** (`?bt=13`). A **Bet365 NÃO tem
API REST**. As odds dela chegam por um **WebSocket proprietário**
(`wss://www.bet365.bet.br/sportspublisher/zap/`) num formato binário próprio,
por trás do Cloudflare e de um token de sessão.

Ou seja: **não dá para raspar a Bet365 do servidor igual à Betano.** O caminho
que é ao mesmo tempo *robusto* e *infra própria* é a **extensão interceptar esse
WebSocket** na sua sessão real do navegador (que já passou o Cloudflare) e
**decodificar as odds direto da fonte** — sem depender das classes hasheadas do
DOM, que mudam toda hora.

Do meu lado eu **não consigo** fazer as odds chegarem (a Bet365 bloqueia a
navegação automatizada). Só a **sua** sessão recebe os frames de odds. Então eu
preciso de uma amostra real deles para escrever o decodificador.

## Passo 1 — instalar o gancho (antes de abrir a liga)

Abra a **Bet365**, F12 → **Console**, cole e Enter:

```js
(() => {
  const OW = window.WebSocket;
  window.__odds = [];
  window.WebSocket = function (u, p) {
    const ws = p ? new OW(u, p) : new OW(u);
    ws.addEventListener('message', (ev) => {
      const d = String(ev.data);
      if (d.length > 40) window.__odds.push(d);   // frames grandes = odds
    });
    return ws;
  };
  window.WebSocket.prototype = OW.prototype;
  Object.getOwnPropertyNames(OW).forEach(k => { try { window.WebSocket[k] = OW[k]; } catch (e) {} });
  return 'GANCHO OK — agora abra uma liga (ex: Série A) e clique em 1-2 jogos';
})()
```

## Passo 2 — gerar tráfego

Sem recarregar a página: clique numa **liga** (ex.: Série A) e abra **1 ou 2
jogos**, com as abas de mercado (Popular, etc.). Espere uns 8 segundos.

## Passo 3 — copiar os frames pra mim

Cole no Console e Enter (ele copia pro clipboard):

```js
(() => {
  const f = window.__odds || [];
  const amostras = f.filter(x => x.length > 40).slice(-4).map(x =>
    x.replace(/[\x00-\x1f]/g, c => '<' + c.charCodeAt(0) + '>').slice(0, 900));
  const dump = { total: f.length, tamanhos: f.map(x => x.length).slice(-12), amostras };
  copy(JSON.stringify(dump, null, 1));
  return { total: f.length, preview: (amostras[0] || '').slice(0, 200) };
})()
```

Depois **cole aqui pra mim** (Ctrl+V) o que foi copiado.

## O que eu faço com isso

Com os frames reais eu escrevo o **decodificador do protocolo zap** e ligo a
extensão para interceptar o WebSocket — aí a Bet365 passa a mandar TODAS as odds
que a tela recebe (todos os jogos, todos os mercados), de forma robusta e
automática, sem clicar de jogo em jogo e sem quebrar quando eles mudam o layout.
