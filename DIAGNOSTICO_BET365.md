# Diagnóstico da Bet365 — para acertar a varredura automática

Eu **não consigo carregar a lista de jogos da Bet365 do meu lado** (ela bloqueia
automação no meu ambiente). No **seu** navegador ela funciona. Então preciso que
você me diga a estrutura real dela — leva 30 segundos.

## Passo a passo

1. Abra a **Bet365** e entre na **lista de uma liga** (ex.: clique em "Série A" e
   veja a lista de jogos com as odds — NÃO entre num jogo, fique na lista).
2. Aperte **F12** → aba **Console**.
3. Cole o comando abaixo e aperte **Enter**:

```js
(() => {
  const odd = [...document.querySelectorAll('*')].find(e => e.children.length===0 && /^\d{1,2}\.\d{2}$/.test((e.textContent||'').trim()));
  const out = { temOdds: !!odd };
  if (odd) {
    let n = odd, chain = [];
    for (let i=0;i<8 && n;i++){ chain.push(n.tagName+'.'+((n.className||'')+'').trim().replace(/\s+/g,'.').slice(0,45)); n = n.parentElement; }
    out.cadeiaDaOdd = chain;
    // procura o container do jogo (tem 2 nomes de time + odds)
    const fix = odd.closest('[class]');
    out.textoDoJogo = fix ? fix.parentElement?.parentElement?.textContent.replace(/\s+/g,' ').slice(0,80) : '';
  }
  // classes candidatas a "jogo clicável"
  const pref = {};
  document.querySelectorAll('[class]').forEach(e => (e.className||'').toString().split(' ').forEach(c => {
    if (/Fixture|Coupon|Participant|Event|Match/i.test(c)) pref[c]=(pref[c]||0)+1;
  }));
  out.classesCandidatas = Object.entries(pref).sort((a,b)=>b[1]-a[1]).slice(0,12);
  copy(JSON.stringify(out, null, 1));
  return out;
})()
```

4. Isso **copia o resultado** para a área de transferência. **Cole aqui pra mim**
   (Ctrl+V numa mensagem).

Com isso eu escrevo os seletores EXATOS da varredura e ela passa a clicar de jogo
em jogo sozinha — sem você trocar na mão.

## Enquanto isso

Já deixei a extensão tentando **auto-iniciar** a varredura quando detecta a lista
(sem precisar do botão). Se os seletores atuais baterem com o layout da sua
Bet365, ela já vai varrer sozinha. O comando acima é o plano B para o caso de os
seletores estarem diferentes.
