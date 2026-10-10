# Família CASCATA DE LIQUIDAÇÕES E EXAUSTÃO

Gerado por `pesquisa/familia_cascata.py` em 2026-10-10 09:54 UTC. Configurações registadas em `ensaios/cascata.csv`: M = 43.

## 1. Hipótese

Uma cascata de liquidações é fluxo forçado: longs liquidados vendem a mercado sem olhar ao preço, o OI cai e o volume é climático. Quando o fluxo forçado acaba, o preço fica abaixo do que os participantes voluntários aceitam e reverte nas horas seguintes. A vantagem, se existir, vem de comprar a reconquista da mínima da vela de cascata (ou vender a reconquista da máxima após cascata de shorts), com o stop logo abaixo (acima) desse extremo e alvo em múltiplos de R. É uma regra de exaustão, não de reversão à média: só actua depois de um extremo de intensidade.

## 2. Validação das colunas de liquidações nos 6 activos (IS, velas de 1 h, logs)

Regressão de log|long_liq| sobre log|ret|, log volume e log taker sell (USD) da mesma vela; idem para short_liq com taker buy. Assimetria = média em velas de queda / média em velas de subida.

| activo | n_1h | R2_log_long | R2_log_short | assim_long_queda/subida | assim_short_queda/subida | p1/p50_long |
|---|---|---|---|---|---|---|
| BTC | 19608 | 0.791 | 0.833 | 2.638 | 0.383 | 0.094 |
| ETH | 19670 | 0.738 | 0.751 | 1.715 | 0.655 | 0.234 |
| SOL | 19645 | 0.396 | 0.370 | 1.453 | 0.737 | 0.534 |
| BNB | 19592 | 0.723 | 0.722 | 1.569 | 0.790 | 0.161 |
| XRP | 19379 | 0.595 | 0.356 | 1.318 | 0.822 | 0.761 |
| DOGE | 19503 | 0.161 | 0.028 | 1.124 | 0.946 | 0.892 |

Leitura: em BTC, ETH e BNB a série distingue direcção (longs liquidam-se em quedas, shorts em subidas) e tem cauda inferior fina; em SOL, XRP e DOGE a assimetria é fraca e o piso é alto (p1/p50 elevado): série modelada. A fonte `liq` usa só BTC, ETH e BNB; a fonte `subst` usa os 6.

### 2b. As liquidações trazem informação além do preço e do volume?

Quedas de 1 h no decil 10 de |ret| e volume (BTC/ETH/BNB, IS), separadas pelo sinal do resíduo da regressão (resíduo > 0 = mais liquidações do que o preço e o volume explicam). Retorno em bps a favor da compra, da abertura da vela seguinte ao fecho de k+h; EP agrupado por hora.


res>0 (mais liq do que o preço explica):

| horizonte_h | n | n_horas | media_bps | ep_bps | t | mediana_bps | frac_pos |
|---|---|---|---|---|---|---|---|
| 1 | 1266 | 798 | 3.7 | 4.3 | 0.9 | 16.7 | 0.6 |
| 4 | 1266 | 798 | 12.0 | 6.7 | 1.8 | 19.3 | 0.6 |
| 12 | 1266 | 798 | 24.5 | 10.4 | 2.3 | 31.1 | 0.6 |
| 24 | 1266 | 798 | 49.2 | 12.4 | 4.0 | 44.8 | 0.6 |

res<=0:

| horizonte_h | n | n_horas | media_bps | ep_bps | t | mediana_bps | frac_pos |
|---|---|---|---|---|---|---|---|
| 1 | 152 | 134 | 4.6 | 14.0 | 0.3 | 21.4 | 0.6 |
| 4 | 152 | 134 | 7.5 | 16.8 | 0.4 | 31.2 | 0.6 |
| 12 | 152 | 134 | 16.1 | 20.9 | 0.8 | 26.2 | 0.6 |
| 24 | 152 | 134 | 1.1 | 29.7 | 0.0 | 6.7 | 0.5 |

### 2c. A queda de OI acrescenta algo? (6 activos, quedas no decil 10 substituto, IS)


oi_chg<0:

| horizonte_h | n | n_horas | media_bps | ep_bps | t | mediana_bps | frac_pos |
|---|---|---|---|---|---|---|---|
| 1 | 1959 | 1102 | 10.9 | 4.9 | 2.2 | 21.7 | 0.6 |
| 4 | 1959 | 1102 | 28.2 | 7.3 | 3.9 | 34.4 | 0.6 |
| 12 | 1959 | 1102 | 56.6 | 11.3 | 5.0 | 38.6 | 0.6 |
| 24 | 1959 | 1102 | 81.0 | 14.8 | 5.5 | 44.6 | 0.6 |

oi_chg>=0:

| horizonte_h | n | n_horas | media_bps | ep_bps | t | mediana_bps | frac_pos |
|---|---|---|---|---|---|---|---|
| 1 | 931 | 679 | 6.0 | 6.9 | 0.9 | 18.4 | 0.6 |
| 4 | 931 | 679 | 37.0 | 12.3 | 3.0 | 35.3 | 0.6 |
| 12 | 931 | 679 | 57.9 | 17.9 | 3.2 | 54.3 | 0.6 |
| 24 | 931 | 679 | 93.1 | 21.4 | 4.3 | 71.5 | 0.6 |

## 3. Estudo de eventos no IS (2023-01-01 a 2025-03-31)

### 3a. Por lado, com espaçamento mínimo entre eventos do mesmo activo e deriva incondicional

Fonte substituta (6 activos). Retorno em bps A FAVOR do lado (compra após cascata de queda; venda após cascata de subida), da abertura da vela de 1 h seguinte ao fecho de k+h; EP agrupado por hora. `deriva_24h_a_favor` = retorno médio a 24 h de TODAS as horas do IS, com o sinal do lado; `excesso_24h` = retorno após o evento menos essa deriva. Um valor negativo na venda significa que o preço CONTINUOU a subir.

| P | espacamento_h | lado | n | n_horas | bps_1h | ep_1h | bps_4h | ep_4h | bps_12h | ep_12h | bps_24h | ep_24h | frac_pos_24h | deriva_24h_a_favor | excesso_24h |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.90 | 4 | compra apos queda | 2621 | 1331 | 8.1 | 4.6 | 25.5 | 7.2 | 52.6 | 11.3 | 79.1 | 14.0 | 0.56 | 17.2 | 61.8 |
| 0.90 | 4 | venda apos subida | 2122 | 1503 | -2.4 | 4.3 | -16.0 | 7.1 | -43.3 | 10.7 | -65.4 | 13.3 | 0.49 | -17.2 | -48.1 |
| 0.90 | 24 | compra apos queda | 1567 | 845 | 9.5 | 4.3 | 20.2 | 7.3 | 46.1 | 12.3 | 60.7 | 15.4 | 0.56 | 17.2 | 43.5 |
| 0.90 | 24 | venda apos subida | 1260 | 947 | -4.2 | 5.6 | -17.2 | 9.2 | -35.1 | 13.7 | -47.7 | 15.4 | 0.49 | -17.2 | -30.5 |
| 0.95 | 4 | compra apos queda | 1314 | 715 | 8.3 | 7.7 | 29.7 | 11.3 | 69.3 | 17.5 | 106.1 | 20.5 | 0.57 | 17.2 | 88.8 |
| 0.95 | 4 | venda apos subida | 1079 | 823 | -2.8 | 7.1 | -20.8 | 11.7 | -65.7 | 15.3 | -91.9 | 19.8 | 0.48 | -17.2 | -74.6 |
| 0.95 | 24 | compra apos queda | 917 | 511 | 4.1 | 7.0 | 13.7 | 11.2 | 54.8 | 17.2 | 76.2 | 22.1 | 0.56 | 17.2 | 59.0 |
| 0.95 | 24 | venda apos subida | 717 | 560 | -10.1 | 8.7 | -34.1 | 14.4 | -55.9 | 17.9 | -69.7 | 22.3 | 0.48 | -17.2 | -52.4 |

### 3b. Por decil de intensidade

Evento = vela de 1 h de queda (compra) ou de subida (venda) cuja intensidade cai no decil d do percentil móvel causal (liquidações: 90 dias; substituto = min(percentil de |ret| a 365 dias, percentil de volume a 90 dias) e taker do lado do movimento). Retorno em bps a favor do lado, da abertura da vela seguinte ao fecho de k+h; eventos do mesmo activo espaçados de 4 h; EP agrupado por hora porque os activos se movem juntos. O decil 10 é a cascata.


**liq_long** (horizontes em horas):

| decil | bps_1 | bps_4 | bps_12 | bps_24 | t_1 | t_4 | t_12 | t_24 | n |
|---|---|---|---|---|---|---|---|---|---|
| 1 | -0.4 | -0.7 | 1.2 | -1.4 | -0.5 | -0.3 | 0.3 | -0.2 | 1245.0 |
| 2 | 0.8 | 1.9 | 3.1 | 1.6 | 1.0 | 0.9 | 0.8 | 0.3 | 1612.0 |
| 3 | 0.3 | 0.9 | 3.0 | 8.1 | 0.3 | 0.4 | 0.7 | 1.2 | 1735.0 |
| 4 | 0.5 | -1.9 | 1.7 | 2.6 | 0.4 | -0.9 | 0.4 | 0.4 | 1941.0 |
| 5 | 1.0 | 1.5 | 0.7 | 1.3 | 0.9 | 0.7 | 0.2 | 0.2 | 2197.0 |
| 6 | -0.5 | -1.3 | -5.2 | -6.4 | -0.5 | -0.6 | -1.2 | -1.1 | 2352.0 |
| 7 | 1.1 | 3.0 | -2.6 | 6.6 | 0.9 | 1.2 | -0.6 | 1.1 | 2590.0 |
| 8 | -0.1 | 3.5 | 5.0 | 12.0 | -0.0 | 1.3 | 1.2 | 1.9 | 2817.0 |
| 9 | 1.6 | 5.1 | 5.3 | 19.1 | 1.2 | 1.9 | 1.2 | 3.0 | 2984.0 |
| 10 | 4.3 | 7.3 | 15.9 | 28.4 | 2.1 | 2.0 | 2.7 | 3.8 | 3082.0 |

**liq_short** (horizontes em horas):

| decil | bps_1 | bps_4 | bps_12 | bps_24 | t_1 | t_4 | t_12 | t_24 | n |
|---|---|---|---|---|---|---|---|---|---|
| 1 | -0.3 | 0.9 | -0.6 | 6.3 | -0.4 | 0.5 | -0.1 | 1.1 | 1534.0 |
| 2 | -0.1 | 0.8 | 0.7 | 5.2 | -0.1 | 0.4 | 0.2 | 0.9 | 1861.0 |
| 3 | 0.9 | 1.8 | -0.5 | -6.0 | 1.0 | 0.9 | -0.1 | -1.0 | 2041.0 |
| 4 | 0.0 | 0.4 | -4.5 | -5.2 | 0.0 | 0.2 | -1.1 | -0.8 | 2144.0 |
| 5 | 0.4 | 2.8 | 2.8 | 0.9 | 0.4 | 1.2 | 0.7 | 0.2 | 2284.0 |
| 6 | 0.8 | 2.1 | 5.7 | -1.2 | 0.8 | 1.0 | 1.3 | -0.2 | 2429.0 |
| 7 | 1.4 | -1.5 | -1.1 | -3.9 | 1.1 | -0.6 | -0.3 | -0.7 | 2530.0 |
| 8 | 1.8 | 3.6 | -4.2 | -9.0 | 1.6 | 1.5 | -1.0 | -1.4 | 2768.0 |
| 9 | 0.3 | -0.4 | -2.5 | -11.1 | 0.2 | -0.1 | -0.5 | -1.8 | 2901.0 |
| 10 | -0.9 | -7.0 | -20.3 | -29.6 | -0.4 | -1.8 | -3.4 | -3.9 | 2795.0 |

**subst_queda** (horizontes em horas):

| decil | bps_1 | bps_4 | bps_12 | bps_24 | t_1 | t_4 | t_12 | t_24 | n |
|---|---|---|---|---|---|---|---|---|---|
| 1 | -0.6 | -0.4 | 3.7 | 5.9 | -0.7 | -0.2 | 1.0 | 1.1 | 5356.0 |
| 2 | -0.8 | -1.4 | -0.6 | 3.6 | -0.8 | -0.7 | -0.1 | 0.7 | 5140.0 |
| 3 | 1.3 | 1.0 | 3.9 | 7.5 | 1.3 | 0.4 | 1.0 | 1.3 | 4682.0 |
| 4 | 0.8 | 3.0 | 2.9 | 10.5 | 0.7 | 1.2 | 0.7 | 1.8 | 4405.0 |
| 5 | 0.7 | 2.4 | 2.3 | 6.9 | 0.5 | 0.9 | 0.5 | 1.1 | 4081.0 |
| 6 | 2.7 | 3.2 | 8.6 | 16.2 | 1.7 | 1.0 | 1.7 | 2.4 | 3850.0 |
| 7 | 1.1 | 4.7 | 8.0 | 25.7 | 0.7 | 1.4 | 1.5 | 3.3 | 3514.0 |
| 8 | 1.9 | 5.6 | 9.8 | 25.1 | 0.9 | 1.4 | 1.6 | 3.0 | 3232.0 |
| 9 | 6.0 | 9.9 | 21.7 | 45.6 | 2.3 | 2.1 | 3.0 | 4.7 | 3008.0 |
| 10 | 8.1 | 25.5 | 52.6 | 79.1 | 1.8 | 3.5 | 4.7 | 5.6 | 2621.0 |

**subst_subida** (horizontes em horas):

| decil | bps_1 | bps_4 | bps_12 | bps_24 | t_1 | t_4 | t_12 | t_24 | n |
|---|---|---|---|---|---|---|---|---|---|
| 1 | -0.3 | -2.4 | -4.7 | -1.1 | -0.4 | -1.2 | -1.3 | -0.2 | 4527.0 |
| 2 | 2.1 | 1.0 | -2.9 | -5.3 | 2.2 | 0.5 | -0.7 | -0.9 | 4291.0 |
| 3 | 1.7 | 2.6 | 3.3 | 0.4 | 1.7 | 1.2 | 0.8 | 0.1 | 4060.0 |
| 4 | -1.8 | 1.0 | 0.3 | -6.4 | -1.5 | 0.4 | 0.1 | -1.0 | 3744.0 |
| 5 | 0.4 | 2.0 | -0.9 | -9.6 | 0.3 | 0.8 | -0.2 | -1.4 | 3529.0 |
| 6 | 2.1 | 2.5 | -0.7 | -4.7 | 1.5 | 0.9 | -0.1 | -0.6 | 3274.0 |
| 7 | 0.2 | 1.3 | 1.2 | -11.9 | 0.2 | 0.4 | 0.2 | -1.6 | 3054.0 |
| 8 | -1.6 | -6.6 | -18.4 | -31.6 | -0.8 | -1.8 | -2.9 | -3.6 | 2860.0 |
| 9 | 0.4 | -4.8 | -18.2 | -34.1 | 0.2 | -1.1 | -2.4 | -3.4 | 2552.0 |
| 10 | -2.4 | -16.0 | -43.3 | -65.4 | -0.6 | -2.3 | -4.0 | -4.9 | 2122.0 |

**liq_long_6** (horizontes em horas):

| decil | bps_1 | bps_4 | bps_12 | bps_24 | t_1 | t_4 | t_12 | t_24 | n |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.7 | 2.4 | -0.4 | -3.6 | 1.5 | 0.9 | -0.1 | -0.5 | 1951.0 |
| 2 | -0.5 | 0.5 | -1.7 | -4.4 | -0.5 | 0.2 | -0.4 | -0.7 | 2936.0 |
| 3 | 0.5 | 0.1 | -4.3 | -4.9 | 0.5 | 0.0 | -1.0 | -0.8 | 3490.0 |
| 4 | 0.7 | 0.1 | 5.4 | 8.8 | 0.6 | 0.0 | 1.3 | 1.5 | 4120.0 |
| 5 | 1.1 | 0.1 | -0.6 | -1.5 | 1.1 | 0.0 | -0.2 | -0.3 | 4621.0 |
| 6 | 0.0 | -0.7 | -1.8 | -1.4 | 0.0 | -0.3 | -0.4 | -0.2 | 4789.0 |
| 7 | 0.2 | 4.6 | 2.6 | 12.7 | 0.1 | 1.8 | 0.6 | 2.2 | 5207.0 |
| 8 | -0.2 | -0.1 | 0.4 | 5.8 | -0.2 | -0.1 | 0.1 | 1.0 | 5691.0 |
| 9 | 2.4 | 7.3 | 8.5 | 21.5 | 1.6 | 2.6 | 1.9 | 3.5 | 6098.0 |
| 10 | 5.3 | 12.2 | 28.7 | 46.5 | 2.4 | 3.1 | 4.7 | 5.7 | 6281.0 |

## 4. Grelha declarada (IS)

Fonte `subst` (6 activos), lado `compra` (o estudo de eventos no IS nega a venda), x percentil P [0.9, 0.95, 0.98] x reconquista ['minima', 'fecho'] x stop ['1 x ATR(14) atr1h', '2 x ATR(14) atr1h'] abaixo da mínima (acima da máxima) da cascata x alvo [2.0, 3.0, 6.0] R = 36 configurações, mais 3 regras literais da tarefa (stop 0,25 ATR de 15 m abaixo da mínima; fontes subst e liq dos dois lados, e subst só compra). Fixos: {'W': 8, 'n_max': 96, 'atr_n': 14} (W velas de 15 m para a reconquista, saída por tempo às 24 h, ATR de 14). Custo 6,5 bps por lado. Todas registadas em `ensaios/cascata.csv`. A grelha foi fixada DEPOIS do estudo de eventos e do diagnóstico de stop (a mínima da cascata é revisitada nas 24 h seguintes em 82 % dos casos em BTC e 84 % em SOL; a mínima menos 1 ATR de 1 h em 55 %), e ANTES de correr qualquer célula.

| fonte | p | reconquista | stop | alvo_R | lados | n | media | ep | PF | acerto | dd | n_pos | R_bruto | R_pct | frac_stop | frac_alvo |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| subst | 0.950 | minima | 0.25 x atr15 | 2.000 | ambos | 2321 | -0.167 | 0.028 | 0.770 | 0.354 | 398.864 | 1 | -0.015 | 1.211 | 0.615 | 0.277 |
| liq | 0.950 | minima | 0.25 x atr15 | 2.000 | ambos | 3351 | -0.242 | 0.024 | 0.691 | 0.346 | 811.888 | 0 | -0.022 | 0.787 | 0.629 | 0.286 |
| subst | 0.950 | minima | 0.25 x atr15 | 2.000 | compra | 1407 | -0.136 | 0.035 | 0.804 | 0.364 | 202.262 | 1 | -0.004 | 1.408 | 0.596 | 0.265 |
| subst | 0.900 | minima | 1 x atr1h | 2.000 | compra | 2363 | -0.025 | 0.025 | 0.955 | 0.421 | 111.705 | 2 | 0.039 | 2.285 | 0.497 | 0.203 |
| subst | 0.900 | minima | 1 x atr1h | 3.000 | compra | 2275 | -0.002 | 0.028 | 0.996 | 0.408 | 92.621 | 3 | 0.062 | 2.279 | 0.506 | 0.102 |
| subst | 0.900 | minima | 1 x atr1h | 6.000 | compra | 2190 | 0.010 | 0.032 | 1.018 | 0.402 | 99.723 | 3 | 0.075 | 2.258 | 0.511 | 0.014 |
| subst | 0.900 | minima | 2 x atr1h | 2.000 | compra | 1960 | 0.038 | 0.022 | 1.097 | 0.498 | 48.509 | 5 | 0.078 | 3.580 | 0.330 | 0.103 |
| subst | 0.900 | minima | 2 x atr1h | 3.000 | compra | 1913 | 0.043 | 0.024 | 1.110 | 0.493 | 48.390 | 4 | 0.084 | 3.554 | 0.332 | 0.033 |
| subst | 0.900 | minima | 2 x atr1h | 6.000 | compra | 1891 | 0.048 | 0.025 | 1.120 | 0.491 | 56.814 | 5 | 0.088 | 3.530 | 0.333 | 0.005 |
| subst | 0.900 | fecho | 1 x atr1h | 2.000 | compra | 2128 | 0.006 | 0.025 | 1.013 | 0.440 | 61.656 | 3 | 0.064 | 2.489 | 0.460 | 0.184 |
| subst | 0.900 | fecho | 1 x atr1h | 3.000 | compra | 2047 | 0.022 | 0.028 | 1.041 | 0.429 | 58.001 | 3 | 0.080 | 2.479 | 0.468 | 0.085 |
| subst | 0.900 | fecho | 1 x atr1h | 6.000 | compra | 1987 | 0.033 | 0.031 | 1.063 | 0.426 | 71.059 | 4 | 0.092 | 2.466 | 0.470 | 0.011 |
| subst | 0.900 | fecho | 2 x atr1h | 2.000 | compra | 1805 | 0.045 | 0.022 | 1.123 | 0.511 | 41.167 | 5 | 0.083 | 3.819 | 0.301 | 0.086 |
| subst | 0.900 | fecho | 2 x atr1h | 3.000 | compra | 1772 | 0.053 | 0.023 | 1.144 | 0.510 | 39.680 | 5 | 0.091 | 3.804 | 0.302 | 0.028 |
| subst | 0.900 | fecho | 2 x atr1h | 6.000 | compra | 1753 | 0.057 | 0.025 | 1.154 | 0.508 | 48.256 | 5 | 0.094 | 3.787 | 0.302 | 0.004 |
| subst | 0.950 | minima | 1 x atr1h | 2.000 | compra | 1240 | 0.011 | 0.034 | 1.021 | 0.440 | 56.705 | 2 | 0.064 | 2.868 | 0.466 | 0.197 |
| subst | 0.950 | minima | 1 x atr1h | 3.000 | compra | 1205 | 0.031 | 0.038 | 1.059 | 0.428 | 52.930 | 3 | 0.085 | 2.839 | 0.475 | 0.093 |
| subst | 0.950 | minima | 1 x atr1h | 6.000 | compra | 1188 | 0.052 | 0.042 | 1.096 | 0.421 | 51.479 | 4 | 0.106 | 2.818 | 0.481 | 0.013 |
| subst | 0.950 | minima | 2 x atr1h | 2.000 | compra | 1097 | 0.035 | 0.029 | 1.094 | 0.500 | 34.129 | 4 | 0.069 | 4.364 | 0.310 | 0.083 |
| subst | 0.950 | minima | 2 x atr1h | 3.000 | compra | 1089 | 0.045 | 0.030 | 1.119 | 0.497 | 36.121 | 4 | 0.079 | 4.342 | 0.313 | 0.024 |
| subst | 0.950 | minima | 2 x atr1h | 6.000 | compra | 1083 | 0.052 | 0.032 | 1.137 | 0.498 | 36.862 | 5 | 0.086 | 4.311 | 0.312 | 0.003 |
| subst | 0.950 | fecho | 1 x atr1h | 2.000 | compra | 1112 | 0.020 | 0.034 | 1.040 | 0.447 | 38.921 | 3 | 0.067 | 3.140 | 0.440 | 0.174 |
| subst | 0.950 | fecho | 1 x atr1h | 3.000 | compra | 1086 | 0.020 | 0.037 | 1.040 | 0.436 | 38.625 | 4 | 0.068 | 3.104 | 0.448 | 0.066 |
| subst | 0.950 | fecho | 1 x atr1h | 6.000 | compra | 1072 | 0.034 | 0.041 | 1.068 | 0.435 | 45.864 | 3 | 0.082 | 3.069 | 0.448 | 0.009 |
| subst | 0.950 | fecho | 2 x atr1h | 2.000 | compra | 1003 | 0.028 | 0.028 | 1.078 | 0.503 | 37.851 | 4 | 0.059 | 4.608 | 0.283 | 0.062 |
| subst | 0.950 | fecho | 2 x atr1h | 3.000 | compra | 999 | 0.041 | 0.030 | 1.115 | 0.504 | 39.234 | 5 | 0.072 | 4.607 | 0.284 | 0.020 |
| subst | 0.950 | fecho | 2 x atr1h | 6.000 | compra | 991 | 0.047 | 0.031 | 1.135 | 0.505 | 39.160 | 5 | 0.079 | 4.592 | 0.284 | 0.002 |
| subst | 0.980 | minima | 1 x atr1h | 2.000 | compra | 504 | 0.023 | 0.050 | 1.048 | 0.464 | 37.144 | 3 | 0.066 | 3.587 | 0.435 | 0.167 |
| subst | 0.980 | minima | 1 x atr1h | 3.000 | compra | 492 | 0.048 | 0.056 | 1.099 | 0.459 | 34.868 | 4 | 0.091 | 3.544 | 0.437 | 0.075 |
| subst | 0.980 | minima | 1 x atr1h | 6.000 | compra | 485 | 0.076 | 0.063 | 1.157 | 0.458 | 29.229 | 5 | 0.120 | 3.523 | 0.439 | 0.012 |
| subst | 0.980 | minima | 2 x atr1h | 2.000 | compra | 464 | 0.050 | 0.042 | 1.144 | 0.526 | 28.082 | 4 | 0.078 | 5.273 | 0.289 | 0.065 |
| subst | 0.980 | minima | 2 x atr1h | 3.000 | compra | 461 | 0.060 | 0.044 | 1.174 | 0.527 | 26.381 | 5 | 0.088 | 5.232 | 0.286 | 0.022 |
| subst | 0.980 | minima | 2 x atr1h | 6.000 | compra | 460 | 0.062 | 0.046 | 1.178 | 0.524 | 26.341 | 5 | 0.090 | 5.231 | 0.289 | 0.002 |
| subst | 0.980 | fecho | 1 x atr1h | 2.000 | compra | 455 | 0.041 | 0.051 | 1.091 | 0.473 | 24.706 | 4 | 0.079 | 3.883 | 0.404 | 0.145 |
| subst | 0.980 | fecho | 1 x atr1h | 3.000 | compra | 443 | 0.038 | 0.055 | 1.084 | 0.465 | 25.801 | 5 | 0.076 | 3.867 | 0.409 | 0.052 |
| subst | 0.980 | fecho | 1 x atr1h | 6.000 | compra | 438 | 0.041 | 0.059 | 1.090 | 0.463 | 24.412 | 3 | 0.080 | 3.828 | 0.411 | 0.007 |
| subst | 0.980 | fecho | 2 x atr1h | 2.000 | compra | 420 | 0.044 | 0.042 | 1.136 | 0.531 | 22.992 | 4 | 0.070 | 5.600 | 0.262 | 0.050 |
| subst | 0.980 | fecho | 2 x atr1h | 3.000 | compra | 418 | 0.049 | 0.043 | 1.152 | 0.531 | 22.366 | 4 | 0.075 | 5.588 | 0.261 | 0.014 |
| subst | 0.980 | fecho | 2 x atr1h | 6.000 | compra | 417 | 0.052 | 0.046 | 1.160 | 0.528 | 22.366 | 4 | 0.078 | 5.585 | 0.264 | 0.002 |

Blocos de 9 células (reconquista x stop), média das médias:

| reconquista | stop | media_bloco | n_total | n_min | n_pos_min | min_media | max_media | PF_medio |
|---|---|---|---|---|---|---|---|---|
| fecho | 1 x atr1h | 0.028 | 10768 | 438 | 3 | 0.006 | 0.041 | 1.059 |
| fecho | 2 x atr1h | 0.046 | 9578 | 417 | 4 | 0.028 | 0.057 | 1.133 |
| minima | 1 x atr1h | 0.025 | 11942 | 485 | 2 | -0.025 | 0.076 | 1.050 |
| minima | 2 x atr1h | 0.048 | 10418 | 460 | 4 | 0.035 | 0.062 | 1.131 |

## 5. Escolha no IS

Centro do bloco com melhor média de bloco (entre blocos com n >= 100 em todas as células), nunca a célula máxima: fonte `subst`, reconquista `minima`, stop 2 x ATR(14) atr1h, P = 0.95, alvo = 3.0 R, lado compra. Célula IS: n = 1089, média 0.045 R (EP 0.030), PF 1.12, DD 36.1 R, 4 activos positivos, R mediano 4.34 % do preço.

**IS, regra escolhida**: n = 1089, média 0.045 R (EP 0.030), mediana -0.009, PF 1.12, acerto 49.7 %, DD máx 36.1 R, R bruto médio 0.079, stop 31 % / alvo 2 %, duração média 19.1 h.

Por activo:

| activo | n | media | erro_padrao | soma | PF | taxa_acerto | dd_max |
|---|---|---|---|---|---|---|---|
| BNB | 171.000 | 0.004 | 0.075 | 0.648 | 1.010 | 0.491 | 8.447 |
| BTC | 192.000 | -0.006 | 0.075 | -1.082 | 0.987 | 0.490 | 13.229 |
| DOGE | 154.000 | 0.178 | 0.080 | 27.353 | 1.584 | 0.545 | 5.950 |
| ETH | 213.000 | -0.045 | 0.065 | -9.641 | 0.891 | 0.441 | 19.323 |
| SOL | 168.000 | 0.045 | 0.073 | 7.487 | 1.123 | 0.512 | 6.531 |
| XRP | 191.000 | 0.127 | 0.075 | 24.163 | 1.380 | 0.518 | 8.660 |

Por ano:

| ano | n | media | erro_padrao | soma | PF | taxa_acerto | dd_max |
|---|---|---|---|---|---|---|---|
| 2023 | 415.000 | 0.076 | 0.043 | 31.394 | 1.248 | 0.530 | 12.808 |
| 2024 | 577.000 | 0.046 | 0.044 | 26.485 | 1.111 | 0.485 | 36.121 |
| 2025 | 97.000 | -0.092 | 0.102 | -8.951 | 0.795 | 0.423 | 19.387 |

Por metade:

| metade | n | media | erro_padrao | soma | PF | taxa_acerto | dd_max |
|---|---|---|---|---|---|---|---|
| 1.a | 479.000 | 0.085 | 0.041 | 40.519 | 1.274 | 0.530 | 12.808 |
| 2.a | 610.000 | 0.014 | 0.043 | 8.408 | 1.032 | 0.470 | 36.121 |

Critérios H1 a H6 no IS (informativo):

| criterio | descricao | valor | limite | passa |
|---|---|---|---|---|
| H1 | n >= 100 | 1089.000 | 100.000 | True |
| H2 | media liquida >= 0.15 R | 0.045 | 0.150 | False |
| H3 | PF >= 1.3 | 1.119 | 1.300 | False |
| H4 | activos positivos >= 3 | 4.000 | 3.000 | True |
| H5 | DD maximo <= 10 R | 36.121 | 10.000 | False |
| H6 | duas metades positivas | 8.408 | 0.000 | True |

Nula com stop à mesma distância relativa mediana (4.34 % do preço), alvo 3.0 R, 96 velas, 400 sinais aleatórios por activo no IS (6 activos): média -0.075 R (EP 0.020), PF 0.78, acerto 42.3 %. Nula SÓ DE COMPRAS nos mesmos instantes (mede a deriva do IS com os mesmos stop e alvo): média -0.034 R (EP 0.020), PF 0.89.


## 6. OOS (2025-04-01 a 2026-09-09), avaliado UMA vez

**OOS, regra escolhida, 6 activos**: n = 533, média -0.112 R (EP 0.043), mediana -0.214, PF 0.76, acerto 41.1 %, DD máx 92.2 R, R bruto médio -0.070, stop 39 % / alvo 2 %, duração média 18.2 h.

Por activo:

| activo | n | media | erro_padrao | soma | PF | taxa_acerto | dd_max |
|---|---|---|---|---|---|---|---|
| BNB | 81.000 | -0.165 | 0.103 | -13.358 | 0.643 | 0.395 | 17.503 |
| BTC | 93.000 | 0.085 | 0.114 | 7.937 | 1.208 | 0.495 | 9.393 |
| DOGE | 81.000 | -0.157 | 0.100 | -12.733 | 0.669 | 0.432 | 17.123 |
| ETH | 118.000 | -0.208 | 0.086 | -24.524 | 0.582 | 0.373 | 26.429 |
| SOL | 84.000 | -0.144 | 0.112 | -12.136 | 0.716 | 0.381 | 18.604 |
| XRP | 76.000 | -0.061 | 0.121 | -4.657 | 0.862 | 0.395 | 17.456 |

Por ano:

| ano | n | media | erro_padrao | soma | PF | taxa_acerto | dd_max |
|---|---|---|---|---|---|---|---|
| 2025 | 285.000 | -0.122 | 0.058 | -34.710 | 0.744 | 0.411 | 57.740 |
| 2026 | 248.000 | -0.100 | 0.064 | -24.761 | 0.781 | 0.411 | 46.752 |

Por metade:

| metade | n | media | erro_padrao | soma | PF | taxa_acerto | dd_max |
|---|---|---|---|---|---|---|---|
| 1.a | 284.000 | -0.122 | 0.058 | -34.578 | 0.745 | 0.412 | 57.740 |
| 2.a | 249.000 | -0.100 | 0.064 | -24.893 | 0.781 | 0.410 | 46.752 |

Critérios H1 a H6 no OOS:

| criterio | descricao | valor | limite | passa |
|---|---|---|---|---|
| H1 | n >= 100 | 533.000 | 100.000 | True |
| H2 | media liquida >= 0.15 R | -0.112 | 0.150 | False |
| H3 | PF >= 1.3 | 0.761 | 1.300 | False |
| H4 | activos positivos >= 3 | 1.000 | 3.000 | False |
| H5 | DD maximo <= 10 R | 92.246 | 10.000 | False |
| H6 | duas metades positivas | -34.578 | 0.000 | False |

**Passa tudo: False.**


## 7. Robustez adicional (regra só de preço, volume e taker)

**2020-09 a 2022-12, 6 activos**: n = 877, média 0.030 R (EP 0.037), mediana -0.051, PF 1.07, acerto 47.5 %, DD máx 44.0 R, R bruto médio 0.053, stop 37 % / alvo 4 %, duração média 18.0 h.

Por activo:

| activo | n | media | erro_padrao | soma | PF | taxa_acerto | dd_max |
|---|---|---|---|---|---|---|---|
| BNB | 128.000 | 0.175 | 0.102 | 22.357 | 1.454 | 0.492 | 4.211 |
| BTC | 201.000 | -0.066 | 0.072 | -13.229 | 0.854 | 0.463 | 21.257 |
| DOGE | 120.000 | 0.197 | 0.110 | 23.614 | 1.563 | 0.525 | 7.532 |
| ETH | 185.000 | -0.100 | 0.080 | -18.550 | 0.804 | 0.405 | 28.903 |
| SOL | 116.000 | 0.121 | 0.103 | 14.092 | 1.308 | 0.526 | 11.280 |
| XRP | 127.000 | -0.018 | 0.082 | -2.331 | 0.948 | 0.488 | 7.722 |

Por ano:

| ano | n | media | erro_padrao | soma | PF | taxa_acerto | dd_max |
|---|---|---|---|---|---|---|---|
| 2020 | 155.000 | 0.091 | 0.086 | 14.166 | 1.259 | 0.497 | 15.127 |
| 2021 | 396.000 | 0.118 | 0.054 | 46.732 | 1.328 | 0.528 | 21.182 |
| 2022 | 326.000 | -0.107 | 0.061 | -34.944 | 0.794 | 0.402 | 43.933 |

Por metade:

| metade | n | media | erro_padrao | soma | PF | taxa_acerto | dd_max |
|---|---|---|---|---|---|---|---|
| 1.a | 521.000 | 0.103 | 0.047 | 53.706 | 1.280 | 0.509 | 21.182 |
| 2.a | 356.000 | -0.078 | 0.057 | -27.752 | 0.842 | 0.427 | 43.933 |

**IS, 18 activos**: n = 2878, média 0.052 R (EP 0.018), mediana 0.018, PF 1.14, acerto 50.9 %, DD máx 87.9 R, R bruto médio 0.081, stop 30 % / alvo 3 %, duração média 19.3 h.

Por activo:

| activo | n | media | erro_padrao | soma | PF | taxa_acerto | dd_max |
|---|---|---|---|---|---|---|---|
| ADA | 178.000 | 0.009 | 0.073 | 1.679 | 1.024 | 0.483 | 11.832 |
| APT | 127.000 | 0.066 | 0.082 | 8.433 | 1.197 | 0.528 | 7.522 |
| ARB | 125.000 | 0.016 | 0.084 | 2.002 | 1.042 | 0.544 | 9.788 |
| AVAX | 154.000 | 0.105 | 0.078 | 16.129 | 1.299 | 0.519 | 11.484 |
| BCH | 140.000 | 0.081 | 0.083 | 11.357 | 1.233 | 0.493 | 6.244 |
| BNB | 171.000 | 0.004 | 0.075 | 0.648 | 1.010 | 0.491 | 8.447 |
| BTC | 192.000 | -0.006 | 0.075 | -1.082 | 0.987 | 0.490 | 13.229 |
| DOGE | 154.000 | 0.178 | 0.080 | 27.353 | 1.584 | 0.545 | 5.950 |
| DOT | 176.000 | 0.043 | 0.073 | 7.556 | 1.114 | 0.523 | 11.233 |
| ETH | 213.000 | -0.045 | 0.065 | -9.641 | 0.891 | 0.441 | 19.323 |
| LINK | 147.000 | 0.056 | 0.071 | 8.191 | 1.176 | 0.551 | 6.944 |
| LTC | 198.000 | 0.016 | 0.066 | 3.259 | 1.047 | 0.515 | 9.875 |
| NEAR | 149.000 | 0.087 | 0.078 | 12.971 | 1.269 | 0.530 | 5.775 |
| OP | 131.000 | 0.075 | 0.091 | 9.877 | 1.199 | 0.519 | 10.463 |
| SOL | 168.000 | 0.045 | 0.073 | 7.487 | 1.123 | 0.512 | 6.531 |
| SUI | 117.000 | 0.096 | 0.090 | 11.217 | 1.299 | 0.530 | 4.799 |
| TRX | 147.000 | 0.045 | 0.096 | 6.685 | 1.102 | 0.469 | 13.638 |
| XRP | 191.000 | 0.127 | 0.075 | 24.163 | 1.380 | 0.518 | 8.660 |

Por ano:

| ano | n | media | erro_padrao | soma | PF | taxa_acerto | dd_max |
|---|---|---|---|---|---|---|---|
| 2023 | 1165.000 | 0.089 | 0.026 | 103.180 | 1.305 | 0.547 | 31.338 |
| 2024 | 1480.000 | 0.056 | 0.028 | 82.224 | 1.137 | 0.495 | 87.870 |
| 2025 | 233.000 | -0.159 | 0.063 | -37.121 | 0.677 | 0.403 | 51.454 |

Por metade:

| metade | n | media | erro_padrao | soma | PF | taxa_acerto | dd_max |
|---|---|---|---|---|---|---|---|
| 1.a | 1328.000 | 0.098 | 0.024 | 129.867 | 1.334 | 0.550 | 31.338 |
| 2.a | 1550.000 | 0.012 | 0.027 | 18.416 | 1.028 | 0.473 | 87.870 |

**OOS, 18 activos (avaliação única, declarada antes)**: n = 1503, média -0.109 R (EP 0.025), mediana -0.204, PF 0.76, acerto 40.9 %, DD máx 239.6 R, R bruto médio -0.073, stop 37 % / alvo 2 %, duração média 18.6 h.

Por activo:

| activo | n | media | erro_padrao | soma | PF | taxa_acerto | dd_max |
|---|---|---|---|---|---|---|---|
| ADA | 77.000 | -0.174 | 0.102 | -13.396 | 0.625 | 0.442 | 21.116 |
| APT | 88.000 | -0.006 | 0.095 | -0.503 | 0.985 | 0.443 | 9.738 |
| ARB | 87.000 | -0.052 | 0.103 | -4.499 | 0.874 | 0.379 | 12.961 |
| AVAX | 75.000 | -0.202 | 0.110 | -15.119 | 0.602 | 0.347 | 21.896 |
| BCH | 73.000 | -0.164 | 0.103 | -11.994 | 0.634 | 0.411 | 16.079 |
| BNB | 81.000 | -0.165 | 0.103 | -13.358 | 0.643 | 0.395 | 17.503 |
| BTC | 93.000 | 0.085 | 0.114 | 7.937 | 1.208 | 0.495 | 9.393 |
| DOGE | 81.000 | -0.157 | 0.100 | -12.733 | 0.669 | 0.432 | 17.123 |
| DOT | 78.000 | -0.068 | 0.101 | -5.275 | 0.833 | 0.462 | 10.479 |
| ETH | 118.000 | -0.208 | 0.086 | -24.524 | 0.582 | 0.373 | 26.429 |
| LINK | 76.000 | -0.148 | 0.101 | -11.221 | 0.666 | 0.408 | 15.161 |
| LTC | 79.000 | -0.031 | 0.103 | -2.441 | 0.916 | 0.443 | 11.580 |
| NEAR | 88.000 | -0.100 | 0.105 | -8.821 | 0.770 | 0.364 | 13.750 |
| OP | 93.000 | -0.174 | 0.090 | -16.157 | 0.637 | 0.409 | 21.094 |
| SOL | 84.000 | -0.144 | 0.112 | -12.136 | 0.716 | 0.381 | 18.604 |
| SUI | 76.000 | 0.015 | 0.112 | 1.160 | 1.042 | 0.434 | 9.406 |
| TRX | 80.000 | -0.204 | 0.117 | -16.326 | 0.624 | 0.350 | 21.582 |
| XRP | 76.000 | -0.061 | 0.121 | -4.657 | 0.862 | 0.395 | 17.456 |

Por ano:

| ano | n | media | erro_padrao | soma | PF | taxa_acerto | dd_max |
|---|---|---|---|---|---|---|---|
| 2025 | 787.000 | -0.103 | 0.034 | -80.910 | 0.772 | 0.409 | 130.789 |
| 2026 | 716.000 | -0.116 | 0.036 | -83.154 | 0.738 | 0.408 | 129.442 |

Por metade:

| metade | n | media | erro_padrao | soma | PF | taxa_acerto | dd_max |
|---|---|---|---|---|---|---|---|
| 1.a | 784.000 | -0.104 | 0.034 | -81.272 | 0.770 | 0.409 | 130.789 |
| 2.a | 719.000 | -0.115 | 0.035 | -82.791 | 0.740 | 0.408 | 129.442 |

Critérios H1 a H6 no OOS, 18 activos:

| criterio | descricao | valor | limite | passa |
|---|---|---|---|---|
| H1 | n >= 100 | 1503.000 | 100.000 | True |
| H2 | media liquida >= 0.15 R | -0.109 | 0.150 | False |
| H3 | PF >= 1.3 | 0.756 | 1.300 | False |
| H4 | activos positivos >= 3 | 2.000 | 3.000 | False |
| H5 | DD maximo <= 10 R | 239.641 | 10.000 | False |
| H6 | duas metades positivas | -82.791 | 0.000 | False |

## 8. Veredicto e honestidade

**Veredicto: a família NÃO passa.** O estudo de eventos no IS mostra um efeito real, acima de 2 erros padrão,
mas só do lado da compra e só no mercado em alta de 2023 a 2025-03; a regra converte pouco dessa deriva em R e
no OOS (avaliado uma vez) perde dinheiro: n 533, média -0,112 R (EP 0,043), PF 0,76, 1 de 6 activos positivo,
DD 92 R, as duas metades negativas. Nos 18 activos o OOS dá -0,109 R (EP 0,025), PF 0,76, 2 de 18 positivos.
Falha H2, H3, H4, H5 e H6; só passa H1.

O que pode estar errado ou é frágil:

1. **O lado da venda nega a hipótese no IS.** Depois de uma cascata de shorts (vela de 1 h extrema em subida,
   taker comprador) o preço CONTINUOU a subir: -48 a -92 bps a 24 h para quem vendesse (t -3 a -4,6). A
   hipótese de exaustão só vale para longs liquidados, o que é indistinguível de "comprar quedas num mercado
   em alta". A nula só de compras no IS dá -0,034 R com os mesmos stop e alvo (a deriva incondicional é +17 bps
   por dia); a regra dá +0,045 R: a vantagem sobre a deriva é 0,08 R, 2,6 EP, e desaparece no OOS.
2. **A mínima da cascata é revisitada em 82 % (BTC) a 84 % (SOL) dos casos nas 24 h seguintes**; a mínima menos
   1 ATR de 1 h em 55 %. A regra literal da tarefa (stop 0,25 ATR de 15 m abaixo da mínima, alvo 2 R, dois
   lados) dá no IS -0,167 R (n 2321, PF 0,77, 62 % stops) com a fonte substituta e -0,242 R (n 3351, PF 0,69)
   com liquidações; só compra -0,136 R. Os stops largos (2 ATR de 1 h, R de 4,3 % do preço) ainda são tocados
   em 31 % dos trades e deixam a média em +0,045 R no IS. Com R de 4,3 % o custo é 0,03 R: o problema não é o
   custo, é o sinal.
3. **O IS do próprio patamar já falhava H2, H3 e H5** (melhor bloco +0,048 R, PF 1,13, DD 36 R). Nenhuma das
   36 células passou de +0,076 R. Foi para o OOS por obrigação de método, não por expectativa.
4. **Regime, não vantagem.** Por ano, a regra só de compras dá +0,091 (2020), +0,118 (2021), -0,107 (2022),
   +0,076 (2023), +0,046 (2024), -0,092 (2025 Q1), -0,122 (2025 Q2 em diante), -0,100 (2026): segue o sinal
   do mercado. É beta ao regime com stop, não exaustão de fluxo forçado.
5. **Liquidações.** Nos 6 activos, a 1 h, o R2 em logs de |long_liq| sobre |ret|, volume e taker sell da mesma
   vela é 0,79 (BTC), 0,74 (ETH), 0,72 (BNB), 0,40 (SOL), 0,59 (XRP), 0,16 (DOGE); SOL, XRP e DOGE não
   distinguem direcção (assimetria queda/subida 1,1 a 1,5; p1/p50 0,53 a 0,89). A informação incremental é
   fraca: nas quedas extremas de BTC/ETH/BNB, resíduo > 0 dá +49 bps a 24 h (n 1266, EP 12) contra +1 bps
   (n 152, EP 30) com resíduo <= 0; a diferença de 48 bps tem EP 32 (1,5 EP). Decil 10 de liquidações sem
   preço/volume extremos: +26 bps (t 3,5), contra +79 do substituto. A fonte `liq` não foi para a grelha; a
   regra literal com `liq` fica registada (-0,242 R). A queda de OI não acrescenta nada (+81 contra +93 bps);
   o filtro taker exclui só 91 eventos em 2712 e é praticamente neutro.
6. **Fuga de informação a declarar.** (a) Antes de a grelha correr, um comando de diagnóstico imprimiu o estudo
   de eventos por lado também no OOS (lado da compra +1 a +13 bps a 24 h, EP 17 a 28). A grelha, o critério de
   escolha (centro do melhor bloco de 9) e o lado só de compra já estavam fixados no código antes dessa
   impressão e não mudaram depois; mesmo assim, o leitor deve saber. (b) Duas configurações foram corridas em
   ensaio seco antes da grelha definitiva (substituto P 0,95 mínima 2 R e liq P 0,95 fecho 2 R, ambas com stop
   0,25 ATR de 15 m): deram -0,167 e -0,198 R no IS; a primeira coincide com a regra literal registada, a
   segunda não está no CSV. (c) O diagnóstico de revisita da mínima foi feito no IS e ditou a escala do stop na
   grelha: é uso legítimo do IS, mas é uma escolha condicionada aos dados.
7. **Erros padrão.** Com espaçamento de 4 h entre eventos do mesmo activo, o t a 24 h do lado da compra é 5,2 a
   5,6; com 24 h (eventos não sobrepostos) cai para 3,4 a 3,9 e a média de 79 para 61 bps (P 0,90) e de 106
   para 76 (P 0,95). O EP agrupado por hora corrige a correlação entre activos, não a sobreposição temporal;
   os números com 24 h são os honestos.
8. **O que não se testou:** cascatas definidas a 4 h; saída a 48 ou 96 h (a deriva continuava até 96 h no IS,
   mas desapareceu no OOS segundo as taxas de base); filtro de regime (teria de ser uma família própria e,
   dado o rácio de variâncias de 2025-26 perto de 1, provavelmente ficaria fechado); funding como
   condicionante; posição no intervalo de dias ou zona estrutural; janela W diferente de 8 velas; o buffer do
   stop em ATR de 15 m com múltiplos maiores. Nenhuma destas variantes foi corrida, portanto M não as inclui.
9. **Binance não é a Hyperliquid:** preço e volume agregados são próximos; o livro, os takers e as liquidações
   não. Mesmo que uma variante passasse, teria de ser validada ao vivo.

Resumo em números: eventos IS lado compra P 0,95 espaçados 24 h, +76 bps a 24 h (EP 22, n 917); regra escolhida
IS +0,045 R (EP 0,030, n 1089, PF 1,12); OOS -0,112 R (EP 0,043, n 533, PF 0,76); 2020-22 +0,030 R (EP 0,037,
n 877); 18 activos OOS -0,109 R (EP 0,025, n 1503). M = 43 linhas em ensaios/cascata.csv: 39 configurações
no IS (3 regras literais + 36 da grelha), 1 linha de robustez nos 18 activos no IS, 1 em 2020-2022 e 2 no OOS
(6 e 18 activos, uma passagem).

