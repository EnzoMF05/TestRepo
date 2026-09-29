# ZEC day-trading bot (sinais no Telegram)

Robô em Python que vigia o **ZEC** e te manda sinais de day trading para o **Telegram**.
**Só emite sinais — não executa ordens** e não precisa de chaves de API de nenhuma exchange
(usa apenas dados públicos). Acompanha cada sinal como um trade virtual e avisa quando toca no
TP1, no TP2 ou no stop.

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

Confirma que consegue ir buscar dados à exchange, mostra o estado atual do mercado e manda uma
mensagem de teste para o Telegram. **Se isto falhar, resolve antes de avançar** (o erro diz qual exchange falhou).

## Antes de confiares nele: corre o backtest

```bash
python -m zec_bot.backtest --days 180 --save-csv zec_15m.csv    # descarrega e testa
python -m zec_bot.backtest --csv zec_15m.csv --side long         # repete sem voltar a descarregar
```

O backtest usa **exatamente o mesmo motor** do robô live, com comissões e derrapagem incluídas
(`FEE_PCT`, `SLIPPAGE_PCT`), e mostra os resultados por setup, por lado e em **duas metades do período**.

Como o ler:
- **R** = unidade de risco (1R = distância entrada→stop). `+20R` a 1% de risco ≈ +20% da conta (sem compor).
- Se só **uma** das metades é lucrativa, o resultado depende do período — não é "edge".
- Com menos de ~100 trades a amostra é fraca. Testa 6–12 meses (`--days 365`).
- **Não afines parâmetros até a curva ficar bonita**: isso é sobre-ajuste e falha ao vivo.
  Se mudares alguma coisa, faz-o com uma razão e confirma nas duas metades.
- Se `SIDE=both` mas só operas spot, testa com `--side long`.

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

Tudo no `.env` (ver `.env.example`). Os mais úteis: `SIDE`, `ACCOUNT_SIZE`, `RISK_PCT`, `ACTIVE_HOURS_UTC`
(ex.: `7-22` para ignorar a madrugada, se descobrires que é pior), `MAX_SIGNALS_PER_DAY`, `DAILY_STOP_R`.
Os parâmetros da estratégia estão em `zec_bot/strategy.py` (`Params`).

## Testes

```bash
pip install pytest && python -m pytest
```

Cobrem: ausência de *lookahead* (os sinais numa barra não mudam com dados futuros), um controlo em
passeio aleatório (sem tendência não pode haver lucro), a gestão do trade (stop/TP1/TP2/breakeven, casos
ambíguos no pior cenário), as regras diárias, os parsers das APIs e que **o robô live e o backtest abrem
exatamente os mesmos trades**.

## Limitações e avisos honestos

- **Não validado em dados reais.** Foi desenvolvido num ambiente sem acesso às exchanges, por isso foi
  testado com dados sintéticos e respostas de API simuladas. Os parâmetros são pontos de partida, não
  resultados de otimização. O primeiro `--check` e o primeiro backtest no teu Mac são a validação a sério.
- **As chamadas às APIs das exchanges não foram testadas contra os servidores reais** (só contra o formato
  documentado). Se uma exchange mudar o formato, o robô tenta automaticamente as outras e avisa-te no Telegram
  se ficar sem dados.
- **Um backtest bom não garante lucro.** O ZEC é volátil e depende de regimes (tendências fortes, notícias,
  listagens/delistagens). Os custos e a execução real (derrapagem, latência entre o sinal e a tua ordem)
  costumam ser piores do que o assumido.
- Os resultados do robô assumem entrada ao preço de fecho da barra do sinal; quando vês a mensagem já
  passaram alguns segundos. Respeita o limite de preço da mensagem.
- Em barras onde o preço toca no stop e no alvo, assume-se sempre o **pior caso** (stop primeiro).
- Se operares com alavancagem, o "tamanho sugerido" mostra a alavancagem implícita: confirma que o stop
  fica bem longe da liquidação.
- **Não é aconselhamento financeiro.** Só arrisca dinheiro que possas perder.
