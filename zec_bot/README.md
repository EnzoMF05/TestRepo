# ZEC day-trading bot (sinais no Telegram)

Robô em Python que vigia o **ZEC perp na Hyperliquid** e te manda sinais de day trading para o **Telegram**,
com o **funding** e o **open interest** de cada momento. **Só emite sinais — não executa ordens** e não
precisa de chaves de API (usa a API pública e gratuita da Hyperliquid; o Coinalyze é opcional).
Acompanha cada sinal como um trade virtual e avisa quando toca no TP1, no TP2 ou no stop.

> ⚠️ **Lê a secção [Limitações](#limitações-e-avisos-honestos) antes de arriscares dinheiro.**
> A estratégia é um ponto de partida sensato, **não foi validada com dados reais de ZEC**.

## Como decide (resumo)

Gráfico de **15m** para entradas, **1h** como filtro de tendência. Só opera a favor da tendência de 1h.

| | LONG | SHORT |
|---|---|---|
| Tendência 1h | preço > EMA55 e EMA21 > EMA55 | o inverso |
| Filtro de força | ADX(14) ≥ 18 no 15m (no pullback, também EMA21 > EMA55 no 15m) | igual (EMA21 < EMA55) |
| **Pullback** | recuo até à EMA21 e fecho de volta acima com candle verde, RSI a subir (40–68), acima do VWAP diário | espelho |
| **Breakout** | fecho acima do máximo das últimas 20 barras, volume ≥ 1.3× média, fecho na parte alta da barra, RSI ≤ 78, acima do VWAP, sem estar esticado (> 2.5 ATR da EMA21) | espelho |
| Stop | mínimo/máximo das últimas 6 barras ± 0.2 ATR (entre 0.8 e 2.5 ATR, e ≥ 0.5% do preço) | espelho |
| Alvos | TP1 = 1.5R (fecha 50%, stop passa a breakeven), TP2 = 3R | espelho |

Regras de disciplina: **1 trade de cada vez**, cooldown de 4 barras entre sinais, máx. **4 sinais/dia**,
e **para o dia** ao atingir **−3R**. Saída a mercado ao fim de 12h.

Cada mensagem traz entrada, stop, alvos, **tamanho sugerido** (para arriscares `RISK_PCT` da `ACCOUNT_SIZE`)
e um limite de preço para não perseguires a entrada.

## Hyperliquid, funding e OI

- **Preços:** os candles vêm da própria Hyperliquid, por isso entrada/stop/alvos batem com o teu gráfico.
  Se a Hyperliquid falhar, o robô **não** usa outra exchange (níveis de outra exchange não servem); avisa-te
  no Telegram ao fim de 10 falhas seguidas. (`ALLOW_FALLBACK=true` liga a reserva, por tua conta e risco.)
- **Em cada sinal** aparece: funding (em %/8h, equivalente à convenção da Binance/Bybit; a Hyperliquid cobra de hora a hora), prémio, OI em $, variação do OI
  na última 1h e 4h, e uma leitura preço+OI (ex.: "preço↑ + OI↑: entram longs novos").
  Avisa também se o funding estiver muito contra o teu lado (squeeze) e se o tamanho sugerido excede a
  alavancagem máxima do ZEC na Hyperliquid.
- **De onde vem o OI histórico:** a Hyperliquid só dá o OI *atual*. O robô guarda amostras de 5 em 5 minutos
  (no ficheiro de estado) e calcula a variação a partir delas, por isso **a variação de 1h/4h só aparece depois
  de 1h/4h a correr**. Com `COINALYZE_API_KEY` (chave gratuita) aparece logo desde o arranque.
- **Filtros opcionais, DESLIGADOS por defeito** (`FUNDING_FILTER`, `OI_FILTER`): bloqueiam longs com funding
  muito alto / shorts com funding muito negativo, e rupturas sem OI a subir. São regras de bolso populares,
  **não há aqui evidência de que melhorem o resultado no ZEC**. Testa-as antes:

```bash
python -m zec_bot.backtest --funding-filter    # corre COM e SEM o filtro no mesmo período e compara
python -m zec_bot.backtest --oi-filter         # precisa de COINALYZE_API_KEY; só cobre ~2-3 semanas
```

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
5. (Opcional) `COINALYZE_API_KEY=...` — a chave gratuita da tua conta Coinalyze.

Se preferires reutilizar o bot que já tens, podes (o envio de mensagens não interfere), mas **mantém
`ENABLE_COMMANDS=false`**: dois programas a ler os comandos do mesmo bot roubam mensagens um ao outro.

### Primeiro teste

```bash
python -m zec_bot --check
```

Confirma que consegue ir buscar os candles à Hyperliquid, mostra o estado atual do mercado, o funding e o OI
(e, se tiveres chave, o do Coinalyze, com o símbolo que usou — **compara os números com o site**) e manda uma
mensagem de teste para o Telegram. **Se isto falhar, resolve antes de avançar** (o erro diz o que falhou).

## Antes de confiares nele: corre o backtest

```bash
python -m zec_bot.backtest                                       # candles da Hyperliquid: ~52 dias
python -m zec_bot.backtest --days 365 --exchange binance --save-csv zec_15m.csv   # histórico longo
python -m zec_bot.backtest --csv zec_15m.csv --side long         # repete sem voltar a descarregar
```

**Limite importante:** a Hyperliquid só guarda as **últimas 5000 barras** (≈ 52 dias em 15m). Para um teste mais
longo usa `--exchange binance` (ZEC/USDT): os preços são muito parecidos mas não idênticos, e o funding/OI
podem diferir. O ideal é fazer os dois: histórico longo na Binance para ver se a ideia aguenta vários regimes,
e o período recente na Hyperliquid para confirmar.

O backtest usa **exatamente o mesmo motor** do robô live, com comissões e derrapagem incluídas
(`FEE_PCT` = 0.045%, o taker base da Hyperliquid; `SLIPPAGE_PCT`), e mostra os resultados por setup, por lado e em **duas metades do período**.

Como o ler:
- **R** = unidade de risco (1R = distância entrada→stop). `+20R` a 1% de risco ≈ +20% da conta (sem compor).
- Se só **uma** das metades é lucrativa, o resultado depende do período — não é "edge".
- Com menos de ~100 trades a amostra é fraca. Em 52 dias de Hyperliquid vais ter poucos: usa também `--days 365` na Binance.
- **Não afines parâmetros até a curva ficar bonita**: isso é sobre-ajuste e falha ao vivo.
  Se mudares alguma coisa, faz-o com uma razão e confirma nas duas metades.
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

`/status` (preço, trade aberto) · `/stats` (resultados) · `/pause` / `/resume` (pausar sinais novos).
Só respondem ao teu `TELEGRAM_CHAT_ID`.

## Configuração

Tudo no `.env` (ver `.env.example`). Os mais úteis: `SIDE`, `ACCOUNT_SIZE`, `RISK_PCT`, `FEE_PCT` (ajusta ao teu nível/desconto de HYPE), `ACTIVE_HOURS_UTC`
(ex.: `7-22` para ignorar a madrugada, se descobrires que é pior), `MAX_SIGNALS_PER_DAY`, `DAILY_STOP_R`.
Os parâmetros da estratégia estão em `zec_bot/strategy.py` (`Params`).

## Testes

```bash
pip install pytest && python -m pytest
```

Cobrem: ausência de *lookahead* (os sinais numa barra não mudam com dados futuros), um controlo em
passeio aleatório (sem tendência não pode haver lucro), a gestão do trade (stop/TP1/TP2/breakeven, casos
ambíguos no pior cenário), as regras diárias, os filtros de funding/OI, os parsers das APIs e que **o robô
live e o backtest abrem exatamente os mesmos trades**. Também garantem que funding e OI são alinhados com
as barras **sem usar informação futura**, e que nenhum teste faz chamadas de rede reais.

## Limitações e avisos honestos

- **Não validado em dados reais.** Foi desenvolvido num ambiente sem acesso às exchanges, por isso foi
  testado com dados sintéticos e respostas de API simuladas. Os parâmetros são pontos de partida, não
  resultados de otimização. O primeiro `--check` e o primeiro backtest no teu Mac são a validação a sério.
- **As chamadas às APIs (Hyperliquid, Coinalyze, exchanges) não foram testadas contra os servidores reais**,
  só contra respostas simuladas no formato documentado. Os formatos da Hyperliquid (`candleSnapshot`,
  `metaAndAssetCtxs`, `fundingHistory`) foram confirmados na documentação; do Coinalyze confirmei a autenticação,
  o formato da resposta e os limites, mas **os nomes exatos dos parâmetros (`symbols`, `interval`, `from`/`to`
  em segundos) e a descoberta do símbolo da Hyperliquid vêm da minha leitura da documentação e podem precisar
  de um ajuste** — o `--check` diz-te logo; se o símbolo estiver errado, define `COINALYZE_SYMBOL`.
  O robô nunca pára por falhas de derivados: os sinais saem na mesma, só sem essas linhas.
- **O OI do Coinalyze pode estar em unidades diferentes do da Hyperliquid** (moeda vs. $): por isso só se
  comparam variações % dentro da mesma fonte, e a mensagem indica quando vem do Coinalyze.
- **Um backtest bom não garante lucro.** O ZEC é volátil e depende de regimes (tendências fortes, notícias,
  listagens/delistagens). Os custos e a execução real (derrapagem, latência entre o sinal e a tua ordem)
  costumam ser piores do que o assumido.
- Os resultados do robô assumem entrada ao preço de fecho da barra do sinal; quando vês a mensagem já
  passaram alguns segundos. Respeita o limite de preço da mensagem.
- Em barras onde o preço toca no stop e no alvo, assume-se sempre o **pior caso** (stop primeiro).
- Se operares com alavancagem, o "tamanho sugerido" mostra a alavancagem implícita: confirma que o stop
  fica bem longe da liquidação.
- **Não é aconselhamento financeiro.** Só arrisca dinheiro que possas perder.
