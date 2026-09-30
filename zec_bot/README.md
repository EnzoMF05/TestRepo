# ZEC day-trading bot (sinais de reversão no Telegram)

Robô em Python que vigia o **ZEC perp na Hyperliquid** e te manda sinais de day trading para o **Telegram**.
**Só emite sinais — não executa ordens** e não precisa de chaves de exchange. Tem dois métodos principais:

- **`STRATEGY=sweep` — o método da tua mesa (CT-NÍVEL)**: sweep de nível + pavio + queda de OI, ordem LIMIT na face.
  É um port do `hourly_scan.py` para um só ativo, verificado contra esse código. **É o que reflete a tua forma de operar.**
- **`STRATEGY=reversal` — reversão por funding extremo da Binance**: foi a minha leitura de três linhas tuas antes de
  ver o teu código. Fica como alternativa/complemento; não é o método da mesa (o funding nem entra nas regras dela).

`STRATEGY=trend` e `both` (tendência, e reversal+trend) também existem.

> ⚠️ **Lê a secção [Limitações](#limitações-e-avisos-honestos) antes de arriscares dinheiro.**
> As regras são as que me descreveste; os números que as definem são propostas minhas e **nada foi validado
> com dados reais de ZEC**.

## Modo `sweep`: o método da mesa (CT-NÍVEL), para o ZEC

Às **:02 de cada hora** avalia a vela 1H que acabou de fechar contra os níveis do teu `niveis.csv`:

| Tier | Condição |
|---|---|
| **L0** | a vela varre um nível (`low <` nível de `cluster_below`, ou `high >` nível de `cluster_above`) |
| **L1** | L0 + pavio de rejeição **≥ 50%** da amplitude da vela |
| **L2** | L1 + OI da hora **≤ −3%** (delta `(close−open)/open` da barra de OI 1H: CoinGlass, senão Coinalyze; ambos do perp da Binance) |

Só o **L2** gera alerta: **ordem LIMIT na face do nível** (nunca a mercado), stop no extremo da vela ∓ 0.1 ATR14,
**alvo 1.5R** (o R inclui os custos: maker 0.015% + 0.02% de derrapagem por lado), e **veto** se `|limite − stop| > 1.2 ATR14`.
Prazos: cancela o limite sem fill às 2h e o trade deixa de valer às 4h. Uma ordem de cada vez.

Em paper, o robô **acompanha cada ordem até ao fim** (fill na face, alvo, stop, fim da validade ou cancelamento) e escreve
o resultado em **R** em `zec_sweep_results.csv`. O `zec_sweep_journal.csv` tem o **mesmo cabeçalho do journal da mesa**
(uma linha por hora avaliada: L0/L1/L2, plano A vs B...), por isso dá para comparar as linhas do ZEC com as do Sniper.

Para usar:

```bash
cp /caminho/da/mesa/niveis.csv ./niveis.csv     # ticker,cluster_below,cluster_above,spot_approx,atualizado_utc
# no .env:  STRATEGY=sweep   COINGLASS_API_KEY=...   COINALYZE_API_KEY=...   (pelo menos uma das chaves de OI)
python -m zec_bot --check                        # mostra níveis, OI da última vela 1H fechada e a avaliação dela
python -m zec_bot
```

O robô lê o `niveis.csv` **de disco a cada hora**: mantém a cópia atualizada (o teu feeder corre noutra máquina).
Sem níveis ou sem OI não há alertas (avisa-te no Telegram uma vez): tal como na mesa, sem OI a vela fica em L1.

**Verificação contra o teu código.** `tests/data/sweep_golden.json` tem 426 casos (velas, níveis, ATR e OI aleatórios,
mais casos-limite exatos como OI = −3.0 ou pavio = 50.0) com as saídas do teu `evaluate()`; o port dá exatamente os
mesmos números. Também bate com os teus `atr14()` e `parse_levels()`. Os sensores de OI seguem o teu código
(mesmos URLs, cabeçalhos, parâmetros e regra de escolha da vela), mas **não foram testados contra os serviços reais**.

**Onde me afasto do original (de propósito) e o que encontrei nele:**

1. **`SWEEP_EXIGE_RECLAIM` não fazia nada.** Ligado, só acrescentava `sem_reclaim` ao texto e o sinal continuava válido
   (o teu `--selftest` falha com ele ligado). Aqui `SWEEP_REQUIRE_RECLAIM=true` invalida mesmo o sinal. Desligado (o
   defeito, a regra da mesa) o comportamento é idêntico ao original.
2. **O "LIMIT na face" pode ser uma ordem a mercado.** Sem reclaim, o fecho fica do lado errado do nível: um limite de compra
   *acima* do preço executa logo. O alerta avisa disso e, no paper, assume-se fill imediato **ao preço do limite** (pior caso).
3. **Prazos.** No original, `cancel_at` e `valid_until` contam desde a **abertura** da vela varrida, não desde o alerta
   (que sai ~1h depois): o "cancel no-fill 2×1H" dá só ~1h para o limite executar e a "validade 4×1H" ~3h. Mantive esse
   comportamento por defeito; `SWEEP_COUNT_FROM_ALERT=true` conta desde o alerta. **Confirma qual é a tua intenção.**
4. **A "face" depende da ordem da lista** de níveis (um evento por lado: o 1º varrido na ordem do CSV). Igual ao original;
   convém o feeder escrever os níveis por proximidade ao preço.
5. **Não há backtest do sweep**: não existe histórico dos níveis de cluster de cada hora. A evidência acumula-se em paper
   (journal + resultados). `python -m zec_bot.backtest` recusa `STRATEGY=sweep` e explica porquê.
6. **O ATR14 inclui a própria vela varrida**, o que o infla e torna o veto de R menos provável (igual ao original).

**Simulação em paper (pior caso):** fill quando o preço toca a face; se na barra do fill também tocou no stop, conta o stop;
numa barra com stop e alvo, ganha o stop; ao fim da validade fecha a mercado ao fecho da barra. Usa barras de 15m para
acompanhar a ordem, mas avalia sempre a vela 1H completa (agregada das 4 barras).

## Modo `reversal`: reversão por funding da Binance (a minha leitura, não o método da mesa)

Traduzi assim as tuas três regras verbais (funding e percentis são ideia minha; o Sniper usa OI e níveis). Um sinal só sai quando **as três condições coincidem** numa barra de 15m fechada:

| Condição | SHORT (longs sobrelotados) | LONG (shorts sobrelotados) |
|---|---|---|
| **1. Mercado carregado** — funding do perp ZECUSDT da Binance, em %/8h | funding **≥ P90** dos últimos 30 dias *e* **≥ +0.03%** | funding **≤ P5** dos últimos 30 dias *e* **≤ −0.02%** ("extremamente") |
| **2. Preço esticado** ("caro"/"barato") | nas últimas 3 barras, **≥ 2.5 ATR acima da EMA55** e RSI **≥ 65** | nas últimas 3 barras, **≥ 2.5 ATR abaixo da EMA55** e RSI **≤ 35** |
| **3. Gatilho de rejeição** | candle vermelho com pavio de topo ≥ 35% da barra (ou a fechar abaixo da mínima anterior) e RSI a descer | candle verde com pavio de fundo ≥ 35% (ou a fechar acima da máxima anterior) e RSI a subir |
| Stop | acima do máximo das últimas 8 barras + 0.2 ATR | abaixo do mínimo das últimas 8 barras − 0.2 ATR |
| Alvos | TP1 = **1R** (fecha 50% e stop a breakeven), TP2 = **2R** | igual |

- **Nunca entra só porque "está caro"**: exige o candle de rejeição (senão seria apanhar uma faca a cair).
- O **percentil** compara o funding de agora com os últimos 30 dias *do próprio ZEC*, por isso não tenho de
  adivinhar o que é "alto" neste ativo; o **mínimo absoluto** impede que um funding perto de zero conte como
  extremo. A base da Binance é +0.01%/8h. O intervalo de funding (8h/4h/1h) é deduzido dos próprios dados.
- **"Evito long quando já está caro":** na reversão os longs só existem quando o preço está *barato* (condição 2).
  Se usares `STRATEGY=trend` ou `both`, a regra `AVOID_EXPENSIVE_LONGS=true` trava os longs de tendência com o
  preço a > 2 ATR da EMA55 ou RSI ≥ 70.
- **Variante com OI:** `REV_USE_OI=true` exige também que o OI tenha **subido ≥ 3% nas últimas 24h** (posições a
  acumular). Está desligada porque só se consegue testar ~30 dias (limite da Binance).
- **Frequência:** o LONG exige o funding na cauda de 5% *e* negativo, o que na Binance é raro. Espera **poucos
  sinais**, sobretudo de long. O backtest diz-te quantos.

Disciplina (igual à versão anterior): **1 trade de cada vez**, cooldown de 4 barras, máx. **4 sinais/dia**, e
**para o dia** ao atingir **−3R**. Saída a mercado ao fim de 12h.

Exemplo de mensagem (gerada pelo próprio robô com dados **simulados**; os números não são reais):

```
🔴 ZEC SHORT — Reversão (longs sobrelotados) (15m)
11/01 09:45

Entrada: 52.90
Stop: 53.51 (1.15%)
TP1: 52.29 (1.0R) → fechar 50% e stop a breakeven
TP2: 51.69 (2.0R)

Tamanho (risco 1% de 2,000$): 33.00 ZEC ≈ 1,746$ (~0.9x)
Porquê: funding +0.062%/8h (P100 dos últimos 30d) · preço 9.4 ATR acima da EMA55 · RSI pico 93 · candle de rejeição
Contexto: 1h em alta (contra-tendência) · ADX 56 · RSI 89
Funding Binance: +0.0620%/8h (longs pagam) · P100 dos últimos 30d · Hyperliquid +0.0100%/8h
OI Binance: 50.00M$ · 1h +4.17% · 4h +4.17% · 24h +4.17% · prémio +0.200%
Leitura: preço↑ + OI↑: entram longs novos
Não entrar se o preço já passou 52.72. Saída a mercado após 12h.
```

### Modo tendência (opcional)

`STRATEGY=trend` ou `both` ativa a estratégia anterior (a favor da tendência de 1h: pullback à EMA21 e rutura
com volume; TP1 1.5R / TP2 3R). Em `both` o relatório do backtest separa cada tipo. Os filtros de
funding/OI (`FUNDING_FILTER`, `OI_FILTER`) só se aplicam a este modo e estão desligados.

## De onde vêm os dados

- **Candles e preços:** Hyperliquid (onde operas), para os níveis baterem com o teu gráfico. Se falhar, o robô
  **não** usa outra exchange (`ALLOW_FALLBACK=true` liga essa reserva por tua conta e risco) e avisa-te ao fim
  de 10 falhas seguidas.
- **Funding e open interest:** perp **ZECUSDT da Binance** (API pública `fapi.binance.com`, sem chave): o mesmo
  mercado que vês no Coinalyze. Não preciso do Coinalyze, porque a Binance dá os mesmos dados diretamente.
  O funding da Hyperliquid (o que pagas) e a alavancagem máxima do ZEC aparecem só como informação.
- **Modo sweep:** os níveis vêm do teu `niveis.csv` (cópia local, relida a cada hora) e o OI da hora vem do
  CoinGlass 1H e/ou Coinalyze 1H (perp da Binance), como na mesa. O funding não entra nas regras do sweep: aparece
  no alerta só como contexto (Binance e Hyperliquid), se estiver disponível.
- O funding usado é o último **liquidado**; o Coinalyze pode mostrar o previsto/atual, por isso podes ver
  diferenças pequenas entre os dois.
- Se a Binance falhar, o robô usa o funding em cache (até 6h, porque muda devagar) e, passado isso, **desativa a
  reversão e avisa-te no Telegram**; nunca inventa crowding.

## Instalação no Mac

Precisas de Python 3.9+ (`python3 --version`).

```bash
git clone <o teu repositório> && cd TestRepo        # ou entra na pasta onde já o tens
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                  # e edita o .env (ver abaixo)
```

### Telegram

1. No Telegram, fala com **@BotFather** → `/newbot` → guarda o **token** (recomendo um bot *novo* só para o ZEC).
2. Abre o teu bot novo e envia-lhe qualquer mensagem (ex.: "olá").
3. Abre no browser `https://api.telegram.org/bot<TOKEN>/getUpdates` e copia o número de `"chat":{"id": ...}`.
4. Põe no `.env`: `TELEGRAM_TOKEN=...` e `TELEGRAM_CHAT_ID=...`.

Se preferires reutilizar o bot que já tens, podes (o envio de mensagens não interfere), mas **mantém
`ENABLE_COMMANDS=false`**: dois programas a ler os comandos do mesmo bot roubam mensagens um ao outro.

### Primeiro teste

```bash
python -m zec_bot --check
```

Confirma que consegue ir buscar os candles à Hyperliquid e o funding/OI à Binance; mostra os números
(**compara-os com o Coinalyze para ZECUSDT da Binance**), o percentil do funding e se, neste momento, o mercado
está "carregado de longs", "carregado de shorts" ou neutro; e manda uma mensagem de teste para o Telegram.
**Se algo falhar, resolve antes de avançar** (o erro diz o que falhou).

## Antes de confiares nele: corre o backtest

```bash
python -m zec_bot.backtest --days 365 --exchange binance --save-csv zec_15m.csv   # reversão, histórico longo
python -m zec_bot.backtest --csv zec_15m.csv --side short                          # só shorts, sem voltar a descarregar
python -m zec_bot.backtest                                                         # candles da Hyperliquid (~52 dias)
python -m zec_bot.backtest --strategy both --csv zec_15m.csv                       # reversão vs tendência, por tipo
python -m zec_bot.backtest --use-oi                                                # reversão exigindo OI (só ~30 dias)
```

Usa **exatamente o mesmo motor** do robô live (as mesmas funções calculam os features de funding e de OI), com
comissões e derrapagem incluídas (`FEE_PCT` = 0.045%, o taker base da Hyperliquid; `SLIPPAGE_PCT`).

**Limites dos dados** (importante para não tirares conclusões a mais):

| Dado | Quanto histórico dá |
|---|---|
| Candles da Hyperliquid | só as últimas 5000 barras (≈ 52 dias) |
| Candles da Binance (`--exchange binance`) | anos (preços muito parecidos com os da Hyperliquid, não idênticos) |
| Funding da Binance | anos; o backtest recua 33 dias antes da 1ª barra para o percentil ter a janela completa |
| Open interest da Binance | **só ~30 dias**: `--use-oi` e `--oi-filter` só testam isso |

Como o ler:
- **R** = unidade de risco (1R = distância entrada→stop). `+20R` a 1% de risco ≈ +20% da conta (sem compor).
- **Reversões são raras.** Com poucos trades (< ~100) a amostra é fraca: usa `--days 365` (ou mais) na Binance.
- Se só **uma** das metades do período é lucrativa, o resultado depende do período — não é "edge".
- **Não afines parâmetros até a curva ficar bonita**: isso é sobre-ajuste e falha ao vivo. Se mudares um limiar,
  faz-o por uma razão e confirma nas duas metades.
- Podes testar cada lado com `--side long` / `--side short`.

Idealmente, depois do backtest, deixa o robô correr **umas semanas só a observar os sinais** (sem dinheiro)
e compara com o backtest.

## Correr

```bash
source .venv/bin/activate
python -m zec_bot
```

Para não deixar o Mac adormecer enquanto corre: `caffeinate -i python -m zec_bot`.

### Arrancar sozinho e reiniciar se falhar (launchd)

```bash
sed "s#__REPO__#$PWD#g" deploy/com.zecbot.plist > ~/Library/LaunchAgents/com.zecbot.plist
plutil -lint ~/Library/LaunchAgents/com.zecbot.plist        # deve dizer OK
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.zecbot.plist
tail -f zec_bot.log                                          # ver o que está a fazer
# para parar:  launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.zecbot.plist
```

Notas: o Mac tem de estar ligado e com internet. Num portátil, fechar a tampa suspende-o mesmo com `caffeinate`.
Se o Mac adormecer e acordar, o robô continua os trades abertos com as barras em falta, mas **não envia sinais
antigos** (só sinais de barras fechadas há menos de 5 minutos).

## Comandos do Telegram (opcional: `ENABLE_COMMANDS=true`)

`/status` (preço, trade aberto, funding e OI) · `/stats` (resultados) · `/pause` / `/resume` (pausar sinais novos).
Só respondem ao teu `TELEGRAM_CHAT_ID`.

## Configuração

Tudo no `.env` (ver `.env.example`, onde cada opção está explicada). Os que mais vais querer mexer:

- **O que é "carregado":** `REV_SHORT_MIN_PCTL` (90) e `REV_SHORT_MIN_FUNDING_8H` (0.03) para os shorts;
  `REV_LONG_MAX_PCTL` (5) e `REV_LONG_MAX_FUNDING_8H` (−0.02) para os longs; `CROWD_WINDOW_DAYS` (30).
  **Estes números são propostas minhas** — diz-me o que consideras "muito" e "extremamente" carregado no ZEC
  (em funding e em OI) e ajusto os valores por defeito.
- `SIDE`, `ACCOUNT_SIZE`, `RISK_PCT`, `FEE_PCT` (ajusta ao teu nível/desconto de HYPE), `ACTIVE_HOURS_UTC`,
  `MAX_SIGNALS_PER_DAY`, `DAILY_STOP_R`.
- Os restantes parâmetros da estratégia (esticão de 2.5 ATR, RSI 65, pavio de 35%, alvos 1R/2R...) estão em
  `zec_bot/strategy.py` (`Params`).

## Testes

```bash
pip install pytest && python -m pytest
```

Cobrem, entre outras coisas: que **os sinais numa barra não mudam com dados futuros** (incluindo funding e OI),
cada parte do gatilho de reversão isolada (retirar qualquer condição faz um teste falhar), controlos em dados
aleatórios (sem informação real a estratégia tem de perder), a gestão do trade (stop/TP1/TP2/breakeven, casos
ambíguos no pior cenário), as regras diárias, os parsers das APIs, mensagens válidas para o Telegram, e que
**o robô live e o backtest abrem exatamente os mesmos trades** (tendência e reversão). Nenhum teste faz
chamadas de rede reais.

## Limitações e avisos honestos

- **Não validado em dados reais.** Foi desenvolvido num ambiente sem acesso às exchanges, por isso foi
  testado com dados sintéticos e respostas de API simuladas. **Não sei se o método da mesa, ou a minha reversão por
  funding, dão dinheiro em ZEC.** No sweep, a lógica de decisão bate certo com o teu código (verificado), mas isso
  prova fidelidade, não rentabilidade; e os limiares da reversão por funding são propostas minhas. O primeiro `--check` e o primeiro backtest no teu Mac são a
  validação a sério.
- **As chamadas às APIs não foram testadas contra os servidores reais**, só contra respostas simuladas no
  formato documentado. Confirmei os formatos na documentação da Hyperliquid (`candleSnapshot`,
  `metaAndAssetCtxs`) e da Binance (`fundingRate`, `premiumIndex`, `openInterestHist`, `fundingInfo`); o
  `openInterest` (OI atual) segue o formato que conheço. Os sensores de OI 1H (CoinGlass/Coinalyze) seguem o teu código
  de produção. Se algum falhar, o `--check` diz-te qual.
- **Sweep em paper:** o robô acompanha cada ordem com barras de 15m, o que é mais fino que 1H mas continua a ser uma
  simulação: o fill real depende da fila do teu livro, e a derrapagem do stop pode ser pior que os 0.02% assumidos.
- **Operar reversão tem um perfil de risco próprio:** o mercado pode continuar "carregado" durante dias e um
  squeeze ou uma tendência forte pode passar por cima do teu stop várias vezes seguidas. Funding extremo não é
  um sinal de viragem por si só — por isso o gatilho de rejeição e o stop são obrigatórios, e o limite diário de
  −3R existe. Espera taxas de acerto altas nalguns períodos e sequências de stops noutros.
- **Um backtest bom não garante lucro.** O ZEC é volátil e depende de regimes (notícias, listagens/delistagens).
  Os custos e a execução real (derrapagem, latência entre o sinal e a tua ordem) costumam ser piores do que o
  assumido. O funding e o OI são da Binance; o que pagas e o que executas é na Hyperliquid.
- Os resultados do robô assumem entrada ao preço de fecho da barra do sinal; quando vês a mensagem já
  passaram alguns segundos. Respeita o limite de preço da mensagem.
- Em barras onde o preço toca no stop e no alvo, assume-se sempre o **pior caso** (stop primeiro).
- Se operares com alavancagem, o "tamanho sugerido" mostra a alavancagem implícita e avisa se exceder o máximo
  do ZEC na Hyperliquid; confirma que o stop fica bem longe da liquidação.
- **Não é aconselhamento financeiro.** Só arrisca dinheiro que possas perder.
