# Família BANDAS COM ÂNCORA COMPATÍVEL: z-score do log-preço a uma âncora de 24, 48 ou 96 h com sigma realizada coerente (a correcção da REVOU)

Resultado: NEGATIVA. A 2 sigma, apostar contra o desvio tem esperança NEGATIVA no IS em todas as
âncoras e horizontes, e é estatisticamente significativa no sentido contrário ao da hipótese (ewma96
toque a 24 h: -103 bps, t agrupado -3,4 nos 6 principais; a regra REVOU, ewma96 reentrada, a 24 h:
-74 bps, t -2,5). Nenhum dos três filtros de regime inverte o sinal. A regra natural (REVOU sem
portão, com escala coerente) perde no IS (-0,163 R nos 6 principais, -0,092 R nos 18), no OOS
(-0,072 R nos 6, -0,046 R nos 18, avaliado uma vez) e em 2020-2022 (-0,133 R), e não é melhor do que
a nula com os mesmos stop e alvo (-0,070 R). Das 48 configurações da grelha, 44 têm média negativa
no IS e nenhuma chega a +0,15 R. A família falha H2, H3, H5 e H6 no OOS.

A única pista, declarada como pista e não como resultado: a 3 sigma (fora da regra REVOU) há um
ressalto curto de 60 a 130 bps nas 2 a 4 h seguintes (t 2,5 a 3,5 nos 6 principais no IS, mas só
1,1 a 1,7 nos 18 activos e 1,4 a 1,9 em 2020-2022), com cerca de 100 a 290 eventos por período.
Não foi testada no OOS e não é a hipótese desta família.

Código: `pesquisa/familia_bandas.py` (usa `harness.py`). Registo: `pesquisa/ensaios/bandas.csv`
(M = 53 linhas: 50 no IS, 48 configurações distintas da grelha mais a regra natural nos 6 principais
e nos 18 activos; 2 OOS, 6 e 18 activos, a mesma configuração; 1 em 2020-09 a 2022-12).
Resultados intermédios em `pesquisa/resultados/bandas_*.csv`.

## 1. Hipótese

A REVOU media o desvio do preço a uma EWMA de 96 h e exigia que a reversão à média tivesse uma
meia-vida de 3 a 24 h. O portão nunca abriu (0 de 4799 horas) porque a meia-vida de um desvio a uma
âncora de 96 h é, por construção, da ordem das dezenas de horas; e sem portão a regra deu 44 sinais
a -0,38 R. A correcção testada aqui tira o portão e torna a âncora e o horizonte coerentes: para
uma âncora de J velas de 15 m, o sigma é a volatilidade realizada de 15 m (EWMA de retornos ao
quadrado com a mesma meia-vida) escalada ao desvio típico de um passeio aleatório face a essa
âncora, o alvo é a própria âncora e a saída por tempo é J velas.

A razão económica para esperar vantagem: em perpétuos de cripto, um desvio de 2 sigma face a uma
média de 1 a 4 dias é frequentemente o resultado de posicionamento alavancado forçado (liquidações
em cascata, stops), e não de informação; quando a pressão forçada acaba, o preço deveria voltar
parcialmente à âncora. Se isto for verdade, o retorno médio nas 2 a 24 h seguintes a |z| > 2 tem de
ser a favor da reversão e de tamanho superior aos 13 bps de custo mais o que o stop come: com
R de 2,5 a 5 % do preço, cerca de 0,2 R acima da nula, isto é, 50 a 100 bps por trade. A hipótese
é falsificável no estudo de eventos antes de qualquer regra de stop e alvo.

As taxas de base já eram contra: o rácio de variâncias de 1 h a 24 h (VR24) é 0,94 na amostra
inteira e 0,99 a 1,02 em 2025-2026; a autocorrelação de 1 h é -0,006 (BTC) a -0,03 (LTC), 1 a 3 %
da variância, muito abaixo do custo.

## 2. Dados e características (tudo causal)

18 perpétuos USDT-M da Binance, velas de 15 m, 2020-09 a 2026-09-09. A família usa só preço e
volume (o VWAP usa `volume_quote` e `volume_base`), por isso correu nos 18 activos e em 2020-2022;
não usa liquidações, OI, funding nem ls_ratio. Verificou-se a causalidade por perturbação da cauda
(multiplicar as últimas 500 velas de BTC por 1,3): diferença máxima no passado 0,0 em todas as
colunas.

Âncoras (J em velas de 15 m) e escala do z:

| Nome | Âncora | Escala do z | |z| > 2 no IS (BTC) |
| --- | --- | --- | --- |
| ewma24 | EWMA do log-preço, meia-vida 48 velas (J = 96, 24 h) | sigma_15m(meia-vida 48) x sqrt(lambda^2/(1-lambda^2)), lambda = 2^(-1/48) | 3,3 % |
| ewma48 | EWMA, meia-vida 96 (J = 192, 48 h) | idem com meia-vida 96 | 3,8 % |
| ewma96 | EWMA, meia-vida 192 (J = 384, 96 h), a âncora da REVOU | idem com meia-vida 192 | 4,6 % |
| vwap96 | VWAP móvel de 384 velas (só no estudo de eventos) | sigma_15m(meia-vida 192) x sqrt(384/3) | 2,0 % |
| media96 | média simples de 384 velas (a sugestão do Bot: bandas sigma em torno da média de 4 dias) | desvio padrão móvel do log-preço em 384 velas (Bollinger clássico) | 15,9 % |

O factor sqrt(lambda^2/(1-lambda^2)) é o desvio padrão teórico de (x - EWMA) quando x é um passeio
aleatório com inovações sigma_15m; um passeio aleatório dá |z| > 2 em 4,5 % das velas, e as âncoras
EWMA ficam em 3,3 a 4,6 %, isto é, a escala está bem calibrada. As bandas Bollinger de 4 dias ficam
fora de 2 sigma 16 % do tempo porque o desvio padrão móvel de um passeio aleatório subestima o
desvio face à média (é a falha conhecida das bandas de Bollinger: a largura mede a dispersão da
janela, não a incerteza da âncora). Sigma mediano no IS (BTC): 1,3 % (ewma24), 1,9 % (ewma48),
2,7 % (ewma96 e vwap96), 1,5 % (media96).

Modos (sempre contra o desvio): `toque` = primeiro fecho com |z| >= Z, lado -sinal(z);
`reentrada` = primeiro fecho com |z| < Z depois de ter estado fora, lado -sinal(z anterior) (a regra
REVOU). Filtros de regime, todos observáveis no fecho k: `vol` = sigma_15m(meia-vida 16 velas) /
sigma_15m(meia-vida 384) < 1 (volatilidade curta abaixo da longa); `vr` = VR(24) de Lo-MacKinlay
sobre retornos de 1 h nos últimos 30 dias (720 velas de 1 h, recalculado de 4 em 4 h, projectado
pela vela de 1 h fechada) < 1 (regime de reversão); `tend` = z do fecho de 4 h face à média de 42
velas de 4 h (7 dias) com o mesmo sinal do lado (comprar quedas em tendência de subida). No IS
(BTC) o filtro `vol` está aberto 68 % do tempo e o `vr` 54 %.

Stop e alvo fixados a priori e fora da grelha: stop a 1 sigma do fecho do sinal no sentido
contrário ao trade (fica perto de |z| = 3), alvo = preço da âncora no fecho do sinal, saída por tempo
ao fecho da vela J. Entrada na abertura da vela seguinte, custo 6,5 bps por lado, uma posição por
activo, tudo em `harness.simular`.

## 3. Estudo de eventos no IS (2023-01-01 a 2025-03-31), antes de qualquer regra

Retorno em bps a favor do lado (contra o desvio), da abertura da vela seguinte ao sinal ao fecho da
vela k+h; eventos espaçados de pelo menos h velas por activo; erro padrão agrupado por bloco de 4 h
entre activos (os activos movem-se juntos; o erro padrão ingénuo subestima em 1,5 a 2 vezes).
Horizontes 8, 16, 32, 96 e 384 velas = 2, 4, 8, 24 e 96 h. Ficheiros `bandas_eventos6_IS.csv`,
`bandas_eventos18_IS.csv`, `bandas_eventos18_PRE.csv`.

### 3.1 Z = 2 (a hipótese da família), 6 principais

| Âncora, modo | n (8 h) | 2 h | 4 h | 8 h | 24 h | 96 h |
| --- | --- | --- | --- | --- | --- | --- |
| ewma24 toque | 1861 | -5 (t -0,7) | 0 (-0,0) | -17 (-1,3) | -18 (-0,9) | -40 (-0,9) |
| ewma24 reentrada | 1852 | -6 (-1,0) | -4 (-0,5) | -15 (-1,3) | -17 (-0,9) | -27 (-0,7) |
| ewma48 toque | 1407 | -7 (-0,9) | -16 (-1,6) | -30 (-2,3) | -65 (-2,8) | -76 (-1,5) |
| ewma48 reentrada | 1406 | -6 (-0,8) | -8 (-0,8) | -24 (-1,8) | -57 (-2,2) | -55 (-1,0) |
| ewma96 toque | 1001 | -13 (-1,5) | -21 (-1,8) | -47 (-2,6) | -103 (-3,4) | -169 (-2,7) |
| ewma96 reentrada (REVOU) | 987 | -3 (-0,4) | +2 (0,2) | -28 (-1,4) | -74 (-2,5) | -140 (-2,2) |
| vwap96 toque | 781 | -6 (-0,5) | -7 (-0,4) | -22 (-0,9) | -55 (-1,7) | -144 (-2,2) |
| vwap96 reentrada | 784 | 0 (-0,0) | +3 (0,2) | -27 (-1,1) | -54 (-1,7) | -157 (-2,3) |
| media96 toque (Bollinger 4 d) | 4878 | -4 (-1,4) | -9 (-2,0) | -11 (-1,7) | -21 (-1,7) | -4 (-0,1) |
| media96 reentrada | 4783 | -3 (-1,3) | -6 (-1,7) | -10 (-1,7) | -20 (-1,8) | -22 (-0,8) |

48 das 50 células são negativas e as outras duas ficam em +2 e +3 bps (t 0,2), abaixo do custo. A fracção de eventos positivos anda em 0,49 a 0,56 e as
medianas são ligeiramente positivas (5 a 20 bps a 4 h), isto é, a maioria dos desvios recua um pouco,
mas a minoria que continua anda muito mais: a média é negativa. Quanto mais longa a âncora e o
horizonte, pior: a 2 sigma face a 96 h, o preço CONTINUA em média 100 a 170 bps nas 24 a 96 h
seguintes. Este é o resultado oposto ao da hipótese, e é significativo (t -2,2 a -3,4) em 7 células.

Nos 18 activos o quadro é o mesmo com menos amplitude: ewma48 toque a 24 h -58 bps (t -2,5),
ewma96 toque a 24 h -52 (t -2,0), ewma96 reentrada a 24 h -37 (t -1,5), ewma24 toque a 4 h -6
(t -0,6), media96 toque a 4 h -10 (t -2,2); nenhuma célula com Z = 2 é positiva acima de 0,3 t.

Z = 2,5 nos 6 principais: ewma24 reentrada -31 bps a 4 h (t -2,3) e -76 a 24 h (t -2,4); ewma48 e
ewma96 entre -8 e -139; só vwap96 toque é positivo (+30 a +72 bps, t 0,4 a 1,2, n 91 a 159).

### 3.2 Z = 3 (a pista), toque

| Amostra | Âncora | n (2 h) | 2 h | 4 h | 8 h | 24 h | 96 h |
| --- | --- | --- | --- | --- | --- | --- | --- |
| IS, 6 principais | ewma24 | 116 | +68 (t 2,5) | +49 (1,5) | +55 (1,1) | +40 (0,7) | +52 (0,5) |
| IS, 6 principais | ewma48 | 110 | +96 (3,4) | +78 (2,0) | +79 (1,3) | +131 (1,6) | +69 (0,6) |
| IS, 6 principais | ewma96 | 111 | +113 (3,5) | +134 (2,5) | +136 (1,9) | +240 (2,3) | +328 (1,9) |
| IS, 18 activos | ewma24 | 367 | +26 (1,1) | +22 (0,7) | -41 (-0,5) | -6 (-0,1) | +52 (0,4) |
| IS, 18 activos | ewma48 | 341 | +37 (1,5) | +36 (0,7) | 0 (-0,0) | +108 (1,2) | +82 (0,6) |
| IS, 18 activos | ewma96 | 287 | +64 (1,7) | +78 (1,3) | +82 (1,0) | +234 (2,0) | +255 (1,6) |
| 2020-2022, 18 activos | ewma24 | 265 | +94 (1,9) | +158 (2,2) | +84 (1,4) | +106 (0,8) | -369 (-2,2) |
| 2020-2022, 18 activos | ewma48 | 254 | +86 (1,6) | +114 (1,3) | +59 (0,8) | +61 (0,4) | -473 (-2,6) |
| 2020-2022, 18 activos | ewma96 | 250 | +81 (1,4) | +89 (0,9) | +107 (1,1) | +75 (0,4) | -460 (-2,2) |

Leitura: a 3 sigma há um ressalto curto (2 a 4 h) de 60 a 130 bps, positivo nos três períodos e
nas três âncoras EWMA, mas só acima de 2 erros padrão nos 6 principais no IS (3 células em 150
testadas; com 150 testes o máximo esperado de |t| sob a nula anda em 2,7 a 3). Nos 18 activos fica
em 1,1 a 1,7 e em 2020-2022 em 1,4 a 1,9. O modo reentrada a Z = 3 é mais fraco (6 principais a
2 h: +14, +43, +57 bps, t 0,6, 1,7, 1,8). A 96 h o sinal inverte em 2020-2022 (-370 a -470 bps): o
que se ganha em 2 h perde-se com folga se se ficar 4 dias. O vwap96 a Z = 3 tem 10 a 16 eventos
nos 6 principais: não conta. Isto NÃO é a hipótese da família (Z = 2, alvo na âncora, saída J) e
não foi testado no OOS; fica registado como hipótese a pré-registar, com saída curta.

### 3.3 Retorno bruto por intervalo de z (IS, 18 activos, amostragem sem sobreposição)

Retorno não assinado nas h velas seguintes, por intervalo de z no fecho k; se houvesse reversão, o
retorno teria o sinal contrário ao de z. Ficheiros `bandas_buckets_IS.csv` (24 h) e `bandas_buckets16_IS.csv` (4 h).

| Âncora | z | n (24 h) | 24 h, bps (t) | n (4 h) | 4 h, bps (t) |
| --- | --- | --- | --- | --- | --- |
| ewma24 | -3..-2 | 139 | -94 (-1,6) | 891 | +12 (0,5) |
| ewma24 | -1..1 | 11101 | +13 (1,2) | 66603 | +1 (0,6) |
| ewma24 | 2..3 | 262 | +75 (1,5) | 1403 | +31 (3,2) |
| ewma48 | -3..-2 | 140 | -7 (-0,1) | 852 | +18 (0,5) |
| ewma48 | 2..3 | 247 | +39 (0,7) | 1469 | +17 (1,7) |
| ewma96 | -3..-2 | 118 | -24 (-0,3) | 712 | +2 (0,0) |
| ewma96 | 1..2 | 1712 | +38 (1,8) | 10337 | +2 (0,6) |
| ewma96 | 2..3 | 255 | +7 (0,2) | 1492 | +8 (0,8) |
| media96 | <-3 | 230 | -102 (-1,4) | 1344 | +12 (0,6) |
| media96 | >3 | 239 | +20 (0,3) | 1320 | +35 (3,2) |

Não há relação monótona negativa entre z e o retorno seguinte em nenhuma âncora. Depois de z em
2..3 (ewma24) ou > 3 (media96) o retorno a 4 h é POSITIVO em +31 e +35 bps (t 3,2): os desvios para
cima continuam. Depois de z em -3..-2 o retorno a 24 h é negativo em ewma24 (-94): as quedas também
continuam. O IS (2023-01 a 2025-03) foi um mercado em alta com deriva positiva (+13 bps por dia no
intervalo central), o que explica parte da assimetria, mas não explica a continuação das quedas.

### 3.4 Filtros de regime (IS, 18 activos, Z = 2)

Para cada âncora e modo, o retorno a favor do lado dividido pelo estado do filtro. Ficheiro
`bandas_filtros_IS.csv` (96 células). Resumo:

* `vol` (volatilidade curta abaixo da longa): piora em 15 das 16 células. Exemplos a 24 h:
  ewma96 reentrada -167 bps (t -3,1, n 123) com o filtro aberto contra -23 (t -0,9, n 1123) fechado;
  ewma48 reentrada -82 (t -2,3) contra -27 (t -1,1); ewma24 reentrada -42 contra -6. Um desvio de
  2 sigma com a volatilidade curta ainda baixa é o início de um movimento, não o fim.
* `vr` (VR24 < 1, regime de reversão): também piora, em 13 das 16 células. A 4 h, ewma48 toque -28 bps (t -2,1) com
  VR < 1 contra +7 (t 0,6) com VR >= 1; ewma24 toque -11 contra +4; ewma48 reentrada -13 contra +14
  (t 1,3). A 24 h, ewma24 toque -37 (t -1,7) contra +10; ewma48 toque -72 (t -2,7) contra -24. O
  rácio de variâncias dos últimos 30 dias não prevê reversão nos 30 dias seguintes; o melhor estado
  é o contrário do esperado e mesmo assim fica em +4 a +14 bps, abaixo do custo.
* `tend` (contra o desvio e a favor da tendência de 7 dias): nas âncoras EWMA quase nunca acontece
  (um desvio de 2 sigma face a 96 h está, por construção, do lado da tendência: n = 0 a 292 em 27
  meses, contra 2200 a 4500 sem filtro); em ewma24 dá +1 bps a 4 h (n 292) e -59 a 24 h (n 274). Em
  media96 há uma célula positiva: reentrada a 24 h +54 bps (t 2,3, n 495) contra -10 sem o filtro;
  a 4 h a mesma célula dá +5 (t 0,6). É 1 célula com t > 2 em 96 (esperam-se 2 a 5 por acaso) e
  não é confirmada no horizonte vizinho. Na grelha (secção 5) essa configuração dá -0,125 R.

Conclusão do estudo de eventos: a hipótese da família (reversão depois de 2 sigma face a uma
âncora coerente de 1 a 4 dias) é falsificada no IS: o retorno a favor é negativo em 50 de 50 células
nos 6 principais e significativamente negativo em 7; nenhum filtro de regime observável a torna
positiva. Pelo protocolo, a família é NEGATIVA e a regra natural corre uma vez só para registo.

### 3.5 Por activo (IS)

Regra REVOU (ewma96 reentrada Z = 2) a 24 h: SOL -154 bps (t -2,2, n 80), DOGE -200 (t -2,2, n 70),
BTC -55 (t -1,6, n 74), ETH -17, APT -152, ARB -81, TRX -66; positivos só ADA +50, SUI +50, BCH +29,
OP +20, DOT +17, BNB +2, LTC +5, todos com t < 0,7. Nenhum activo com t > 1. Ewma24 toque a 4 h:
TRX -22 (t -2,6), ETH -20 (t -1,9), LINK -27 (t -1,5); XRP +29 (t 1,2) é o único acima de 1.

## 4. Regra natural (fixada a priori) no IS, e a nula

Regra: ewma96, reentrada, Z = 2, sem filtro, stop a 1 sigma, alvo na âncora, saída ao fim de 384
velas (a REVOU sem portão). R mediano 4,3 % do preço nos 6 principais e 5,1 % nos 18 (a nula
teórica para este R é -13 bps / R = -0,03 R).

| Amostra | n | média R (ep) | PF | acerto | DD R | stop / alvo / tempo | activos + | metades |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| IS, 6 principais | 532 | -0,163 (0,049) | 0,73 | 36,8 % | 100,0 | 57 / 17 / 26 % | 0 de 6 | -37,4 / -49,4 |
| IS, 18 activos | 1502 | -0,092 (0,030) | 0,84 | 40,1 % | 180,6 | 55 / 18 / 27 % | 4 de 18 | -107,7 / -29,8 |
| Nula, mesma escala, 18 activos | 2910 | -0,070 (0,022) | 0,88 | 39,6 % | 300,3 | 52 / 19 / 29 % | | |

Nula: 300 sinais ao acaso por activo (semente 7) no IS com stop 1 sigma_ewma96, alvo 2 R, 384 velas.
A regra natural nos 18 activos é indistinguível da nula (-0,092 contra -0,070, diferença 0,6 erro
padrão); nos 6 principais é pior do que a nula em 1,7 erros padrão. R bruto médio -0,131 (6) e
-0,062 (18): a perda não é só custo.

Por activo (IS, 6 principais): BNB -0,19 (n 81), BTC -0,02 (80), DOGE -0,16 (95), ETH -0,26 (107),
SOL -0,20 (93), XRP -0,11 (76). Nos 18: positivos DOT +0,12 (79), LTC +0,15 (64), SUI +0,18 (65),
OP +0,02 (84); mais negativos APT -0,28, ETH -0,26, SOL -0,20, BNB -0,19, TRX -0,18.
Por ano (18): 2023 -0,183 (n 561), 2024 -0,083 (740), 2025 T1 +0,132 (201, ep 0,087).
Critérios no IS (18): H1 passa (1502), H2 falha (-0,092), H3 falha (0,84), H4 passa (4), H5 falha
(180,6 R), H6 falha (-107,7 / -29,8). 2303 sinais ignorados por já haver posição aberta (a posição
dura em média 49 h).

## 5. Grelha no IS (48 configurações, 18 activos, todas registadas)

Grelha declarada antes de ver os resultados: 4 âncoras (ewma24, ewma48, ewma96, media96) x 2 modos
x 4 filtros com Z = 2 (32) mais 4 âncoras x 2 modos x Z em {2,5; 3,0} sem filtro (16). Stop, alvo e
saída fixos (secção 2). Ficheiro `bandas_grelha_IS.csv`.

Resumo: 44 das 48 configurações têm média negativa; nenhuma chega a +0,15 R nem a PF 1,3; nenhuma
tem as duas metades positivas. As únicas positivas são as de Z = 3 em ewma48 e ewma96:

| Âncora, modo, Z, filtro | n | média R (ep) | PF | acerto | DD R | activos + | metades + | R % preço |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ewma96 toque 3,0 nenhum | 220 | +0,132 (0,098) | 1,23 | 42,7 % | 20,1 | 12 | 1 de 2 | 6,2 |
| ewma96 reentrada 3,0 nenhum | 221 | +0,118 (0,091) | 1,22 | 44,8 % | 23,5 | 11 | 1 de 2 | 6,6 |
| ewma48 toque 3,0 nenhum | 292 | +0,053 (0,081) | 1,09 | 42,1 % | 25,2 | 9 | 1 de 2 | 4,1 |
| ewma48 reentrada 3,0 nenhum | 309 | +0,010 (0,072) | 1,02 | 41,8 % | 26,6 | 10 | 1 de 2 | 4,4 |
| ewma96 reentrada 2,0 vr | 975 | -0,061 (0,038) | 0,89 | 40,9 % | 96,0 | 5 | 0 | 5,2 |
| ewma96 reentrada 2,5 nenhum | 629 | -0,067 (0,050) | 0,89 | 39,1 % | 64,6 | 7 | 0 | 5,9 |
| ewma96 reentrada 2,0 nenhum (natural) | 1502 | -0,092 (0,030) | 0,84 | 40,1 % | 180,6 | 4 | 0 | 5,1 |
| media96 reentrada 2,0 vr | 6767 | -0,092 (0,016) | 0,87 | 36,3 % | 743,1 | 2 | 0 | 2,0 |
| media96 reentrada 2,0 nenhum | 9293 | -0,104 (0,014) | 0,85 | 36,0 % | 1101,1 | 1 | 0 | 2,1 |
| ewma24 toque 2,0 nenhum | 4246 | -0,125 (0,019) | 0,80 | 37,7 % | 545,7 | 2 | 0 | 2,4 |
| ewma24 reentrada 2,0 nenhum | 4580 | -0,139 (0,017) | 0,77 | 39,4 % | 639,4 | 0 | 0 | 2,5 |
| ewma48 toque 2,0 nenhum | 2475 | -0,179 (0,024) | 0,72 | 35,9 % | 454,3 | 0 | 0 | 3,5 |
| ewma48 toque 2,0 vol | 533 | -0,262 (0,051) | 0,62 | 32,8 % | 146,9 | 3 | 0 | 2,9 |
| ewma48 toque 2,0 tend | 26 | -0,481 (0,190) | 0,34 | 23,1 % | 17,6 | 3 | 0 | 3,8 |

Efeito dos filtros na grelha (Z = 2): `vol` piora a média em todas as 8 células onde se aplica
(ewma96 reentrada -0,092 para -0,248; ewma24 reentrada -0,139 para -0,223; media96 toque -0,127
para -0,166); `vr` melhora 0,02 a 0,03 R em ewma96 e media96 e piora 0,03 a 0,05 em ewma24, sempre
negativo; `tend` deixa 0 a 292 trades nas âncoras EWMA e -0,125 a -0,127 R em media96. Z = 2,5 é
pior do que Z = 2 em ewma24 e ewma48 e igual em ewma96 e media96. As bandas de 4 dias do Bot
(media96) dão -0,09 a -0,17 R em todas as 12 configurações, com 4400 a 9300 trades, acerto 25 a
37 % e drawdowns de 470 a 1145 R.

Escolha pelo centro de um patamar (regra declarada no código: configurações com média >= 0,15 R,
PF >= 1,3 e n >= 100; o grupo âncora x modo com mais membros; dentro dele a mediana): o conjunto é
vazio, logo não há patamar e a regra natural é a única que vai ao OOS. As quatro configurações de
Z = 3 ficam a 0,2 a 1,3 erros padrão de zero, com uma metade negativa cada, e são coerentes com o
estudo de eventos (ressalto curto que a saída a 2 a 4 dias dilui): não servem para escolher.

## 6. OOS (2025-04-01 a 2026-09-09), avaliado UMA vez, regra natural

| Amostra | n | média R (ep) | PF | acerto | DD R | activos + | 2025 (n) | 2026 (n) | metades |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 6 principais | 341 | -0,072 (0,066) | 0,88 | 37,8 % | 41,8 | 2 de 6 | -0,008 (186) | -0,149 (155) | -1,5 / -23,1 |
| 18 activos | 970 | -0,046 (0,038) | 0,92 | 41,2 % | 112,6 | 7 de 18 | +0,106 (506) | -0,212 (464) | +51,8 / -96,4 |

R mediano 4,0 % (6) e 4,9 % (18) do preço; R bruto médio -0,037 e -0,014 (a perda líquida é
quase toda custo: a regra não tem vantagem nem desvantagem bruta no OOS, o que é coerente com o
VR24 de 0,99 a 1,02 em 2025-2026). Saídas: 56 / 22 / 22 % (6) e 53 / 19 / 28 % (18) por stop, alvo
e tempo.

Por activo (OOS): XRP +0,152 (n 54, ep 0,174), ETH +0,010 (62), BTC -0,062 (52), BNB -0,142 (53),
SOL -0,148 (66), DOGE -0,239 (54); nos 18, positivos também LTC +0,094 (44), APT +0,090 (49),
ADA +0,048 (53), LINK +0,031 (57), NEAR +0,028 (48); mais negativos DOGE -0,239, ARB -0,202,
SUI -0,191. Nenhum activo com |média| > 1 erro padrão.

Critérios H1 a H6 no OOS, 6 principais (a amostra do utilizador): H1 passa (341 >= 100); H2 falha
(-0,072 < 0,15); H3 falha (0,88 < 1,3); H4 falha (2 < 3); H5 falha (41,8 > 10); H6 falha (2.a metade
-23,1). Nos 18 activos: H1 passa, H4 passa (7), H2, H3, H5 e H6 falham. A família NÃO PASSA.

## 7. Robustez adicional: 2020-09 a 2022-12 (regra só de preço), 18 activos

n 1304, média -0,133 R (ep 0,033), PF 0,78, acerto 37,3 %, DD 195,8 R, 2 de 16 activos positivos
(TRX +0,19, LINK +0,07; APT e OP só existem a partir de 2022), R mediano 6,9 % do preço (volatilidade
mais alta). Por ano: 2020 -0,010 (n 147), 2021 -0,151 (532), 2022 -0,147 (625). Metades -88,8 /
-85,0. Em 2020-2022 o estudo de eventos tinha dado ressalto positivo de 20 a 30 bps a 2 a 4 h depois
de 2 sigma (t 1,7 a 2,5) mas -75 a -130 bps a 96 h: a regra, com alvo na âncora e saída a 4 dias,
apanha a segunda parte. Nos três períodos (2020-22, IS, OOS) a regra natural é negativa: -0,133,
-0,092, -0,046 R.

## 8. Honestidade: o que pode estar errado, o que é frágil, o que não se testou

* O estudo de eventos tem 150 células por amostra (5 âncoras x 2 modos x 3 Z x 5 horizontes), mais
  96 de filtros e 70 de intervalos de z; os t agrupados têm de ser lidos com esse número em mente.
  O resultado NEGATIVO não depende disso: a 2 sigma, 48 das 50 células dos 6 principais são negativas e as outras duas não passam de +3 bps.
  O resultado da pista (Z = 3, 2 a 4 h) depende: 3 células com t > 2,5 em 150, não confirmadas acima
  de 2 nos 18 activos nem em 2020-2022.
* O erro padrão agrupado por bloco de 4 h trata blocos diferentes como independentes; com horizontes
  de 24 a 96 h e eventos espaçados por activo mas não entre activos, ainda há sobreposição entre
  blocos e os t a 24 e 96 h estão sobrestimados (em módulo) talvez em 1,3 a 1,5 vezes. Isso enfraquece
  tanto os -3,4 como os +2,3.
* O IS 2023-01 a 2025-03 é um mercado em alta; a deriva positiva (cerca de 13 bps por dia) favorece
  compras e penaliza vendas simetricamente na média "a favor do lado", por isso não cria por si a
  média negativa, mas torna assimétricos os resultados por lado (não separados neste relatório; o
  CSV de trades permite fazê-lo).
* A escala do z assume passeio aleatório com inovações sigma_15m; em regimes com saltos a
  distribuição tem caudas mais pesadas e |z| > 3 acontece mais do que 0,3 %. Isso não muda a
  conclusão a 2 sigma.
* O stop a 1 sigma do fecho é uma escolha; um stop mais largo reduz a fracção de stops (53 a 60 %)
  mas aproxima o resultado da nula (-13 bps / R), não de +0,15 R, porque o retorno bruto a favor é
  negativo ou nulo. Não se testaram alvos em múltiplos de R nem saídas curtas (2 a 4 h) porque a
  hipótese da família é a reversão até à âncora; a pista de Z = 3 pediria exactamente isso e tem de
  ser um teste novo, pré-registado, e não uma continuação desta grelha.
* O filtro `vr` usa VR(24) em janela de 30 dias; janelas mais longas (90 dias) ou q = 4 não foram
  testadas. O filtro `tend` quase não deixa passar sinais nas âncoras EWMA, por construção; uma
  versão com âncora de tendência mais curta (2 dias) mudaria isso, mas o estudo de eventos sem filtro
  já é negativo em todas as células.
* Uma posição por activo e saída a 384 velas descartam 60 % dos sinais (2303 ignorados contra 1502
  trades no IS); correr sem essa restrição daria mais trades sobrepostos, não mais vantagem (o
  estudo de eventos, que não tem a restrição, já é negativo).
* O VWAP de 384 velas (vwap96) não entrou na grelha; no estudo de eventos é o menos negativo a Z = 2
  (t -0,5 a -2,3) e o único positivo a Z = 2,5 nos 6 principais (+30 a +72 bps, t <= 1,2, n 91 a
  159). Não há aí nada acima de 2 erros padrão.
* Binance não é a Hyperliquid: os preços são próximos (o estudo é só de preço), mas uma execução
  manual na Hyperliquid na abertura da vela seguinte terá deslize adicional em movimentos de 2 a 3
  sigma; isso só piora os números.
* Não se testou: lados em separado (compras e vendas), âncoras de 8 e 12 h, z do preço face à
  âncora com sigma de Parkinson, o ressalto de Z = 3 com saída a 8 ou 16 velas, nem o inverso da
  hipótese (seguir o rompimento de 2 sigma a 24 h, que no IS dá +50 a +100 bps brutos a 24 h mas em
  2020-2022 a 2 a 4 h dá o contrário).

## 9. Ficheiros

* `pesquisa/familia_bandas.py`: código (etapas `eventos`, `natural`, `grelha`, `oos`, `tudo`).
* `pesquisa/ensaios/bandas.csv`: 53 linhas (M), uma por configuração e período.
* `pesquisa/resultados/bandas_eventos6_IS.csv`, `bandas_eventos18_IS.csv`, `bandas_eventos18_PRE.csv`:
  estudo de eventos; `bandas_buckets_IS.csv`, `bandas_buckets16_IS.csv`: por intervalo de z;
  `bandas_filtros_IS.csv`: filtros de regime; `bandas_grelha_IS.csv`: grelha;
  `bandas_trades_natural_IS6.csv`, `bandas_trades_natural_IS18.csv`, `bandas_trades_nula_IS18.csv`,
  `bandas_trades_OOS6.csv`, `bandas_trades_OOS18.csv`, `bandas_trades_PRE18.csv`: trades.
