# Pesquisa: dados primeiro, hipóteses depois, estratégia no fim

Pasta de investigação separada do `medidor` (que não se altera). Tudo em pandas e numpy, português
europeu, sem travessões. A ordem de trabalho é: validar as colunas, medir as taxas de base, calibrar
cada família com estudos de eventos, simular no IS, registar TODOS os ensaios e só depois avaliar
o OOS uma única vez.

## Ambiente

```
PY=/tmp/claude-0/-home-user-TestRepo/401fa93a-318c-525f-aa0e-ce261d9dea8b/scratchpad/chk/venv/bin/python
cd /home/user/TestRepo/pesquisa
```

Os parquet de 15 m (um por activo, `BTCUSDT_15m.parquet`, ...) ficam na pasta apontada por
`PESQUISA_DADOS` (por omissão `.../scratchpad/pesquisa/dados`). Não estão no repositório.

## Dados

`preparar_dados.py <pasta_destino>` lê os masters de 15 m do repositório público
`kbsingh1399/backtesting_data` (pasta `Binance_Data`, um parquet por activo, 2020-09 a 2026-09-10, com
funding, OI, liquidações, volumes taker, rácios long/short e a coluna `is_imputed_metrics`), guarda só as
colunas brutas e imprime um resumo de qualidade por activo. O caminho de origem está no topo do script.
`diagnostico_portao_revou.py` reproduz, com o código do `medidor/`, a fracção de horas VERDE do portão
REVOU em 2025-2026 (0,00 por cento em BTC, ETH, SOL e DOGE).

## Correr

```
$PY -m unittest teste_harness -v      # testes do harness, dados sintéticos, < 60 s
$PY -m pyflakes *.py                  # lint
$PY validar_colunas.py                # liquidações, OI, ls_ratio, funding: derivadas ou usáveis?
$PY taxas_de_base.py                  # autocorrelação, rácio de variâncias, velas extremas, nula
$PY familia_bandas.py tudo         # família BANDAS (z a âncora EWMA/VWAP/média de 24, 48, 96 h com sigma coerente, a correcção da REVOU): eventos, regra natural, grelha, OOS
$PY familia_estrutura.py tudo      # família ESTRUTURA (4 h autoriza, 1 h confirma, 15 m entra): eventos, regra natural, grelha, OOS
$PY familia_aglomeracao.py tudo    # família AGLOMERAÇÃO (funding, OI contra o preço, ls_ratio_top, basis): decis, regra natural, grelha de 48, OOS
$PY familia_fluxo.py tudo          # família FLUXO TAKER (desequilíbrio taker, CVD, divergência preço/CVD, absorção, volume climático): eventos, regra natural, grelha de 48, OOS
$PY familia_transversal.py tudo    # família TRANSVERSAL (desvio do alt face ao BTC, beta móvel de 30 d, resíduo a 4 h e 24 h, filtro de funding alt menos BTC): eventos, regra natural, grelha de 48, OOS, 2020-2022, variante com perna no BTC
```

Os três últimos escrevem CSV em `resultados/` e imprimem as tabelas; cada família escreve o seu relatório em `relatorios/<familia>.md` e regista todas as configurações em `ensaios/<familia>.csv`.

## O harness (`harness.py`)

| Função | Para quê |
| --- | --- |
| `carregar(simbolo, de, ate)` | DataFrame de 15 m indexado pela abertura (UTC), com `fecho_em`, `imputado`, `activo` e as colunas brutas |
| `agregar(df, "1h"\|"4h"\|"1d")` | velas superiores completas, alinhadas a UTC; `fecho_em` diz quando ficam disponíveis |
| `ultima_superior_fechada`, `projectar_superior` | levar características de 1 h/4 h para a grelha de 15 m sem lookahead |
| `retorno_log`, `vol_realizada_ewma`, `vol_parkinson_ewma`, `atr` | retorno e escalas de volatilidade |
| `vwap_movel`, `zscore_ancorado` | desvio a uma âncora (VWAP, média, EWMA) numa janela |
| `extremos_moveis`, `varrimento`, `posicao_no_intervalo` | estrutura: extremos de N velas, varrimento com reconquista, posição no intervalo |
| `desequilibrio_taker`, `cvd` | fluxo taker |
| `z_robusto`, `percentil_movel`, `mascarar_imputado` | normalização robusta das métricas (nunca em velas imputadas) |
| `racio_variancias`, `racio_variancias_movel`, `autocorrelacao` | Lo-MacKinlay com z robusto; autocorrelação |
| `simular(df15, sinais, stop, alvo, n_max, custo_bps=6.5, escala=None, alvo_em_R=False, uma_posicao=True)` | regras comuns de execução; devolve trades em R líquido |
| `sinais_aleatorios` | a nula com os mesmos stop e alvo |
| `metricas(trades, de, ate)`, `criterios(trades, de, ate)` | n, média, erro padrão, PF, acerto, DD; H1 a H6 |
| `registar_ensaio(familia, parametros, metricas)` / `contar_ensaios` | `ensaios/<familia>.csv`, uma linha por configuração, inclusive as descartadas |
| `estudo_de_eventos(df, eventos, horizontes)` | retorno médio e mediano após o evento, com erro padrão |
| `PERIODOS`, `recortar(df, "IS"\|"IS_preco"\|"OOS")` | IS 2023-01-01 a 2025-03-31 (preço: desde 2020-09); OOS 2025-04-01 a 2026-09-09 |

Regras de simulação (iguais para todas as famílias): sinal no fecho da vela k, entrada na abertura
de k+1; R = distância da entrada ao stop; stop tocado pela mínima/máxima, preenchido no nível ou na
abertura se esta já saltou o stop (o pior); alvo no nível; alvo e stop na mesma vela = stop; saída
por tempo ao fecho da vela n_max; custo 6,5 bps por lado sobre entrada e saída, convertido a R.

## Regras da casa

* Toda a característica na vela k só usa dados até `fecho_em[k]`; o teste `TesteCaracteristicasCausais`
  perturba a cauda e exige que o passado não mude. Qualquer função nova entra nesse teste.
* Métricas (funding, OI, liquidações, ls_ratio) nunca em velas com `imputado = 1`, e só depois de
  `validar_colunas.py` dizer que a coluna não é derivada do preço e do volume.
* O OOS avalia-se UMA vez por família, com a configuração escolhida no IS. O relatório diz quantas
  configurações (M) foram experimentadas, lidas de `ensaios/<familia>.csv`.
* A Binance não é a Hyperliquid: preços e funding são próximos, livro e liquidações não. Tudo o que
  depender de liquidações tem de ser validado ao vivo depois.

## Resultado

Está em `relatorios/SINTESE.md` (cinco linhas na secção 0, tabela comparativa na 2, verificação
independente na 3, resposta aos sete pontos do Bot na 5, recomendação na 6, pistas na 8, matemática das
características na 9, limites na 10). Em resumo: seis famílias, 311 configurações registadas, nenhuma
passa H2 nem H3 fora da amostra; os cinco OOS primários juntos (7111 trades) dão +0,006 R bruto e
-0,086 R líquido. A recomendação, já aplicada no `medidor/` (secção 11 da síntese), é não implementar
uma regra, deixar a emissão desligada, recolher dados da própria Hyperliquid, correr o único teste
pré-registado em dados novos e medir os trades discricionários com o medidor.
