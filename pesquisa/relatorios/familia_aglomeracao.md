# Família AGLOMERAÇÃO EM PERPÉTUOS: funding extremo, OI contra o preço, ls_ratio_top, basis

Resultado: NEGATIVA. O estudo de eventos no IS (2023-01-01 a 2025-03-31, 6 activos, 118 224 horas
x activo) não mostra nenhum retorno a favor do lado contrarian acima de 2 erros padrão agrupados em
nenhuma das cinco características nem em nenhum dos quatro horizontes; o melhor t é +0,13 (funding
z, 4 h) e os restantes 19 testes são negativos (t entre -0,1 e -2,3). Onde há algo perto de 2 erros
padrão, o sinal vai no sentido CONTRÁRIO à hipótese: funding alto é seguido de subida e OI a cair é
seguido de subida. A regra natural, fixada a priori (funding z >= 2 sobre 30 dias, stop 1,5 sigma
diária, saída a 48 h), perde no IS (-0,043 R, n 795) e no OOS (-0,082 R, n 280, avaliado uma vez);
47 das 48 configurações da grelha têm média negativa no IS e a 48.ª tem +0,007 R com PF 1,02. A
família não passa H2, H3, H4, H5 nem H6.

Código: `pesquisa/familia_aglomeracao.py` (usa `harness.py`). Registo: `pesquisa/ensaios/aglomeracao.csv`
(M = 52 linhas: 48 da grelha no IS, 1 regra natural no IS, 2 diagnósticos IS de sinal invertido, 1 OOS).
Resultados intermédios em `pesquisa/resultados/aglomeracao_*.csv` (decis, teste contrarian, grelha,
trades da regra natural no IS e no OOS, trades dos diagnósticos) e `aglomeracao_escolha.json`.

## 1. Hipótese

Num perpétuo, posicionamento concentrado de um lado tem preço: os longos pagam funding quando a
multidão está longa, o OI cresce quando entram posições novas, o rácio long/short das grandes contas
desvia-se, o basis abre. Se a multidão está presa de um lado, o movimento que a espreme (liquidações
em cascata, stops) deveria ir contra ela, e a reversão deveria demorar 4 a 48 h (o tempo de 1 a 6
pagamentos de funding e de o OI se limpar). Daí as regras contrarian: funding muito alto vende,
muito baixo compra; OI a crescer com o preço a cair compra (shorts novos a aglomerar), com o preço a
subir vende (longs novos a perseguir); ls_ratio_top muito alto vende, muito baixo compra. Os horizontes
são mais longos do que nas famílias de preço (saída por tempo a 1 a 3 dias) e o stop é em múltiplos
de sigma diária, porque a tese é de posicionamento e não de microestrutura intra-hora. A hipótese é
falsificável no estudo de eventos: se, depois de um extremo de posicionamento, o preço não anda em
média contra a multidão nas 4 a 72 h seguintes, não há stop nem saída que criem a vantagem. O que
tinha de aparecer: com R de 5 a 6 % do preço (1,5 sigma diária), o limiar H2 de +0,15 R exige cerca
de 75 a 90 bps de movimento esperado a favor por trade, e a nula com este R fica perto de -0,02 R.

## 2. Dados e características (tudo causal)

6 perpétuos USDT-M da Binance (BTC, ETH, SOL, BNB, XRP, DOGE), velas de 15 m, carregadas desde
2022-10-01 para aquecer as janelas (2022 está 87 % imputado até meados de Dezembro; as métricas
imputadas são mascaradas com `mascarar_imputado`, por isso as janelas só ficam cheias dentro de
Janeiro-Fevereiro de 2023). A família depende de métricas, logo NÃO corre em 2020-2022 nem nos 12
alts restantes. O funding do BNB não é fiável (54 % de zeros, 1,7 mudanças por dia; `validar_colunas.py`),
por isso o BNB fica fora das regras de funding (F e P) e entra só nas de OI e ls_ratio.

Características na vela de 1 h FECHADA (`harness.agregar`), levadas ao 15 m por `projectar_superior`
(só velas de 1 h com fecho <= fecho da vela de 15 m); o sinal só nasce nas velas de 15 m que coincidem
com um fecho de hora, e a entrada é na abertura do 15 m seguinte:

* `z_f`: z robusto do funding (taxa de 8 h, em %) sobre 30 dias (720 velas de 1 h, mínimo 15 dias),
  escala mínima 0,002 % (o funding fica preso em 0,01 % e o MAD vai a zero);
* `p_f`: percentil do funding em 90 dias (2160 velas, mínimo 45 dias);
* `z_b`: z robusto do basis (perp menos spot, em bps do preço) sobre 30 dias, escala mínima 1 bp;
* `z_oi`: z robusto da variação do OI em 24 h (soma de `oi_change_pct`, que está em contratos e não
  em USD) sobre 30 dias, escala mínima 0,05 pontos; `ret24`: retorno log de 24 h;
* `z_l`: z robusto do `ls_ratio_top` sobre 30 dias, escala mínima 0,01 (nunca o nível: ACF 0,9997);
* `sigma`: sigma diária em preço = vol EWMA (meia-vida 7 dias) dos retornos de 1 h x sqrt(24) x fecho.

Verificação de causalidade: multiplicar por 1,3 preço, funding, OI, oi_change, ls_ratio e basis das
últimas 200 h de ETH não altera nenhuma das 7 características no passado (diferença máxima 0,0 no 1 h
e na projecção ao 15 m).

Parâmetros fixos declarados antes de olhar para trades: janelas 720 e 2160 velas de 1 h, mínimos
360 e 1080, escalas mínimas acima, OI a 24 h, meia-vida da vol 168 h, sem alvo (só stop e tempo).

## 3. Estudo de eventos no IS, antes de qualquer regra

Painel de 1 h dos 6 activos no IS: 118 224 linhas, 19 704 horas distintas. Retorno log em bps da
ABERTURA da vela de 1 h seguinte até ao FECHO de k+h, h = 4, 24, 48, 72 h. Deriva do período (todas
as horas): +2,8 / +16,8 / +33,3 / +49,0 bps (medianas 1,9 / 6,1 / 13,3 / 15,9): o IS foi um bull
market. Erro padrão AGRUPADO por bloco de h horas (os activos movem-se juntos e os horizontes
sobrepõem-se; o agrupado é 2,3 a 8 vezes o ingénuo). Decis com desempate por ordem porque o z do
funding tem milhares de zeros exactos (funding preso em 0,01 %); os empates ficam nos decis centrais.

### 3.1 Teste da hipótese: retorno A FAVOR do lado contrarian nos extremos

Decil 1 compra e decil 10 vende (funding, basis, ls_ratio); para o OI, decil 10 com preço a cair compra
e com preço a subir vende. bps a favor do lado, erro padrão agrupado, t:

| característica | n | 4 h | 24 h | 48 h | 72 h |
| --- | --- | --- | --- | --- | --- |
| funding z 30 d (5 activos) | 19 366 | +0,4 (ep 3,1; t 0,13) | -13,6 (16,2; -0,84) | -40,4 (30,1; -1,34) | -47,9 (45,7; -1,05) |
| funding percentil 90 d (5) | 19 006 | -0,4 (3,2; -0,12) | -16,8 (15,1; -1,11) | -41,6 (29,4; -1,42) | -57,4 (42,8; -1,34) |
| basis z 30 d (6) | 23 242 | 0,0 (2,6; 0,02) | -7,0 (11,6; -0,60) | -31,4 (23,1; -1,36) | -48,4 (36,2; -1,33) |
| ls_ratio_top z 30 d (6) | 23 242 | -1,2 (2,3; -0,54) | -2,0 (10,6; -0,19) | -3,6 (22,1; -0,16) | -3,5 (33,1; -0,11) |
| OI 24 h z 30 d, contra o preço (6) | 11 446 | -6,6 (2,9; -2,29) | -23,7 (14,0; -1,69) | -41,3 (22,6; -1,83) | -42,2 (32,7; -1,29) |

Fracção de eventos positivos entre 0,49 e 0,53 em todas as células. Nenhum t > 2 a favor. Os lados
estão equilibrados (metade compras, metade vendas), por isso a deriva do bull market não explica os
números: o excesso sobre a deriva coincide com o retorno bruto a menos de 1 bp (coluna
`excesso_deriva_bps` em `aglomeracao_eventos_contrarian_IS.csv`). Conclusão a priori: a família é
NEGATIVA; vai ao OOS só a regra natural.

### 3.2 O que os decis mostram (excesso sobre a média de todas as horas, bps; t agrupado)

Funding percentil 90 d: decil 10 (percentil >= 0,889) +4,6 / +45,8 / +97,4 / +140,6 (t 0,9 / 2,0 /
2,1 / 2,0); decil 9 +6,4 / +22,7 / +49,4 / +65,8 (t 2,0 / 1,3 / 1,3 / 1,1); decil 1 (funding <= 0,076)
+3,8 / +12,3 / +14,3 / +25,9 (t 0,9 / 0,7 / 0,4 / 0,6). Funding z 30 d: decil 10 (z >= 1,27) +4,6 /
+40,5 / +84,7 / +118,5 (t 1,0 / 1,7 / 1,7 / 1,6); decil 1 (z <= -1,9) +5,4 / +13,3 / +3,9 / +22,6
(t <= 1,3). Leitura: funding alto no IS foi CONTINUAÇÃO (o bull market pagava-se em funding e
continuava), não reversão; funding muito negativo não deu ressalto mensurável.

OI 24 h com preço a cair: decil 1 (OI a cair >= 1,44 sigma robustas, isto é, desalavancagem numa
queda) +12,3 / +44,0 / +59,8 / +62,0 (t 2,3 / 1,9 / 2,0 / 1,5); decil 9 (OI a crescer 0,87 a 1,48)
-10,2 / -46,2 / -58,0 / -52,4 (t -3,0 / -2,4 / -1,8 / -1,3); decil 10 -15,8 / -38,6 / -33,5 / -7,5
(t -2,7 / -1,5 / -0,8 / -0,1). OI com preço a subir: decil 1 (OI a cair numa subida) -2,1 / +38,1 /
+72,6 / +103,6 (t -0,4 / 2,0 / 2,1 / 2,2); decil 10 +3,3 / +11,3 / +13,3 / +16,2 (t <= 0,9).
Leitura: OI a crescer com o preço a cair foi seguido de MAIS queda (a hipótese dizia squeeze para
cima); OI a cair, em qualquer direcção do preço, foi seguido de subida. É coerente com o ressalto
após liquidações do `taxas_de_base.py` (que também desapareceu no OOS lá medido).

Basis z: decil 10 -0,7 / +4,0 / +35,0 / +58,2 (t <= 1,0), decil 1 -0,6 / -9,9 / -27,7 / -38,5 (t >= -0,9):
nada. ls_ratio_top z: decil 10 +2,5 / +13,6 / +30,6 / +43,6 (t <= 0,9), decil 1 +0,1 / +9,5 / +23,5 /
+36,6 (t <= 1,0): nada, e os dois extremos têm o mesmo sinal.

Multiplicidade: são 6 tabelas x 10 decis x 4 horizontes = 240 células correlacionadas; 6 a 8 células
com |t| entre 2,0 e 3,0 é o que se espera do acaso. Nenhuma delas é a favor da hipótese contrarian.

## 4. Regra natural (fixada a priori) no IS

`F`: z_f >= 2 vende, z_f <= -2 compra; stop 1,5 sigma diária; sem alvo; saída por tempo ao fecho da
192.ª vela de 15 m (48 h); uma posição por activo; 5 activos (sem BNB). 16 353 horas com condição
verdadeira, 15 558 ignoradas por posição aberta, 795 trades.

n 795; média -0,043 R (ep 0,036); mediana -0,107; PF 0,887; acerto 45,3 %; DD máximo 65,5 R; R médio
6,04 % do preço; duração média 39,8 h; 70 % saem por tempo, 30 % por stop. Compras 492 (média +0,046 R),
vendas 303 (-0,187 R). Por activo (n, média): BTC 122, -0,048; ETH 124, -0,005; SOL 150, -0,075; XRP
204, +0,012; DOGE 195, -0,097 (1 em 5 positivo). Metades: 1.ª 400 trades +0,035 R, 2.ª 395 -0,122 R.
Por ano: 2023 373, +0,007; 2024 349, -0,098; 2025 (até Março) 73, -0,033. Vendas negativas em todos os
5 activos (BTC -0,274, DOGE -0,187, XRP -0,176, ETH -0,160, SOL -0,136); compras positivas em BTC
(+0,145), ETH (+0,126), XRP (+0,093) e negativas em SOL e DOGE.

Nula com o mesmo stop e n_max (6 activos x 800 sinais ao acaso no IS): média +0,017 R (ep 0,023),
PF 1,05, acerto 46,9 %, 25 % stops. A regra está 0,06 R abaixo da nula (1,4 erros padrão).

Funding recebido/pago durante a posição (não entra no simulador; medido à parte nas liquidações das
00:00, 08:00 e 16:00 UTC): +0,006 R em média no IS (vendas +0,022 R, compras -0,004 R); com ele a média
passa de -0,043 para -0,037 R. Não muda nada.

## 5. Grelha (48 configurações, declarada antes de correr), IS

Regras F (z_f, limiar 2 e 3), P (percentil 90 d, 0,95 e 0,98), O (z_oi 1,5 e 2,5 com direcção pelo
ret24), L (z_l, 2 e 3) x stop {1,0; 1,5} sigma diária x n_max {96, 192, 288} velas de 15 m. Tudo em
`ensaios/aglomeracao.csv` e `resultados/aglomeracao_grelha_IS.csv`.

| regra | n (mín a máx) | média R (mín a máx) | PF (mín a máx) | activos positivos |
| --- | --- | --- | --- | --- |
| F funding z | 498 a 1262 | -0,173 a -0,015 | 0,72 a 0,95 | 0 a 1 de 5 |
| P funding percentil | 363 a 998 | -0,043 a +0,007 | 0,90 a 1,02 | 2 a 3 de 5 |
| O OI contra o preço | 465 a 1324 | -0,179 a -0,091 | 0,68 a 0,77 | 0 a 1 de 6 |
| L ls_ratio_top z | 175 a 831 | -0,291 a -0,041 | 0,56 a 0,86 | 1 a 2 de 6 |

47 de 48 negativas; a única positiva é P 0,95, stop 1,5, 288 velas: +0,007 R (ep 0,044), PF 1,017,
n 588. Em todas as regras, as vendas perdem (médias -0,086 a -0,385 R) e as compras compensam em
parte (F e P +0,04 a +0,18 R, L +0,13 a +0,35 R com 23 a 170 trades); em O até as compras perdem
(-0,049 a -0,179 R). Limiar mais apertado piora sempre (F 3 pior do que F 2 nas 6 células; L 3 pior do
que L 2 nas 6; O 2,5 pior ou igual a O 1,5): o extremo mais extremo não é mais contrarian. Stop 1,5
sigma é menos mau do que 1,0 em 23 de 24 pares. Horizonte: sem padrão. A média alisada com os vizinhos
(critério declarado para escolher o centro de um patamar) é negativa em todas as 48 células (F -0,068
a -0,085; P -0,015 a -0,020; O -0,129 a -0,146; L -0,106 a -0,148), por isso não há patamar a escolher
e a função `escolher` devolve None para as quatro regras.

Diagnóstico IS-only com o SINAL INVERTIDO (fora da grelha, registado, nunca candidato ao OOS; feito
depois de o estudo de eventos mostrar o sinal ao contrário): F_inv (seguir o funding) n 759, média
-0,000 R (ep 0,038), PF 0,999, 2 de 5 activos positivos, metades -0,118 / +0,134; O_inv (comprar
desalavancagem) n 887, +0,034 R (ep 0,034), PF 1,095, DD 30,5 R, 3 de 6 positivos (DOGE +0,168, SOL
+0,104, BTC +0,056; ETH -0,092), por ano 2023 -0,021, 2024 +0,111, 2025 -0,052. Nenhum passaria H2,
H3, H5 ou H6 em amostra. O sinal invertido também não é uma estratégia.

## 6. OOS (2025-04-01 a 2026-09-09), avaliado UMA vez

Configuração: a regra natural, pela razão declarada ("estudo de eventos negativo: só a regra
natural"), gravada em `resultados/aglomeracao_escolha.json`. 3641 horas com condição verdadeira, 3361
ignoradas por posição aberta, 280 trades.

n 280; média -0,082 R (ep 0,055); mediana -0,159; PF 0,805; acerto 43,9 %; DD máximo 35,6 R; R médio
5,20 % do preço; duração média 38,3 h; 65 % saem por tempo, 35 % por stop. Compras 254 (média -0,070 R),
vendas 26 (-0,200 R): no OOS o funding quase nunca ficou 2 sigmas acima da mediana de 30 dias, e a
regra passou a ser uma regra de comprar funding negativo. Por activo (n, média, soma): BTC 41, -0,259,
-10,6; ETH 58, -0,223, -12,9; SOL 53, -0,121, -6,4; XRP 76, +0,002, +0,1; DOGE 52, +0,132, +6,9 (2 de
5 positivos, um deles por 0,1 R). Metades: 1.ª (até 2025-12-20) 162 trades, -0,013 R; 2.ª 118, -0,177 R.
Por ano: 2025 163, -0,010 R, PF 0,97; 2026 117, -0,182 R, PF 0,63. Funding recebido durante a posição:
+0,0005 R em média (irrelevante).

Critérios H1 a H6 no OOS: H1 n >= 100: 280, PASSA; H2 média >= +0,15 R: -0,082, FALHA; H3 PF >= 1,3:
0,805, FALHA; H4 >= 3 activos positivos: 2, FALHA; H5 DD <= 10 R: 35,6, FALHA; H6 duas metades
positivas: -2,1 e -20,9, FALHA. A família NÃO PASSA.

Robustez em 2020-2022: não aplicável (família de métricas; 2022 está 87 % imputado e os alts têm
2020-2021 imputados).

## 7. Honestidade

* O que pode estar errado na hipótese e não no teste: no IS, funding alto foi continuação, e OI a
  crescer contra a queda foi mais queda. Isto é compatível com um mercado em que a multidão longa
  tinha razão (bull de 2023-25.03) e em que as quedas com OI a crescer eram o início de cascatas e não
  o fim. Num regime diferente o sinal pode inverter-se, mas isso é precisamente o que torna a regra
  inutilizável sem um filtro de regime que não se testou e que, pelo historial do REVOU, provavelmente
  fecharia sempre.
* O que é frágil no teste: (1) 240 células de decis e 20 testes contrarian; |t| perto de 2 em 6 a 8
  células é o que o acaso dá, e nenhuma é a favor; (2) o erro padrão agrupado por bloco de h horas
  corrige a correlação entre activos e a sobreposição dentro do bloco, não entre blocos vizinhos,
  por isso ainda subestima um pouco a 48 e 72 h; (3) os sinais são de nível (a condição mantém-se
  verdadeira horas seguidas) com uma posição por activo, logo os trades são quase todos reentradas na
  mesma situação: 795 trades no IS são talvez 150 a 250 episódios independentes; (4) R de 5 a 6 % do
  preço esconde o custo (0,02 R) e torna os números em R pequenos: -0,082 R no OOS são -43 bps de
  preço por trade; (5) o z do funding tem escala mínima 0,002 %, escolhida à vista das distribuições
  e não optimizada; outra escala mudaria quantas horas passam o limiar, não o sinal do resultado,
  porque a regra P (percentil, sem escala) dá o mesmo.
* O que não se testou: entrada no fecho de 4 h em separado (os fechos de 4 h são um quarto dos fechos
  de 1 h testados); sinais compostos (funding E OI E ls_ratio ao mesmo tempo); sinais relativos entre
  activos (funding do SOL face ao do BTC); a versão "seguir a multidão" fora do IS (de propósito: o
  sinal invertido foi visto no IS e seria escolhido a posteriori); liquidações (modeladas nos alts,
  usáveis com reservas em BTC/ETH/BNB; a família não as usou).
* Binance não é a Hyperliquid: o funding da Hyperliquid liquida-se de hora a hora com fórmula
  própria (prémio mais juro de 0,01 % por 8 h) e o OI e o ls_ratio são de outro livro. A direcção do
  resultado (nenhuma reversão contrarian mensurável a 4 a 72 h) é sobre o posicionamento agregado do
  mercado, que os dois livros partilham em grande parte; os valores exactos não transferem.
* O que fica de útil: um estudo de eventos limpo que diz que, em 2023-25, os extremos de funding, OI,
  basis e ls_ratio NÃO precederam reversões em 4 a 72 h; que o único efeito perto de 2 erros padrão
  (OI a cair, preço a subir depois) é a mesma deriva pós-liquidação que já se viu em `taxas_de_base.py`
  e que desapareceu no OOS; e que uma regra de funding de sentido fixo troca de natureza quando o
  regime de funding muda (no OOS 254 compras contra 26 vendas).
