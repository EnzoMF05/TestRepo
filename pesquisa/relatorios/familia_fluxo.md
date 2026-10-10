# Família FLUXO TAKER: desequilíbrio taker, CVD, divergência preço/CVD, absorção, volume climático

Resultado: NEGATIVA. O estudo de eventos no IS (2023-01-01 a 2025-03-31, 6 activos principais, 96
testes com erro padrão agrupado) não mostra nenhum retorno a favor do lado contrarian acima de 2 erros
padrão que tenha dimensão económica: o único t >= 2 é +1,5 bps a 1 h (absorção Z = 2 sem confirmação,
t 2,29), 9 vezes abaixo do custo de ida e volta de 13 bps; o único |t| >= 2 com dimensão vai CONTRA a
hipótese (divergência de 4 h confirmada, 1 h: -1,8 bps, t -2,26). Nos 18 activos nenhum dos 96 testes
chega a |t| = 2. As 48 configurações da grelha perdem no IS (média de -0,179 a -0,027 R, PF de 0,68 a
0,93, zero configurações com as duas metades positivas) e 26 das 48 são piores do que a nula de sinais
aleatórios com o mesmo stop e alvo (-0,065 R) por mais de 2 erros padrão. A regra natural fixada a
priori (divergência preço/CVD de 16 velas com vela de confirmação, stop 1,5 ATR de 1 h, alvo 3 ATR,
saída a 24 h) dá -0,141 R no IS (n 6937) e -0,104 R no OOS (n 4661, avaliado uma vez); perde também
em 2020-2022 (-0,077 R, n 17 472, 18 activos). A família não passa H2, H3, H4, H5 nem H6 em nenhum
período.

Código: `pesquisa/familia_fluxo.py` (usa `harness.py`). Registo: `pesquisa/ensaios/fluxo.csv`
(M = 54 linhas: 48 da grelha no IS, 2 da regra natural no IS (6 e 18 activos), 1 diagnóstico IS com o
sinal invertido, 2 OOS na mesma passagem (6 principais e 18 activos), 1 em 2020-2022). Resultados
intermédios em `pesquisa/resultados/fluxo_*.csv` (eventos a 6 e 18 activos no IS e em 2020-2022,
decis, grelha, trades da regra natural, da nula, do diagnóstico, do OOS e de 2020-2022) e
`fluxo_escolha.json`.

## 1. Hipótese

Num perpétuo o preço só anda quando alguém agride o livro. O volume taker por lado das klines da
Binance diz quem agrediu em cada vela de 15 m. A tese é a do esgotamento do agressor: no extremo de
um movimento (a) o preço faz um novo extremo mas o CVD acumulado não acompanha (o novo máximo foi
feito com menos compra agressiva do que o anterior: divergência), ou (b) o fluxo agressor é muito
forte mas o preço não anda (o passivo absorveu: absorção), ou (c) há um clímax de volume com
movimento largo (os últimos a entrar entraram), ou (d) o desequilíbrio taker acumulado em 4 h é
extremo (o lado agressor já gastou o que tinha). Em qualquer dos quatro casos o preço deveria reverter
nas 1 a 24 h seguintes, e uma vela de 15 m que fecha contra o movimento (confirmação) deveria
seleccionar os casos em que a reversão já começou. O que tinha de aparecer no estudo de eventos: com
R de 1,3 a 2,7 % do preço (1,5 a 3 ATR de 1 h), o limiar H2 de +0,15 R exige 20 a 40 bps de movimento
esperado a favor por trade acima da nula; a nula com R de 1,4 % custa -0,10 R. Se, depois do evento, o
preço não anda em média contra o movimento de forma mensurável a 1, 4, 8 ou 24 h, não há stop, alvo
nem confirmação que fabriquem a vantagem.

## 2. Dados e características (tudo causal)

Velas de 15 m dos 18 perpétuos USDT-M da Binance, 2020-09 a 2026-09-09. O fluxo taker
(`taker_buy_vol_btc`, `taker_sell_vol_btc`, em unidades do activo) é campo das klines e não métrica
imputada: taker buy + taker sell = `volume_base` em 100 % das velas, sem NaN, 6 velas a zero em 211 mil
no BTC. Por isso a família corre em todos os anos e em todos os activos; os 6 principais (BTC, ETH,
SOL, BNB, XRP, DOGE) são a amostra primária e os 18 e 2020-2022 são robustez.

Características no fecho de cada vela de 15 m (`caracteristicas` em `familia_fluxo.py`):

* `d1h`, `d4h`: desequilíbrio taker (buy - sell)/(buy + sell) sobre 4 e 16 velas; `z_d1h`, `z_d4h`: z
  robusto (mediana e MAD) sobre 30 dias (2880 velas, mínimo 1440), escala mínima 0,01;
* `cvd`: soma acumulada de taker buy - taker sell desde o início da série; `cvdmaxN`, `cvdminN`: máximo e
  mínimo do CVD nas N velas ANTERIORES (exclui a actual), N em {16, 32, 96};
* `hmaxN`, `lminN`: máxima e mínima das N velas anteriores (`extremos_moveis`);
* `vol1h_pct`: percentil de 30 dias do volume quote de 1 h (soma de 4 velas);
* `ret1h`, `sigma1h`: retorno log de 4 velas e vol EWMA de 15 m (meia-vida 96 velas) x 2;
* `atr1h`: ATR(14) de Wilder em velas de 1 h fechadas, projectado ao 15 m por `projectar_superior` (só
  velas de 1 h com fecho <= fecho da vela de 15 m).

Eventos (lado +1 compra, -1 venda), antes da confirmação:

* `div N`: máxima > `hmaxN` e `cvd` < `cvdmaxN` vende; mínima < `lminN` e `cvd` > `cvdminN` compra;
* `abs Z`: |`z_d1h`| >= Z e |`ret1h`| < 0,5 `sigma1h`; lado = -sinal(`d1h`);
* `clim P`: `vol1h_pct` >= P e |`ret1h`| >= 1 `sigma1h`; lado = -sinal(`ret1h`);
* `des4h Z`: |`z_d4h`| >= Z; lado = -sinal(`d4h`).

Confirmação de preço (a mesma em todas): a vela do sinal fecha no sentido do trade (fecho < abertura
para vender). O estudo de eventos mede com e sem confirmação; as regras usam sempre a confirmada.

Verificação de causalidade: multiplicar por 1,3 preço, volumes e volumes taker das últimas 200 velas
de ETH (2024-01 a 2024-06) não altera nenhuma das 28 colunas nem nenhum dos 12 sinais no passado
(diferença máxima 0, sinais alterados 0; `verificar_causalidade`).

Parâmetros fixos declarados antes de olhar para trades: janela do z e do percentil 30 dias, escala
mínima 0,01, meia-vida da vol 96 velas, fracção da absorção 0,5 sigma, mínimo do clímax 1 sigma, saída
por tempo a 96 velas (24 h), uma posição por activo, custo 6,5 bps por lado.

## 3. Estudo de eventos no IS, antes de qualquer regra

Retorno log em bps da ABERTURA da vela seguinte ao sinal até ao FECHO de k+h, multiplicado pelo lado
(a favor do trade), h = 4, 16, 32, 96 velas (1, 4, 8, 24 h). Eventos espaçados de pelo menos h velas
por activo. Erro padrão AGRUPADO por bloco temporal de max(4 h, h) (os activos movem-se juntos; o
ingénuo subestima o erro). Deriva incondicional do IS nos 6 principais, mesma medida: +0,7 / +2,8 /
+5,5 / +16,6 bps a 1 / 4 / 8 / 24 h (foi um bull market).

### 3.1 Seis principais, ambos os lados (bps a favor, erro padrão agrupado, t)

Com confirmação (a versão que as regras usam):

| regra | limiar | n a 4 h | 1 h | 4 h | 8 h | 24 h |
| --- | --- | --- | --- | --- | --- | --- |
| div | 16 | 11 272 | -1,8 (0,8; t -2,26) | +1,0 (2,0; 0,49) | -0,1 (3,2; -0,04) | +8,1 (7,5; 1,08) |
| div | 32 | 7713 | -0,6 (1,1; -0,57) | +1,6 (2,5; 0,61) | +0,3 (4,1; 0,07) | +3,7 (8,2; 0,45) |
| div | 96 | 3642 | -1,3 (1,7; -0,77) | -1,9 (3,7; -0,52) | -4,4 (5,6; -0,78) | -13,2 (10,8; -1,22) |
| abs | 1,5 | 6365 | +0,1 (0,7; 0,15) | +0,4 (1,5; 0,28) | -0,7 (2,7; -0,24) | +1,8 (7,5; 0,23) |
| abs | 2,0 | 2611 | +1,1 (0,9; 1,14) | +0,2 (2,2; 0,08) | -4,2 (4,1; -1,02) | +15,1 (9,1; 1,66) |
| abs | 2,5 | 915 | +0,3 (1,5; 0,19) | -0,1 (3,7; -0,03) | -2,8 (5,5; -0,50) | +15,6 (10,7; 1,46) |
| clim | 0,98 | 1695 | -0,6 (4,5; -0,13) | -8,3 (8,5; -0,97) | -18,3 (12,5; -1,46) | -3,1 (18,0; -0,17) |
| clim | 0,99 | 1027 | -1,3 (6,1; -0,22) | -12,8 (11,4; -1,12) | -27,6 (15,5; -1,78) | -2,0 (24,4; -0,08) |
| clim | 0,995 | 618 | +2,5 (8,1; 0,30) | -14,5 (15,8; -0,92) | -16,2 (20,6; -0,78) | +7,9 (32,7; 0,24) |
| des4h | 1,5 | 7855 | -0,4 (0,6; -0,67) | -0,5 (1,8; -0,26) | +1,0 (3,2; 0,31) | -4,5 (7,6; -0,59) |
| des4h | 2,0 | 3519 | +0,4 (0,9; 0,41) | -0,5 (2,5; -0,20) | +1,3 (3,8; 0,34) | -3,0 (8,4; -0,36) |
| des4h | 2,5 | 1387 | +0,1 (1,4; 0,07) | -3,9 (3,9; -1,03) | -10,7 (7,1; -1,51) | -13,4 (12,6; -1,06) |

Dos 48 testes confirmados o t máximo é +1,66 e o mínimo -2,26; a média das 48 médias é -2,3 bps. Sem
confirmação (outros 48 testes) o único t >= 2 é abs Z = 2 a 1 h: +1,5 bps (ep 0,65; t 2,29; n 5636),
sem dimensão económica. Fracção positiva entre 0,49 e 0,56 em todos os 96 testes (a mediana da
divergência é positiva porque o IS teve deriva positiva e porque 60 % dos sinais de divergência são
vendas, ver 3.2).

### 3.2 Por lado: o que parece sinal é deriva e continuação

Nas vendas após divergência confirmada (6 principais, IS): div 16 a 24 h -4,6 bps (ep 11,2); div 96 a
24 h -29,7 bps (ep 13,7; t -2,17). Nas compras: div 16 +27,2 (14,7; t 1,85); div 32 +27,3 (14,8;
1,84); div 96 +16,0 (20,1; 0,79). Compras positivas e vendas negativas com a mesma magnitude da deriva
do período é a assinatura de um bull market, não de uma vantagem: a soma dos dois lados é zero.

O clímax é o caso mais claro: a 24 h as compras (depois de uma queda climática) dão +57 bps (ep 27; t
2,14) e as vendas (depois de uma subida climática) dão -65 bps (ep 25; t -2,60) com P = 0,98. Ou seja,
depois de uma vela climática o preço SUBIU em média 60 bps nas 24 h seguintes qualquer que fosse a
direcção do clímax: beta ao bull market concentrada nas horas de volume alto, e continuação depois de
subidas climáticas. A hipótese de reversão após clímax é falsificada no sentido contrário.

Divergência de 16 velas confirmada a 1 h: -2,2 bps nas vendas (t -2,10) e -1,2 nas compras (t -0,89).
O "falso rompimento com vela de rejeição" continua, em média, no sentido do rompimento na hora
seguinte.

### 3.3 Decis do desequilíbrio (sem limiar, relação monótona)

Decis de `d4h` e `d1h` no IS (6 principais, amostragem de 4 em 4 velas, 11 800 por decil), excesso do
retorno bruto face à média de todas as velas, erro padrão agrupado: a 4 h o excesso vai de -1,8 a +1,0
bps (|t| <= 0,8) para `d4h` e de -1,8 a +3,0 bps (|t| <= 1,3) para `d1h`; a 24 h de -4,1 a +6,4 (|t| <=
0,7) e de -7,9 a +6,3 (|t| <= 0,9). Não há relação monótona em nenhum sentido: o decil 10 (fluxo
comprador extremo de 4 h) rende +3,2 bps a 4 h e o decil 1 rende +3,7. O desequilíbrio taker de 1 e 4 h
não prevê o retorno seguinte nem a favor nem contra.

### 3.4 Dezoito activos no IS e 2020-2022

18 activos, IS, 96 testes: nenhum com |t| >= 2; máximo +1,88 (abs Z = 2 confirmada, 24 h, +11,1 bps,
ep 5,9); divergência 16 confirmada 24 h +4,4 (6,0; 0,73); des4h Z = 2 confirmada 24 h +4,0 (7,1; 0,57).

18 activos, 2020-09 a 2022-12, 48 testes confirmados: 3 com t >= 2 (div 32 a 24 h +21,5 bps, ep 10,5, t
2,06; clim 0,98 a 1 h +15,3, ep 6,9, t 2,22; clim 0,99 a 4 h +30,0, ep 14,2, t 2,12); nenhum deles se
repete no IS (div 32 a 24 h +3,7, t 0,45; clim 0,98 a 1 h -0,6; clim 0,99 a 4 h -12,8). Com 48 testes
esperam-se 1 a 2 acima de 2 por acaso; e o clímax de 2020-2022 vai no sentido oposto ao de 2023-2025
(reversão a 1-4 h em 2020-22, continuação a 4-8 h no IS). Nada estável entre períodos.

### 3.5 Por activo (div 16 confirmada, 24 h, IS, 18 activos)

Média entre -20,0 (AVAX, ep 17,7) e +30,0 bps (XRP, ep 18,7); 12 positivos e 6 negativos; nenhum com
|t| >= 2 (máximo XRP 1,60, mínimo LTC -1,18). des4h Z = 2 a 4 h: entre -10,6 (SOL, t -1,53) e +7,4 (OP,
t 0,90).

Decisão a priori (secção 2 do método): sem nenhum teste a favor acima de 2 erros padrão com
dimensão económica, a família é NEGATIVA; a regra natural corre uma vez para registo e vai ao OOS.

## 4. Regra natural (fixada a priori) no IS

div N = 16 confirmada, stop 1,5 ATR(14) de 1 h, alvo 3 ATR, saída ao fecho da vela 96, uma posição
por activo.

6 principais, IS: n 6937, média -0,141 R (ep 0,016), PF 0,79, acerto 36 %, DD 980 R, R bruto -0,033,
duração média 10,4 h, 60 % stop, 25 % alvo, R mediano 1,36 % do preço, 11 056 sinais ignorados por
posição aberta. Por activo: BNB -0,159, BTC -0,207, DOGE -0,103, ETH -0,197, SOL -0,111, XRP -0,052
(os 6 negativos, erros padrão 0,037 a 0,040). Por ano: 2023 -0,174 (n 2894), 2024 -0,117 (3241), 2025
-0,117 (802). Metades: -0,171 e -0,113.

18 activos, IS: n 21 321, média -0,108 R (ep 0,009), PF 0,84, 18 activos negativos (de -0,226 TRX a
-0,032 DOT).

Nula com o mesmo stop, alvo e saída (sinais ao acaso, 6 principais, IS, 800 por activo, 3323 trades
após uma posição por activo): -0,065 R (ep 0,023), PF 0,90, R bruto +0,044. A regra natural é 0,076 R
PIOR do que a nula, 4,7 erros padrão combinados: o sinal selecciona continuação, não reversão.

Diagnóstico (só IS, registado como diagnóstico, não candidata): a regra natural com o sinal INVERTIDO
dá -0,061 R (ep 0,016, n 6899), PF 0,91, igual à nula. A diferença bruta entre seguir e contrariar o
falso rompimento é 0,080 R x 1,36 % = cerca de 11 bps por trade, menos do que o custo de 13 bps.

## 5. Grelha no IS (48 configurações, 6 principais, todas registadas)

4 regras x 3 limiares x stop {1,5; 3,0} ATR x alvo {1,5; 3,0} ATR; n_max 96 fixo. Nenhuma positiva.

| regra | média mín. | média máx. | média das 12 | PF mín. | PF máx. |
| --- | --- | --- | --- | --- | --- |
| div | -0,151 | -0,071 | -0,113 | 0,74 | 0,83 |
| abs | -0,141 | -0,027 | -0,091 | 0,74 | 0,93 |
| clim | -0,166 | -0,056 | -0,104 | 0,76 | 0,85 |
| des4h | -0,179 | -0,092 | -0,136 | 0,68 | 0,78 |

n entre 650 (clim 0,995, 3/3) e 9054 (div 16, 1,5/1,5). Com stop 1,5 ATR a média das 24 configurações
é -0,143 R; com stop 3 ATR é -0,080 R: a diferença é exactamente o custo em R (R mediano 1,2 a 1,4 %
contra 2,3 a 2,7 %), não vantagem. R bruto positivo em 5 das 48 (máximo +0,040, abs 2,5, 3/3, n 755),
todas abaixo do custo. Activos positivos: 0 em 36 configurações, 1 a 2 em 9, 3 em 2 (abs 2,5 com alvo 3
ATR, n 755 e 801, médias -0,027 e -0,097). Configurações com as duas metades positivas: 0 de 48. 26 das
48 ficam mais de 2 erros padrão ABAIXO da nula (-0,065 R) e 0 acima.

Patamar (média >= 0,15 R, PF >= 1,3, n >= 100): vazio. Por decisão a priori a regra natural vai ao OOS.

## 6. OOS, avaliado UMA vez (2025-04-01 a 2026-09-09)

Regra natural, 6 principais: n 4661, média -0,104 R (ep 0,019), PF 0,85, acerto 37 %, DD 506 R, R
bruto +0,013, duração 10,1 h, 59 % stop, 27 % alvo, R mediano 1,29 %. Por activo: BNB -0,212 (n 808),
BTC -0,150 (816), DOGE -0,061 (749), ETH -0,063 (727), SOL -0,027 (768), XRP -0,097 (793): 0 positivos.
Por ano: 2025 -0,087 (n 2552), 2026 -0,124 (2109). Metades: -0,089 e -0,120.

| critério | valor OOS | limite | passa |
| --- | --- | --- | --- |
| H1 n | 4661 | >= 100 | sim |
| H2 média líquida | -0,104 R | >= +0,15 | não |
| H3 PF | 0,85 | >= 1,3 | não |
| H4 activos positivos | 0 de 6 | >= 3 | não |
| H5 DD máximo | 506 R | <= 10 | não |
| H6 metades | -220 e -263 R | ambas > 0 | não |

Mesma passagem, 18 activos (robustez): n 13 857, média -0,097 R (ep 0,011), PF 0,85, 1 activo positivo
(BCH +0,003), 2025 -0,082, 2026 -0,117.

2020-09 a 2022-12, 18 activos (robustez adicional, dados de klines): n 17 472, média -0,077 R (ep
0,010), PF 0,88, 1 activo positivo (DOGE +0,013), 2020 -0,091, 2021 -0,070, 2022 -0,080.

Em três períodos disjuntos (2020-22, 2023-25.03, 2025.04-26.09) a regra dá -0,077, -0,141 e -0,104 R,
sempre abaixo da nula correspondente. Não há nada para a Hyperliquid validar ao vivo.

## 7. Honestidade

* O que está testado: 4 leituras do fluxo taker, 3 limiares cada, com e sem confirmação, 4 horizontes,
  6 e 18 activos, 3 períodos; 48 configurações de execução; nula com a mesma escala; sinal invertido.
  M = 54 configurações registadas. Nada foi escolhido no OOS.
* O que pode estar errado no sentido de esconder uma vantagem: (1) a divergência é medida no CVD
  acumulado desde o início da série e nos extremos de N velas; uma divergência em pontos de estrutura
  (máximos de swing) é mais rara e não foi testada; (2) a absorção usa |ret 1 h| < 0,5 sigma, uma
  definição; (3) a confirmação é uma única vela de 15 m contra o movimento; uma reconquista do nível
  (fecho de volta do lado certo do extremo varrido) foi testada na família ESTRUTURA, não aqui; (4) os
  horizontes param em 24 h.
* O que pode estar errado no sentido de sobrestimar: nada relevante, o resultado é negativo em tudo.
* Limites dos dados: o volume taker da Binance é agregado por vela de 15 m e não distingue ordens de
  tamanho, nem icebergs, nem o livro; absorção a sério mede-se no livro (ordens passivas que se
  reabastecem), que não está nestes dados. Na Hyperliquid o fluxo é outro e o livro é outro. Uma
  vantagem de microestrutura de fluxo, se existir, está em escalas abaixo de 15 m e precisa de dados de
  trades e de livro ao vivo, não de klines.
* O ep agrupado por bloco temporal corrige a correlação entre activos e a sobreposição de horizontes;
  os ep ingénuos são 1,0 a 1,4 vezes menores (mediana 1,2). Com os ingénuos passam a 3 os testes com
  t >= 2 nos 6 principais, todos SEM confirmação: div 16 a 4 h +2,5 bps (t 2,02), abs 2 a 1 h +1,5 bps
  (t 2,40) e clim 0,995 a 1 h +15,9 bps (t 2,07, n 1038, igual ao custo de 13 bps e que cai para +2,5
  bps, t 0,30, com a confirmação). Nenhuma conclusão muda.
* Resultado da grelha por regra, dos mais perto de zero: absorção Z = 2,5 com stop 3 e alvo 3 ATR
  (-0,027 R, n 755, PF 0,93, 3 activos positivos, ep 0,030). É compatível com a nula (-0,065 +- 0,023) e
  tem 1 configuração em 48 com esta posição relativa; não é um patamar.

## 8. Critérios H1 a H6 no OOS

H1 sim (4661); H2 não (-0,104 R); H3 não (0,85); H4 não (0 de 6); H5 não (506 R); H6 não (ambas
negativas). A família FLUXO TAKER não passa.
