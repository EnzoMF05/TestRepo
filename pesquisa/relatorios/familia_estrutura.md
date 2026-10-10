# Família ESTRUTURA: varrimento de extremos de 4 h, exaustão no 1 h, reconquista no 15 m

Resultado: NEGATIVA. O estudo de eventos no IS não mostra nada acima de 2 erros padrão em nenhum
modo, filtro ou horizonte; a regra natural, fixada a priori, perde no IS (-0,074 R), no OOS
(-0,055 R, avaliado uma vez) e em 2020-2022 (-0,048 R); nenhuma das 48 configurações da grelha de
sensibilidade tem média positiva em amostra. A família não passa H2, H3, H5 nem H6.

Código: `pesquisa/familia_estrutura.py` (usa `harness.py`). Registo: `pesquisa/ensaios/estrutura.csv`
(M = 53 linhas: 51 no IS, 50 configurações distintas; 1 OOS; 1 período 2020-09 a 2025-03).
Resultados intermédios em `pesquisa/resultados/estrutura_*.csv`.

## 1. Hipótese

A ideia do método (sugestão 7 do Bot do utilizador): um extremo de 4 h que é varrido (a mínima fura o
mínimo das N velas anteriores e o fecho volta acima) é um lugar onde stops de compradores foram
executados e vendedores tardios ficaram presos; se a vela de 1 h que fez o extremo mostra exaustão
(amplitude e volume altos, fecho no terço oposto, pressão taker a secar) e o 15 m reconquista o nível,
o movimento seguinte deveria ser a favor da reconquista, com um stop natural abaixo do extremo
varrido e um alvo a 2 a 3 R. É uma hipótese de microestrutura (liquidez varrida, posicionamento
preso), não de reversão estatística à média, por isso não depende da meia-vida que falsificou o
REVOU. O que tem de acontecer para haver vantagem: a esperança de retorno a favor do lado tem de
exceder 13 bps de custo mais o que o stop "come", isto é, cerca de 0,2 R acima da nula com R de 2 a
3 % do preço (40 a 60 bps por trade). A hipótese é falsificável no estudo de eventos: se, em média, o
preço não anda a favor da reconquista nas 4 a 48 h seguintes, não há regra de stop e alvo que a salve.

## 2. Dados e características (tudo causal)

18 perpétuos USDT-M da Binance, velas de 15 m, 2020-09 a 2026-09-09. A família usa só preço, volume
e volume taker (campos reais das klines), por isso correu nos 18 activos e também em 2020-2022; não
usa liquidações, OI, funding nem ls_ratio (ver `validar_colunas.py`: nos alts a série de liquidações
é modelada). Velas superiores de 1 h e 4 h vêm de `harness.agregar` e são levadas ao 15 m por
`projectar_superior`, que só usa velas superiores já fechadas (fecho_em <= fecho da vela de 15 m).

Modos de autorização (N em velas superiores):

* `4h_N` (N = 12, 24; 2 e 4 dias): `harness.varrimento` na vela de 4 h. O sinal nasce no fecho da
  vela de 4 h varrida e a entrada é na abertura da vela de 15 m seguinte. Nota de honestidade: neste
  modo o "fecho de 15 m acima do nível" é sempre verdadeiro no instante do fecho do 4 h (é a mesma
  vela), por isso a reconquista no 15 m não acrescenta informação; verificou-se nos trades: 100 % dos
  sinais nascem ao minuto 0 de uma hora múltipla de 4.
* `1h_N` (N = 24, 48): o mesmo na vela de 1 h (só no estudo de eventos).
* `15m4h_N` (N = 12, 24): varrimento AO VIVO no 15 m do mínimo (máximo) das últimas N velas de 4 h
  fechadas: uma mínima de 15 m fura o nível e, dentro de 8 velas (2 h), um fecho de 15 m volta acima
  dele (fecho anterior abaixo, ou furo e reconquista na mesma vela). Aqui o 15 m decide a entrada.

Filtros acumulativos: `A` só varrimento e reconquista; `B` + exaustão no 1 h (alguma das últimas 4
velas de 1 h fechadas com amplitude E volume acima do percentil 0,80 de 168 h e fecho no terço oposto
ao varrimento; e desequilíbrio taker da última vela de 1 h a favor do trade face à média das 3
anteriores); `C` + posição no intervalo das últimas 120 velas de 4 h (20 dias) nos 20 % extremos.

Stop: extremo do varrimento menos (mais) 0,5 ATR(14) de 1 h. Alvo em múltiplos de R. Saída por
tempo ao fecho de N_MAX velas de 15 m. Simétrico para vendas. Parâmetros fixos declarados antes de
olhar para trades: P = 0,80, janela 168 h, terço = 2/3, 4 velas de 1 h, 120 velas de 4 h, zona 20 %,
folga 0,5 ATR, janela de reconquista 8 velas, ATR 14.

## 3. Estudo de eventos no IS (2023-01-01 a 2025-03-31), antes de qualquer regra

Retorno em bps, a favor do lado, da ABERTURA da vela de 15 m seguinte ao sinal até ao FECHO de k+h
(h = 16, 48, 96, 192 velas = 4, 12, 24, 48 h), 18 activos em conjunto. O erro padrão é AGRUPADO por
bloco de 4 h (os activos varrem extremos ao mesmo tempo; o erro ingénuo subestima 1,5 a 3 vezes).
`media_R` é o mesmo retorno dividido pela distância entrada-stop (R), sem simular o stop nem custos.
`deriva` é a média incondicional do retorno a h velas no IS (18 activos): +4,6 bps a 48 velas,
+9,2 a 96; `excesso` = média menos deriva (para compras) ou mais deriva (para vendas).

Resumo: em 72 células "ambos os lados" (6 modos x 3 filtros x 4 horizontes) o maior t positivo é
0,67 (15m4h_12 B, 4 h: +12,7 bps, EP 19). Compras: o maior t é 1,79 (1h_24 B, 48 velas: +29 bps,
EP 16, excesso +25 bps, media_R +0,05); com 72 células de compra e 72 de venda isto é o que o acaso
dá. Vendas: sistematicamente negativas (t até -4,7 em 1h_24 C a 192 velas, -146 bps), e o excesso
sobre a deriva também é negativo (-50 a -160 bps a 192 velas nos filtros B e C): no IS os varrimentos
de máximos foram seguidos de continuação para cima, não de reversão. Em unidades de R, nenhuma célula
tem media_R acima de +0,10 e a maioria está entre -0,05 e -0,30. Critério declarado: nada acima de 2
erros padrão a favor, logo a família é NEGATIVA antes de simular.

## 4. Regra natural (fixada a priori) e ablações no IS

Porque o estudo de eventos foi negativo, não há patamar a escolher. A configuração avaliada no OOS
foi fixada ANTES da grelha, pela descrição do utilizador: `4h_12`, filtro `B`, alvo 2 R, 96 velas de
15 m (24 h), stop 0,5 ATR(1 h) abaixo do extremo. Testaram-se separadamente as três camadas no IS,
18 activos, 6,5 bps por lado:

| filtro | n | média R | EP | PF | acerto | DD (R) | activos > 0 | R mediano (% preço) | nula teórica (R) | stop / alvo / tempo |
|---|---|---|---|---|---|---|---|---|---|---|
| A só varrimento e reconquista | 9610 | -0,075 | 0,012 | 0,87 | 0,41 | 831 | 2 de 18 | 2,16 | -0,060 | 49 % / 18 % / 32 % |
| B + exaustão 1 h (regra natural) | 2427 | -0,074 | 0,021 | 0,84 | 0,45 | 201 | 3 de 18 | 3,11 | -0,042 | 40 % / 11 % / 49 % |
| C + posição no intervalo | 955 | -0,119 | 0,034 | 0,77 | 0,43 | 127 | 3 de 18 | 3,29 | -0,039 | 47 % / 11 % / 42 % |

A nula teórica é -13 bps / R, isto é, o que sinais aleatórios com o mesmo R perdem só em custos
(confirmado em `taxas_de_base.py`: -0,061 R com R de 2,15 %). As três camadas ficam AO NÍVEL ou
ABAIXO da nula: o retorno bruto médio é +0,000 R (A), -0,024 R (B) e -0,072 R (C). Cada filtro reduz
n (9610 para 2427 para 955) sem melhorar a média; o filtro C piora. Por lado no IS (B): compras
-0,063 R (n 1190), vendas -0,084 R (n 1237). Por ano (B): 2023 -0,055 (n 1028), 2024 -0,109
(n 1137), 2025 (jan-mar) +0,008 (n 262). Metades: -0,048 e -0,098.

## 5. Grelha de sensibilidade no IS (48 configurações, todas registadas)

Declarada antes de correr: modos {4h_12, 4h_24, 15m4h_12, 15m4h_24} x filtros {A, B, C} x alvo
{2, 3} R x N_MAX {48, 96} velas. Serve só para medir quantas configurações ficariam positivas em
amostra; NÃO escolheu a regra do OOS. Resultado: 0 de 48 com média > 0; 0 de 48 com média >= 0,15 R;
a melhor é -0,040 R (EP 0,037; 15m4h_12 C, alvo 3 R, 48 velas, n 1018, PF 0,92), a pior -0,143 R
(15m4h_24 A, alvo 3 R, 48 velas, n 11367). Os modos com R pequeno (15m4h A, R mediano 1,5 %) perdem
mais porque o custo pesa mais em R (nula -0,09 R); os filtros C com R de 2,4 a 3,3 % perdem perto da
nula (-0,04 a -0,06 R contra nula -0,04 a -0,05 R). Em nenhum caso há sinal de vantagem bruta.
Tabela completa na secção 9.

## 6. OOS (2025-04-01 a 2026-09-09), avaliado UMA vez, regra natural

| | valor |
|---|---|
| n | 1478 |
| média líquida | -0,055 R (EP 0,027) |
| mediana | -0,19 R |
| PF | 0,88 |
| acerto | 44,2 % |
| DD máximo | 106,5 R |
| retorno bruto médio | -0,001 R (nula teórica -0,045 R para R mediano de 2,90 %) |
| saídas | stop 40 %, alvo 12 %, tempo 48 % |
| activos positivos | 5 de 18 (LTC +0,149 n 75, SUI +0,044 n 89, XRP +0,018 n 75, DOT +0,017 n 79, AVAX +0,000 n 87) |
| 6 principais | BTC -0,030 (n 95), ETH -0,033 (94), SOL -0,030 (80), BNB -0,234 (73), XRP +0,018 (75), DOGE -0,246 (89) |
| por ano | 2025 (abr-dez) -0,076 (n 763, PF 0,85); 2026 (jan-set) -0,033 (n 715, PF 0,93) |
| metades | 1.ª -0,077 (n 743), 2.ª -0,033 (n 735) |
| por lado | compras -0,135 R (n 717); vendas +0,020 R (n 761) |

Critérios: H1 n >= 100: passa (1478). H2 média >= +0,15 R: falha (-0,055). H3 PF >= 1,3: falha
(0,88). H4 >= 3 activos positivos: passa (5; a nula também passa isto com 18 activos). H5 DD <= 10 R:
falha (106,5). H6 duas metades positivas: falha (ambas negativas). Não passa.

## 7. Robustez adicional: 2020-09 a 2022-12 (regra só de preço)

n 1859, média -0,048 R (EP 0,024), PF 0,90, acerto 45 %, DD 105 R, 3 de 16 activos positivos
(XRP +0,184 n 110, AVAX +0,065 n 126, TRX +0,033 n 114). Por ano: 2020 (set-dez) -0,246 (n 276),
2021 -0,022 (n 756), 2022 -0,006 (n 827). Por lado: compras +0,049 R (n 839), vendas -0,128 R
(n 1020). O lado que "funciona" troca com o regime (IS: ambos negativos; OOS: vendas +0,02; 2020-22:
compras +0,05), o que é a assinatura de deriva de mercado, não de vantagem do sinal. Em três períodos
disjuntos, 5764 trades no total, a regra natural dá -0,074, -0,055 e -0,048 R: coerente com a nula
(-0,04 a -0,05 R) menos um pouco.

## 8. Honestidade: o que pode estar errado, o que é frágil, o que não se testou

* A "reconquista no 15 m" no modo `4h_N` é tautológica (secção 2). O modo `15m4h_N`, onde o 15 m
  decide, foi testado e também é negativo (grelha, e estudo de eventos: t máximo 0,67). Uma
  implementação com entrada "limite" no nível, em vez de à abertura da vela seguinte, daria entradas
  melhores mas não resolve um retorno esperado bruto de 0 bps.
* Os parâmetros fixos (P 0,80, 168 h, 0,5 ATR, 120 velas, 20 %) foram escolhidos por mim antes de
  olhar para trades; não foram varridos. Com 48 configurações todas negativas e um estudo de eventos
  plano, não é plausível que uma folga de 0,3 ou um percentil 0,90 mudem o sinal da média.
* A exaustão usa "alguma das últimas 4 velas de 1 h" e o taker da última; é uma de muitas definições
  possíveis. O estudo de eventos sem exaustão (filtro A) já era plano, por isso o problema está na
  premissa (o varrimento prevê o retorno seguinte), não no filtro.
* Não se testou: alvo no lado oposto do intervalo (em vez de múltiplos de R); níveis de 1 d; só
  compras; só o OOS para compras. Uma regra "só vendas" teria OOS +0,02 R com n 761 (abaixo de H2),
  e no IS e em 2020-22 as vendas perderam: escolhê-la agora seria escolher no OOS.
* O lado comprador em `1h_24 B` a 48 velas (+29 bps, t 1,8, excesso +25 bps) é o único vestígio
  positivo; é uma de 144 células por lado e está abaixo de 2 EP agrupados; se se quiser seguir, tem de
  ser com um OOS NOVO (dados posteriores a 2026-09), porque este OOS já foi usado.
* O erro padrão agrupado por bloco de 4 h ainda assume independência entre blocos; eventos
  consecutivos no mesmo activo sobrepõem-se a 96 e 192 velas, por isso os t a esses horizontes são
  optimistas em módulo. Isto só reforça a conclusão negativa.
* Binance não é a Hyperliquid: as mínimas varridas e os stops executados são diferentes de livro
  para livro. Isto afectaria uma regra positiva; não ressuscita uma negativa.
* Sem lookahead: todas as características usam `projectar_superior` (velas superiores fechadas) e
  janelas para trás; os sinais no modo 4h nascem exactamente no fecho da vela de 4 h e entram na
  abertura seguinte. As funções do harness usadas têm teste de causalidade em `teste_harness.py`; o
  código novo (exaustão, 15m4h) usa só rolling/shift/ffill para trás.

Conclusão para a Adriana: o "4 h autoriza, 1 h confirma, 15 m entra" com varrimento de extremos, tal
como descrito, não tem vantagem mensurável em 18 activos e 6 anos; o retorno bruto esperado após a
reconquista é 0 a 10 bps e os custos são 13 bps. Qualquer versão discricionária do método teria de
provar que o olho acrescenta o que a regra não tem, e isso mede-se com o medidor de execução, não com
este backtest.

## 9. Tabelas completas

Estudo de eventos (IS, 18 activos, erro padrão agrupado por bloco de 4 h):

Nota: nas tabelas geradas a partir dos CSV os decimais usam ponto.

#### Ambos os lados, h=48 velas de 15 m

| modo | filtro | n | n_blocos | media_bps | ep_agrupado_bps | t_agrupado | mediana_bps | frac_pos | media_R |
|---|---|---|---|---|---|---|---|---|---|
| 4h_12 | A | 11966 | 3627 | -2.0 | 6.4 | -0.32 | +17.3 | 0.54 | -0.02 |
| 4h_12 | B | 2556 | 1223 | -0.9 | 14.2 | -0.07 | +13.9 | 0.53 | -0.02 |
| 4h_12 | C | 992 | 588 | -28.5 | 28.7 | -0.99 | +20.2 | 0.53 | -0.11 |
| 4h_24 | A | 7811 | 2849 | -12.4 | 8.4 | -1.46 | +19.3 | 0.54 | -0.08 |
| 4h_24 | B | 2026 | 1004 | -11.8 | 16.6 | -0.71 | +11.4 | 0.52 | -0.05 |
| 4h_24 | C | 954 | 564 | -29.3 | 29.7 | -0.99 | +20.6 | 0.53 | -0.10 |
| 1h_24 | A | 31108 | 4469 | -0.9 | 4.1 | -0.21 | +9.9 | 0.52 | -0.06 |
| 1h_24 | B | 4050 | 1654 | -6.0 | 9.6 | -0.62 | +12.7 | 0.53 | -0.05 |
| 1h_24 | C | 1565 | 828 | -15.9 | 17.5 | -0.91 | +35.6 | 0.56 | -0.08 |
| 1h_48 | A | 19927 | 3910 | -1.1 | 5.3 | -0.21 | +12.8 | 0.53 | -0.07 |
| 1h_48 | B | 3183 | 1399 | -5.0 | 11.4 | -0.44 | +17.5 | 0.54 | -0.06 |
| 1h_48 | C | 1437 | 773 | -10.6 | 18.6 | -0.57 | +38.4 | 0.56 | -0.06 |
| 15m4h_12 | A | 39885 | 4161 | -0.2 | 5.6 | -0.03 | +16.0 | 0.53 | -0.07 |
| 15m4h_12 | B | 3368 | 1230 | -0.9 | 19.0 | -0.05 | +22.7 | 0.55 | -0.04 |
| 15m4h_12 | C | 1578 | 691 | -3.6 | 34.2 | -0.10 | +41.2 | 0.57 | -0.05 |
| 15m4h_24 | A | 25524 | 3480 | -8.6 | 7.3 | -1.17 | +19.5 | 0.54 | -0.12 |
| 15m4h_24 | B | 2727 | 1032 | -13.4 | 22.7 | -0.59 | +25.4 | 0.55 | -0.08 |
| 15m4h_24 | C | 1478 | 651 | -11.6 | 36.0 | -0.32 | +40.6 | 0.57 | -0.07 |

#### Ambos os lados, h=96 velas de 15 m

| modo | filtro | n | n_blocos | media_bps | ep_agrupado_bps | t_agrupado | mediana_bps | frac_pos | media_R |
|---|---|---|---|---|---|---|---|---|---|
| 4h_12 | A | 11966 | 3627 | +3.7 | 7.9 | 0.47 | +26.0 | 0.54 | -0.02 |
| 4h_12 | B | 2556 | 1223 | +3.2 | 16.4 | 0.19 | +26.0 | 0.54 | -0.05 |
| 4h_12 | C | 992 | 588 | -18.7 | 31.4 | -0.59 | +31.7 | 0.54 | -0.17 |
| 4h_24 | A | 7811 | 2849 | -3.9 | 9.8 | -0.39 | +28.1 | 0.54 | -0.08 |
| 4h_24 | B | 2026 | 1004 | -1.0 | 19.1 | -0.05 | +30.1 | 0.54 | -0.06 |
| 4h_24 | C | 954 | 564 | -15.2 | 32.4 | -0.47 | +38.0 | 0.54 | -0.14 |
| 1h_24 | A | 31108 | 4469 | -2.0 | 6.3 | -0.31 | +15.6 | 0.52 | -0.10 |
| 1h_24 | B | 4050 | 1654 | -7.4 | 14.6 | -0.51 | +23.4 | 0.53 | -0.12 |
| 1h_24 | C | 1565 | 828 | -15.9 | 25.2 | -0.63 | +47.1 | 0.55 | -0.20 |
| 1h_48 | A | 19927 | 3910 | -5.5 | 8.2 | -0.67 | +21.4 | 0.53 | -0.14 |
| 1h_48 | B | 3183 | 1399 | -7.6 | 17.0 | -0.44 | +33.9 | 0.54 | -0.15 |
| 1h_48 | C | 1437 | 773 | -9.4 | 26.6 | -0.35 | +54.2 | 0.56 | -0.19 |
| 15m4h_12 | A | 39885 | 4161 | +0.1 | 7.7 | 0.02 | +26.4 | 0.53 | -0.09 |
| 15m4h_12 | B | 3368 | 1230 | -2.5 | 19.7 | -0.13 | +43.7 | 0.55 | -0.13 |
| 15m4h_12 | C | 1578 | 691 | -17.1 | 34.2 | -0.50 | +54.1 | 0.56 | -0.21 |
| 15m4h_24 | A | 25524 | 3480 | -9.8 | 9.5 | -1.03 | +27.8 | 0.53 | -0.16 |
| 15m4h_24 | B | 2727 | 1032 | -14.6 | 22.7 | -0.64 | +40.7 | 0.54 | -0.17 |
| 15m4h_24 | C | 1478 | 651 | -26.7 | 35.7 | -0.75 | +52.9 | 0.56 | -0.24 |

#### Compras, h=48 velas de 15 m

| modo | filtro | n | n_blocos | media_bps | ep_agrupado_bps | t_agrupado | mediana_bps | frac_pos | media_R | excesso |
|---|---|---|---|---|---|---|---|---|---|---|
| 4h_12 | A | 5754 | 1671 | -2.1 | 12.0 | -0.18 | +19.8 | 0.54 | -0.05 | -6.7 |
| 4h_12 | B | 1244 | 487 | +1.1 | 26.6 | 0.04 | +23.8 | 0.54 | -0.03 | -3.4 |
| 4h_12 | C | 435 | 194 | -59.9 | 60.7 | -0.99 | +20.9 | 0.52 | -0.21 | -64.5 |
| 4h_24 | A | 3598 | 1130 | -14.8 | 16.7 | -0.89 | +21.3 | 0.54 | -0.12 | -19.4 |
| 4h_24 | B | 943 | 358 | -19.3 | 33.1 | -0.58 | +20.4 | 0.53 | -0.09 | -23.9 |
| 4h_24 | C | 412 | 179 | -54.6 | 63.9 | -0.85 | +23.0 | 0.53 | -0.19 | -59.2 |
| 1h_24 | A | 14550 | 2536 | +12.1 | 7.2 | 1.67 | +14.0 | 0.53 | -0.04 | +7.5 |
| 1h_24 | B | 1958 | 638 | +29.4 | 16.5 | 1.79 | +22.6 | 0.55 | +0.05 | +24.9 |
| 1h_24 | C | 609 | 247 | +47.0 | 34.6 | 1.36 | +70.9 | 0.61 | +0.08 | +42.4 |
| 1h_48 | A | 9019 | 1811 | +12.2 | 9.7 | 1.25 | +18.2 | 0.54 | -0.07 | +7.6 |
| 1h_48 | B | 1485 | 492 | +33.4 | 20.3 | 1.64 | +32.8 | 0.56 | +0.05 | +28.8 |
| 1h_48 | C | 563 | 226 | +48.1 | 37.1 | 1.30 | +72.8 | 0.61 | +0.09 | +43.5 |
| 15m4h_12 | A | 18274 | 2069 | +10.5 | 10.3 | 1.02 | +23.9 | 0.55 | -0.05 | +5.9 |
| 15m4h_12 | B | 1663 | 486 | +21.6 | 35.4 | 0.61 | +37.7 | 0.57 | +0.05 | +17.1 |
| 15m4h_12 | C | 657 | 227 | +10.2 | 76.5 | 0.13 | +59.4 | 0.61 | +0.03 | +5.6 |
| 15m4h_24 | A | 10843 | 1418 | +2.7 | 14.2 | 0.19 | +25.9 | 0.55 | -0.09 | -1.9 |
| 15m4h_24 | B | 1253 | 365 | +6.4 | 45.6 | 0.14 | +41.6 | 0.58 | +0.03 | +1.8 |
| 15m4h_24 | C | 581 | 201 | +1.2 | 85.4 | 0.01 | +66.9 | 0.62 | +0.03 | -3.4 |

#### Compras, h=96 velas de 15 m

| modo | filtro | n | n_blocos | media_bps | ep_agrupado_bps | t_agrupado | mediana_bps | frac_pos | media_R | excesso |
|---|---|---|---|---|---|---|---|---|---|---|
| 4h_12 | A | 5754 | 1671 | +15.8 | 14.0 | 1.13 | +33.3 | 0.55 | -0.04 | +6.6 |
| 4h_12 | B | 1244 | 487 | +27.1 | 28.8 | 0.94 | +35.2 | 0.56 | -0.02 | +18.0 |
| 4h_12 | C | 435 | 194 | -3.9 | 63.6 | -0.06 | +38.7 | 0.55 | -0.20 | -13.0 |
| 4h_24 | A | 3598 | 1130 | +14.6 | 18.0 | 0.81 | +38.4 | 0.56 | -0.07 | +5.4 |
| 4h_24 | B | 943 | 358 | +21.9 | 35.7 | 0.61 | +42.2 | 0.57 | -0.04 | +12.7 |
| 4h_24 | C | 412 | 179 | +10.5 | 66.6 | 0.16 | +54.5 | 0.57 | -0.13 | +1.4 |
| 1h_24 | A | 14550 | 2536 | +11.4 | 11.2 | 1.02 | +17.2 | 0.52 | -0.08 | +2.2 |
| 1h_24 | B | 1958 | 638 | +29.3 | 25.1 | 1.17 | +38.0 | 0.55 | -0.03 | +20.2 |
| 1h_24 | C | 609 | 247 | +57.0 | 47.9 | 1.19 | +88.4 | 0.59 | -0.06 | +47.8 |
| 1h_48 | A | 9019 | 1811 | +9.2 | 15.2 | 0.60 | +25.0 | 0.53 | -0.15 | -0.0 |
| 1h_48 | B | 1485 | 492 | +34.9 | 30.4 | 1.15 | +54.0 | 0.57 | -0.05 | +25.7 |
| 1h_48 | C | 563 | 226 | +66.0 | 50.9 | 1.30 | +104.9 | 0.61 | -0.04 | +56.9 |
| 15m4h_12 | A | 18274 | 2069 | +18.2 | 14.1 | 1.29 | +35.7 | 0.55 | -0.05 | +9.0 |
| 15m4h_12 | B | 1663 | 486 | +30.0 | 34.4 | 0.87 | +53.9 | 0.56 | -0.05 | +20.8 |
| 15m4h_12 | C | 657 | 227 | +18.1 | 72.0 | 0.25 | +68.7 | 0.57 | -0.15 | +8.9 |
| 15m4h_24 | A | 10843 | 1418 | +13.7 | 18.0 | 0.76 | +43.1 | 0.55 | -0.08 | +4.5 |
| 15m4h_24 | B | 1253 | 365 | +13.0 | 42.6 | 0.30 | +48.4 | 0.55 | -0.10 | +3.8 |
| 15m4h_24 | C | 581 | 201 | +4.5 | 80.0 | 0.06 | +69.4 | 0.57 | -0.20 | -4.7 |

#### Vendas, h=48 velas de 15 m

| modo | filtro | n | n_blocos | media_bps | ep_agrupado_bps | t_agrupado | mediana_bps | frac_pos | media_R | excesso |
|---|---|---|---|---|---|---|---|---|---|---|
| 4h_12 | A | 6212 | 2509 | -1.9 | 6.0 | -0.32 | +15.0 | 0.53 | +0.01 | +2.6 |
| 4h_12 | B | 1312 | 797 | -2.9 | 11.6 | -0.25 | +7.3 | 0.52 | -0.01 | +1.6 |
| 4h_12 | C | 557 | 398 | -4.0 | 17.5 | -0.23 | +20.1 | 0.54 | -0.03 | +0.6 |
| 4h_24 | A | 4213 | 1934 | -10.2 | 7.1 | -1.44 | +17.1 | 0.53 | -0.04 | -5.7 |
| 4h_24 | B | 1083 | 672 | -5.3 | 11.9 | -0.44 | +8.0 | 0.52 | -0.01 | -0.7 |
| 4h_24 | C | 542 | 388 | -10.1 | 18.0 | -0.56 | +19.8 | 0.53 | -0.03 | -5.6 |
| 1h_24 | A | 16558 | 3414 | -12.2 | 5.0 | -2.43 | +6.5 | 0.51 | -0.08 | -7.6 |
| 1h_24 | B | 2092 | 1127 | -39.1 | 10.9 | -3.58 | +5.4 | 0.51 | -0.14 | -34.5 |
| 1h_24 | C | 956 | 583 | -55.9 | 17.9 | -3.13 | +12.9 | 0.53 | -0.18 | -51.4 |
| 1h_48 | A | 10908 | 2811 | -12.1 | 5.8 | -2.08 | +8.3 | 0.52 | -0.08 | -7.5 |
| 1h_48 | B | 1698 | 957 | -38.5 | 12.2 | -3.17 | +7.7 | 0.51 | -0.15 | -34.0 |
| 1h_48 | C | 874 | 549 | -48.5 | 18.8 | -2.58 | +20.6 | 0.53 | -0.17 | -43.9 |
| 15m4h_12 | A | 21611 | 3047 | -9.2 | 6.0 | -1.52 | +9.3 | 0.52 | -0.08 | -4.6 |
| 15m4h_12 | B | 1705 | 798 | -22.9 | 15.3 | -1.50 | +9.9 | 0.52 | -0.13 | -18.3 |
| 15m4h_12 | C | 921 | 468 | -13.4 | 21.7 | -0.62 | +25.3 | 0.54 | -0.10 | -8.8 |
| 15m4h_24 | A | 14681 | 2457 | -16.9 | 7.4 | -2.27 | +14.2 | 0.53 | -0.14 | -12.3 |
| 15m4h_24 | B | 1474 | 690 | -30.3 | 17.0 | -1.78 | +9.6 | 0.52 | -0.17 | -25.7 |
| 15m4h_24 | C | 897 | 454 | -19.8 | 22.0 | -0.90 | +18.3 | 0.53 | -0.13 | -15.3 |

#### Vendas, h=96 velas de 15 m

| modo | filtro | n | n_blocos | media_bps | ep_agrupado_bps | t_agrupado | mediana_bps | frac_pos | media_R | excesso |
|---|---|---|---|---|---|---|---|---|---|---|
| 4h_12 | A | 6212 | 2509 | -7.5 | 8.9 | -0.84 | +19.2 | 0.53 | -0.01 | +1.7 |
| 4h_12 | B | 1312 | 797 | -19.5 | 17.1 | -1.14 | +14.5 | 0.52 | -0.08 | -10.3 |
| 4h_12 | C | 557 | 398 | -30.2 | 25.9 | -1.17 | +19.0 | 0.52 | -0.15 | -21.0 |
| 4h_24 | A | 4213 | 1934 | -19.6 | 10.3 | -1.90 | +17.9 | 0.53 | -0.08 | -10.5 |
| 4h_24 | B | 1083 | 672 | -21.0 | 18.2 | -1.15 | +15.7 | 0.52 | -0.08 | -11.8 |
| 4h_24 | C | 542 | 388 | -34.8 | 26.7 | -1.31 | +20.7 | 0.52 | -0.14 | -25.6 |
| 1h_24 | A | 16558 | 3414 | -13.7 | 7.4 | -1.86 | +14.3 | 0.52 | -0.11 | -4.5 |
| 1h_24 | B | 2092 | 1127 | -41.9 | 16.4 | -2.56 | +8.4 | 0.51 | -0.20 | -32.7 |
| 1h_24 | C | 956 | 583 | -62.4 | 27.0 | -2.31 | +18.1 | 0.52 | -0.29 | -53.2 |
| 1h_48 | A | 10908 | 2811 | -17.6 | 8.5 | -2.07 | +18.5 | 0.52 | -0.14 | -8.4 |
| 1h_48 | B | 1698 | 957 | -44.7 | 17.8 | -2.51 | +13.1 | 0.51 | -0.24 | -35.5 |
| 1h_48 | C | 874 | 549 | -58.0 | 28.0 | -2.07 | +27.4 | 0.52 | -0.29 | -48.8 |
| 15m4h_12 | A | 21611 | 3047 | -15.2 | 8.3 | -1.83 | +17.9 | 0.52 | -0.13 | -6.0 |
| 15m4h_12 | B | 1705 | 798 | -34.1 | 20.2 | -1.69 | +27.7 | 0.53 | -0.20 | -25.0 |
| 15m4h_12 | C | 921 | 468 | -42.2 | 28.7 | -1.47 | +47.6 | 0.55 | -0.26 | -33.0 |
| 15m4h_24 | A | 14681 | 2457 | -27.2 | 10.1 | -2.69 | +16.8 | 0.52 | -0.22 | -18.0 |
| 15m4h_24 | B | 1474 | 690 | -38.0 | 21.8 | -1.74 | +28.6 | 0.53 | -0.23 | -28.8 |
| 15m4h_24 | C | 897 | 454 | -46.9 | 28.4 | -1.65 | +47.7 | 0.55 | -0.27 | -37.7 |


#### Grelha de sensibilidade IS (48 configurações; media e ep em R líquido; dd em R; R_pct = R mediano em % do preço)

| modo | filtro | alvo_R | n_max | n | media | ep | PF | acerto | dd | n_pos | R_pct | frac_stop | dur_h |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 4h_12 | A | 2 | 48 | 10402 | -0.088 | 0.010 | 0.82 | 0.44 | 931 | 0 | 2.12 | 0.39 | 8.4 |
| 4h_12 | A | 2 | 96 | 9610 | -0.075 | 0.012 | 0.87 | 0.41 | 831 | 2 | 2.16 | 0.49 | 13.3 |
| 4h_12 | A | 3 | 48 | 10313 | -0.094 | 0.011 | 0.81 | 0.43 | 1002 | 1 | 2.11 | 0.40 | 8.8 |
| 4h_12 | A | 3 | 96 | 9355 | -0.078 | 0.013 | 0.87 | 0.39 | 837 | 2 | 2.15 | 0.51 | 14.5 |
| 4h_12 | B | 2 | 48 | 2485 | -0.062 | 0.017 | 0.83 | 0.46 | 184 | 3 | 3.10 | 0.29 | 9.8 |
| 4h_12 | B | 2 | 96 | 2427 | -0.074 | 0.021 | 0.84 | 0.45 | 201 | 3 | 3.11 | 0.40 | 16.6 |
| 4h_12 | B | 3 | 48 | 2482 | -0.067 | 0.017 | 0.82 | 0.46 | 192 | 2 | 3.10 | 0.29 | 10.0 |
| 4h_12 | B | 3 | 96 | 2412 | -0.072 | 0.022 | 0.85 | 0.44 | 197 | 5 | 3.11 | 0.41 | 17.3 |
| 4h_12 | C | 2 | 48 | 967 | -0.118 | 0.028 | 0.73 | 0.46 | 128 | 2 | 3.29 | 0.36 | 9.2 |
| 4h_12 | C | 2 | 96 | 955 | -0.119 | 0.034 | 0.77 | 0.43 | 127 | 3 | 3.29 | 0.47 | 15.0 |
| 4h_12 | C | 3 | 48 | 966 | -0.120 | 0.029 | 0.72 | 0.45 | 132 | 2 | 3.29 | 0.36 | 9.4 |
| 4h_12 | C | 3 | 96 | 954 | -0.131 | 0.035 | 0.75 | 0.42 | 140 | 4 | 3.29 | 0.48 | 15.9 |
| 4h_24 | A | 2 | 48 | 6944 | -0.110 | 0.012 | 0.77 | 0.44 | 770 | 0 | 2.28 | 0.39 | 8.5 |
| 4h_24 | A | 2 | 96 | 6594 | -0.099 | 0.014 | 0.82 | 0.41 | 695 | 0 | 2.31 | 0.49 | 13.6 |
| 4h_24 | A | 3 | 48 | 6899 | -0.118 | 0.012 | 0.75 | 0.43 | 819 | 0 | 2.28 | 0.40 | 8.9 |
| 4h_24 | A | 3 | 96 | 6525 | -0.111 | 0.015 | 0.81 | 0.39 | 782 | 2 | 2.30 | 0.51 | 14.7 |
| 4h_24 | B | 2 | 48 | 1978 | -0.088 | 0.019 | 0.77 | 0.46 | 184 | 2 | 3.18 | 0.30 | 9.7 |
| 4h_24 | B | 2 | 96 | 1946 | -0.102 | 0.023 | 0.79 | 0.44 | 212 | 2 | 3.19 | 0.42 | 16.4 |
| 4h_24 | B | 3 | 48 | 1973 | -0.094 | 0.019 | 0.76 | 0.46 | 197 | 1 | 3.18 | 0.30 | 9.9 |
| 4h_24 | B | 3 | 96 | 1939 | -0.101 | 0.024 | 0.79 | 0.44 | 216 | 2 | 3.18 | 0.42 | 17.1 |
| 4h_24 | C | 2 | 48 | 929 | -0.112 | 0.029 | 0.74 | 0.46 | 118 | 2 | 3.33 | 0.36 | 9.2 |
| 4h_24 | C | 2 | 96 | 917 | -0.110 | 0.035 | 0.79 | 0.43 | 117 | 4 | 3.33 | 0.47 | 15.0 |
| 4h_24 | C | 3 | 48 | 928 | -0.117 | 0.030 | 0.73 | 0.46 | 126 | 3 | 3.33 | 0.36 | 9.4 |
| 4h_24 | C | 3 | 96 | 916 | -0.125 | 0.036 | 0.76 | 0.42 | 133 | 5 | 3.33 | 0.48 | 15.9 |
| 15m4h_12 | A | 2 | 48 | 17534 | -0.128 | 0.009 | 0.79 | 0.40 | 2260 | 0 | 1.46 | 0.53 | 6.2 |
| 15m4h_12 | A | 2 | 96 | 16752 | -0.119 | 0.010 | 0.82 | 0.37 | 2021 | 0 | 1.48 | 0.60 | 8.8 |
| 15m4h_12 | A | 3 | 48 | 17233 | -0.132 | 0.010 | 0.79 | 0.37 | 2289 | 0 | 1.46 | 0.54 | 6.9 |
| 15m4h_12 | A | 3 | 96 | 16077 | -0.121 | 0.012 | 0.83 | 0.33 | 1967 | 0 | 1.50 | 0.63 | 10.4 |
| 15m4h_12 | B | 2 | 48 | 2158 | -0.071 | 0.023 | 0.86 | 0.44 | 185 | 3 | 2.19 | 0.44 | 7.7 |
| 15m4h_12 | B | 2 | 96 | 2148 | -0.087 | 0.026 | 0.85 | 0.40 | 211 | 3 | 2.19 | 0.54 | 11.7 |
| 15m4h_12 | B | 3 | 48 | 2153 | -0.089 | 0.025 | 0.83 | 0.43 | 231 | 2 | 2.19 | 0.45 | 8.2 |
| 15m4h_12 | B | 3 | 96 | 2137 | -0.121 | 0.028 | 0.81 | 0.37 | 286 | 2 | 2.20 | 0.56 | 13.2 |
| 15m4h_12 | C | 2 | 48 | 1019 | -0.047 | 0.034 | 0.91 | 0.45 | 98 | 6 | 2.41 | 0.44 | 7.6 |
| 15m4h_12 | C | 2 | 96 | 1014 | -0.059 | 0.038 | 0.90 | 0.41 | 116 | 7 | 2.42 | 0.53 | 11.6 |
| 15m4h_12 | C | 3 | 48 | 1018 | -0.040 | 0.037 | 0.92 | 0.44 | 102 | 6 | 2.40 | 0.44 | 8.1 |
| 15m4h_12 | C | 3 | 96 | 1012 | -0.064 | 0.042 | 0.89 | 0.39 | 106 | 5 | 2.42 | 0.55 | 13.1 |
| 15m4h_24 | A | 2 | 48 | 11488 | -0.132 | 0.011 | 0.78 | 0.40 | 1533 | 0 | 1.57 | 0.52 | 6.3 |
| 15m4h_24 | A | 2 | 96 | 11075 | -0.123 | 0.012 | 0.82 | 0.37 | 1410 | 1 | 1.60 | 0.60 | 9.0 |
| 15m4h_24 | A | 3 | 48 | 11367 | -0.143 | 0.012 | 0.77 | 0.38 | 1639 | 0 | 1.58 | 0.54 | 7.0 |
| 15m4h_24 | A | 3 | 96 | 10840 | -0.131 | 0.014 | 0.81 | 0.33 | 1468 | 1 | 1.61 | 0.63 | 10.7 |
| 15m4h_24 | B | 2 | 48 | 1738 | -0.099 | 0.025 | 0.81 | 0.43 | 182 | 4 | 2.29 | 0.44 | 7.8 |
| 15m4h_24 | B | 2 | 96 | 1729 | -0.109 | 0.029 | 0.82 | 0.40 | 203 | 3 | 2.29 | 0.54 | 11.9 |
| 15m4h_24 | B | 3 | 48 | 1736 | -0.107 | 0.027 | 0.79 | 0.42 | 203 | 3 | 2.29 | 0.45 | 8.2 |
| 15m4h_24 | B | 3 | 96 | 1725 | -0.133 | 0.031 | 0.79 | 0.37 | 249 | 3 | 2.30 | 0.56 | 13.3 |
| 15m4h_24 | C | 2 | 48 | 952 | -0.062 | 0.035 | 0.88 | 0.44 | 98 | 7 | 2.51 | 0.44 | 7.6 |
| 15m4h_24 | C | 2 | 96 | 948 | -0.066 | 0.040 | 0.89 | 0.41 | 111 | 5 | 2.51 | 0.53 | 11.7 |
| 15m4h_24 | C | 3 | 48 | 951 | -0.045 | 0.038 | 0.91 | 0.44 | 98 | 6 | 2.51 | 0.45 | 8.1 |
| 15m4h_24 | C | 3 | 96 | 946 | -0.056 | 0.043 | 0.91 | 0.39 | 98 | 6 | 2.51 | 0.55 | 13.1 |

