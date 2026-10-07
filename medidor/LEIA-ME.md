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

1. **Sinais e registo sombra.** Resultado médio dos sinais ao fim de 300 s, percentagem em que a ordem passiva teria preenchido, e o valor esperado de cada rota (V agressiva e V passiva, regra 6.5), comparadas nos mesmos sinais. Depois, os mesmos números separados pelo estado do livro e pelo fluxo no instante do sinal.
2. **Custo por ordem.** E, F, A e D em bps, com aberturas e fechos em blocos separados, e por rota (taker ou maker). Nas aberturas, também por estado do livro: se VERMELHO custar mais do que VERDE, o detector está a avisar bem.
3. **Tempo em cada estado.** Percentagens do dia inteiro e alarmes mais frequentes.

Três cuidados de leitura:

- O resultado até 300 s mede a qualidade da entrada, não o resultado do trade. Acima disso, o relatório trata o número como o drift do sinal, não como a entrada. Numa reversão é normal o preço andar contra o sinal nos primeiros minutos. Para trades de horas, acrescente a duração típica a `horizontes_s`.
- "Passiva preenche pelo menos X %" é um mínimo: a simulação põe a ordem no fim da fila e ignora cancelamentos à frente.
- D só conta na primeira ordem ligada a cada sinal. Uma segunda abertura sem sinal próprio fica fora, e o relatório diz quantas foram.

A primeira linha mostra o progresso: a Etapa 2 pede 100 sinais e 30 ordens de abertura. São mínimos para ler os totais; não chegam para encher todas as combinações de estado, rota e activo.

## 8. Bilhete

Com o medidor a correr, `python3 medidor.py bilhete BTC compra 1000` imprime a ordem pronta e não a envia. O teto é um preço, `p* = mid × (1 ± B / 10⁴)`, arredondado para dentro. A rota atravessa só se o pior preço do tamanho pedido, em unidades `Q = T / mid`, ficar dentro desse teto, e o estado não for vermelho. O impacto médio é o que essa conta produz, não um segundo teste.

Por baixo vem a regra medida, V agressiva contra V passiva, nos sinais completos desse activo. Com menos de 100 não muda a rota: os números são só leitura. Aos 100, se a passiva valer mais, o bilhete deixa de atravessar e fica no toque. O contrário não acontece: a medida nunca autoriza atravessar um livro que o teto ou o vermelho proibiu.

## 9. Privacidade

Tudo fica neste computador. O medidor não envia os seus registos para lado nenhum. O endereço da conta é informação pública na Hyperliquid e serve só para receber os seus fills.

## 10. O que foi testado e o que falta confirmar

Testado: 78 testes das fórmulas e das peças, mais uma simulação com cerca de 90 conferências contra uma bolsa simulada que fala o formato documentado da Hyperliquid. Inclui queda de ligação, paragem de dados sem queda, fills parciais, sinais atrasados e paragem a meio. Corre em Python 3.9 e 3.13.

Por confirmar na sua máquina:

- **Ligação real à Hyperliquid.** O ambiente onde o medidor foi escrito não a alcança. `bash arrancar.sh verificar` é esse teste.
- **CoinGlass.** A medida de liquidações só liga com a chave em `config.ini`. O plano Professional inclui o fluxo de ordens de liquidação (o mesmo que o Standard); o heatmap não é usado, porque é uma imagem e o detector precisa do fluxo. O medidor subscreve as duas grafias do canal (`liquidation_orders` e `liquidationOrders`) e, se o websocket não entregar, pede o mesmo fluxo na API REST v4. A mesma ordem não conta duas vezes. O painel mostra dois contadores: liquidações recebidas e, dessas, as dos seus activos. Em cada sinal fresco ficam também os dólares liquidados a favor e contra a ordem, nos últimos 60 s.
- **Relógio.** Os custos de decisão comparam a hora do computador com a da bolsa. Mantenha a hora automática do macOS ligada.

## 11. Para quem alterar o código

- `nucleo.py` tem as fórmulas, sem rede nem ficheiros. É a referência para as Etapas 3 a 5.
- `medidor.py` trata das ligações, dos registos, do painel e do relatório.
- Depois de qualquer alteração, com o ambiente activo (`source .venv/bin/activate`):

```
python3 testes/teste_nucleo.py
python3 testes/teste_medidor.py
python3 testes/simulacao.py
```
