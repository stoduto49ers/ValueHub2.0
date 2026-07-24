# Bet365 — dump do JOGO aberto (para acertar o leitor de odds)

A varredura já navega pelos jogos. Falta o **leitor** acertar como a Bet365
mostra as odds no layout atual (as classes mudam). Preciso de UM dump da página
de um jogo aberto — com isso escrevo o extrator exato.

## Passo a passo

1. Na **Bet365**, **abra um jogo** (clique num jogo até ver os mercados:
   Resultado Final, Mais/Menos, etc.).
2. F12 → **Console**. Cole e Enter:

```js
(() => {
  const out = [];
  const arias = [...document.querySelectorAll('[aria-label]')]
    .map(e => (e.getAttribute('aria-label') || '').trim())
    .filter(a => /\d\.\d|odds|bet on|aposta/i.test(a));
  out.push('ARIAS(' + arias.length + '): ' + arias.slice(0, 8).join('  ||  '));

  const ODD = /^\d{1,3}\.\d{1,2}$/;
  const odds = [...document.querySelectorAll('div,span')]
    .filter(e => e.childElementCount === 0 && ODD.test((e.textContent || '').trim()) && e.offsetParent);
  out.push('ODDS_NA_TELA: ' + odds.length);

  if (odds.length) {
    let n = odds[0], c = [];
    for (let i = 0; i < 7 && n; i++) { c.push(n.tagName + '.' + ((n.className || '') + '').trim().replace(/\s+/g, '.').slice(0, 34)); n = n.parentElement; }
    out.push('CADEIA_ODD: ' + c.join('  >  '));
    const card = odds[0].closest('div');
    const up = card && card.parentElement && card.parentElement.parentElement;
    out.push('TEXTO_AO_REDOR: ' + (up ? up.textContent.replace(/\s+/g, ' ').slice(0, 120) : ''));
  }

  const titulos = [...document.querySelectorAll('[role="heading"],[class*="Header" i],[class*="Title" i]')]
    .map(e => (e.textContent || '').replace(/\s+/g, ' ').trim())
    .filter(t => t && t.length > 2 && t.length < 45);
  out.push('TITULOS(' + titulos.length + '): ' + [...new Set(titulos)].slice(0, 8).join('  |  '));

  return out.join('\n');
})()
```

3. Ele devolve **texto puro**. Copia o que aparecer e cola aqui pra mim.

Com o `ARIAS` (se existir "Bet on X with odds Y") eu ligo o leitor por aria-label
(robusto, imune a mudança de classe). Se não houver aria, uso a `CADEIA_ODD` +
`TITULOS` para escrever o extrator estrutural exato. Uma vez e fecha.

## Enquanto isso — o que já mudou

- **W/L/P (planilhar)**: era cache do navegador. O servidor agora manda os
  arquivos sem cache. Dê **Ctrl+F5** no painel uma vez e os botões funcionam.
- **Bet365 da odds-api removida**: só o FanDuel (props) vem da odds-api agora.
- **Varredura**: cole as URLs de **jogo** (as que têm `/E<id>/`, como as que
  você já colou) — a extensão navega e captura cada uma. Se o leitor ainda não
  bater com o layout, o dump acima resolve.
