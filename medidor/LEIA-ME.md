# Medidor de execução (Etapa 2)

O medidor liga-se à Hyperliquid, calcula o estado do livro de cada activo, e regista quanto custam os seus sinais e os seus fills. Só lê dados. Não tem chaves e não envia ordens.

As fórmulas são as do documento "Scalping na Hyperliquid: execução e controlo de slippage" (secções 5 e 6).

## 1. Instalar (uma vez)

1. Descompacte a pasta `medidor` para onde quiser, por exemplo para Documentos.
2. Abra a aplicação **Terminal**. Escreva `cd ` (com um espaço no fim), arraste a pasta `medidor` para a janela e carregue em Enter.
3. Escreva:

```
bash arrancar.sh verificar
```

Na primeira vez prepara o ambiente e instala a única biblioteca necessária (`websockets`); precisa de internet e demora cerca de um minuto. Se o macOS pedir para instalar as "command line developer tools", aceite e repita o comando.

A verificação testa as ligações reais a partir do seu computador. Cada linha começa por `OK`, `AVISO` (informação, não impede nada) ou `FALHA`. Se aparecer alguma `FALHA`, copie o texto todo para se poder corrigir.

## 2. Configurar

Abra `config.ini` com o TextEdit. Três linhas importam para começar:

| Linha | O que pôr |
| --- | --- |
| `ativos` | Os activos a medir, com o nome exacto da Hyperliquid: `BTC, ETH, SOL` |
| `endereco` | O endereço público da conta (`0x...`), para medir os fills. É só o endereço, nunca uma chave. Vazio = mede só mercado e sinais |
| `alvo_bps` | O alvo típico dos trades, em bps. Define o orçamento de custo: 15 % deste valor |

O resto tem valores de partida e está explicado no próprio ficheiro.

## 3. Correr

```
bash arrancar.sh
```

Para parar, carregue em Ctrl+C. Ao parar, o medidor escreve o que estava a meio.

Nos primeiros 30 minutos o estado é `AQUECIMENTO`: ainda não há histórico para comparar. Se o parar e voltar a arrancar, recupera o histórico das últimas 24 horas a partir do disco.

O comando impede o Mac de adormecer por inactividade. Fechar a tampa suspende-o na mesma; nesse caso o medidor regista o intervalo como "sem dados" e continua quando o Mac acordar.

## 4. Registar sinais

Um sinal é o instante em que decide entrar. Há duas formas de o registar.

**À mão**, noutra janela do Terminal, na mesma pasta:

```
bash arrancar.sh sinal BTC compra
bash arrancar.sh sinal ETH venda 4310.5
```

**Pelos bots**, acrescentando uma linha ao fim de `dados/sinais.csv`:

```
hora,ativo,lado,preco,alvo_bps,tamanho_usd,nota
,BTC,compra,,,,
2026-10-07T13:42:05Z,ETH,venda,4310.5,40,2500,reversal
```

- Só `ativo` e `lado` são obrigatórios. `lado` aceita compra/venda, long/short, buy/sell.
- `preco` é opcional: é o nível que tinha em mente. Se o indicar, o medidor regista a que distância o mercado já estava dele no instante do sinal, e depois quanto pior (ou melhor) foi o preço pago.
- `hora` vazia quer dizer "agora". Com hora, use o formato `2026-10-07T13:42:05Z` (UTC). Sem o `Z`, é lida como hora local do computador.
- Cada linha tem de acabar com mudança de linha. O ficheiro só se acrescenta; não se reescreve.
- A linha tem de ser escrita no instante da decisão. Um sinal que chega com mais de 2 segundos de atraso ainda mede o resultado e o registo sombra, mas fica fora da regra passiva/agressiva, porque o livro desse instante já não existe.
- Sinais escritos com o medidor parado, ou com mais de 2 horas, não são medidos.

Sem sinais, o medidor mede o mercado e os fills (execução, taxa, selecção adversa). Com sinais, mede também o atraso de decisão e o registo sombra, que são o que decide a rota passiva ou agressiva.

## 5. Ler o painel

| Coluna | Significado |
| --- | --- |
| ESTADO | VERDE, AMARELO, VERMELHO, AQUECIMENTO ou SEM_DADOS |
| SPREAD | Melhor venda menos melhor compra, em bps |
| PROF.UTIL | Dólares em livro até ao teto de preço, já com o desconto de 50 %. Um `+` no fim indica que o teto fica para lá dos 20 níveis visíveis: o valor real é maior |
| VOL | Volatilidade do último minuto sobre a da última meia hora |
| INTENS | Valor negociado por segundo, últimos 10 s sobre a última meia hora |
| FLUXO | Fluxo de ordens dos últimos 10 s; positivo = pressão compradora. Não entra no estado: tem lado, e fica registado em cada sinal a favor ou contra a ordem |
| MK-OR | Distância entre mark price e oracle, em bps |
| IMP.REF | Custo previsto, em bps, de uma ordem a mercado do tamanho de referência |
| MOTIVO | Medidas em alarme e o percentil em que estão |

"Atraso dos dados" é o tempo entre a bolsa gerar um preço e ele chegar ao seu computador.

É normal ver AMARELO com frequência nesta fase. Cada medida passa o seu percentil 80 um quinto do tempo, por construção. O relatório mostra a percentagem de tempo em cada estado, e é com esse número que depois se afinam os limiares.

## 6. Ficheiros na pasta `dados`

| Ficheiro | Conteúdo |
| --- | --- |
| `sinais.csv` | Entrada: os sinais, uma linha cada |
| `registo_sinais.csv` | Por sinal: estado do livro, impacto previsto, teto, registo sombra e resultado a 5, 30, 60 e 300 s |
| `registo_fills.csv` | Por fill: execução (E), taxa (F), selecção adversa (A), sinal a que se liga e custo de decisão (D) |
| `metricas/` | Por activo e por dia: as medidas e o estado, de 5 em 5 segundos |
| `estado.json` | O estado actual de cada activo, reescrito a cada segundo. Os outros bots podem lê-lo |
| `relatorio.txt` | O último relatório |
| `medidor.log` | Avisos e erros |

Um campo vazio quer dizer "não se sabe": o medidor não preenche preços de instantes em que esteve sem dados.

Para ver um registo numa folha de cálculo, abra uma cópia. Se gravar por cima com outro separador, o medidor deixa de o conseguir ler.

## 7. Relatório

```
bash arrancar.sh relatorio
```

1. **Sinais e registo sombra.** Resultado médio dos sinais ao fim de 300 s, percentagem em que a ordem passiva teria preenchido, e o valor esperado de cada rota (V agressiva e V passiva, regra 6.5), comparadas nos mesmos sinais. Por baixo, a diferença V passiva − V agressiva calculada sinal a sinal e o seu erro padrão: "clara" se passa 2 erros padrão, "dentro do ruído" se não. Depois, os mesmos números separados pelo estado do livro, pelo fluxo no instante do sinal e, com a chave da CoinGlass, pelas liquidações nos 60 s anteriores.
2. **Custo por ordem.** E, F, A e D em bps, com aberturas e fechos em blocos separados, e por rota (taker ou maker). Nas aberturas, também por estado do livro: se VERMELHO custar mais do que VERDE, o detector está a avisar bem.
3. **Tempo em cada estado.** Percentagens do dia inteiro e alarmes mais frequentes.

Três cuidados de leitura:

- O resultado até 300 s mede a qualidade da entrada, não o resultado do trade. Acima disso, o relatório trata o número como o drift do sinal, não como a entrada. Numa reversão é normal o preço andar contra o sinal nos primeiros minutos. Para trades de horas, acrescente a duração típica a `horizontes_s`.
- "Passiva preenche pelo menos X %" é um mínimo: a simulação põe a ordem no fim da fila e ignora cancelamentos à frente.
- D só conta na primeira ordem ligada a cada sinal. Uma segunda abertura sem sinal próprio fica fora, e o relatório diz quantas foram.

A primeira linha mostra o progresso: a Etapa 2 pede 100 sinais e 30 ordens de abertura. Entre parênteses diz quantos sinais estão completos e quantos entram na regra: os 100 que o bilhete exige são os que entram na regra, completos e com impacto medido e viável. São mínimos para ler os totais; não chegam para encher todas as combinações de estado, rota e activo.

## 8. Bilhete

Com o medidor a correr, `python3 medidor.py bilhete BTC compra 1000` imprime a ordem pronta e não a envia. O teto é um preço, `p* = mid × (1 ± B / 10⁴)`, arredondado para dentro. A rota atravessa só se o pior preço do tamanho pedido, em unidades `Q = T / mid`, ficar dentro desse teto, e o estado não for vermelho. O impacto médio é o que essa conta produz, não um segundo teste.

O bilhete lê o `estado.json`. Se esse ficheiro tiver mais de `sem_dados_s` (10 s), ou tiver sido escrito com o medidor sem ligação, não há bilhete: o livro desse instante já não existe. Em aquecimento também não há ordem nenhuma, nem passiva.

O tamanho sai em dólares e em unidades do activo, arredondado para baixo ao passo da bolsa (`szDecimals`), que é o número que se escreve na ordem. Se o tamanho arredondado der zero, ou ficar abaixo de `minimo_ordem_usd`, o bilhete avisa que a ordem não é enviável. Se a fotografia do livro for velha, avisa que só o melhor preço é actual.

Por baixo vem a regra medida, V agressiva contra V passiva, nos sinais completos desse activo com impacto medido e viável. Com menos de 100 não muda a rota: os números são só leitura. Aos 100, se a passiva valer mais, o bilhete deixa de atravessar e fica no toque. O contrário não acontece: a medida nunca autoriza atravessar um livro que o teto ou o vermelho proibiu.

A mesma regra traz a diferença V passiva − V agressiva calculada sinal a sinal e o seu erro padrão. Por defeito basta a passiva valer mais. Com `regra_exige_margem = sim` em `config.ini`, a medida só tira a travessia se a diferença passar 2 erros padrão; dentro do ruído fica a rota do livro, e o bilhete diz porquê.

## 9. Privacidade

Tudo fica neste computador. O medidor não envia os seus registos para lado nenhum. O endereço da conta é informação pública na Hyperliquid e serve só para receber os seus fills.

## 10. O que foi testado e o que falta confirmar

Testado: 288 testes das fórmulas e das peças (62 do núcleo, 44 do medidor, 93 do núcleo de reversão, 66 do sinalizador, 23 do backtest), mais duas simulações contra bolsas simuladas que falam o formato documentado da Hyperliquid: a do medidor, com 91 conferências, e a do sinalizador, com 89, em tempo acelerado, com velas, histórico, funding, posições, leaderboard e CoinGlass falsos. Incluem queda de ligação, paragem de dados sem queda, fills parciais, sinais atrasados, paragem a meio, rearranque, o bilhete com estado velho e a regra com e sem margem. Corre em Python 3.9 e 3.13; a versão 2.5 e o sinalizador foram conferidos em 3.13 com websockets 17 e compilados em 3.11.

Por confirmar na sua máquina:

- **Ligação real à Hyperliquid.** O ambiente onde o medidor foi escrito não a alcança. `bash arrancar.sh verificar` é esse teste.
- **CoinGlass.** A medida de liquidações só liga com a chave em `config.ini`. O plano Professional inclui o fluxo de ordens de liquidação (o mesmo que o Standard); o heatmap não é usado, porque é uma imagem e o detector precisa do fluxo. O medidor subscreve as duas grafias do canal (`liquidation_orders` e `liquidationOrders`), lê os campos nas duas grafias que a documentação mostra (`volume_usd` e `volUsd`) e, se o websocket não entregar, pede o mesmo fluxo na API REST v4. A mesma ordem não conta duas vezes. O painel mostra dois contadores: liquidações recebidas e, dessas, as dos seus activos. Em cada sinal fresco ficam também os dólares liquidados a favor e contra a ordem, nos últimos 60 s.
- **Relógio.** Os custos de decisão comparam a hora do computador com a da bolsa. Mantenha a hora automática do macOS ligada.

## 11. Para quem alterar o código

- `nucleo.py` tem as fórmulas, sem rede nem ficheiros. É a referência para as Etapas 3 a 5.
- `medidor.py` trata das ligações, dos registos, do painel e do relatório.
- `reversao.py` tem as fórmulas da estratégia de reversão (Etapa 1), puras como o `nucleo.py`; `sinalizador.py` é o processo ao vivo que escreve os sinais; `backtest_reversao.py` corre a mesma regra sobre velas guardadas. A especificação está em `ESTRATEGIA-REVERSAO.md`.
- `../pesquisa/` é a investigação em dados reais que falsificou a regra: harness comum, seis famílias de hipóteses, ensaios registados e relatórios. Não é importada pelo medidor nem pelo sinalizador; tem o seu próprio `README.md`.
- Depois de qualquer alteração, com o ambiente activo (`source .venv/bin/activate`):

```
python3 testes/teste_nucleo.py
python3 testes/teste_medidor.py
python3 testes/teste_reversao.py
python3 testes/teste_sinalizador.py
python3 testes/teste_backtest.py
python3 testes/simulacao.py
python3 testes/simulacao_sinalizador.py
```

## 12. Sinalizador de reversão (Etapa 1)

**Estado em 2026-10-10: a regra de entrada (REVOU v2) foi falsificada em dados reais e o sinalizador não emite sinais por defeito.** Em 18 perpétuos, de 2020-09 a 2026-09, o portão esteve aberto em 0 por cento das horas de 2025-2026, e a regra sem portão perdeu o custo, tal como as seis famílias de hipóteses de reversão testadas com o mesmo protocolo (estrutura 4 h / 1 h / 15 m, cascata, aglomeração, bandas, fluxo taker, transversal). O relatório está em `../pesquisa/relatorios/SINTESE.md`; a especificação, `ESTRATEGIA-REVERSAO.md`, fica como registo, com o aviso no topo.

O que o sinalizador faz com `emitir = nao`, o defeito do `config.ini`:

1. **Recolhe e guarda os dados da própria Hyperliquid**: velas de 15 m e de 1 h com fluxo, negócios grandes, rajadas, TWAP, open interest, funding, liquidações e posições. São os dados que faltam para testar qualquer hipótese sobre baleias, cascatas e absorção na plataforma onde se opera; a pesquisa só teve dados da Binance, que tem outro livro. Quanto mais meses, melhor: 6 a 12 meses é o mínimo útil.
2. **Continua a avaliar a regra REVOU v2** e a registar os seus trades virtuais em `dados/registo_sinalizador.csv`, mas não escreve em `dados/sinais.csv` nem notifica; o log diz `NAO emitido (emitir = nao)`.
3. **Corre, só em registo, o teste pré-registado** da síntese (ressalto de 2 h depois de 3 sigma), em `dados/registo_teste_3sigma.csv`.

Nesta fase, os sinais que o medidor mede são os que se registam à mão (`bash arrancar.sh sinal ATIVO LADO`, secção 4): os trades discricionários. A pesquisa mostra que a versão mecânica do método não tem vantagem; se a versão com olho tiver, só o medidor o pode dizer, depois de 100 sinais.

### Pôr a correr

1. `bash arrancar.sh sinalizador verificar` confere as oito hipóteses sobre a API da Hyperliquid (formato das velas, ordem dos endereços nos negócios, unidade do open interest, funding, hash dos TWAP, leaderboard, CoinGlass, atraso). Uma linha `FALHA` corrige-se antes de continuar; `AVISO` é informação.
2. `bash arrancar.sh sinalizador historico` descarrega as velas de 1 hora e de 15 minutos e o funding para `dados/velas/` e `dados/funding/`. A Hyperliquid dá até 5000 velas por intervalo: cerca de 208 dias de 1 hora e 52 de 15 minutos. Repita de vez em quando para a amostra crescer.
3. Numa janela do Terminal, `bash arrancar.sh` (o medidor). Noutra, `bash arrancar.sh sinalizador` (o sinalizador). Partilham a pasta `dados`: só o sinalizador escreve em `sinais.csv` (e só com `emitir = sim`) e só o medidor nos seus registos. No arranque o sinalizador avisa que a emissão está desligada.

O sinalizador precisa de pelo menos 480 velas de 1 hora fechadas, que o histórico já traz, e calibra o limiar do teste no arranque. O medidor tem os seus 30 minutos de aquecimento. O teste pré-registado precisa de 384 velas de 15 minutos (4 dias) de aquecimento, que o histórico também traz.

### Teste pré-registado (ressalto de 3 sigma)

Regras fechadas antes de olhar para os dados, iguais à secção 8.1 da síntese: âncora EWMA do log-preço com meia-vida de 96 velas de 15 minutos (24 horas; é a âncora `ewma48` da pesquisa, com janela de 48 horas), sigma dos retornos de 15 minutos com a mesma meia-vida, `z` escalado ao desvio padrão teórico do passeio aleatório; sinal no primeiro fecho de 15 minutos com `|z| >= 3` vindo de `|z| < 3`, lado contra o desvio; entrada na abertura da vela seguinte; stop a `sigma x sqrt(8)` do fecho do sinal, preenchido no pior entre o nível com 5 bps de deslize e a abertura; alvo a 1,5 R; saída por tempo ao fecho da 8.ª vela (2 horas); uma posição por activo; 6,5 bps por lado. Só contam sinais a partir de 2026-09-10: o período anterior foi gasto na pesquisa e não serve para confirmar nada.

Avaliação única, com 100 trades, nunca antes:

```
bash arrancar.sh sinalizador teste3sigma
```

Antes dos 100 trades o comando só diz quantos faltam. Com 100, imprime média, erro padrão, factor de lucro, acerto, drawdown (total e por activo), o valor da nula (o custo em R) e os veredictos H1 a H6, com H5 por activo. Se não passar, a hipótese fica rejeitada e não se afina nada: afinar depois de ver o resultado é o que a pesquisa proíbe. Se passar, e só então, faz sentido pensar em `emitir = sim`. Com os activos do `config.ini`, 100 trades são meses: a pesquisa estima 9 a 24 meses em 18 activos.

### Quando aparece um sinal (só com `emitir = sim`)

O sinalizador escreve a linha, regista-a no log e, no macOS, mostra uma notificação com som e com o comando do bilhete. Quem opera:

1. Corre `python3 medidor.py bilhete ATIVO LADO TAMANHO` e lê a rota, o tipo (IOC ou ALO), o preço e o tamanho em unidades.
2. Coloca a ordem na Hyperliquid à mão. Um ou dois minutos de atraso custam pouco face a um alvo de mais de 100 bps; o medidor mede esse custo na coluna D.
3. Coloca de imediato o stop e o alvo na bolsa. Os dois preços estão em `dados/estado_sinalizador.json`, no trade aberto do activo (`p_stop` e `p_alvo`), e o stop também na nota do sinal (`pst=`).
4. Quando o trade virtual fecha, por alvo, stop, tempo ou invalidação, o sinalizador escreve a linha em `dados/registo_sinalizador.csv`, regista no log e volta a notificar. Fecha-se a posição à mão se ainda estiver aberta.

Nos primeiros 30 trades virtuais fechados de cada activo (fase `cal`) o tamanho é sempre `tamanho_base`, 1000 usd, para o medidor acumular sinais comparáveis. Depois (fase `op`) o tamanho sai do menor de quatro tectos: Kelly fraccionário, risco por trade, volatilidade alvo e liquidez, sobre `capital_usd`. Se a taxa de acerto medida não paga os custos, o activo passa a `sombra`: continua a registar trades virtuais mas não escreve sinais.

### Ficheiros próprios do sinalizador

| Ficheiro | Conteúdo |
| --- | --- |
| `dados/velas/ATIVO_15m_DIA.csv`, `dados/velas/ATIVO_1h_DIA.csv` | Velas, e no 15 m as colunas de fluxo, negócios grandes, rajadas, TWAP, open interest, funding, liquidações e posições |
| `dados/funding/ATIVO.csv` | Funding por hora |
| `dados/registo_sinalizador.csv` | Um trade virtual da regra REVOU v2 por linha: entrada, alvo, stop, tempo, saída, resultado bruto, funding e líquido, nota |
| `dados/registo_teste_3sigma.csv` | Um trade virtual do teste pré-registado por linha: hora e preço do sinal, `z`, entrada, stop, alvo, saída, motivo, velas, resultado bruto e líquido em bps e em R |
| `dados/estado_sinalizador.json` | Estado de cada activo e trade aberto, reescrito de forma atómica |
| `dados/baleias.csv` | Lista diária de grandes contas, só prefixos e hashes dos endereços |
| `dados/ensaios.csv` | Todas as configurações experimentadas, para o controlo de sobre-ajuste |
| `dados/sinalizador.log` | Avisos e erros, com rotação |

Outros comandos: `teste3sigma` avalia o teste pré-registado (uma vez, com 100 trades); `relatorio` cruza o registo próprio com o do medidor e agrupa pela nota; `baleias` refaz a lista; `ensaios` lista as configurações; `reset` levanta os disjuntores.

### CoinGlass

Opcional. A única linha a preencher é `chave` na secção `[coinglass]` do `config.ini`; a chave nunca aparece em registos, notas nem no log. Com ela, o sinalizador ganha as liquidações por lado em tempo real (o termo "cascata esgotada") e, para a lista de baleias, as posições acima de 1 milhão de dólares na Hyperliquid. Sem ela, esses termos valem zero e a nota diz `na`. Para a pesquisa a chave não foi necessária: o preço e o funding vieram de seis anos de dados públicos, e para as liquidações e o livro da Hyperliquid a CoinGlass também não serve, porque mede outro livro.

### Backtest

```
python3 backtest_reversao.py --ativo BTC                    só velas e funding (variante A)
python3 backtest_reversao.py --ativo BTC --walk             walk-forward, IS 90 dias e OOS 30
python3 backtest_reversao.py --ativo BTC --nulo --placebo   passeio aleatório sem portão e âncora deslocada
python3 backtest_reversao.py --ativo BTC --degradada-1h     gatilho nos fechos de 1 hora, para os 208 dias
```

O backtest da REVOU v2 fica como ferramenta sobre as velas guardadas pelo sinalizador. O que ele dá nos 52 dias de 15 minutos da Hyperliquid não muda o veredicto: a regra foi avaliada em seis anos de dados em `../pesquisa/` e perdeu. Os critérios de aceitação de qualquer regra futura são os H1 a H6 da síntese, só em dados novos, com a avaliação única.

### Pesquisa

A pasta `../pesquisa/` tem o harness comum (`harness.py`, com 37 testes), a validação das colunas, as taxas de base, as seis famílias (`familia_*.py`), todos os ensaios (`ensaios/*.csv`, 311 linhas), os resultados (`resultados/*.csv`) e os relatórios (`relatorios/`). Precisa de pandas, numpy e pyarrow, e dos parquet de 15 minutos de 18 perpétuos (repositório público `kbsingh1399/backtesting_data`, preparados por `preparar_dados.py`), que não estão no repositório por serem grandes. O `README.md` da pasta diz como correr. Para a leitura basta a síntese.
