# Estratégia de reversão na Hyperliquid (Etapa 1): especificação REVOU v2

> Documento de especificação produzido pelo painel de desenho (4 desenhos, 3 juízes, síntese) e implementado por `reversao.py`, `sinalizador.py` e `backtest_reversao.py`. A lista completa da API, dos parâmetros e dos testes acompanha-o em `docs/reversao-api.txt`.

## ESTRATEGIA_REVERSAO.md: REVOU, versão 2 (especificação final)

Este documento é a especificação que `reversao.py` (fórmulas puras), `sinalizador.py` (processo ao vivo), `backtest_reversao.py` (backtest por eventos), `config.ini` (secção `[sinalizador]`) e os testes implementam tal como está. Parte do desenho REVOU, vencedor dos três julgamentos, corrige todos os erros apontados e enxerta o que os juízes pediram dos outros três desenhos. Quando o texto e o código divergirem, manda o código e o erro é deste documento. Todas as fórmulas estão numeradas e a notação é única da secção 2 até ao fim.

---

## 0. Numa página

- **O sinalizador não escolhe a rota nem envia ordens.** Escreve uma linha em `dados/sinais.csv` no fecho da vela de 15 minutos, com `hora, ativo, lado, preco, alvo_bps, tamanho_usd, nota`, e o medidor mede-a como mede qualquer sinal. Nada mais é partilhado: o sinalizador nunca escreve nos ficheiros do medidor.
- **A tese é uma hipótese nula computável.** O desvio do log-preço a uma âncora exponencial de 1 hora é, se o preço for um passeio aleatório, um AR(1) com coeficiente exactamente igual ao factor da âncora, `phi = lambda_A`. Só há estratégia quando `phi` fica significativamente abaixo de `lambda_A`: a reversão que se negoceia é a reversão **em excesso** face ao passeio aleatório, e o factor de captura `rho` é zero, por construção, sob a nula.
- **Contexto na vela de 1 hora, gatilho no fecho da vela de 15 minutos.** O contexto diz se há reversão (teste contra a nula, rácio de variâncias, meia-vida útil, volatilidade sem choque). O gatilho é a **reentrada** na banda de `z_in` sigma de equilíbrio, depois do primeiro sinal de viragem, nunca durante a queda ou a subida. Com cascatas de liquidação em curso não se entra: a rajada forçada tem de ter acabado.
- **O alvo é uma esperança condicional**, não um nível desejado: o fecho esperado do desvio até ao stop por tempo, descontada a deriva da âncora. O stop é a banda de `z_stop`. O tempo máximo é `n_H` meias-vidas, com tecto de 16 horas.
- **As baleias confirmam e dimensionam; nunca disparam nem dirigem.** Oito medidas de fluxo e de posicionamento, cada uma com limiar congelado antes de medir, somam uma pontuação que só altera o factor de tamanho. Tudo fica na nota para o relatório cruzar com os resultados do medidor. Sem a chave da CoinGlass, cada termo que dela depende vale zero e o campo vale `na`.
- **Tamanho:** Kelly fraccionário nas unidades certas, risco por trade, volatilidade alvo e liquidez, o mínimo dos quatro, com tecto absoluto; em fase de calibração (menos de 30 trades virtuais fechados) escreve-se sempre `tamanho_base`, para o medidor acumular os 100 sinais da sua regra.
- **Tudo o que depende de velas tem backtest; tudo o que depende de negócios, posições e liquidações só se mede ao vivo**, e o sinalizador grava-o desde o primeiro dia para que, com o tempo, também tenha backtest.
- **Valores de partida:** `h_A = 96`, `N = 720`, `z_in = 2,0`, `z_out = 0,5`, `z_stop = 3,0`, `z_veto = 4,0`, `n_H = 2`, `H em [3, 24]`, `t_crit` calibrado (cerca de -3,3), `k_kelly = 0,25`, `risco_por_trade = 0,005`, `tamanho_base = 1000`. Tudo em `config.ini`, nunca no código.

---

## 1. Tese

### 1.1 O que se negoceia

O log-preço `x_t` de um perp afasta-se de uma âncora causal `A_t` (média exponencial de meia-vida longa) e volta. Isso é verdade para qualquer série: o próprio passeio aleatório "volta" à sua média móvel, porque a média corre atrás do preço. A esperança positiva não está em "estar fora da banda" mas em três coisas mensuráveis:

1. **O desvio reverte mais depressa do que o passeio aleatório explica.** Sob a nula, `d_t = x_t - A_t` é um AR(1) com `phi = lambda_A` (secção 4.2) e toda a reversão de `d` é deriva da âncora, com ganho zero. Só há estratégia quando o coeficiente estimado fica significativamente abaixo de `lambda_A`.
2. **A entrada faz-se na reentrada**, no fecho da vela de 15 minutos em que `|z|` volta para dentro da banda, já do mesmo lado e a aproximar-se da âncora. Isto evita comprar cascatas de liquidação em curso. Na Hyperliquid, uma posição acima de 100 000 USDC é liquidada parcialmente (20 por cento vai ao livro como ordem de mercado) e, nos 30 segundos de arrefecimento que se seguem, uma nova liquidação do mesmo utilizador é da posição inteira. A rajada é visível nos negócios e a regra exige que tenha acabado.
3. **O alvo é a esperança condicional do processo**, descontada a deriva da âncora e o que o processo não chega a percorrer até ao stop por tempo. O medidor usa 15 por cento desse alvo como orçamento de entrada.

A evidência externa (autocorrelação negativa de 1 a 4 horas em BTC depois de movimentos grandes; rajadas periódicas nos quartos de hora, que aconselham a entrada passiva) sustenta a escala e o instante. **Nada disto prova a estratégia em perps da Hyperliquid em 2026.** Isso é o que o walk-forward e os 100 sinais medidos pelo medidor dizem, e enquanto não disserem, o sinalizador escreve com tamanho de calibração.

### 1.2 O que a estratégia é e não é

Com `h_A = 96` horas e meia-vida útil entre 3 e 24 horas, `sigma_eq` mede oscilações de dias em torno de um valor de referência de vários dias. A estratégia é, portanto, de **intervalo** (mean reversion em regime de lateralização medida), não de reversão intradiária pura. Em tendência, o rácio de variâncias e o tecto da meia-vida fecham o portão, e um sinalizador fechado não prova nada: só o backtest e o relatório dizem se existe esperança.

---

## 2. Notação única

```
BPS = 1e4                      1 bp = 1e-4
lado em {+1 compra, -1 venda}
t   índice da vela de 1 hora;  k  índice da vela de 15 minutos
c, o, h, l, v, n               fecho, abertura, máximo, mínimo, volume (unidades do activo), n.º de negócios
x = ln c ; r_t = x_t - x_{t-1} (retorno log de 1 h) ; r_k = x_k - x_{k-1} (15 m)
Delta = 1 vela de 1 h
EWMA_h(y): s <- lambda s + (1 - lambda) y ; w <- lambda w + (1 - lambda) ; valor = s / w ; lambda = 2^(-1/h)
            (classe nucleo.Ewma, meia-vida h em velas, com correcção de arranque)
pos_W(y): posição percentil de y nas últimas W observações da própria medida, empates a meio
            (classe nucleo.JanelaPercentil)
mediana, MAD: MAD = mediana(|y - mediana(y)|)
nan = não se sabe; "na" na nota = não configurado
```

Custo positivo é custo; resultado positivo é a favor. Todos os preços que entram nas fórmulas são fechos de velas oficiais da Hyperliquid (canal `candle`), nunca preços construídos pelo sinalizador.

---

## 3. Dados

### 3.1 Fontes e campos exactos

| Fonte | Pedido | Campos usados | Uso |
| --- | --- | --- | --- |
| Hyperliquid WS | `{"type":"candle","coin":X,"interval":"15m"}` e `"1h"` | `t, T, s, i, o, c, h, l, v, n` | velas; fecho da vela quando chega `t` novo ou o relógio corrigido passa `T + folga` |
| Hyperliquid REST | `{"type":"candleSnapshot","req":{"coin","interval","startTime","endTime"}}` | os mesmos | histórico no arranque (até 5000 velas por intervalo: cerca de 208 dias de 1 h e 52 dias de 15 m); guardado em `dados/velas/` |
| Hyperliquid WS | `{"type":"trades","coin":X}` | `side (B/A), px, sz, time, tid, hash, users [comprador, vendedor]` | fluxo agressor, negócios grandes, endereços, rajadas, TWAP, HHI, CVD, atraso |
| Hyperliquid WS | `{"type":"activeAssetCtx","coin":X}` | `funding, openInterest, premium, markPx, oraclePx, dayNtlVlm` | funding, OI em dólares, prémio, liquidez |
| Hyperliquid REST | `{"type":"metaAndAssetCtxs"}` de 15 em 15 min | os mesmos | redundância do canal |
| Hyperliquid REST | `{"type":"fundingHistory","coin","startTime"}` | `fundingRate, premium, time` por hora | 30 dias para mediana e MAD do funding; custo de funding no backtest |
| Hyperliquid REST | `{"type":"meta"}`, `{"type":"vaultDetails","vaultAddress"}` | `szDecimals`, endereços do HLP | validação dos nomes; exclusões |
| Hyperliquid REST | `{"type":"clearinghouseState","user"}` (peso 2) | `assetPositions[].position.{coin, szi, positionValue, liquidationPx, leverage.value}` | posições das baleias, combustível, ímanes; tarefa própria de 15 em 15 min, no máximo 100 endereços |
| Hyperliquid GET | `stats-data.hyperliquid.xyz/Mainnet/leaderboard` (não documentado) | `ethAddress, accountValue, windowPerformances[{roi, pnl, vlm}]` | lista diária de baleias (alternativa sem chave) |
| CoinGlass (opcional) | WS `liquidationOrders` e REST `/api/futures/liquidation/order`; `/api/hyperliquid/whale-position`; `/api/hyperliquid/whale-alert` | `usd_value, side, time, price`; `user, symbol, position_value_usd, liq_price, position_action` | liquidações por lado; lista de baleias e liquidados recentes |
| medidor (só leitura) | `dados/estado.json`, `dados/registo_sinais.csv`, relatório | `ligado, hora_utc, estado, imp_ref_bps, qmax_usd_*, liq_long_usd, liq_short_usd`; `r_900, r_3600, r_14400`; `E, F, A, D` por rota | nota, tecto de liquidez, custos medidos, relatório cruzado |

Nenhum pedido REST corre no caminho crítico entre o fecho da vela e a escrita da linha: as tarefas de sonda guardam o último valor conhecido e a sua idade, e a avaliação lê memória. Não se subscreve `userFills` de terceiros: o limite de 10 utilizadores por IP já conta o endereço do utilizador que o medidor subscreve.

### 3.2 Hipóteses da API a confirmar com `sinalizador.py verificar`

- H1 Formato do canal `candle` e de `candleSnapshot` (campos `t, T, o, c, h, l, v, n`; `v` em unidades do activo).
- H2 Ordem de `users` nos negócios: `[comprador, vendedor]`. Teste: num negócio `side = B` o preço tem de ser maior ou igual à melhor venda do bbo do medidor nesse instante. Se falhar, o lado passa a derivar do tick (`px >= mid` é compra) e `verificar` avisa.
- H3 `openInterest` em unidades do activo (converte-se com `markPx`). Teste: `openInterest x markPx` tem de ficar entre 1e6 e 1e11 dólares em BTC.
- H4 `funding` do `activeAssetCtx` já é a taxa por hora. Teste: coincide com a última linha de `fundingHistory`.
- H5 Negócios de TWAP e de liquidação trazem `hash` com todos os dígitos a zero. Teste: fracção de negócios com hash a zeros entre 0,1 e 20 por cento.
- H6 Esquema do leaderboard. Se mudar, usa-se o último `dados/baleias.csv`.
- H7 A CoinGlass inclui a Hyperliquid no fluxo de liquidações. Se não incluir, as liquidações são de Binance, OKX e Bybit e o campo `liqsrc` da nota diz `agg`.
- H8 Atraso local menos bolsa, medido pela mediana de `(local - time)` dos últimos 200 negócios, inferior a 3 s.

Cada hipótese tem uma degradação declarada na secção correspondente.

---

## 4. Contexto de 1 hora

Recalculado uma vez por activo no fecho de cada vela de 1 h, só com velas fechadas. A vela `t` conta como fechada quando chega no canal `candle` uma vela com `t` maior, ou quando `relógio_local - atraso >= T_t + folga_fecho_ms`. Os valores ficam em vigor até ao fecho seguinte e são lidos pela vela de 15 m.

### 4.1 Âncora e desvio

```
(1)  lambda_A = 2^(-1/h_A)                                     h_A = 96 velas de 1 h
(2)  A_t = EWMA_{h_A}(x)_t                                      com correcção de arranque
(3)  d_t = x_t - A_t
(4)  dA_t = A_t - A_{t-1} = ((1 - lambda_A)/lambda_A) d_t       identidade exacta
```

`h_A = 96` obedece à regra `h_A >= 4 H_max`, para que o factor de captura (secção 7.1) fique acima de 0,75 em toda a gama de meias-vidas admitidas.

### 4.2 Hipótese nula computável

Se `x_t = x_{t-1} + eps_t` com `eps` de variância qualquer, então, pela definição (2),

```
(5)  d_t = lambda_A d_{t-1} + lambda_A eps_t
```

Logo, sob a nula, `d` é um AR(1) estacionário com `phi = lambda_A` (0,9928 com `h_A = 96`), e **não** `phi = 1`. Esta é a nula que se testa. Qualquer portão que apenas exija `theta > 0` ou "meia-vida dentro de um intervalo que contém `h_A`" aceita um passeio aleatório e foi rejeitado.

### 4.3 Ajuste AR(1) sem intercepto, correcção de enviesamento e erro padrão robusto

Janela móvel de `N` velas de 1 h fechadas (`N = 720` de partida, mínimo `n_min = 480`, cresce até `n_max = 2160` com o histórico em disco):

```
(6)  phi_hat = S_xy / S_xx ,  S_xy = soma_{t} d_t d_{t-1} ,  S_xx = soma_{t} d_{t-1}^2
(7)  phi_c = phi_hat (1 + 2/N)                                  correcção do AR(1) SEM intercepto
(8)  e_t = d_t - phi_c d_{t-1} ;  s^2 = soma e_t^2 / (N - 1)
(9)  SE_rob = sqrt( soma d_{t-1}^2 e_t^2 ) / S_xx                HC0
(10) t_nulo = (phi_c - lambda_A) / SE_rob
```

Validade do ajuste: `0 < phi_c < 1` e `N >= n_min`; caso contrário o contexto é VERMELHO com motivo `ajuste`. A correcção (7) é a do modelo sem intercepto (enviesamento cerca de `-2 phi/N`; medido `-0,0024` em 600 réplicas da nula com `N = 720`, contra `0,0028` previsto); a correcção de Kendall `(1 + 3 phi)/N` é a do modelo com intercepto e sobrecorrige, pelo que foi rejeitada. O mesmo `phi_c` entra nos resíduos (8), no erro padrão (9) e em `sigma_eq` (12).

### 4.4 Limiar do teste e calibração por simulação

Com `N (1 - lambda_A)` perto de 5, a estatística (10) está em regime próximo da raiz unitária e não é exactamente normal: em 600 réplicas da nula com `N = 720`, `t_nulo` tem média 0,3, desvio 1,1 e quantil 0,1 por cento em -3,3 (inovações homoscedásticas) a -3,4 (volatilidade estocástica). Por isso o limiar não é fixado numa normal:

```
(11) t_crit = quantil_{alpha_nula}( t_nulo sob a nula simulada com o mesmo N e lambda_A )
             alpha_nula = 0,001 ; 1000 réplicas ; semente fixa ; recalculado só quando N muda
             fallback = t_nulo_max = -3,0 se calibrar_nula = nao
```

A função `calibrar_t_crit(n, lam_a, replicas, semente)` é determinista e corre no arranque (menos de 2 s por activo em Python puro); o valor fica em `estado_sinalizador.json` e na nota (`tc=`).

### 4.5 Parâmetros do processo de Ornstein-Uhlenbeck

```
(12) theta = -ln(phi_c) / Delta ;  H = ln 2 / theta  (velas de 1 h) ;  sigma_eq = sqrt( s^2 / (1 - phi_c^2) )
(13) theta_x = ln( lambda_A / phi_c )                           reversão em excesso (só esta é negociável)
(14) SE(theta) = SE_rob / phi_c ;  IC95 = theta -+ 1,96 SE(theta)  (método delta; só para o relatório)
(15) sigma_r = sqrt( soma_{t} r_t^2 / N )                        desvio padrão do retorno horário, mesma janela
```

`sigma_eq` é o desvio padrão estacionário de `d` (em unidades de log-preço) e equivale a `sigma^2/(2 theta)` do OU contínuo com `phi = e^(-theta Delta)`. Em 200 réplicas de um OU com `H = 8`, (12) devolve mediana 7,5 com intervalo 5,6 a 10,7 entre os percentis 5 e 95: a incerteza de `H` é de cerca de 35 por cento e propaga-se ao alvo e ao tempo máximo; o relatório mostra-a.

### 4.6 Rácio de variâncias robusto (veto de tendência)

Sobre os `r_t` da mesma janela, com `q = q_vr = 8`, `mu = média(r)`:

```
(16) sigma_1^2 = soma (r_t - mu)^2 / (N - 1)
(17) sigma_q^2 = soma_{t>=q} (x_t - x_{t-q} - q mu)^2 / m ,   m = (N - q + 1)(1 - q/N)
(18) VR(q) = sigma_q^2 / (q sigma_1^2)
(19) delta_j = N soma_{t>j} (r_t - mu)^2 (r_{t-j} - mu)^2 / [ soma (r_t - mu)^2 ]^2
(20) theta_q = soma_{j=1}^{q-1} [ 2 (q - j)/q ]^2 delta_j
(21) z*(q) = sqrt(N) (VR(q) - 1) / sqrt(theta_q)
(22) z_simples(q) = (VR(q) - 1) / sqrt( 2 (2q - 1)(q - 1) / (3 q N) )      só para o teste de implementação
```

Permitido se `VR(8) < vr_max_verde = 1,0`; vetado se `VR(8) > vr_veto = 1,2` e `z*(8) > zvr_veto = 2`. Não se exige significância de `VR < 1`: com 30 dias não a tem; é um veto de tendência e um diagnóstico.

### 4.7 Volatilidade de 15 minutos (Parkinson) e choque

Mantidas a cada fecho de 15 m, lidas pelo contexto e pelo gatilho:

```
(23) rp_k = ( ln(h_k / l_k) )^2 / (4 ln 2)                      estimador de Parkinson por vela de 15 m
(24) v_c = EWMA_{h_vc}(rp) ,  v_l = EWMA_{h_vl}(rp)              h_vc = 8, h_vl = 96 velas de 15 m
(25) sigma_15 = sqrt(v_l) ;  vol_razao_15 = sqrt( v_c / v_l )
```

Parkinson é cerca de cinco vezes mais eficiente do que a variância dos fechos para a mesma amostra, por isso substitui a EWMA de `r^2` em `sigma_15`. `vol_razao_15 <= vol_razao_max = 2,0` é condição do contexto; acima disso é regime de cascata e a estimativa do OU não vale.

### 4.8 Veto de sobre-reacção diária

```
(26) ret_dia_t = x_t - x_{abertura 00:00 UTC do dia}
(27) veto_dia = |ret_dia_t| > k_dia sigma_r sqrt(24)             k_dia = 2 (0 desliga)
```

Depois de um dia anormal há evidência de momentum intradiário e não de reversão (Caporale e Plastun).

### 4.9 Bandas e verificação empírica das caudas

```
(28) z_t = d_t / sigma_eq
(29) P_ancora = exp(A_t) ;  P^{+-}(z) = exp( A_t +- z sigma_eq )  para z em {z_out, z_in, z_stop, z_veto}
(30) q_z,t = quantil empírico 0,954 de |z_s| , s em [t - N_z, t - 1]     N_z = 720 (rolante, SEM a vela t)
(31) z_in_ef = z_in            se q_z <= z_in_emp_max (2,5)
             = q_z             caso contrário (registado na nota como zin=; nunca afinado pelo P/L)
```

Sob OU gaussiano `P(|z| <= 2) = 0,954`, logo o quantil a comparar com `z_in = 2` é o 95,4 (e não o 97,7, que vale 2,27 na normal). Se as caudas forem muito mais pesadas, a banda alarga para o quantil empírico, calculado só com velas anteriores, o que exclui lookahead no backtest.

### 4.10 Posicionamento de contexto (funding, open interest, prémio)

Do último `activeAssetCtx` recebido antes de `T_t` (idade registada):

```
(32) z_F,t = ( F_t - mediana_720(F) ) / MAD_720(F)                F = funding por hora; histórico de fundingHistory
(33) OI_t = openInterest_t x markPx_t   (USD) ;  dOI_exc = OI_k / OI_{início da excursão} - 1
(34) pm_t = BPS (markPx - oraclePx) / oraclePx                     prémio local, em bps
```

Estes números entram na pontuação das baleias (secção 6) e na nota, nunca no portão.

### 4.11 Estado do contexto

```
(35) VERDE     se  t_nulo <= t_crit  e  VR(8) < 1,0  e  H_min <= H <= H_max  e  vol_razao_15 <= vol_razao_max
               e  não veto_dia  e  ajuste válido
     AMARELO   se  t_crit < t_nulo <= -2  com as outras condições verdadeiras   (regista-se, não se sinaliza)
     VERMELHO  caso contrário (motivos: nulo, vr, meia_vida, vol, dia, ajuste)
```

`H_min = 3`, `H_max = 24`. Sob a nula, `phi_c` tem desvio padrão 0,0045 e `H <= 24` exige `phi_c <= 0,9715`, 4,7 desvios abaixo de `lambda_A`: o tecto da meia-vida, sozinho, já rejeita o passeio aleatório; o teste (10) acrescenta protecção quando a heteroscedasticidade inflaciona o erro padrão. O estado e todos os números ficam em `dados/estado_sinalizador.json`.

### 4.12 O que não se calcula aqui

Estado do livro, impacto, teto e rota são do medidor. Hurst, filtro de Kalman, HMM e modelos logísticos foram avaliados e excluídos: redundantes com (10) e (21) ou impossíveis de sustentar com a amostra da Etapa 1.

---

## 5. Gatilho no fecho da vela de 15 minutos

### 5.1 Instante e ordem de actualização

A avaliação faz-se uma vez por activo e por vela de 15 m, no instante `min( chegada de vela com t > t_k no canal candle , relógio_local - atraso >= T_k + folga_fecho_ms )`, com `folga_fecho_ms = 1500`. Se o fecho de 15 m coincide com um fecho de 1 h, actualiza-se **primeiro** o contexto de 1 h (secção 4) e só depois se avalia o gatilho: a vela de 1 h está fechada nesse instante, não há lookahead. Todos os acumuladores de negócios e de contexto já estão actualizados à chegada de cada mensagem; no fecho só há comparações e a escrita.

```
(36) z_k = ( ln c_k - A_ult ) / sigma_eq,ult
```

`A_ult` e `sigma_eq,ult` são os valores do último fecho de 1 h anterior ou igual a `T_k`. O salto de `z` no fecho de hora é de factor `lambda_A` (0,7 por cento com `h_A = 96`), desprezável e documentado. `z_{k-1}` é o valor **registado** no fecho anterior, nunca recalculado com a âncora nova.

### 5.2 Máquina de estados por activo

Estados DENTRO (`|z| < z_in_ef`) e FORA (`|z| >= z_in_ef`). Ao sair guarda-se `lado_exc = sign(z)`, `OI_início`, `k_fora = 0`, `ext = |z|`; a cada fecho FORA, `k_fora += 1` e `ext = max(ext, |z_k|)`. A excursão termina quando `|z_k| < z_in_ef`; se termina do lado oposto (`sign(z_k) != lado_exc`) não há sinal e o estado volta a DENTRO.

```
(37) k_max = min( k_max_tecto , round( 8 k_H H_0 ) )            k_H = 1 (duas meias-vidas em velas de 15 m: 4 H por meia-vida x 2); k_max_tecto = 64
```

O veto de duração escala com a meia-vida estimada; um valor fixo de 16 velas seria incompatível com `H_max = 24`.

### 5.3 Condições de entrada (todas avaliadas no fecho k; a primeira que falha é registada como motivo)

```
G0  dados:      sem corte de ligação nas últimas 4 velas de 15 m; contexto de 1 h com idade < 75 min;
                atraso medido < 3000 ms; vela k com n >= 1 negócio
G1  contexto:   estado VERDE em (35)
G2  reentrada:  estado_{k-1} = FORA  e  |z_k| < z_in_ef  e  sign(z_k) = lado_exc  e  |z_k| < |z_{k-1}|
G3  caminho:    |z_k| >= z_out + z_min_resto                     z_min_resto = 0,75
G4  excursão:   ext < z_veto (4,0)  e  k_fora <= k_max (37)
G5  choque:     |r_k| <= k_choque sigma_15 (k_choque = 4)  e  vol_razao_15 <= vol_razao_max
G6  fluxo:      nenhuma rajada de liquidação com s = -lado nos últimos rajada_silencio_s (60 s)   (secção 6.4)
                e  B_k > b_veto (-2)                                                              (secção 6.7)
G7  exclusividade: sem trade virtual aberto no activo; sem arrefecimento activo (2 velas após um stop);
                uma entrada por excursão; soma dos tamanhos abertos + tamanho <= expo_max E
G8  geometria:  G >= g_min_x_custo x c_L  (3 x 13 = 39 bps de partida)  e  tamanho >= minimo_ordem_usd
                e  fase != sombra                                                                (secção 8.6)
```

Saída do gatilho: `lado = -sign(z_k)`; `preco = c_k`; `d_0 = ln c_k - A_ult`; `alvo_bps = G` (secção 7.1); `tamanho_usd` (secção 8); `nota` (secção 9). Sobre G4: uma leitura de `4 sigma_eq` tem probabilidade `6e-5` sob o modelo: é quebra de regime, não oportunidade. Sobre G5: o fecho de 15 m é o instante das rajadas de quarto de hora; um choque na própria vela de reentrada é cascata ainda em curso.

### 5.4 Lookahead excluído por construção

Só velas fechadas; `A_ult`, `sigma_eq`, `phi_c`, `q_z` estimados com velas até ao último fecho de 1 h; `z_{k-1}` registado; as janelas de fluxo terminam em `T_k`; nenhum pedido de rede entre o fecho e a escrita; o backtest usa as mesmas funções de `reversao.py` com a mesma ordem de actualização (1 h antes de 15 m) e o teste do GBM (secção 11.6) mede-o.

---

## 6. Baleias: nadar com elas sem lhes seguir a direcção

Princípio: as baleias **confirmam e dimensionam**, nunca disparam. Nenhuma táctica tem evidência revista por pares; os limiares foram congelados antes de medir e só o relatório os promove. Cada medida fica na nota para o relatório a cruzar com `r_3600` e `r_14400` do medidor. Endereços nunca vão para ficheiro inteiros: `h_i = sha256(endereço)[:10]`.

### 6.1 Fluxo agressor (canal trades)

Por negócio `i`: `s_i = +1 se side = B, -1 se side = A`; `ntl_i = px_i sz_i`; `agr_i = users[0] se B, users[1] se A`; `pas_i` o outro (H2).

```
(38) V_B(w) = soma ntl_i [s_i = +1] , V_A(w) = soma ntl_i [s_i = -1]   na janela w (15 m ou 1 h; nucleo.JanelaSoma)
(39) OFI_w = ( V_B - V_A ) / ( V_B + V_A )  em [-1, 1]
(40) CVD_k = soma_{i até T_k} s_i ntl_i   desde o início da excursão (diagnóstico; reiniciado em cada corte)
```

### 6.2 Negócios grandes, absorção e insistência

```
(41) Q99 = quantil 0,99 de ntl nas últimas 24 h (nucleo.JanelaPercentil com n_max = 20 000); grande_i = [ntl_i >= Q99]
(42) FLX_1h = soma_{grandes} s_i ntl_i / soma_{grandes} ntl_i  em [-1, 1] ;  pos_FLX = pos_7d(|FLX_1h|)
(43) REP_1h = n_rep^+ - n_rep^- ,  repetido = endereço agressor com >= 3 negócios grandes na hora;
     sinal pelo notional líquido; excluídos HLP, cofre liquidador e endereços com |líquido| < 0,1 x bruto (market maker)
(44) HHI_pas,k = soma_e ( v_e / V_Z )^2   sobre os negócios da vela k com px para lá de P^{+-}(z_in_ef),
     v_e = notional em que e foi passivo; só na nota (hhi=)
```

### 6.3 Absorção residual (regressão fluxo-preço)

Sobre as últimas 96 velas de 15 m fechadas antes de `k`, `u_j = (V_B,j - V_A,j) / EWMA_96(V_B + V_A)_{j-1}`:

```
(45) beta = soma r_j u_j / soma u_j^2 ;  s_e^2 = soma (r_j - beta u_j)^2 / 95
(46) a_k = lado ( r_k - beta u_k ) / s_e
```

`a_k` é quanto o preço ficou, em desvios do resíduo, acima do que o fluxo agressor implicava, no sentido da reversão: a assinatura da absorção passiva.

### 6.4 Rajadas de liquidação e TWAP

Definições conforme a mecânica documentada (20 por cento ao livro acima de 100 000 USDC; arrefecimento de 30 s; depois a posição inteira):

```
(47) bloco = negócios consecutivos do mesmo agressor h com intervalos <= 1000 ms;
     rajada = bloco com soma ntl >= Q99 e |ln(px_último / px_primeiro)| >= rajada_amp_bps / BPS (2 bps);
     s_raj = s do bloco ; raj_ms = time do último negócio
(48) rajada_contra_k = existe rajada com s_raj = -lado e T_k - raj_ms <= rajada_silencio_s x 1000   (60 s = dois arrefecimentos)
(49) twap_k = -1 se existe agressor com >= 4 negócios de hash a zeros (H5) nos últimos 600 s, intervalos entre 10 e 50 s,
                 e s = -lado ; +1 se s = lado ; 0 caso contrário
```

O critério de amplitude substitui "atravessou 2 níveis do livro", porque o sinalizador não subscreve o livro. Sem H5 confirmada, `twap` usa só a cadência e a nota regista `twapsrc=cad`.

### 6.5 Posições das baleias (REST, tarefa própria)

Lista diária `dados/baleias.csv` (prefixo de 10 caracteres e hash, nunca o endereço inteiro), refeita às 00:00 UTC a partir do leaderboard (H6) ou de `whale-position` (chave). Filtros, todos em `config.ini`: `accountValue >= 1e6`; `roi` positivo em `week` e `month`; `vlm_month / accountValue <= 50`; direccionalidade `|N_24| / G_24 >= 0,30` para os endereços vistos nos negócios (exclui market makers); sem liquidação nos últimos 7 dias (`whale-alert` ou rajada própria detectada); excluídos os cofres do HLP (lista em `enderecos_excluidos`, confirmada por `vaultDetails`); no máximo `baleias_max = 100`, ordenados por `accountValue`; subcontas agrupadas por `subAccounts`. Sonda `clearinghouseState` de `sonda_s = 900` em 900 s (200 de peso por 15 min; o sinalizador nunca passa de 400 de peso por minuto).

```
(50) POS_k = soma positionValue [szi > 0] - soma positionValue [szi < 0] ;  dPOS = POS_k - POS_{k-16}  (4 h)
(51) FUEL_k = soma positionValue das posições do lado contrário ao sinal com liquidationPx a menos de pct_fuel (2 %)
              do preço, na direcção do stop, / OI_k
(52) iman_k = 1 se a excursão atravessou a faixa de 25 bps com maior soma de positionValue por liquidationPx
              (até 3 % do preço) e a vela de reentrada fechou de volta; 0 caso contrário; só na nota
```

Degradação: sem lista ou sem sonda, `POS`, `dPOS`, `FUEL`, `iman` valem `na` e os termos correspondentes valem 0.

### 6.6 Liquidações (CoinGlass, opcional)

Com `[coinglass] chave`: reutiliza `tarefa_coinglass`, `_poll_rest`, `base_da_liquidacao`, `volume_liquidacao`, `lado_liquidacao`, `chave_liquidacao` do medidor, ou lê `liq_long_usd` e `liq_short_usd` de `estado.json` se o medidor estiver a correr com a chave.

```
(53) LIQ_contra_k = USD liquidados nos últimos 15 min do lado contrário à entrada (longs liquidados para uma compra)
     liq_k = LIQ_contra_k / max( LIQ_contra sobre a excursão )  ;  "na" sem chave
```

### 6.7 Pontuação e factor de tamanho

Cada termo em `{-1, 0, +1}`, limiares em `config.ini`:

```
T1 absorção pelos grandes:   +1 se lado x FLX_1h >= limiar_flx (0,3) e pos_FLX >= 0,8 ;  -1 se lado x FLX_1h <= -limiar_flx
T2 insistência:              +1 se lado x REP_1h >= limiar_rep (2) ;  -1 se <= -limiar_rep
T3 multidão do lado errado:  +1 se lado x z_F <= -limiar_fz (1,0) e dOI_exc > 0 ;  -1 se lado x z_F >= +limiar_fz
T4 cascata esgotada (chave): +1 se liq_k <= limiar_liq (0,5) ;  -1 se liq_k > 0,8 ;  0 se "na"
T5 posicionamento (sonda):   +1 se lado x dPOS > 0 e pos_7d(|dPOS|/OI) >= 0,8 ;  -1 se lado x dPOS < 0 na mesma condição ;  0 se "na"
T6 TWAP contra:              -1 se twap_k = -1 ;  0 caso contrário
T7 absorção residual:        +1 se a_k >= limiar_abs (1,0) e lado x u_k <= 0 ;  -1 se a_k <= -limiar_abs
T8 desalavancagem:           +1 se pos_2880( ln( OI_k / max(OI_{k-8..k-1}) ) ) <= 0,10 ;  0 se o histórico tem < 960 valores
(54) B_k = T1 + T2 + T3 + T4 + T5 + T6 + T7 + T8
(55) f_B = 0,5 se B_k <= 0 ;  1,0 se 1 <= B_k <= 2 ;  1,25 se B_k >= 3 ;  e f_B = min(f_B, 0,5) se FUEL/OI > pct_fuel
     veto (G6) se B_k <= b_veto (-2)
```

Uma única linha por activo e por fecho: as variantes com e sem filtro não escrevem linhas separadas (os fills do medidor ligar-se-iam só à mais recente). Em fase `cal`, `f_B` fica na nota mas não se aplica ao tamanho (o medidor precisa de um tamanho de referência constante). O prémio `pm` e `HHI` ficam só na nota, por serem colineares com `z_F` e com T1.

---

## 7. Risco e saída

Níveis fixados no fecho `k` da entrada e congelados: `A_0 = A_ult`, `sigma_0 = sigma_eq`, `phi_0 = phi_c`, `H_0 = H`, `z_0 = z_k`, `d_0 = ln c_k - A_0`, `lado = -sign(z_0)`.

### 7.1 Alvo como esperança condicional

```
(56) tau_max = min( n_H H_0 , tmax_h ) velas de 1 h          n_H = 2 ; tmax_h = 16 ;  tmax_15 = round(4 tau_max)
(57) rho = 1 - phi_0 (1 - lambda_A) / ( lambda_A (1 - phi_0) )          factor de captura em preço
(58) G = BPS x min( rho |d_0| (1 - phi_0^tau_max) ,  |d_0| - z_out sigma_0 )
```

Sob o OU, `E[d_tau] = d_0 phi_0^tau`; o fecho esperado do desvio até `tau_max` é `|d_0|(1 - phi_0^tau_max)`, mas uma parte é a âncora a andar, pela identidade (4); `rho` desconta-a. `rho = 0` exactamente sob a nula (`phi = lambda_A`); `rho = 0,962, 0,920, 0,878, 0,836, 0,753` para `H = 4, 8, 12, 16, 24` com `h_A = 96`. Exemplo: `z_0 = 1,9`, `z_out = 0,5`, `H = 8`, `tau_max = 16`: primeiro termo `0,920 x 1,9 x 0,750 = 1,31 sigma_0`, segundo `1,40 sigma_0`; com `sigma_0 = 1 %`, `G = 131 bps`. O medidor tira daqui `B = 0,15 G` para o teto de entrada.

### 7.2 Stop

```
(59) P_stop = exp( A_0 - lado z_stop sigma_0 ) ;  L = BPS ( z_stop - |z_0| ) sigma_0        z_stop = 3,0
```

Avaliado ao **mark** (é ao mark que a Hyperliquid liquida) e, no backtest, à mínima ou máxima da vela de 15 m. Com `z_0 = 1,9`, `L = 1,1 sigma_0` e `G/L = 1,19`. Nunca se encurta o stop pela geometria: se `G` não paga (G8), não há sinal.

### 7.3 Tempo e invalidação

Saída por tempo ao fecho da vela `tmax_15` após a entrada. Invalidação pelo contexto (fecho de 1 h durante o trade), sai ao fecho de 15 m seguinte com motivo `invalidacao`:

```
(60) inval = t_nulo > -2  ou  ( VR(8) > vr_veto e z* > zvr_veto )  ou  |z| >= z_veto
             ou  a âncora reestimada moveu P_alvo mais de 0,5 sigma_0 contra o trade
```

Rajadas novas, TWAP contra e FUEL durante o trade **não** fecham a posição (seria sobre-ajuste); ficam registados no trade virtual.

### 7.4 Taxa de acerto teórica (diagnóstico, nunca promessa)

```
(61) S(z) = integral_0^z exp(u^2/2) du   (Simpson, 200 passos)
(62) P_teo = ( S(z_stop) - S(|z_0|) ) / ( S(z_stop) - S(z_out) )
```

`P_teo = 0,899` para `(1,9; 0,5; 3)`, `0,949` para `(1,5; 0,5; 3)`. O stop por tempo e as caudas pesadas baixam-na muito; o relatório mostra, por caixas de `z_0`, `p_hat` contra `P_teo` (ECE, Brier) para ver onde o modelo falha.

### 7.5 Trade virtual e registo próprio

O sinalizador mantém um trade virtual por activo em `dados/registo_sinalizador.csv` (`id, hora, ativo, lado, preco, A_0, sigma_0, phi_0, H_0, G, L, tmax_15, fase, f_B, hora_saida, preco_saida, motivo em {alvo, stop, tempo, invalidacao}, velas, mae_bps, mfe_bps, r_bruto_bps, funding_bps, r_liq_bps, nota`), avançado a cada fecho de 15 m: alvo se o **fecho** passa `P_alvo = exp(A_0 + lado z_out sigma_0)`; stop se a mínima/máxima (ou o mark) toca `P_stop`, preenchido ao nível do stop com deslize de `imp_bps`; se alvo e stop na mesma vela, conta o stop. As saídas nunca vão para `sinais.csv`: o medidor contá-las-ia como entradas do lado contrário.

### 7.6 Disjuntores (param a emissão; nunca fecham posições; o trade virtual continua)

```
(63) perdas_dia:   soma r_liq dos trades fechados no dia UTC <= -perdas_dia_x_g x mediana(G)   (3)  -> sem linhas até à meia-noite UTC
     seguidas:     >= perdas_seguidas (5) stops consecutivos no activo               -> activo parado até ao próximo fecho de 1 h VERDE
     cvar:         média dos 5 % piores r_liq dos últimos 100 <= -cvar_x_l x mediana(L) (2) -> tudo parado até `sinalizador.py reset`
     regime:       VR(8) sobre 2160 velas > 1 em 3 reestimações seguidas            -> activo desligado até nova validação
```

---

## 8. Dimensionamento

### 8.1 Custos

```
(64) f_esp = lado x F_t x BPS x tau_max        funding esperado em bps; positivo = custo (F > 0: os longs pagam)
(65) c_W = t_entrada + imp + t_maker + f_esp    defeito 4,5 + 2 + 1,5 = 8 bps (+ f_esp)
     c_L = t_entrada + imp + t_taker + imp + f_esp   defeito 4,5 + 2 + 4,5 + 2 = 13 bps (+ f_esp)
```

Substituídos pelos medidos (`E + F + A + D` médios por rota e activo, do relatório do medidor) quando há pelo menos 30 aberturas medidas; `imp` é `imp_ref_bps` mediano do medidor se existir.

### 8.2 Equilíbrio e taxa de acerto

```
(66) p* = ( L + c_L ) / ( G + L + c_L - c_W )                      nucleo.acerto_equilibrio
(67) p_hat = ganhos / n   (últimos n <= 200 trades virtuais fechados do activo; de todos enquanto n_activo < 30)
(68) p_inf = [ p_hat + z^2/(2n) - z sqrt( p_hat(1 - p_hat)/n + z^2/(4n^2) ) ] / ( 1 + z^2/n ) ,  z = 1,96
(69) m = max( m_min , z sqrt( p_hat (1 - p_hat) / n ) )            m_min = 0,05 ; margem que cresce com a incerteza
```

Exemplo: `G = 131`, `L = 110`, `c_W = 8`, `c_L = 13`: `p* = 123/246 = 0,500`.

### 8.3 Kelly nas unidades certas

A aposta é a perda no stop, não o nocional:

```
(70) b = ( G - c_W ) / ( L + c_L ) ;  f* = ( p_inf (b + 1) - 1 ) / b          f* > 0 sse p_inf > p*
(71) N_kelly = k_kelly f* E BPS / ( L + c_L )                              k_kelly = 0,25
```

### 8.4 Risco, volatilidade e liquidez

```
(72) N_risco = risco_por_trade x E x BPS / ( L + c_L )                     0,005 (0,5 % do capital por stop, custos incluídos)
(73) N_vol = vol_alvo_dia x E / ( sigma_r sqrt(24) )                       0,01 (1 % ao dia por posição)
(74) N_liq = min( liq_fraccao x dayNtlVlm / 96 , qmax_usd do lado em estado.json se tiver < 10 s )   liq_fraccao = 0,01
```

### 8.5 Tamanho

```
(75) tamanho_usd = clamp( f_B x min( N_kelly, N_risco, N_vol, N_liq ) , minimo_ordem_usd , tamanho_max_frac x E )
     fase cal (n < n_cal = 30): tamanho_usd = tamanho_base (1000), f_B não se aplica
```

Exemplo com `E = 20 000`, `p_inf = 0,56`, `sigma_r = 0,5 %`: `b = 1,0`, `f* = 0,12`, `N_kelly = 48 780`, `N_risco = 8 130`, `N_vol = 8 165`; manda `N_risco` e o tecto `0,25 E = 5 000`. Com edge pequeno e bem estimado o Kelly raramente manda; o que manda é o risco por trade, como deve ser nos primeiros 100 sinais. O relatório regista qual dos tectos mandou em cada sinal (`cap=`).

### 8.6 Fases por activo

```
(76) cal:    n < n_cal                         escreve com tamanho_base
     op:     n >= n_cal e p_inf > p* + m        escreve com (75)
     sombra: n >= n_cal e p_inf <= p* + m       NÃO escreve em sinais.csv; o trade virtual continua a ser
                                               registado para p_hat se actualizar e o activo poder voltar a op
```

Exposição total: soma dos tamanhos abertos `<= expo_max x E` (0,75) e uma posição por activo. A alavancagem da conta não é conhecida pelo sinalizador; a nota traz `lev_max = 1 / ( 5 L / BPS + 1/(2 maxLeverage) )`, a alavancagem a que a distância ao `liquidationPx` próprio é pelo menos `5 L`.

---

## 9. A linha escrita e a nota

Espelho de `cmd_sinal` do medidor, com os dois campos a mais: ficheiro aberto em modo `a`, `csv.writer`, uma única chamada `write` de uma linha terminada em `\n`, fechado de imediato; cabeçalho só quando o ficheiro está vazio; `hora = iso_utc(agora_ms())` no instante da decisão (meta: menos de 1 s após o fecho; a latência decisão-escrita é registada). `ativo` é o nome exacto de `[geral] ativos`; `lado` é `compra` ou `venda`; `preco = c_k`; `alvo_bps = G` com 1 casa; `tamanho_usd` com 0 casas.

Nota: ASCII, sem vírgulas, sem aspas, sem quebras de linha, ponto decimal, primeiro token = variante:

```
revou v=2 var=<hash6> fase=<cal|op> z0= zp= ext= kf= H= phi= t= tc= vr8= zin= sig= G= L= tmax= P= pst= ofi= flx= rep= abs= fz=
doi= doi8= pm= liq= liqsrc=<cg|agg|na> raj= twap= twapsrc= pos= dpos= fuel= iman= hhi= bal= fb= cap= ses= dow= hr= med=<on|off> ctx=VERDE
```

`var` é `hash_variante(params)[:6]` dos parâmetros em vigor; `ses` em `{asia, europa, eua, fds}`; `dow` o dia da semana; `hr` a hora UTC; `med=off` quando `estado.json` falta, tem mais de 10 s ou `ligado = false` (o sinal escreve-se na mesma; o log avisa que não vai ser medido). `interpretar_nota` inverte `nota_sinal` e o relatório agrupa por qualquer campo.

---

## 10. Convivência com o medidor

- `config.ini` partilhado: o sinalizador lê `[geral]`, `[orcamento]`, `[sombra]`, `[coinglass]` por `md.Config` e a secção nova `[sinalizador]`, que o medidor ignora.
- O utilizador põe `[sombra] horizontes_s = 5, 30, 60, 300, 900, 3600, 14400` e `horizonte_regra_s = 3600`; o sinalizador avisa no arranque se 900, 3600 e 14400 faltarem. Não se pede 57600: um sinal aberto 16 h em memória do medidor perde-se a cada paragem do Mac; o resultado a essa escala é do trade virtual.
- O sinalizador lê `estado.json` só para a nota, para `N_liq` e para o aviso de frescura; nunca filtra sinais pelo estado do livro (isso enviesaria a amostra para os instantes fáceis).
- Orçamento de rate: o sinalizador fica abaixo de 400 de peso por minuto; `candleSnapshot` pesa 20 mais 1 por 60 velas e só corre no arranque e no comando `historico`.
- Uma ligação websocket própria (candle 15 m e 1 h, trades, activeAssetCtx por activo), com o mesmo padrão de ping, vigia e religação exponencial do medidor, e o mesmo descarte do lote antigo de negócios.

---

## 11. Backtest, métricas e critério de aceitação

### 11.1 O que se testa com velas e o que só se mede ao vivo

Com velas (`candleSnapshot` e `dados/velas/`): toda a camada OU (1 a 15), o teste da nula e a sua calibração (10, 11), o rácio de variâncias (16 a 21), Parkinson e o choque (23 a 25), o veto diário (26, 27), as bandas e o quantil empírico (28 a 31), o gatilho G0 a G5, G7 e G8, as saídas por alvo, stop, tempo e invalidação, as taxas e o funding (uma linha por hora em `fundingHistory`). Isto é a **variante A**.

Só ao vivo: lado agressor e endereços (OFI, FLX, REP, HHI, rajadas, TWAP, absorção residual), open interest e prémio ao segundo (`activeAssetCtx`; a Hyperliquid não dá histórico de OI), posições e `liquidationPx` das baleias, liquidações da CoinGlass, e os custos reais `E, F, A, D` do medidor. Por isso o sinalizador grava desde o primeiro dia, por vela de 15 m e activo, em `dados/velas/<ATIVO>_15m_<dia>.csv`, as colunas `v_b, v_a, n_grandes, flx, rep, abs, hhi, raj, twap, oi_usd, funding, premium, liq_long, liq_short, pos, fuel, iman, atraso_ms, corte`. A **variante B** (termos de baleias) só se valida no período recolhido, com as mesmas regras, e nunca se afirma mais do que isso.

### 11.2 Motor

`backtest_reversao.simular` percorre as velas com as mesmas funções de `reversao.py` do processo ao vivo, no fecho de cada vela, com a ordem de actualização 1 h antes de 15 m. Convenções conservadoras: entrada ao fecho da vela de reentrada mais metade do spread mediano (do medidor, ou 1 bp) mais `imp` na rota agressiva (taker 4,5 bps + `imp`), ou maker 1,5 bps com probabilidade de preenchimento `pi` (do registo sombra do medidor por activo, senão 0,6) na rota passiva, entrando a taker na vela seguinte se não preencher e a condição se mantiver; alvo só conta se o **fecho** passar o nível; stop conta se a **mínima ou máxima** tocar, preenchido ao nível com deslize `imp`; alvo e stop na mesma vela contam stop; saída no alvo a maker, no stop, tempo e invalidação a taker + `imp`; funding por hora inteira dentro do trade com o sinal (64). Resultado líquido:

```
(77) r_bruto = BPS lado ln( p_saída / p_entrada ) ;  funding_bps = BPS lado soma_{horas} F_h ;  r_liq = r_bruto - custos_rota - funding_bps
```

### 11.3 Dados e janelas

`candleSnapshot` dá 208 dias de 1 h e 52 dias de 15 m por activo. Com 52 dias **não existe uma janela completa** de walk-forward no 15 m: na Etapa 1 o backtest de 15 m é um ensaio de sanidade de uma janela, e a validação séria só existe depois de o comando `historico` ter acumulado 120 dias de velas de 15 m (cerca de 70 dias após o arranque). Até lá corre a **variante 1h-degradada** (gatilho avaliado nos fechos de 1 h, `z_k` substituído por `z_t`), assinalada como tal, sobre os 208 dias: janelas rolantes IS 90 dias e OOS 30 dias (4 janelas), purga igual a `tmax_h` e embargo de 1 por cento. Com um ano de velas de 15 m há 9 janelas.

### 11.4 Grelha e registo de ensaios

A grelha **só corre quando há `n >= 200` trades por activo** no período; antes disso os valores de partida são fixos e contam como um único ensaio. Grelha declarada: `z_in {1,8; 2,0; 2,2}`, `z_out {0,25; 0,5}`, `z_stop {2,75; 3,0; 3,5}`, `n_H {1,5; 2; 3}`, `z_min_resto {0,5; 0,75}`, `k_H {0,75; 1}`: 216 configurações, todas registadas em `dados/ensaios.csv` (data, parâmetros, hash, n, expectância, EP, SR), incluindo as descartadas. `h_A`, `N`, `t_crit` e os limiares das baleias ficam fora da grelha. Em cada IS escolhe-se o centro de um patamar (vizinhos a um passo também positivos), nunca o máximo. `M` no DSR é o número de linhas de `ensaios.csv`.

### 11.5 Métricas por trade (sem anualização)

`n`; expectância de `r_liq` em bps com erro padrão `sd/sqrt(n)`; `p_hat` e `p_inf`; `p*` com os custos da rota; PF; razão ganho médio sobre perda média; mediana e quantis 5 e 95; MAE e MFE; drawdown máximo em bps e maior série de perdas; duração média; fracção de saídas por motivo; sinais por dia e custo diário; WFE = expectância OOS / IS; IC de Spearman entre `z_0` e `r_liq`; ECE e Brier de `P_teo` contra o resultado por caixas de `z_0`.

```
(78) SR = média(r_liq) / sd(r_liq)
(79) PSR(SR*) = Phi[ (SR - SR*) sqrt(n - 1) / sqrt( 1 - g3 SR + (g4 - 1) SR^2 / 4 ) ]          g3 assimetria, g4 curtose bruta
(80) SR* = sqrt(V) [ (1 - gamma) Phi^-1(1 - 1/M) + gamma Phi^-1(1 - 1/(M e)) ] ,  gamma = 0,5772 ,  V = var dos SR entre configurações
(81) DSR = PSR(SR*) ;  PBO por CSCV com S = 16 blocos sobre a matriz (trades x configurações) com somas por bloco
```

`Phi` por `math.erf`; `Phi^-1` por bissecção sobre `erf`.

### 11.6 Nulos e placebo

- **GBM com o portão desligado:** 1000 caminhos com a `sigma_r` do activo, sem reversão, mesma regra de entrada e saída; a expectância bruta tem de ser estatisticamente zero e a líquida igual a menos os custos (teste de ausência de lookahead e de erro de implementação). Com o portão ligado o GBM quase não gera trades, o que se documenta mas não se usa como teste.
- **`p_nulo`:** taxa de acerto do rótulo "alvo antes do stop em `tmax_15`" sob GBM para o mesmo `(G, L, tmax_15)`, semente fixa, 10 000 caminhos: é a referência de `P_teo` sem reversão.
- **Placebo de âncora deslocada:** a mesma regra com `A_t` substituída por `A_{t - u}` com `u` uniforme em `[48, 240]` velas (10 realizações, semente fixa), ou com as bandas de outro activo: separa "reversão após `|z|` grande" de "qualquer vela ampla reverte um pouco".

### 11.7 Critério de aceitação (todos, por activo, no OOS encadeado, nunca no IS)

1. `n_OOS >= 100` trades.
2. `p_inf > max(p*, p_nulo) + 0,05`, com os custos medidos pelo medidor quando existam.
3. Expectância líquida OOS com `t = média / (sd / sqrt(n)) >= 3` (coerente com o -3 do portão).
4. Mediana das expectâncias OOS por janela `> 0` e fracção de janelas positivas `>= 0,6`; `WFE >= 0,5`.
5. `PBO < 0,2` e `DSR >= 0,95` com `M` igual ao total de `ensaios.csv`.
6. Expectância acima do quantil 0,95 do nulo GBM e acima do placebo com diferença `> 2` erros padrão.
7. Os 4 troços (`range_split`) com o mesmo sinal.
8. Ao vivo, após 100 sinais medidos pelo medidor: `r_3600` médio menos custos dentro de 2 erros padrão do backtest; senão o activo volta a `cal`.

Activos que falham ficam desligados em `config.ini` e só se religam com nova validação; a validação repete-se de 30 em 30 dias.

---

## 12. Parâmetros (`[sinalizador]` em `config.ini`)

| Chave | Defeito | Significado e justificação |
| --- | --- | --- |
| `h_a` | 96 | Meia-vida da âncora em velas de 1 h; `>= 4 H_max` para `rho >= 0,75` |
| `n_ajuste`, `n_min`, `n_max` | 720, 480, 2160 | Janela do AR(1); 720 dá IC de `theta` com largura cerca de 35 % (medido); cresce com o histórico |
| `calibrar_nula`, `alpha_nula`, `replicas_nula`, `semente_nula` | sim, 0.001, 1000, 7 | Limiar `t_crit` por simulação da nula (11); medido -3,3 a -3,4 em N = 720 |
| `t_nulo_max` | -3.0 | Fallback de `t_crit`; -3 é o limiar de factores novos de Harvey, Liu e Zhu |
| `t_amarelo` | -2.0 | Fronteira do AMARELO (regista-se, não se sinaliza) |
| `q_vr`, `vr_max_verde`, `vr_veto`, `zvr_veto` | 8, 1.0, 1.2, 2.0 | Rácio de variâncias: uma meia-vida típica; veto de tendência clara |
| `h_min`, `h_max` | 3, 24 | Meia-vida útil em velas de 1 h: abaixo é microestrutura, acima é mais lento do que o horizonte |
| `h_vc`, `h_vl`, `vol_razao_max`, `k_choque` | 8, 96, 2.0, 4 | Parkinson curto e longo em velas de 15 m; cascata; choque na vela de reentrada (6e-5 sob normal) |
| `k_dia` | 2 | Veto de sobre-reacção diária em desvios padrão; 0 desliga |
| `z_in`, `z_out`, `z_stop`, `z_veto` | 2.0, 0.5, 3.0, 4.0 | Histerese de Chan; P(\|z\| > 2) = 4,6 % das velas; stop a cerca de 1,1 sigma da entrada; 4 sigma é quebra |
| `n_z`, `z_in_emp_max` | 720, 2.5 | Quantil 0,954 rolante de \|z\|; substitui `z_in` se as caudas forem pesadas |
| `z_min_resto` | 0.75 | Caminho mínimo até à banda de saída, em sigma, para G pagar os custos |
| `k_h`, `k_max_tecto` | 1, 64 | Duração máxima da excursão em meias-vidas (37) e tecto de 16 h |
| `n_h`, `tmax_h` | 2, 16 | Tempo máximo em meias-vidas (75 % do caminho esperado) e tecto de 16 h |
| `folga_fecho_ms`, `atraso_max_ms`, `idade_ctx_max_min` | 1500, 3000, 75 | Fecho da vela com atraso corrigido; G0 |
| `rajada_dt_ms`, `rajada_amp_bps`, `rajada_silencio_s` | 1000, 2, 60 | Bloco do mesmo agressor; amplitude mínima; dois arrefecimentos de 30 s |
| `twap_min_negocios`, `twap_janela_s`, `twap_dt_min_s`, `twap_dt_max_s` | 4, 600, 10, 50 | TWAP por hash a zeros e cadência (modo aleatório de 10 a 50 s) |
| `q_grande`, `n_grandes_max` | 0.99, 20000 | Negócio grande pelo quantil do próprio activo, nunca em dólares fixos |
| `limiar_flx`, `limiar_rep`, `limiar_fz`, `limiar_liq`, `limiar_abs`, `pct_fuel`, `pos_alto`, `pos_baixo` | 0.3, 2, 1.0, 0.5, 1.0, 0.02, 0.8, 0.1 | Limiares de T1 a T8, congelados antes de medir, fora da grelha |
| `b_veto`, `fb_baixo`, `fb_alto`, `b_alto` | -2, 0.5, 1.25, 3 | Veto e factor de tamanho (55) |
| `enderecos_excluidos` | HLP e cofre liquidador | Confirmados por `vaultDetails`; separados por `;` |
| `baleias_max`, `sonda_s`, `baleia_valor_min_usd`, `baleia_vlm_racio_max`, `baleia_direcc_min`, `baleia_liq_dias` | 100, 900, 1e6, 50, 0.30, 7 | Lista e sonda de baleias (secção 6.5) |
| `capital_usd` | 20000 | E |
| `k_kelly`, `risco_por_trade`, `vol_alvo_dia`, `liq_fraccao` | 0.25, 0.005, 0.01, 0.01 | Quarto de Kelly (44 % do crescimento com 6 % da variância); 0,5 % por stop; 1 % de vol diária; 1 % de uma vela de volume |
| `tamanho_base`, `tamanho_max_frac`, `expo_max`, `n_cal`, `m_min` | 1000, 0.25, 0.75, 30, 0.05 | Fase de calibração = tamanho de referência do medidor; tecto por sinal; exposição total; margem mínima |
| `g_min_x_custo` | 3 | G tem de valer pelo menos 3 x c_L |
| `custo_taxa_entrada_bps`, `imp_defeito_bps`, `c_w_bps`, `c_l_bps` | 4.5, 2, 8, 13 | Custos de defeito até haver 30 aberturas medidas |
| `perdas_dia_x_g`, `perdas_seguidas`, `cvar_x_l` | 3, 5, 2 | Disjuntores (63) |
| `arrefecimento_velas` | 2 | Velas de 15 m sem nova entrada após um stop |
| `veto_sessao` | (vazio) | Sessões desligadas (`fds`, `asia`...), vazio por defeito; só após o relatório mostrar expectância negativa com 2 EP |
| `pasta_velas`, `url_leaderboard` | dados/velas, stats-data.hyperliquid.xyz/Mainnet/leaderboard | Ficheiros próprios; URL só para a simulação |

Recomendações noutras secções: `[sombra] horizontes_s = 5, 30, 60, 300, 900, 3600, 14400`, `horizonte_regra_s = 3600`; `[coinglass] chave` opcional, nunca em registos nem na nota. Parâmetros livres sujeitos a grelha: 6 (`z_in, z_out, z_stop, n_h, z_min_resto, k_h`); os restantes são fixados pela estatística ou pela convenção e cada alteração conta como ensaio em `ensaios.csv`.

---

## 13. Módulos

| Ficheiro | Responsabilidade |
| --- | --- |
| `medidor/reversao.py` | Fórmulas puras (1) a (81), sem rede, ficheiros nem relógio; dataclasses frozen `AjusteOU, Contexto, Excursao, Sinal, Tamanho, Vela, Posicao`; `nan` para desconhecido, `ValueError` para entradas impossíveis; importa só `math, bisect, collections, dataclasses, hashlib, statistics, random` e `nucleo` (`Ewma, lam, JanelaSoma, JanelaPercentil, acerto_equilibrio, media, mediana, BPS`); cada função com docstring de uma linha e o número da fórmula |
| `medidor/sinalizador.py` | Processo ao vivo (`python3 sinalizador.py correr`): `ConfigSinalizador`, histórico no arranque, ligação própria, relógio de fechos, avaliação no fecho, escrita em `sinais.csv`, trade virtual, registo próprio, estado atómico, tarefas de baleias e CoinGlass, disjuntores, CLI (`correr, verificar, historico, baleias, relatorio, ensaios, reset`); importa de `medidor`: `agora_ms, iso_utc, interpretar_hora, num, flt, positivo, Registo, RegistoDiario, pedir_info, obter_sz_decimals, URLS, Config, base_da_liquidacao, volume_liquidacao, lado_liquidacao, chave_liquidacao` |
| `medidor/backtest_reversao.py` | Backtest por eventos sobre `dados/velas/` ou `candleSnapshot`, com as mesmas funções de `reversao.py`; custos; walk-forward com purga e embargo; grelha com registo total; DSR, PBO, nulos e placebo; relatório em texto |
| `medidor/testes/teste_reversao.py` | `unittest` das fórmulas com valores à mão e séries sintéticas |
| `medidor/testes/teste_sinalizador.py` | `unittest` das peças sem rede, com a classe `Base` do medidor |
| `medidor/testes/simulacao_sinalizador.py` | Ponta a ponta com a bolsa falsa do medidor estendida (candle, candleSnapshot, fundingHistory, clearinghouseState, leaderboard, CoinGlass falsa) |
| `medidor/config.ini` | Secção `[sinalizador]` comentada |

A API completa, assinatura a assinatura, está na lista `api` que acompanha este documento; a implementação segue-a tal como está.

---

## 14. Testes (resumo; a lista completa acompanha o documento)

- AR(1) sobre passeio aleatório devolve `phi_c` perto de `lambda_A`, `t_nulo` com média perto de 0 e `rho = 0`; sobre OU com `H` conhecido recupera `H` dentro do intervalo e `t_nulo <= t_crit`.
- `calibrar_t_crit` é determinista (mesma semente, mesmo valor bit a bit) e dá cerca de -3,3 para `N = 720`.
- `z*(q)` coincide com `z_simples(q)` em dados homoscedásticos.
- `S(z)` e `P_teo` dão 0,899 para (1,9; 0,5; 3).
- O gatilho exige FORA anterior, não dispara duas vezes na mesma excursão, não dispara do lado oposto.
- `alvo_bps` não excede a distância à banda de saída; `k_max` escala com `H`; `tau_max` respeita o tecto.
- Wilson e Kelly nos casos limite (`p_inf <= p*` dá `f* <= 0`); `N_kelly` nas unidades da perda no stop.
- Nota sem vírgulas nem quebras; `interpretar_nota` inverte `nota_sinal`.
- Pontuação com cada táctica isolada e com todos os `na`.
- GBM com a regra inteira (portão desligado): expectância média igual a menos os custos (ausência de lookahead).
- Sinalizador: ordem 1 h antes de 15 m no fecho comum; escrita de 7 campos com `\n`; activo fora da lista recusado; uma entrada por excursão; trade virtual por alvo, stop, tempo e invalidação; `estado_sinalizador.json` atómico; aviso com `estado.json` velho; chave da CoinGlass ausente de logs e notas; recusa acima de `expo_max`; relatório que agrupa pela nota.

---

## 15. Limites honestos

1. A hipótese central (reversão em excesso face ao passeio aleatório) pode não existir em perps da Hyperliquid em 2026; o portão fecha e o sinalizador cala-se, o que é correcto e inútil. Só o backtest e os 100 sinais medidos dizem se há estratégia.
2. `theta` é mal estimado: com 720 velas o intervalo de `H` tem cerca de 35 por cento de largura; alvo, tempo máximo e `k_max` herdam esse erro e `alvo_bps` pode estar 30 por cento errado como orçamento do medidor.
3. O teste (10) está em regime próximo da raiz unitária; o limiar calibrado por simulação cobre a distribuição sob inovações gaussianas e com volatilidade estocástica, não todas as formas de heteroscedasticidade; e com `H_max = 24` o tecto da meia-vida é, na prática, o portão que mais fecha.
4. Não-estacionaridade: `phi`, `sigma_eq` e o funding mudam de regime em dias; a janela móvel e o veto de VR reagem com atraso de dezenas de velas. O BOCPD ficou fora da Etapa 1.
5. A estratégia é de intervalo, não de reversão intradiária; a evidência citada é de BTC spot em 2021 e de outras bolsas; nenhuma é da Hyperliquid.
6. Custos: 13 bps de ida e volta sobem `p*` para 0,50 com `G` perto de 130; o fecho de 15 m coincide com a rajada de quarto de hora e com o pior estado do livro; se a rota passiva do medidor não preencher nas reentradas, o edge pode desaparecer no impacto.
7. Caudas: as bandas assumem inovações gaussianas; o quantil empírico corrige `z_in` mas não o stop ao mark em gaps; o mark (mediana de três fontes) pode tocar o stop sem o livro lá chegar.
8. Baleias: nenhuma táctica tem evidência; limiares arbitrários e congelados; o leaderboard é sobrevivência e não documentado; as hipóteses H2, H3 e H5 são inferências; o endereço pode ser cobertura, market maker, isco ou martingale; só entram no tamanho, mas um `f_B` errado custa dinheiro na mesma.
9. Sobre-ajuste: 216 configurações por activo e três activos; o PBO de uma única realização sem edge varia entre 0,19 e 0,71; a grelha só corre com `n >= 200` e, mesmo assim, a tentação de afinar os limiares das baleias depois de ver o relatório é o maior risco humano do projecto.
10. Amostra: 52 dias de velas de 15 m no arranque, nenhuma janela de walk-forward completa, cerca de 0,8 reentradas por dia e por activo sob o modelo e menos ao vivo com o portão; 100 trades por activo demoram meses; até lá tudo é provisório e o tamanho fica em calibração.
11. Dependências de API não confirmadas a partir deste ambiente (H1 a H8); cada uma tem um teste em `verificar` e uma degradação, mas um formato diferente deixa o sinalizador sem velas e sem sinais.
12. A âncora exponencial é uma decisão de modelo; outra âncora daria outro universo de parâmetros e cada escolha conta como ensaio.
13. O sinalizador não gere a posição real: as saídas são virtuais; quem opera executa-as à mão (a Etapa 4 não existe) e a diferença entre a saída virtual ao fecho e a real é um custo não medido.
14. Três perps correlacionados com BTC: três sinais na mesma hora são quase um só trade; `expo_max` limita, mas o `n` efectivo é menor do que o contado.