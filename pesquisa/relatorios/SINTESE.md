# SÍNTESE: seis famílias de hipóteses, nenhuma passa; a recomendação é não implementar uma regra

Arquitecto final, 2026-10-10. Lê-se depois dos seis relatórios em `pesquisa/relatorios/familia_*.md`.
Verificação independente em `pesquisa/verificar_sintese.py` (recalcula as métricas OOS a partir dos
ficheiros de trades, sem passar pelo código das famílias).

## 0. Resultado em cinco linhas

1. Seis famílias, 311 configurações registadas (M), seis avaliações OOS únicas: nenhuma passa H2, H3,
   H5 nem H6; três passam H4 por acaso. Veredicto: NÃO há regra mecânica para implementar no sinalizador.
2. Juntando os cinco OOS primários (7111 trades, 2025-04-01 a 2026-09-09): retorno BRUTO médio +0,006 R,
   líquido -0,086 R. As regras não seleccionam nada antes dos custos; a perda líquida é o custo.
3. O estudo de eventos foi negativo ANTES de qualquer regra em cinco famílias; na sexta (cascata) o efeito
   do IS era só do lado comprador num mercado em alta e desapareceu no OOS.
4. O mercado de 2025-2026 é um passeio aleatório a 1 h a 24 h (rácio de variâncias 0,99 a 1,02, contra
   0,90 a 0,93 em 2020-2024): a reversão à média que o REVOU e as bandas assumem deixou de existir.
5. O que fica: um conjunto limpo de resultados negativos, uma pista (ressalto de 2 a 4 h depois de 3
   sigma) que só pode ser testada em dados novos, e a conclusão de que a vantagem da Adriana, se existir,
   é discricionária e só se mede com o medidor de execução, não com um backtest.

## 1. Protocolo (igual para todas as famílias)

Dados: 18 perpétuos USDT-M da Binance, velas de 15 m, 2020-09 a 2026-09-09, com funding, OI,
liquidações, ls_ratio, basis (métricas nunca usadas em velas imputadas; `validar_colunas.py` antes de
qualquer uso). Custo 6,5 bps por lado. Sinal no fecho de k, entrada na abertura de k+1. Stop tocado pela
mínima/máxima, preenchido no pior entre o nível e a abertura; alvo e stop na mesma vela = stop; saída por
tempo ao fecho de N velas. IS 2023-01-01 a 2025-03-31 (famílias só de preço também 2020-09 a 2022-12);
OOS 2025-04-01 a 2026-09-09, avaliado UMA vez por família com a regra fixada a priori ou escolhida no IS.
Critérios H1 a H6 do utilizador, só no OOS: n >= 100; média líquida >= +0,15 R; PF >= 1,3; >= 3 activos
positivos; DD máximo <= 10 R; duas metades positivas. Ordem de trabalho: taxas de base, estudo de eventos
com erro padrão agrupado (os activos movem-se juntos), regra natural, grelha de sensibilidade registada
linha a linha, OOS.

## 2. Tabela comparativa

Estudo de eventos = o melhor t agrupado A FAVOR da hipótese no IS e o que apareceu contra. OOS = conjunto
primário de cada família (entre parênteses o conjunto de robustez na mesma passagem). Metades em soma de
R. M = linhas em `ensaios/<familia>.csv`. "Refutadores" = a minha verificação independente (secção 3) e
as fugas declaradas pelas próprias famílias; não foram entregues verificações adversariais externas.

| Família | Estudo de eventos (IS) | OOS n | Média R (EP) | PF | DD (R) | Activos + | Metades (R) | M | Veredicto dos refutadores |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ESTRUTURA (4 h varre, 1 h exaure, 15 m reconquista) | 72 células, t máximo +0,67; compras t 1,79 (1h_24 B, +29 bps a 12 h); vendas t até -4,7 (continuação) | 1478 (18 activos) | -0,055 (0,027) | 0,88 | 106,5 | 5 de 18 | -57,2 / -24,3 | 53 | Recalculado dos trades: coincide. Bruto -0,001 R = nula. 3 períodos disjuntos -0,074 / -0,055 / -0,048. "Reconquista 15 m" tautológica no modo 4 h; modo 15m4h também negativo. NEGATIVA |
| CASCATA (|ret| e volume extremos a 1 h, taker do lado, reconquista da mínima) | compra após queda decil 10: +79 bps a 24 h (t 5,6; sem sobreposição +61, t 3,9); venda após subida -65 bps (continuação) | 533 (6 activos; 18: 1503) | -0,112 (0,043) (18: -0,109) | 0,76 | 92,2 | 1 de 6 | -34,6 / -24,9 | 43 | Sem ficheiro de trades OOS em resultados/: cruzado só com ensaios/cascata.csv (coincide). IS +0,045 R já falhava H2, H3, H5. Fuga declarada: estudo de eventos OOS impresso antes da grelha. Regime (compras em bull), não exaustão. NEGATIVA |
| AGLOMERAÇÃO (funding, OI contra preço, ls_ratio_top, basis) | 20 testes contrarian, nenhum t > 0,2 a favor; funding alto = continuação (t 2,0 a 2,1); OI a cair = subida (t 2,0 a 2,3) | 280 (5 activos) | -0,082 (0,055) | 0,81 | 35,6 | 2 de 5 | -2,1 / -20,9 | 52 | Recalculado: coincide. IS -0,043 contra nula +0,017. No OOS 254 compras e 26 vendas: a regra mudou de natureza com o regime de funding. NEGATIVA |
| BANDAS (z a âncora EWMA 24/48/96 h com sigma coerente; a correcção da REVOU) | Z = 2: 48 de 50 células negativas, ewma96 toque 24 h -103 bps (t -3,4); pista Z = 3 a 2 h +68/+96/+113 bps (t 2,5 a 3,5 nos 6; 1,1 a 1,7 nos 18) | 341 (6 activos; 18: 970) | -0,072 (0,066) (18: -0,046) | 0,88 | 41,8 | 2 de 6 | -1,5 / -23,1 | 53 | Recalculado: coincide. IS -0,163 (6) pior do que a nula (-0,070) em 1,7 EP. Bandas de 4 dias do Bot (media96): -0,09 a -0,17 R em 12 de 12 configurações. 2020-22 -0,133. NEGATIVA |
| FLUXO TAKER (desequilíbrio, CVD, divergência, absorção, clímax) | 96 testes, único t >= 2 a favor é +1,5 bps a 1 h; contra: div16 -1,8 bps (t -2,26); clímax seguido de +60 bps em qualquer direcção | 4661 (6 activos; 18: 13 857) | -0,104 (0,019) (18: -0,097) | 0,85 | 505,5 | 0 de 6 | -219,8 / -263,5 | 54 | Recalculado: coincide. IS -0,141 é 4,7 EP PIOR do que a nula (-0,065): o sinal selecciona continuação. Invertido dá a nula. 3 períodos -0,077 / -0,141 / -0,104. NEGATIVA |
| TRANSVERSAL (resíduo do alt face ao BTC, beta 30 d) | |z| >= 2: t entre -1,1 e +0,9; cauda |z| >= 3 a 24 h +163 bps (t 2,9, 244 compras) com sinal invertido em 2020-22 (t -2,7); resíduo coberto continua | 351 (5 alts; 17: 1247) | +0,007 (0,058) (17: +0,023) | 1,02 | 36,8 | 2 de 5 | +3,0 / -0,6 | 56 | Recalculado: coincide. IS -0,122 contra nula +0,013 (3 EP abaixo). OOS 0,09 R acima da nula (1,6 EP) e 2026 negativo nos 5 e nos 17. Perna BTC pior em tudo (-0,038). NEGATIVA |

Critérios no OOS primário: H1 passa nas seis; H2, H3, H5 e H6 falham nas seis; H4 passa em ESTRUTURA
(5 de 18, que a nula também passa com 18 activos) e nos conjuntos secundários de BANDAS (7 de 18) e
TRANSVERSAL (10 de 17). Nenhuma família passa. Combinações não se avaliaram porque nenhuma componente
tem vantagem sozinha (condição declarada no protocolo).

Decomposição do OOS primário em bruto e custo (recalculada dos trades):

| Família | R mediano (% preço) | Custo médio (R) | Bruto (R) | Líquido (R) | Saídas |
| --- | --- | --- | --- | --- | --- |
| ESTRUTURA | 2,90 | -0,055 | -0,001 | -0,055 | tempo 48 %, stop 40 %, alvo 12 % |
| BANDAS (6) | 4,05 | -0,036 | -0,037 | -0,072 | stop 56 %, tempo 22 %, alvo 22 % |
| AGLOMERAÇÃO | 5,25 | -0,027 | -0,055 | -0,082 | tempo 65 %, stop 35 % |
| FLUXO (6) | 1,29 | -0,117 | +0,013 | -0,104 | stop 59 %, alvo 27 %, tempo 14 % |
| TRANSVERSAL (5) | 5,12 | -0,028 | +0,036 | +0,007 | tempo 64 %, stop 36 % |
| Cinco juntas | | | +0,006 | -0,086 (EP 0,015, n 7111) | |

Em três famílias o bruto é zero a menos de um erro padrão; em duas é ligeiramente negativo; em uma
ligeiramente positivo (0,6 EP). Nenhuma tem retorno bruto que pague 13 bps.

## 3. Verificação independente (o papel dos refutadores)

`verificar_sintese.py` leu os nove ficheiros de trades OOS e recalculou n, média, erro padrão, PF, DD
(curva acumulada por saída, todos os activos juntos), metades (ponto médio do OOS), activos positivos,
lados e critérios. Resultado: todos os números coincidem com os relatórios até à terceira casa. Todos os
sinais têm `sinal_em` dentro de 2025-04-01 a 2026-09-09. Cada `ensaios/<familia>.csv` tem 1 a 3 linhas
OOS, sempre a MESMA configuração em conjuntos diferentes de activos: o OOS foi de facto avaliado uma vez
por família. Causalidade: todas as famílias usam `projectar_superior` (só velas superiores fechadas) e
janelas para trás, e cinco das seis correram o teste de perturbação da cauda com diferença 0,0 no
passado; `sinal_em` é o fecho da vela k e `entrada_em` a abertura de k+1 (o mesmo instante), sem
lookahead. Limites da verificação: a CASCATA não gravou trades OOS, só a linha do registo; não repeti os
estudos de eventos nem as grelhas; não auditei linha a linha o código das características.

Preenchimento do stop (ponto 4 do Bot): o simulador preenche no pior entre o nível e a abertura da vela,
mas em mais de 20 000 trades OOS a abertura nunca saltou o stop (0,0 %), porque em velas de 15 m
contínuas a abertura é o fecho anterior e o stop teria disparado na vela anterior. Logo o preenchimento
"no nível" é o que os dados permitem e é OPTIMISTA face à Hyperliquid ao vivo (deslize numa cascata);
qualquer teste futuro deve acrescentar um deslize explícito nos stops (por exemplo 5 a 10 bps).

## 4. O que os dados mostram (transversal às famílias)

1. Taxas de base: autocorrelação de 1 h entre -0,006 (BTC) e -0,03 (LTC), 1 a 3 % da variância, muito
   abaixo do custo. Rácio de variâncias 1 h para 24 h (mediana dos 18 activos): 0,90 (2020), 0,91 (2021),
   0,96 (2022), 0,93 (2023), 0,92 (2024), 0,99 (2025), 1,02 (2026). A ligeira reversão de 2020-2024
   desapareceu exactamente no OOS. Qualquer regra de reversão calibrada até 2024 ficou sem o efeito.
2. Depois de uma vela de 1 h extrema (|ret| acima do percentil 99), no IS o preço subiu 233 bps em 24 h
   após quedas (t 8,1) E 126 bps após subidas (t 5,1): não era exaustão, era beta a um mercado em alta
   concentrado nas horas de volatilidade. No OOS: +28 bps (t 0,8) após quedas e -38 (t -1,1) após subidas.
3. O lado que "funciona" troca com o regime em todas as famílias de preço: compras ganham em 2020-21 e
   2023-24, vendas ganham no OOS. É a assinatura de deriva de mercado, não de vantagem do sinal.
4. Métricas de perpétuos (funding, OI, ls_ratio_top, basis): nos extremos não há reversão contrarian
   mensurável a 4 a 72 h; funding alto foi continuação; OI a crescer com o preço a cair foi mais queda. Os
   dois extremos do ls_ratio_top têm o mesmo sinal. Funding recebido durante as posições vale +0,006 R
   no IS e +0,0005 R no OOS: irrelevante.
5. Liquidações da Binance: em logs, |ret|, volume e taker da mesma vela explicam 72 a 83 % de
   log(liquidações) em BTC/ETH/BNB; em SOL/XRP/DOGE a série não distingue direcção (assimetria 1,1 a 1,5,
   percentil 1 alto): modelada. A informação incremental é 48 bps com EP 32 (1,5 EP). Não há base para
   regras com liquidações, OI ou baleias nestes dados.
6. Fluxo taker a 15 m e 4 h não prevê o retorno seguinte nem a favor nem contra (decis planos, |t| <= 1,3);
   a divergência preço/CVD confirmada por vela de rejeição é ligeiramente de CONTINUAÇÃO, mas a diferença
   bruta (11 bps) não paga 13 bps.
7. Critério H5 (DD <= 10 R, todos os activos juntos) é quase inatingível mesmo para uma vantagem real:
   para trades independentes com média +0,15 R e desvio padrão 0,9 a 1,2 R (o observado é 0,9 a 1,3), a
   probabilidade de DD <= 10 R é 0,70 a 0,93 com n 100, 0,27 a 0,74 com n 300 e 0,00 a 0,22 com n 1500.
   Com média +0,30 R e sd 1,2, ainda 0,18 com n 1500. Combinado com H1 (n >= 100) e com 5 a 18 activos
   em simultâneo, H5 reprova quase tudo o que H2 e H3 aprovariam. Sugestão: DD <= 10 R POR ACTIVO, ou
   DD <= 50 % da soma líquida do período, ou DD em fracção da conta com o dimensionamento real.

## 5. Resposta aos sete pontos do Bot

1. Filtro de regime (portão REVOU fechado 4799 de 4799 horas): confirmado e explicado. A meia-vida de um
   desvio a uma âncora de 96 h é por construção da ordem de dezenas de horas; exigir 3 a 24 h fecha
   sempre. Tornar a âncora e o horizonte coerentes (família BANDAS) tira o portão e mostra que a reversão
   não existe a 2 sigma: -74 bps a 24 h (t -2,5) na regra REVOU sem portão. O problema não era o portão.
2. Regra de entrada: provado que não tem vantagem. Com escala coerente e 18 activos, a regra REVOU dá
   -0,092 R no IS (n 1502) e -0,046 R no OOS (n 970), indistinguível da nula (-0,070 R).
3. Histórico: resolvido com 6 anos de Binance em 18 activos; a CoinGlass não é necessária para o preço.
   Para liquidações e livro da Hyperliquid, a CoinGlass também não serve (é outro livro); secção 7.
4. Stop: a regra "pior entre nível e abertura" está no simulador; nos dados de 15 m nunca disparou.
   O risco real é o deslize ao vivo, que fica por medir.
5. Baleias e liquidações (T1 a T8): sem backtest e, com estes dados, sem base: as liquidações da Binance
   são em grande parte derivadas do preço e do volume e são modeladas nos alts; a Hyperliquid tem outro
   livro. Ficam DESLIGADOS até haver prova com dados da própria Hyperliquid.
6. Critérios H1 a H6: aplicados tal como pedidos, só no OOS. Nenhuma família passa H2 e H3, que são os que
   medem vantagem. H5 precisa de ser reespecificado (secção 4.7), mas isso não muda nenhum veredicto.
7. Método estrutural (4 h autoriza, 1 h confirma, 15 m entra): testado literalmente (ESTRUTURA) e nas
   variantes de posição no intervalo e de bandas sigma à média de 4 dias (BANDAS, media96). Retorno
   bruto 0 a 10 bps após a reconquista contra 13 bps de custo; as bandas de 4 dias perdem em 12 de 12
   configurações com acerto de 25 a 37 %. Em forma mecânica, o método não tem vantagem mensurável.

## 6. Recomendação

NÃO implementar uma regra de entrada no sinalizador. Nenhuma das seis famílias tem retorno esperado
bruto que pague o custo, em nenhum dos três períodos (2020-22, 2023-25.03, 2025.04-26.09), e o OOS foi
gasto: qualquer regra escolhida agora com estes dados seria escolhida no OOS. Em concreto:

* Desligar no `medidor/config.ini` a regra REVOU v2 da secção `[sinalizador]` e os termos de baleias e
  liquidações T1 a T8 (incluindo o veto por B_k), e deixá-los desligados até existir um teste
  pré-registado positivo. O medidor continua a medir a execução; não se altera nada no `medidor/`
  nesta fase (foi só referência).
* Não afrouxar filtros, não inverter sinais (as versões invertidas foram vistas no IS e dão a nula), não
  escolher o lado vencedor do OOS (as vendas no OOS, as compras em 2023-24: é o regime).
* Se o objectivo é servir a Adriana, a única forma de provar vantagem é medir os trades discricionários
  dela com o medidor e aplicar-lhes H1 a H6 (com H5 reespecificado) depois de 100 trades. O backtest
  mostra que a versão mecânica do método não tem vantagem; se a versão com olho tiver, é o olho, e isso
  só se mede ao vivo.
* Uma pista fica para pré-registo (secção 8.1), a testar só em dados posteriores a 2026-09-09 ou em
  dados da Hyperliquid, com regras fechadas antes de olhar.

## 7. O que faria falta

1. Dados da Hyperliquid, não da Binance: trades, livro L2 (snapshots), preenchimentos do cofre liquidador
   e do HLP (as liquidações reais), funding horário, OI, para os 18 perpétuos e por 6 a 12 meses. Só com
   isto se testam cascata, absorção e baleias como fenómenos da plataforma onde se opera.
2. Um OOS novo: tudo o que esteja depois de 2026-09-09. As pistas da secção 8 precisam de 100 a 150
   sinais, isto é, 9 a 24 meses em 18 activos.
3. Deslize explícito nos stops (5 a 10 bps) e nos alvos manuais, calibrado pelo medidor de execução.
4. Critérios reespecificados: H5 por activo ou em fracção da soma; H6 em metades com n mínimo; e um
   critério de sobrevivência à nula (média >= nula + 2 EP) além do limiar absoluto.
5. Um diário de trades discricionários (Adriana) com sinal, entrada, stop, alvo, saída e razão, para
   que o medidor produza o mesmo relatório que as famílias produziram.

## 8. Pistas que ficam (não são regras; não foram testadas no OOS, ou foram e falharam)

### 8.1 Ressalto de 2 a 4 h depois de 3 sigma (BANDAS, Z = 3, modo toque)

IS, 6 principais, a 2 h: ewma24 +68 bps (t 2,5, n 116), ewma48 +96 (t 3,4, n 110), ewma96 +113 (t 3,5,
n 111); 18 activos +26/+37/+64 (t 1,1/1,5/1,7, n 287 a 367); 2020-22 +94/+86/+81 (t 1,9/1,6/1,4) mas
-370 a -470 bps a 96 h. São 3 células com t > 2,5 em 150; não confirmadas acima de 2 fora dos 6
principais. É a única pista com sinal consistente nos três períodos ao horizonte curto. Pré-registo
proposto (teste, não regra): âncora ewma48; sinal no primeiro fecho de 15 m com |z| >= 3 (fórmula 2);
lado contra o desvio; entrada na abertura seguinte; stop a 1 sigma_15m x sqrt(8) do fecho do sinal
(escala de 2 h) contra o trade; alvo 1,5 R; saída por tempo ao fecho da 8.ª vela (2 h); uma posição por
activo; 6,5 bps por lado mais 5 bps de deslize no stop; 18 activos; critérios H1 a H6 com H5 por
activo; só com dados posteriores a 2026-09-09, n >= 100, avaliação única.

### 8.2 Compras após cascata de queda (CASCATA)

IS +76 bps a 24 h sem sobreposição (t 3,4 a 3,9), regra +0,045 R; OOS -0,112 R. Já foi ao OOS e falhou;
só voltaria com dados de liquidações da Hyperliquid, como fenómeno diferente.

### 8.3 Compras em 1h_24 B a 12 h (ESTRUTURA)

+29 bps, t 1,8, excesso +25 bps, 1 em 144 células por lado. Fraca; só com OOS novo.

### 8.4 Momentum do desvio relativo (TRANSVERSAL, hipótese inversa)

Resíduo coberto decil 10: +27 bps de resíduo a 24 h no IS (t 2,0), +69 a +177 bps em 2020-22 (t 1,9 a
3,9). Precisaria de um estudo de eventos próprio e de uma perna no BTC executada a mão; não é um
candidato a regra discricionária.

### 8.5 Comprar desalavancagem (AGLOMERAÇÃO, O invertido)

IS +0,034 R, PF 1,10, 2023 -0,02, 2024 +0,11, 2025 -0,05; é a deriva pós-liquidação que desapareceu no
OOS das taxas de base. Não seguir.

## 9. Matemática das características (numerada, para referência)

1. Retorno log: r_k = ln(c_k / c_{k-1}). Retorno do estudo de eventos a favor do lado: lado x
   (ln c_{k+h} - ln o_{k+1}) x 10^4 bps.
2. Âncora EWMA do log-preço com meia-vida m velas: A_k = lambda A_{k-1} + (1 - lambda) ln c_k,
   lambda = 2^(-1/m); sigma_15m,k^2 = lambda sigma_15m,k-1^2 + (1 - lambda) r_k^2;
   z_k = (ln c_k - A_k) / (sigma_15m,k x sqrt(lambda^2 / (1 - lambda^2))). O factor é o desvio padrão
   teórico de (x - EWMA) para um passeio aleatório; calibrado: |z| > 2 em 3,3 a 4,6 % das velas contra 4,5 %
   teóricos. Para o VWAP de J velas a escala é sigma_15m x sqrt(J/3); as bandas de Bollinger clássicas
   usam o desvio padrão móvel e ficam fora de 2 sigma 16 % do tempo.
3. ATR(14) de Wilder em velas de 1 h fechadas: TR = max(h - l, |h - c_ant|, |l - c_ant|),
   ATR_k = (13 ATR_{k-1} + TR_k) / 14, projectado ao 15 m pela última vela de 1 h com fecho <= fecho da
   vela de 15 m.
4. Varrimento (ESTRUTURA): nível L = min(l das N velas superiores anteriores); sinal de compra se
   l_k < L e c_k > L; stop = l_k - 0,5 ATR_1h; R = entrada - stop; alvo = entrada + 2 R. Simétrico
   para venda. Exaustão 1 h: (h - l)/c >= P_0,80 móvel de 168 h E volume_quote >= P_0,80 E c no terço
   oposto; desequilíbrio taker d = (buy - sell)/(buy + sell) da última vela de 1 h acima (compra) ou
   abaixo (venda) da média das 3 anteriores. Posição no intervalo: (c - min_N)/(max_N - min_N).
5. Cascata (fonte substituta): vela de 1 h com r < 0, min(percentil móvel 365 d de |r|, percentil
   móvel 90 d de volume_quote) >= 0,95 e d < 0; reconquista = primeiro fecho de 15 m acima da mínima da
   cascata nas 8 velas seguintes; stop = mínima - 2 ATR_1h; alvo 3 R; 96 velas.
6. z robusto (AGLOMERAÇÃO, FLUXO, funding, OI, ls_ratio): z_k = (x_k - mediana_W) / max(1,4826 MAD_W,
   escala mínima), janela W de 30 dias só com velas não imputadas; percentil móvel causal de 90 dias para
   o funding. Sigma diária em preço: vol EWMA (meia-vida 168 h) dos retornos de 1 h x sqrt(24) x c;
   stop = 1,5 sigma diária; saída a 192 velas de 15 m.
7. Variação do OI a 24 h: soma de oi_change_pct em 24 velas de 1 h; direcção pelo retorno de 24 h.
8. Fluxo taker: d_1h, d_4h = (sum buy - sum sell)/(sum buy + sum sell) em 4 e 16 velas de 15 m;
   CVD_k = soma acumulada de (buy - sell); divergência de venda se h_k > max(h das N anteriores) e
   CVD_k < max(CVD das N anteriores); confirmação c_k < o_k; stop 1,5 ATR_1h, alvo 3 ATR_1h (2 R);
   absorção: |z_d1h| >= Z e |r_1h| < 0,5 sigma_1h; clímax: percentil 30 d do volume de 1 h >= P e
   |r_1h| >= sigma_1h.
9. Resíduo transversal: beta_k = cov(r_alt, r_btc)/var(r_btc) em 720 velas de 1 h;
   e_k = r_alt,k - beta_k r_btc,k; z_res,h = (ret_h,alt - beta ret_h,btc)/(sig_e x sqrt(h)), sig_e = vol
   EWMA (168 h) de e. Diferencial de funding alt menos BTC com z robusto de 30 d.
10. Rácio de variâncias de Lo-MacKinlay: VR(q) = var(r_q)/(q var(r_1)) sobre retornos de 1 h, com z
    robusto à heterocedasticidade; VR < 1 indica reversão, VR > 1 momentum.
11. Simulação: entrada = o_{k+1}; stop preenchido em min(nível, o_j) para compra (max para venda) na
    primeira vela j em que l_j <= nível (h_j >= nível); alvo no nível; ambos na mesma vela = stop; saída
    por tempo em c_{k+N}; custo em R = (entrada + saída) x 0,00065 / R; R líquido = R bruto - custo.
12. Erro padrão agrupado: as médias do estudo de eventos usam blocos temporais de 4 h (ou de h horas) com
    todos os activos no mesmo bloco; o agrupado é 1,5 a 8 vezes o ingénuo. Nula: sinais aleatórios com o
    mesmo stop, alvo e saída, que custam -13 bps / R (R em fracção do preço).

## 10. Limites desta síntese

* Binance não é a Hyperliquid: preços e funding são próximos; livro, takers e liquidações não. Os
  resultados negativos sobre preço transferem-se; qualquer resultado positivo futuro teria de ser
  validado ao vivo.
* Multiplicidade: 6 famílias x 50 a 150 células de eventos x 48 configurações; as poucas células com
  |t| entre 2 e 3,5 são o que o acaso dá e nenhuma confirma a hipótese que a família testava.
* O erro padrão agrupado por bloco não corrige a sobreposição entre blocos vizinhos a 24 a 96 h; os |t|
  a esses horizontes estão sobrestimados em módulo talvez 1,3 a 1,5 vezes, nos dois sentidos.
* O IS (2023-01 a 2025-03) foi um mercado em alta com deriva de 13 a 17 bps por dia; o OOS não. Várias
  conclusões por lado são de regime.
* O OOS está gasto para as seis famílias e para as suas variantes óbvias (lados, inversões, limiares
  vizinhos). Qualquer teste novo precisa de dados novos.
* Preenchimento de stops no nível em 100 % dos casos: optimista face ao deslize real.
* Não auditei linha a linha o código das características; confiei nos testes de causalidade por
  perturbação (5 de 6 famílias) e na recomputação das métricas a partir dos trades (5 de 6 famílias
  com trades OOS gravados; CASCATA só pelo registo).
* Regras com 7 a 11 parâmetros fixos (não varridos) em cada família: um ajuste local poderia mudar
  números, mas não um estudo de eventos plano em 18 activos e 6 anos.

## 11. Ficheiros

* Relatórios: `relatorios/familia_estrutura.md`, `familia_cascata.md`, `familia_aglomeracao.md`,
  `familia_bandas.md`, `familia_fluxo.md`, `familia_transversal.md`.
* Registo de todas as configurações (M = 311): `ensaios/*.csv`.
* Verificação: `verificar_sintese.py` (recomputação OOS, decomposição bruto/custo, lados, critérios).
* Taxas de base e validação das colunas: `taxas_de_base.py`, `validar_colunas.py`, `resultados/taxas_*.csv`,
  `resultados/validar_colunas*.csv`.

## 11. Nota de implementação (depois da síntese, 2026-10-10)

A recomendação da secção 6 foi posta em prática no `medidor/`, com o mínimo de alterações:

* `config.ini`, secção `[sinalizador]`: `emitir = nao` (defeito). O sinalizador avalia a regra REVOU v2 e
  regista os seus trades virtuais, mas não escreve em `sinais.csv` nem notifica; o log diz
  `NAO emitido (emitir = nao)`. Os termos T1 a T8 ficam sem efeito porque só dimensionam sinais que não se
  emitem. Recolha de dados da Hyperliquid inalterada (é o que a secção 7.1 pede).
* Teste pré-registado 8.1: `TesteTresSigma` em `reversao.py`, exactamente com as regras acima (âncora
  EWMA de 96 velas de 15 m, que é a `ewma48` desta pesquisa; sigma da mesma meia-vida; |z| >= 3 vindo de
  < 3; entrada na abertura seguinte; stop a sigma sqrt(8) com 5 bps de deslize, preenchido no pior entre
  nível e abertura; alvo 1,5 R; 8 velas; 6,5 bps por lado; só sinais a partir de 2026-09-10). Registo em
  `dados/registo_teste_3sigma.csv`; `python3 sinalizador.py teste3sigma` recusa-se a mostrar métricas
  antes de 100 trades e, com 100, aplica H1 a H6 com H5 por activo (secção 4.7). Nunca escreve sinais.
* `TradeVirtual.avancar`: stop preenchido na abertura quando a vela abre para lá do stop (ponto 4 do Bot).
* Testes: 288 unitários (o harness desta pasta tem mais 37) e duas simulações; `pesquisa/` não é
  importada pelo `medidor/`.
