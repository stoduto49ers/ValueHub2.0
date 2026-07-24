# Como rodar o Value HUB + resolver a Bet365

## Entenda: são DUAS coisas separadas

1. **O servidor** (o "cérebro", em Python) — roda no seu PC, faz o scraping da
   Pinnacle/Betano e os cruzamentos. **Não há "python.py"**: o comando já está
   pronto no `run.bat`.
2. **A extensão do Chrome** (JavaScript) — só serve para a **Bet365** (que a
   gente não consegue raspar do servidor por causa do Cloudflare). Ela lê as
   odds da tela que você já abriu e manda para o servidor.

A Betano, a Pinnacle e o FanDuel **não** precisam da extensão — o servidor faz
sozinho. A extensão é **exclusivamente para a Bet365**.

---

## PASSO 1 — Ligar o servidor

Dê dois cliques em **`run.bat`** (na pasta do projeto). Vai abrir uma janela
preta e, depois de alguns segundos, o painel fica disponível em:

> http://localhost:8000

Deixe essa janela aberta enquanto usar o HUB. (Se quiser pelo terminal:
`C:\Users\stodu\.conda\envs\nfl_env\python.exe -m valuehub`)

---

## PASSO 2 — Carregar a extensão no Chrome (só uma vez)

1. Abra o Chrome e vá em **`chrome://extensions`** (digite na barra de endereço).
2. No canto superior direito, ligue o **"Modo do desenvolvedor"**.
3. Clique em **"Carregar sem compactação"** (Load unpacked).
4. Selecione a pasta:
   `C:\Users\stodu\OneDrive\Área de Trabalho\ValueHub2.0\extension`
5. Deve aparecer o cartão **"Value HUB — Extrator de Odds"**. Pronto.

> Se aparecer algum erro em vermelho no cartão, me mande o texto — eu corrijo.

---

## PASSO 3 — Usar na Bet365

1. Com o servidor ligado (Passo 1), abra **https://www.bet365.bet.br**.
2. Passe o **"não sou robô" / Cloudflare** normalmente (é você, humano — isso
   é legítimo).
3. Navegue até um **jogo** (clique num evento para ver os mercados).
4. A extensão captura sozinha o que estiver na tela e envia ao servidor.

### Como saber se está funcionando (o indicador ao vivo)

Abra o painel (http://localhost:8000) numa outra aba. Na **barra de status do
topo** vai aparecer algo como:

> 📡 bet365: 24 mercados (há 3s)

Se aparecer isso, **está capturando** — os value bets da Bet365 vão surgir nas
abas *Value Bets* / *Value Props* conforme baterem contra as sharps.

---

## Se a Bet365 NÃO aparecer no indicador

A Bet365 muda os nomes internos das classes do site de tempos em tempos, então
os "seletores" que a extensão usa podem ter mudado. Se depois de abrir um jogo
o indicador **não** mostrar a bet365, me diga e a gente conserta assim:

1. Na página do jogo da Bet365, aperte **F12** (abre as ferramentas do
   desenvolvedor) → aba **Console**.
2. Cole este comando e aperte Enter:
   ```js
   copy(document.querySelector('.gl-MarketGroupPod')?.outerHTML || 'NAO ACHOU')
   ```
3. Isso copia um pedaço do HTML. Cole aqui pra mim (Ctrl+V numa mensagem).
   Com isso eu atualizo os seletores da extensão para o layout atual da Bet365.

> Observação honesta: eu não consigo carregar a extensão do meu lado para
> testar — o carregamento no Chrome só você faz. Toda a lógica do servidor
> (o cruzamento das odds da Bet365 contra as sharps) já está testada e
> funcionando; o que pode precisar de ajuste é só a leitura do DOM da Bet365,
> que a gente resolve com o HTML que você copiar.
