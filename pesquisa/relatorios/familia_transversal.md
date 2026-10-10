# Família TRANSVERSAL: desvio relativo do alt face ao BTC (retorno do alt menos beta x retorno do BTC)

Resultado: NEGATIVA. No IS (2023-01-01 a 2025-03-31, 5 alts principais contra o BTC, 98 520 horas x
activo), o teste da hipótese com o limiar natural (|z| >= 2 do resíduo a 4 h ou a 24 h) não dá nenhum
retorno a favor do lado contrarian acima de 2 erros padrão agrupados em nenhum dos quatro horizontes
(t entre -1,1 e +0,9). A única cauda com t > 2 (resíduo de 24 h com |z| >= 3: +46 bps a 4 h e +163 bps
a 24 h, t 2,6 e 2,9, n 814) está concentrada em 244 compras, desaparece a 48 e 72 h (t 1,8 e 1,5) e
tem o sinal INVERTIDO em 2020-2022 (-116 bps a 4 h, t -2,7; -539 bps a 72 h, t -2,3). O resíduo coberto
(o que ganha a variante com perna no BTC) mostra continuação e não reversão: o decil 10 do resíduo de
24 h anda mais +27 bps de resíduo nas 24 h seguintes (t 2,0) no IS, e +69 a +177 bps em 2020-2022.
A regra natural, fixada a priori (resíduo de 24 h, |z| >= 2, stop 1,5 sigma diária, saída a 48 h, sem
filtro), perde no IS (-0,122 R, n 564, PF 0,74, DD 91 R, 1 activo positivo em 5) e no OOS, avaliado uma
vez, fica em +0,007 R (n 351, PF 1,02, DD 37 R, 2 activos positivos em 5, segunda metade negativa).
Das 48 configurações da grelha, 44 têm média negativa no IS; as 4 positivas (+0,054 a +0,077 R, n 121
a 206) estão todas na saída a 24 h com |z| >= 3 e ficam negativas a 48 e 72 h: não há patamar, e a regra
de escolha (média e média alisada positivas, n >= 100) não escolheu nenhuma. A família não passa H2,
H3, H4, H5 nem H6 em nenhum conjunto (5 alts, 17 alts, 2020-2022, com ou sem perna no BTC).

Código: `pesquisa/familia_transversal.py` (usa `harness.py`). Registo: `pesquisa/ensaios/transversal.csv`
(M = 56 linhas: 48 da grelha no IS, regra natural IS e IS com perna no BTC, OOS 5 alts e OOS com perna
no BTC, 17 alts em IS, OOS e 2020-2022, 5 alts em 2020-2022). Resultados intermédios em
`pesquisa/resultados/transversal_*.csv` (decis, teste contrarian e por activo em IS e em 2020-2022,
grelha, trades de cada avaliação) e `transversal_resumo.json`.

## 1. Hipótese

Os alts seguem o BTC com um beta que, nos 30 dias anteriores, anda entre 0,8 (BNB) e 1,4 (SOL). Quando
um alt se afasta do que o seu beta justifica, caindo mais do que o BTC numa queda ou subindo menos, ou o
contrário, parte desse desvio deveria ser liquidez e posicionamento forçado (stops, liquidações em cascata
no alt, market makers a cobrir inventário) e não informação própria do alt, e deveria reverter em 1 a 3
dias, quando o fluxo forçado se esgota e o alt volta à sua relação com o BTC. É a ideia de "sobre-reacção
ao BTC": o choque comum chega primeiro ao BTC e é amplificado nos alts, com menos profundidade de livro.
O sinal é só no alt (compra quando caiu demais face ao BTC, venda quando subiu demais), porque o
utilizador opera à mão; a variante com perna no BTC (posição contrária de beta x nocional em BTC, fechada
ao mesmo tempo) retira o risco de mercado e fica exposta só ao resíduo, e é avaliada à parte. O
diferencial de funding alt menos BTC entra como filtro de aglomeração: não se compra o alt quando a
multidão já está longa no alt (diferencial alto) nem se vende quando já está curta.

A hipótese é falsificável no estudo de eventos: se, depois de um desvio relativo de 2 a 3 sigma, o alt
não anda em média contra o desvio nas 4 a 72 h seguintes (em retorno bruto, para o sinal só no alt; em
resíduo, para a variante coberta), não há stop nem saída que criem a vantagem. O que tinha de aparecer:
com R de 5 a 6 % do preço (1,5 sigma diária), o limiar H2 de +0,15 R exige cerca de 75 a 90 bps de
movimento esperado a favor por trade; a nula com este R fica em +0,01 R no IS e -0,08 R no OOS.

## 2. Dados e características (tudo causal)

Perpétuos USDT-M da Binance, velas de 15 m desde 2020-09-01. Referência: BTC. Alts principais: ETH, SOL,
BNB, XRP, DOGE (eventos, regra natural, grelha, OOS). Versão só de preço (sem filtro de funding): os 17
alts da lista (ETH, SOL, BNB, XRP, ADA, DOGE, LTC, LINK, DOT, AVAX, NEAR, TRX, BCH, APT, ARB, OP, SUI)
em IS, OOS e 2020-2022. O funding do BNB não é fiável (50 % de zeros), por isso o BNB fica fora das
configurações com filtro.

Características na vela de 1 h FECHADA do alt, alinhada à vela de 1 h fechada do BTC na mesma hora
(`harness.agregar`), levadas ao 15 m por `projectar_superior` (só velas de 1 h com fecho <= fecho da
vela de 15 m); o sinal só nasce nas velas de 15 m que coincidem com um fecho de hora e a entrada é na
abertura do 15 m seguinte:

* `beta`: cov(r_alt, r_btc) / var(r_btc) em 720 velas de 1 h (30 dias, mínimo 360), retornos log de 1 h,
  janela fechada em k;
* `e1`: resíduo de 1 h = r_alt - beta x r_btc; `sig_e`: vol EWMA (meia-vida 168 h) do resíduo;
* `z_res4` = (ret4h_alt - beta x ret4h_btc) / (sig_e x sqrt(4)); `z_res24` idem a 24 h, / (sig_e x sqrt(24));
* `z_ret24`: retorno próprio do alt a 24 h / (sigma_1h x sqrt(24)), para saber se o resíduo acrescenta
  alguma coisa ao retorno bruto do alt;
* `z_fd`: z robusto do diferencial de funding (alt menos BTC, taxa de 8 h em %) sobre 30 dias, escala
  mínima 0,002 %, só em velas não imputadas (`mascarar_imputado`);
* `sigma`: sigma diária em preço do alt = vol EWMA (meia-vida 168 h) dos retornos de 1 h x sqrt(24) x fecho.

Parâmetros fixos declarados antes de olhar para trades: janela do beta 720 h (mínimo 360), meia-vida da
vol 168 h, janela do z do funding 720 h, escala mínima 0,002 %, limiar do filtro +-1, sem alvo (só stop e
tempo), horizontes do estudo 4, 24, 48, 72 h.

Verificação de causalidade: multiplicar por 1,3 o preço e o funding do ETH e o preço, retorno e funding
do BTC nas últimas 200 h não altera nenhuma das 7 características no passado (diferença máxima 0,0 no
1 h e na projecção ao 15 m).

Beta médio no IS: BNB 0,81, ETH 0,99, XRP 1,00, DOGE 1,27, SOL 1,40 (quartis 0,68 a 1,52). Em 2020-2022:
BNB 0,99, XRP 1,06, DOGE 1,10, ETH 1,13, SOL 1,34.

## 3. Estudo de eventos no IS, antes de qualquer regra

Painel de 1 h dos 5 alts no IS: 98 520 linhas, 19 704 horas distintas. Retorno log em bps da ABERTURA da
vela de 1 h seguinte até ao FECHO de k+h, h = 4, 24, 48, 72 h, (a) do alt (o que ganha o sinal só no
alt) e (b) do resíduo a frente, alt menos beta_k x BTC (o que ganha a variante com perna no BTC). Deriva
do período (todas as horas): +2,7 / +16,2 / +32,0 / +47,0 bps (medianas 1,9 / 5,3 / 10,0 / 12,6): bull
market. Erro padrão AGRUPADO por bloco de h horas (os alts movem-se juntos e os horizontes sobrepõem-se;
o agrupado é 1,5 a 4 vezes o ingénuo).

### 3.1 Teste da hipótese: retorno A FAVOR do lado contrarian nos extremos

z <= -limiar compra o alt, z >= +limiar vende. bps a favor do lado, erro padrão agrupado, t (n =
horas-evento, com sobreposição):

| característica | alvo | limiar | n (compras / vendas) | 4 h | 24 h | 48 h | 72 h |
| --- | --- | --- | --- | --- | --- | --- | --- |
| z_res4 | alt | 2 | 4458 (1949 / 2509) | +5,0 (ep 5,7; t 0,9) | -0,1 (15,7; 0,0) | -10,5 (20,1; -0,5) | -21,5 (24,6; -0,9) |
| z_res4 | alt | 3 | 1451 (522 / 929) | -1,7 (10,7; -0,2) | +9,4 (28,9; 0,3) | -28,7 (34,3; -0,8) | -41,6 (38,8; -1,1) |
| z_res24 | alt | 2 | 4655 (1830 / 2825) | +3,1 (7,2; 0,4) | +1,2 (25,5; 0,0) | -22,3 (36,9; -0,6) | -55,0 (51,1; -1,1) |
| z_res24 | alt | 3 | 814 (244 / 570) | +46,1 (17,9; 2,6) | +163,1 (57,3; 2,9) | +123,2 (67,8; 1,8) | +121,7 (79,6; 1,5) |
| z_res4 | resíduo | 2 | 4458 | +6,3 (4,3; 1,5) | -7,2 (11,3; -0,6) | -24,0 (15,3; -1,6) | -19,6 (18,2; -1,1) |
| z_res24 | resíduo | 2 | 4655 | +1,0 (5,2; 0,2) | -17,1 (18,7; -0,9) | -53,4 (30,1; -1,8) | -48,4 (43,1; -1,1) |
| z_ret24 (retorno próprio) | alt | 2 | 4527 (1880 / 2647) | +5,6 (9,8; 0,6) | +25,4 (38,5; 0,7) | -41,0 (50,1; -0,8) | -75,9 (64,4; -1,2) |

Por lado (bps a favor, limiar 2, alvo alt): compras após z_res24 <= -2 +13 / +73 / +94 / +66 a 4 / 24 /
48 / 72 h; vendas após z_res24 >= +2 -4 / -45 / -98 / -133. As compras ganham a deriva do bull market
(+16 a +47 bps é a deriva de todas as horas), as vendas perdem-na e mais: o excesso sobre a deriva
multiplicada pelo lado é +3,7 / +4,6 / -15,5 / -44,9 bps (t 0,5 / 0,2 / -0,4 / -0,9). Nada.

A única linha acima de 2 erros padrão é z_res24 com |z| >= 3 a 4 h e 24 h. Decomposta: 244 compras
+78 / +400 / +373 / +368 bps; 570 vendas +33 / +62 / +16 / +16 bps. É a cauda extrema de quedas
relativas de 24 h num bull market (3 sigma do resíduo de 24 h: 0,8 % das horas), com erro padrão
agrupado de 57 bps a 24 h; 1 em 44 testes (11 configurações x 4 horizontes) com t 2,9 não é prova com
este número de comparações, e a secção 3.3 mostra que o sinal se inverte em 2020-2022.

Filtro de funding (sem BNB, 4 alts): z_res24, limiar 2, com filtro n 3087, +7,9 / +6,7 / -27,7 / -40,8
bps (t 0,9 / 0,2 / -0,6 / -0,6); sem filtro nos mesmos 4 alts n 3711, +2,2 / -2,2 / -29,9 / -60,2 (t 0,3
/ -0,1 / -0,7 / -1,0). z_res4 com filtro n 2804, +5,2 / -6,6 / -17,7 / -31,6 (t 0,7 / -0,3 / -0,7 /
-0,9). O filtro retira 17 a 21 % dos eventos e melhora a média em 5 a 20 bps, dentro de meio erro padrão:
não transforma nada.

### 3.2 Decis (excesso sobre a média de todas as horas, bps; t agrupado)

z_res24 -> retorno do alt: decil 1 (z < -1,04) +5,4 / +0,8 / +28,3 / +12,9 (t 1,1 / 0,0 / 0,8 / 0,3);
decil 10 (z > 1,05) +5,4 / +23,1 / +37,2 / +50,8 (t 1,4 / 1,3 / 1,1 / 1,1). Os dois extremos são
seguidos de deriva POSITIVA (é o efeito de volatilidade num bull market: horas extremas em qualquer
direcção antecedem subidas), não de reversão: para a hipótese, o decil 10 tinha de ser negativo.
z_res4 -> retorno do alt: decil 1 +12,8 / +27,9 / +39,5 / +44,1 (t 3,0 / 1,8 / 1,6 / 1,2); decil 10 +0,8
/ +20,2 / +37,7 / +48,2 (t 0,2 / 1,5 / 1,5 / 1,3). O decil 1 a 4 h (+12,8 bps, t 3,0) é a única célula
com t > 2 nos decis, e 12,8 bps é 1/10 do custo de ida e volta com R de 6 %.
z_res24 -> resíduo a frente: decil 10 +4,3 / +27,1 / +40,9 / +28,6 (t 1,4 / 2,0 / 1,7 / 0,9); decil 1
-0,4 / -7,4 / -9,6 / -10,6. O resíduo CONTINUA: o alt que se afastou para cima do BTC continua a
afastar-se. z_res4 -> resíduo: decil 10 -0,3 / +12,5 / +23,9 / +24,7, decil 1 +6,2 / -1,5 / -2,3 /
-7,7 (todos |t| < 1,5).
z_ret24 (retorno próprio): decil 10 +3,9 / +8,0 / +73,7 / +83,8 (t 0,9 / 0,4 / 2,0 / 1,6): momentum
do alt a 48-72 h, não reversão. O resíduo não acrescenta reversão ao retorno bruto.

### 3.3 Por activo (|z| >= 2, retorno do alt a favor, IS)

| activo | z_res4 24 h | z_res4 48 h | z_res24 24 h | z_res24 48 h |
| --- | --- | --- | --- | --- |
| ETH | +19 (ep 19; t 1,0; n 980) | +26 (t 1,1) | +61 (ep 34; t 1,8; n 1062) | +93 (t 2,1) |
| SOL | -68 (ep 33; t -2,1; n 912) | -83 (t -1,9) | -74 (ep 40; t -1,8; n 1051) | -101 (t -1,4) |
| BNB | +13 (t 0,7) | -7 (t -0,3) | +14 (t 0,4) | +7 (t 0,2) |
| XRP | -12 (t -0,3) | +19 (t 0,4) | +15 (t 0,2) | -17 (t -0,2) |
| DOGE | +47 (t 1,4) | -6 (t -0,1) | -10 (t -0,1) | -113 (t -1,0) |

ETH reverte, SOL continua, os outros três nada: heterogéneo e sem padrão por beta (SOL tem o beta mais
alto e é onde a hipótese mais falha).

### 3.4 Robustez só de preço: 2020-09 a 2022-12 (5 alts, 101 921 horas x activo)

Deriva +3,0 / +17,9 / +36,4 / +56,1 bps (medianas 2,4 / 0,0 / -5,9 / 7,1). Teste contrarian (bps a
favor, t agrupado): z_res4 limiar 2: -3 / -83 / -80 / -143 (t -0,3 / -1,8 / -1,8 / -2,3); z_res24 limiar
2: -40 / -74 / -140 / -263 (t -2,6 / -1,1 / -1,5 / -2,3); z_res24 limiar 3: -116 / -152 / -216 / -539
(t -2,7 / -1,0 / -1,1 / -2,3); resíduo coberto z_res24 limiar 2: -42 / -137 / -200 / -355 (t -3,3 / -2,3
/ -2,4 / -3,5). Decis do resíduo coberto z_res24: decil 10 +27 / +69 / +92 / +177 (t 3,9 / 1,9 / 1,7 /
2,4), decil 1 -6 / -27 / -38 / -64. Em 2020-2022 o desvio relativo tinha MOMENTUM claro (o alt que
descolava do BTC continuava a descolar, 1 a 3 dias); no IS esse momentum enfraqueceu até perto de zero
com uma cauda de compras a 3 sigma a favor; não há reversão em nenhum dos dois períodos. O filtro de
funding não se testa em 2020-2022 (métricas 87 % imputadas em 2022, alts imputados em 2020-2021: 13
eventos).

Conclusão do estudo de eventos: a hipótese de reversão do desvio relativo NÃO tem suporte. Pelo
protocolo, a família é NEGATIVA; a regra natural e a grelha correm para registo e o OOS avalia só a regra
natural.

## 4. Regra natural (fixada a priori) no IS

Resíduo de 24 h, |z| >= 2 (compra se <= -2, venda se >= +2), stop 1,5 sigma diária, sem alvo, saída por
tempo ao fecho de 192 velas de 15 m (48 h), sem filtro, 5 alts, uma posição por activo.

IS: n 564, média -0,122 R (ep 0,041), mediana -0,278, PF 0,743, acerto 43,4 %, DD 90,7 R, bruto -0,097
R, duração média 35,3 h, 41 % saem por stop, R médio 5,97 % do preço. Compras 233 (-0,043 R), vendas 331
(-0,178 R). Por activo: ETH +0,044 (n 112, PF 1,12), XRP -0,102, BNB -0,138, DOGE -0,156, SOL -0,247
(PF 0,54): 1 positivo em 5. Por ano: 2023 -0,020 (n 240), 2024 -0,191 (n 260), 2025 (Jan-Mar) -0,226
(n 64). Metades: -0,016 e -0,211. Nula com o mesmo stop e n_max (800 sinais aleatórios por activo):
+0,013 R +- 0,025, PF 1,04, n 1491. A regra fica 0,135 R ABAIXO da nula, mais de 3 erros padrão.

Variante com perna no BTC (posição contrária de beta x nocional em BTC, fechada ao fecho da vela de 15 m
em que o alt sai, 6,5 bps por lado sobre o nocional do BTC): n 564, média -0,140 R (ep 0,032), PF 0,635,
acerto 40,1 %, DD 82 R, 0 activos positivos (ETH passa de +0,044 a -0,116). Cobrir o BTC piora: o
resíduo continua, não reverte (secção 3.2).

## 5. Grelha no IS (M = 48, toda registada)

h em {4, 24} x Z em {2, 3} x filtro em {0, 1} x stop em {1,0, 1,5} sigma x N_MAX em {96, 192, 288}. Com
filtro, o BNB fica fora (4 alts). Resultados: 44 das 48 com média negativa; mínimo -0,159 R (h 24, Z 2,
stop 1,0, 288), máximo +0,077 R (h 24, Z 3, filtro 1, stop 1,5, 96, n 121). Médias por eixo: h 4 sem
filtro -0,085, com filtro -0,053; h 24 sem filtro -0,070, com filtro -0,059; N_MAX 96 -0,033, 192 -0,082,
288 -0,085; Z 2 -0,093, Z 3 -0,041; stop 1,0 -0,076, 1,5 -0,057. Compras em média +0,072 R e vendas
-0,148 R nas 48 (a deriva do bull market: vender alts em 2023-2024 perdeu dinheiro).

As 4 configurações positivas, todas h 24, Z 3, N_MAX 96: sem filtro stop 1,0 +0,064 (n 206, PF 1,13, 4
activos positivos), stop 1,5 +0,068 (n 176, PF 1,20, 4 positivos); com filtro stop 1,0 +0,054 (n 141,
PF 1,11), stop 1,5 +0,077 (n 121, PF 1,23, DD 7,3 R). Os vizinhos a 192 e 288 velas são todos negativos
(-0,041 a -0,106): a média alisada sobre (Z, stop, N_MAX) é negativa em todas as 48 (melhor -0,038). Não
há patamar; é a cauda de 3 sigma de 24 h do estudo de eventos, que vive só nas primeiras 24 h, com 40 a
68 compras a carregar o resultado, e que em 2020-2022 tinha o sinal contrário. A regra de escolha (média
e média alisada positivas, n >= 100) não escolheu nenhuma configuração: o OOS avalia a regra natural.

## 6. OOS (2025-04-01 a 2026-09-09), avaliado UMA vez, regra natural, 5 alts

n 351, média +0,007 R (ep 0,058), mediana -0,032, PF 1,017, acerto 48,1 %, DD 36,8 R, bruto +0,036 R,
duração 35,8 h, 36 % por stop, R médio 5,03 % do preço. Compras 140 (+0,017), vendas 211 (+0,001). Por
activo: XRP +0,110 (n 75, PF 1,28), DOGE +0,072 (n 70), ETH -0,003 (n 75), BNB -0,062 (n 67), SOL -0,100
(n 64): 2 positivos em 5. Por ano: 2025 (Abr-Dez) +0,035 (n 189, PF 1,10), 2026 -0,026 (n 162, PF 0,95).
Metades: +3,0 R e -0,6 R. Nula no OOS com o mesmo stop e n_max: -0,085 R +- 0,031, PF 0,79. A regra fica
0,09 R acima da nula no OOS (1,6 erros padrão da regra), mas a 0,14 R do limiar H2 e sem PF, DD nem
metades.

Com perna no BTC: n 351, média -0,038 R (ep 0,038), PF 0,875, DD 23,5 R, 1 activo positivo (XRP +0,042),
as duas metades negativas.

Critérios H1 a H6 no OOS (5 alts, sinal só no alt): H1 n 351 >= 100 PASSA; H2 média +0,007 < 0,15 FALHA;
H3 PF 1,02 < 1,3 FALHA; H4 2 activos positivos < 3 FALHA; H5 DD 36,8 R > 10 FALHA; H6 segunda metade
-0,6 R FALHA. Não passa.

## 7. Versão só de preço nos 17 alts e em 2020-2022 (mesma regra natural, sem filtro)

IS 17 alts: n 1980, média -0,095 R (ep 0,022), PF 0,794, DD 241 R, 3 activos positivos em 17 (ETH +0,044,
NEAR +0,040, LTC +0,032), compras +0,005, vendas -0,175; por ano 2023 -0,079, 2024 -0,104, 2025 -0,118.
OOS 17 alts (avaliação única, declarada a priori): n 1247, média +0,023 R (ep 0,030), PF 1,056, acerto
48,1 %, DD 57,1 R, 10 activos positivos em 17 (AVAX +0,220, DOT +0,194, OP +0,181, XRP +0,110; NEAR
-0,165, TRX -0,126, SOL -0,100, ARB -0,092), compras -0,010, vendas +0,045; 2025 +0,064 (n 642, PF 1,17),
2026 -0,019 (n 605, PF 0,96); metades +35,3 R e -6,2 R. Passa H1 e H4; falha H2, H3, H5, H6.
2020-09 a 2022-12, 17 alts (APT e OP quase sem dados): n 1514, média -0,062 R (ep 0,025), PF 0,858, DD
145 R, 6 positivos em 15 (TRX +0,150, LTC +0,106, LINK +0,088), por ano 2020 +0,132 (n 211), 2021 -0,105
(n 669), 2022 -0,082 (n 634). 5 alts em 2020-2022: n 628, -0,175 R, PF 0,644, 0 positivos, 2021 -0,236,
2022 -0,189.

Em 7 conjuntos (IS 5, IS 17, 2020-22 5, 2020-22 17, OOS 5, OOS 17, mais as duas variantes com perna no
BTC) a média vai de -0,175 a +0,023 R; nenhum chega a metade do limiar H2.

## 8. Critérios H1 a H6 (OOS, configuração natural, sinal só no alt, 5 alts)

| critério | valor | limite | passa |
| --- | --- | --- | --- |
| H1 n | 351 | >= 100 | sim |
| H2 média líquida | +0,007 R | >= +0,15 R | não |
| H3 factor de lucro | 1,02 | >= 1,3 | não |
| H4 activos positivos | 2 de 5 | >= 3 | não |
| H5 drawdown máximo | 36,8 R | <= 10 R | não |
| H6 duas metades positivas | +3,0 / -0,6 R | ambas > 0 | não |

Passa: NÃO. Nos 17 alts (OOS): H1 e H4 passam, H2 (+0,023), H3 (1,06), H5 (57 R) e H6 (-6,2 R) falham.

## 9. Honestidade

* O que pode estar errado a favor da hipótese: o beta de 30 dias em retornos de 1 h é ruidoso (quartis
  0,7 a 1,5) e inclui a própria hora k; um beta a 4 h ou a 1 dia, ou um beta encolhido para 1, daria
  resíduos diferentes. Mas o teste com o retorno próprio do alt (z_ret24, beta implícito 0) dá o mesmo
  nada (t 0,6 / 0,7 / -0,8 / -1,2) e o sinal a 3 sigma inverte-se entre períodos com qualquer beta: a
  falha não é do estimador.
* O erro padrão agrupado por bloco de h horas corrige a sobreposição e a correlação entre alts, mas
  não a heterocedasticidade por regime; os t de 2,6 e 2,9 na cauda de 3 sigma são, se alguma coisa,
  sobrestimados. Com 44 testes no IS, esperam-se 2 com |t| > 2 por acaso.
* A grelha tem 4 células positivas (h 24, Z 3, N_MAX 96) com n 121 a 206. Se se tivesse escolhido a
  melhor (+0,077 R, PF 1,23, DD 7,3 R), seria escolher o máximo de uma grelha sem patamar, contra o
  protocolo, e o resultado em 2020-2022 do mesmo evento (t -2,7 a 4 h) diz que não é estável. Não se
  avaliou no OOS de propósito; quem quiser testá-la tem de o fazer numa amostra nova.
* No OOS a regra natural ficou 0,09 R acima da nula (+0,007 contra -0,085, 1,6 ep): há um sinal fraco de
  que as entradas não são aleatórias no OOS, consistente com o "ressalto após queda" ter desaparecido e
  as vendas terem deixado de perder a deriva (as vendas passam de -0,178 R no IS a +0,001 no OOS porque o
  bull market acabou). É regime, não vantagem: 2026 é negativo (-0,026) nos 5 alts e nos 17.
* O filtro de funding só se testou em 2023-2025 e em 4 alts; o diferencial de funding na Binance não é
  o da Hyperliquid, e o filtro mudou as médias em 5 a 20 bps, dentro do erro: não se pode dizer que
  filtre nem que não filtre.
* A perna no BTC foi simulada com saída ao FECHO da vela de 15 m em que o alt sai (o stop do alt dispara
  dentro da vela e o BTC fecha-se depois); é uma aproximação razoável para execução manual, e o custo
  (6,5 bps por lado sobre beta x nocional) está incluído. A variante é pior em todos os conjuntos,
  coerente com o resíduo ter continuação: a cobertura retira a parte que ganhava (a deriva do BTC nas
  compras) e deixa a parte que perde.
* Não se testou: beta a horizontes mais longos (1 dia, 1 semana), resíduo face a um cabaz de alts em vez
  do BTC (pares alt/alt), horizontes de saída abaixo de 24 h com stops mais apertados (a nula com R de
  1 % fica em -0,12 R, pelo que o limiar fica ainda mais longe), e a hipótese inversa (momentum do
  desvio relativo, que em 2020-2022 tinha t de 2 a 3,5 no resíduo coberto e no IS t de 2,0 a 24 h);
  essa hipótese inversa é a única pista que este estudo deixa, e teria de começar por um estudo de
  eventos próprio, não por inverter esta regra.
* A Binance não é a Hyperliquid: preços e funding são próximos, mas a profundidade do livro dos alts e as
  liquidações não; um desvio relativo na Hyperliquid pode ser maior e mais rápido do que aqui, e isso só
  se mede ao vivo.

## 10. Ficheiros

* `pesquisa/familia_transversal.py`: características, estudo de eventos, regra natural, grelha, escolha,
  OOS, 17 alts, 2020-2022, variante com perna no BTC, verificação de causalidade.
* `pesquisa/ensaios/transversal.csv`: M = 56 configurações registadas.
* `pesquisa/resultados/transversal_eventos_decis_{IS,PRE}.csv`, `transversal_eventos_contrarian_{IS,PRE}.csv`,
  `transversal_eventos_por_activo_{IS,PRE}.csv`, `transversal_grelha_IS.csv`, `transversal_escolha.json`,
  `transversal_trades_natural_{IS,OOS,PRE}.csv`, `transversal_trades_naturalBTC_{IS,OOS}.csv`,
  `transversal_trades_natural18_{IS,OOS,PRE}.csv`, `transversal_resumo.json`.
