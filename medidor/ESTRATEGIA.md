# Medidor de execução na Hyperliquid

## Estratégia, matemática e documentação (versão 2.5)

Este documento acompanha o código da pasta `medidor/`: `nucleo.py` (fórmulas puras), `medidor.py` (ligações, registos, painel, relatório e bilhete), `config.ini`, `arrancar.sh`, `LEIA-ME.md` e a pasta `testes/`. Cada fórmula abaixo indica a função que a implementa e a secção do documento "Scalping na Hyperliquid: execução e controlo de slippage" de onde vem. Quando o texto e o código divergirem, manda o código, e o erro é deste documento.

---

## 0. Numa página

- **O medidor não escolhe o trade.** O sinal vem de fora, dos bots de reversão, por `dados/sinais.csv`. O medidor responde só à execução: dado o sinal, o livro aguenta o tamanho, em que estado está e, depois de haver histórico, se atravessar pagou mais do que esperar.
- **Há dois portões e não se misturam.** O primeiro é o livro, neste instante. O segundo é a medida, nos sinais já fechados desse activo.
- **Quatro saídas possíveis do bilhete.** IOC no teto (agressiva), ALO no toque (passiva), aguardar (aquecimento, sem ordem) e sem bilhete (sem livro, estado velho, sem ligação).
- **Nada é enviado.** O medidor não tem chaves. A Etapa 4, enviar a ordem, não existe.
- **Os números de partida.** Alvo G = 30 bps, fracção φ = 0,15, orçamento da entrada B = 4,5 bps, desconto do livro θ = 0,5, tamanho de referência 1000 usd, horizonte da regra 300 s. Tudo em `config.ini`, nunca no código.

---

## 1. Estratégia

### 1.1 Onde começa e onde acaba

O sinal é o instante em que alguém decide entrar: activo, lado e, se existir, o preço que tinha em mente, o alvo e o tamanho. O medidor regista nesse instante o livro (mid, spread, desequilíbrio, estado, fluxo, liquidações), calcula o impacto previsto do tamanho pedido, simula uma ordem passiva no toque, e depois mede o que o mid fez a 5, 30, 60 e 300 segundos. Com o endereço público da conta, liga cada fill de abertura ao sinal que o originou e decompõe o custo em quatro peças: decisão, execução, taxa e selecção adversa.

Acaba no bilhete: a ordem pronta, com tipo, preço e tamanho, impressa e não enviada. Quem envia é a pessoa.

### 1.2 Portão 1: o livro, neste instante

O estado do livro de cada activo é recalculado a cada segundo e vale uma destas cinco palavras:

| Estado | Quando | O que o bilhete faz |
| --- | --- | --- |
| `SEM_DADOS` | Sem melhor preço, ou mais de `sem_dados_s` (10 s) sem mensagens, ou ligação caída | Não há bilhete |
| `AQUECIMENTO` | Menos de `aquecimento_min` (30 min) de histórico para comparar | Não há rota, nem passiva |
| `VERMELHO` | Alguma medida passa o percentil 95 do seu próprio histórico e o seu piso | Passiva no toque (ALO). Não se atravessa, e o fluxo a favor não muda isto |
| `AMARELO` | Alguma medida passa o percentil 80 e o seu piso | Atravessa (IOC no teto) só se o pior preço do tamanho pedido ficar dentro do teto; senão, passiva no toque |
| `VERDE` | Nenhuma medida em alarme | Igual ao amarelo |

Duas coisas deste portão são deliberadas. O critério da travessia é o **pior preço**, não o impacto médio: uma ordem que em média cabe no orçamento mas toca um nível para lá do teto não atravessa. E o teto é um preço arredondado **para dentro**, para a ordem nunca pagar mais do que foi autorizado.

É normal ver amarelo com frequência: por construção cada medida passa o seu percentil 80 um quinto do tempo. Os pisos de relevância existem para que um spread de 2 ticks num activo de tick fino não dê alarme.

### 1.3 Portão 2: a medida

Nos sinais já fechados do activo que **entram na regra** (resultado a 300 s conhecido, registo sombra concluído, impacto medido e viável), a regra 6.5 compara o valor esperado de atravessar com o de esperar. Com menos de 100 sinais os números aparecem e não mudam a rota. Aos 100, se esperar valer mais, o bilhete deixa de atravessar e fica no toque. Nunca faz o contrário: a medida não autoriza o que o teto ou o vermelho proibiu.

A versão 2.5 acrescenta a margem: a diferença entre as duas rotas calculada sinal a sinal, com erro padrão. Por defeito basta a passiva valer mais. Com `regra_exige_margem = sim`, a medida só tira a travessia quando a diferença passa dois erros padrão. A regra base não muda; a opção só a torna mais exigente.

### 1.4 O que não decide

- **O fluxo de ordens (OFI)** tem lado. Fica registado em cada sinal, a favor ou contra a ordem, e no bilhete; por defeito não entra no estado nem muda a rota.
- **As liquidações** só existem com a chave da CoinGlass. Entram no estado, sem lado, e ficam registadas por sinal a favor e contra. Não escolhem a rota.
- **O impacto médio** é consequência da conta do pior preço, não um segundo critério.
- **A profundidade em dólares** é outra conta; a rota segue Q = T / mid em unidades. Quando as duas discordam, o bilhete diz.

### 1.5 Horizontes

Aos 300 s o resultado mede a entrada, não o trade. Numa reversão é normal o preço andar contra o sinal nos primeiros minutos. Para a regra valer para o trade de horas, muda-se `horizonte_regra_s` para 3600 ou 14400, acrescenta-se esse valor a `horizontes_s`, e volta-se a medir. O veredito dos 300 s não serve para uma reversão de quatro horas.

### 1.6 Etapas

- **Etapa 2, medir.** Este medidor. Pede 100 sinais que entrem na regra e 30 ordens de abertura antes de os totais se lerem.
- **Etapa 3, o bilhete.** `python3 medidor.py bilhete ATIVO LADO [TAMANHO]`. Imprime, não envia.
- **Etapa 4, enviar.** Não existe. Só depois de o ponto 5 da lista final estar estável.

---

## 2. Matemática

### 2.1 Notação

- `lado` = +1 compra, −1 venda. 1 bp = 10⁻⁴; no código `BPS = 1e4`.
- `b`, `a`: melhor compra e melhor venda. `q_b`, `q_a`: tamanhos nesses preços.
- `T`: tamanho em dólares. `Q`: tamanho em unidades do activo.
- Custo positivo é custo; resultado positivo é a favor.

### 2.2 Topo do livro (secção 6.1; `nucleo.mid`, `spread_bps`, `desequilibrio`, `mid_ponderado`)

```
m   = (a + b) / 2
s   = 10⁴ · (a − b) / m                      spread em bps
I   = (q_b − q_a) / (q_b + q_a)             desequilíbrio, −1 a +1; 0 se o total for 0
m_w = m + (a − b) / 2 · I                     mid ponderado
```

### 2.3 Orçamento, teto e tamanho (secções 6.2 e 6.9; `orcamento_bps`, `preco_teto`, `arredondar_preco`, `passo_preco`, `arredondar_tamanho`)

```
B  = φ · G                                    4,5 bps com φ = 0,15 e G = 30
p* = m · (1 + lado · B / 10⁴)                 teto, por unidade
Q  = T / m                                    unidades
```

O teto é arredondado **para dentro**: para baixo numa compra, para cima numa venda, com as regras de preço da Hyperliquid: no máximo 5 algarismos significativos e no máximo `6 − szDecimals` casas decimais; preços inteiros são sempre válidos. No código, `casas = max(0, min(6 − szDecimals, 4 − expoente(m)))` e o passo de preço é `10^−casas`. O tamanho da ordem arredonda-se sempre para baixo a `szDecimals` casas.

Cada sinal pode trazer o seu `alvo_bps`; o orçamento desse sinal é `φ · alvo`. O bilhete usa o orçamento de `config.ini`.

### 2.4 Impacto previsto e tamanho máximo (secção 6.2; `impacto`, `tamanho_max`, `Ativo.niveis`)

O livro que se percorre é o lado consumido (asks numa compra, bids numa venda), melhor preço primeiro. O primeiro nível é sempre o melhor preço actual; da fotografia do `l2Book` ficam só os níveis para lá dele. Em cada nível `i` assume-se disponível `θ · q_i`, com θ = `desconto_livro` = 0,5.

```
x_i   = min(θ · q_i, resto)                   o que se consome no nível i
p̄     = Σ p_i · x_i / Σ x_i                   preço médio da caminhada
Imp(Q) = 10⁴ · lado · (p̄ − m) / m             custo face ao mid, já com meio spread
viável ⇔ Σ θ · q_i ≥ Q                        (tolerância 10⁻⁹ · Q); senão Imp = ∞
p_pior = último nível tocado
```

```
Q_max = Σ θ · q_i   nos níveis com lado · p_i ≤ lado · p*   (pára no primeiro fora)
Q_max_usd = Σ θ · q_i · p_i   nos mesmos níveis
truncado ⇔ todos os níveis visíveis cabem no teto           (Q_max é um mínimo)
```

### 2.5 A rota (Etapa 3; `decidir_rota`, `emitir_bilhete`, `rota_com_medida`, `medidor.montar_bilhete`)

Com os níveis do livro (`decidir_rota`):

1. Sem livro (`estado` vazio ou `SEM_DADOS`, bid ou ask inválidos, sem níveis): **sem_dados**, não há bilhete.
2. `AQUECIMENTO`: **aguardar**, sem tipo nem preço.
3. `VERMELHO`: **passiva**, ALO no toque (`b` numa compra, `a` numa venda).
4. Senão, `cabe ⇔ viável ∧ lado · p_pior ≤ lado · p*` (tolerância 10⁻⁹): **agressiva**, IOC em `p*`; caso contrário **passiva**, ALO no toque.

Sem os níveis (`emitir_bilhete`, só quando o estado não traz o livro): `cabe ⇔ Q_max_usd ≥ T`, no teto já arredondado.

A medida (2.12 e 2.13) só muda `agressiva` em `passiva`, com `n ≥ 100`. Depois, o tamanho: `Q_unid = ⌊T / m⌋_szDecimals`, valor `Q_unid · p_bilhete`; o bilhete avisa se `Q_unid = 0` ou se o valor fica abaixo de `minimo_ordem_usd`.

O bilhete recusa um `estado.json` com mais de `sem_dados_s` ou escrito com o medidor sem ligação, porque o livro desse instante já não existe.

### 2.6 Volatilidade, intensidade e latência (secção 6.3; `retorno_bps`, `Ewma`, `lam`, `desvio_latencia_bps`)

Por amostra (1 s), com Δt em segundos limitado a `[10⁻³, 10 · amostra_s]`:

```
r_t = 10⁴ · ln(m_t / m_{t−1})                retorno em bps
x_t = r_t² / Δt                               variância por segundo
```

Média exponencial com correcção do arranque, meia-vida `h` em amostras:

```
λ = 2^(−1/h)
s ← λ · s + (1 − λ) · x
w ← λ · w + (1 − λ)
valor = s / w
```

`σ_c` usa h = 60 s, `σ_l` usa h = 1800 s; `σ = √valor` em bps/√s. `vol_razao = σ_c / σ_l`. Só dá alarme depois de 300 amostras da longa.

A intensidade é o valor negociado por segundo em cada amostra, com EWMA de 10 s sobre EWMA de 1800 s: `intens_razao = int_c / int_l`.

O custo esperado da latência, disponível e não usado no estado: `E|δ| = σ · √τ · √(2/π)`.

O atraso dos dados é a mediana de `(hora local − hora da bolsa)` das últimas 200 mensagens de melhor preço.

### 2.7 Fluxo de ordens (secção 6.4, Cont, Kukanov e Stoikov; `ofi_evento`, `JanelaSoma`)

Em cada actualização do melhor preço, com 0 = antes e 1 = depois:

```
e = 1[b₁ ≥ b₀] · q_b₁ − 1[b₁ ≤ b₀] · q_b₀ − 1[a₁ ≤ a₀] · q_a₁ + 1[a₁ ≥ a₀] · q_a₀
OFI₁₀ = Σ e   nos últimos 10 s
ofi_norm = OFI₁₀ / EWMA₆₀((q_b + q_a) / 2)   em unidades do topo médio
ofi_lado = lado · ofi_norm                   positivo = a favor da ordem
```

### 2.8 Percentil com empates a meio (secção 6.8; `JanelaPercentil`)

Sobre as últimas `N` amostras de cada medida:

```
pos(x) = ( #{y < x} + ½ · #{y = x} ) / N        entre 0 e 1
N = janela_horas · 3600 / (amostra_s · passo_historico)      17 280 por defeito
```

O histórico cresce de `passo_historico` em `passo_historico` amostras (5 s). Os empates contam metade para um valor muito repetido, por exemplo o spread no mínimo, ficar a meio e não no topo. `NaN` nunca entra. Ao arrancar, o histórico de toda a janela é relido do disco.

### 2.9 Detector (secção 5; `Leitura`, `classificar`, `Ativo.amostrar`)

Cada medida produz uma leitura `(valor, posição, relevante, invertida)`. A posição calcula-se **antes** de juntar o valor ao histórico. Uma medida só alarma se a posição existir e passar o piso. Nas medidas invertidas lê-se `1 − posição`.

```
VERMELHO   se alguma posição' ≥ percentil_vermelho / 100   (0,95)
AMARELO    senão, se alguma ≥ percentil_amarelo / 100       (0,80)
VERDE      senão
AQUECIMENTO enquanto o histórico tem menos de N_aq = max(5, aquecimento_min · 60 / (amostra_s · passo_historico))   (360 pontos = 30 min)
SEM_DADOS  sem melhor preço, silêncio > sem_dados_s, ou ligação caída
```

| Medida | Valor que entra no percentil | Relevante se | Invertida |
| --- | --- | --- | --- |
| spread | spread em ticks, `round((a − b) / passo)` | `s_bps ≥ piso_spread_bps` (1,0) | não |
| profundidade | `min(Q_max_usd compra, Q_max_usd venda)`, com θ, no teto arredondado | `prof < piso_profundidade_x · tamanho_usd` (20 × 1000) | **sim** |
| volatilidade | `σ_c / σ_l` | `≥ piso_vol_razao` (1,5) e 300 amostras | não |
| intensidade | `int_c / int_l` | `≥ piso_intensidade_razao` (2,0) e 300 amostras | não |
| mark-oráculo | `10⁴ · |mark − oracle| / oracle` | `≥ piso_mark_oraculo_bps` (5,0) | não |
| liquidações | usd liquidados do activo nos últimos 60 s (só com chave) | `≥ piso_liquidacoes_usd` (100 000); só com histórico ≥ N_aq e valor > 0 | não |
| fluxo (opcional) | `|ofi_norm|`, só com `fluxo_no_estado = sim` | `≥ piso_ofi` (1,0) | não |

Os motivos saem como `nome pNN`, com o percentil bruto.

### 2.10 Registo sombra: a ordem passiva simulada (secção 6.6; `OrdemSombra`)

No instante do sinal `t₀`, uma ordem passiva ao melhor preço do próprio lado, posta no fim da fila:

```
p_pas = b (compra) ou a (venda)
F     = q_b ou q_a                            fila à frente, pior caso
Q     = T / m₀
prazo = prazo_s                               30 s
```

Para cada negócio `(px, sz)` em `[t₀, t₀ + prazo]`, com `tol = 10⁻¹² · p_pas`:

- `lado · (p_pas − px) > tol`: o preço atravessou o nosso; **preenche**.
- `|px − p_pas| ≤ tol`: `V ← V + sz`; preenche quando `V ≥ F + Q`.

Um sinal atrasado repete os negócios já vistos desde `t₀`. `π` é a fracção de sinais preenchidos e é um **mínimo**: ignora cancelamentos à frente na fila. O ganho da passiva é o meio spread no instante do sinal:

```
g = 10⁴ · lado · (m₀ − p_pas) / m₀           ≥ 0
```

`sombra_preenchida` é 1, 0 (prazo coberto por dados, sem corte) ou vazio (faltaram dados).

### 2.11 Resultado do sinal (secção 6.5; `resultado_bps`)

```
R_h = 10⁴ · lado · (m_h − m₀) / m₀
```

`m₀` é o mid em vigor no instante do sinal e `m_h` o mid em vigor em `t₀ + h`, ambos no relógio local, nos horizontes `horizontes_s` (5, 30, 60, 300 s). Fica em branco se houve um corte de dados entre o último preço conhecido e esse instante, ou se os dados não voltaram dentro de `sem_dados_s`. O medidor nunca inventa um preço.

### 2.12 Regra passiva ou agressiva (secção 6.5; `regra_rotas`, `rota_com_medida`, `MINIMO_REGRA`)

Nos sinais que entram na regra:

```
V_ag  = R̄ − (Imp̄ + f_t)
V_pas = π · (R̄_preenchidos + ḡ_preenchidos − f_m)          0 se π = 0
rota  = passiva se V_pas > V_ag, senão agressiva            empate fica na agressiva
```

`f_t` e `f_m` são as taxas medidas nos fills (média de F por tipo, quando há pelo menos 5 ordens desse tipo); senão as de `config.ini`, 4,5 e 1,5 bps.

A medida só actua com `n ≥ MINIMO_REGRA = 100`, só quando a rota do livro é agressiva, e só para a tornar passiva.

### 2.13 Margem da regra (versão 2.5; `margem_regra`, `margem_clara`)

Por sinal `i`, com `pre_i = 1` se a sombra preencheu:

```
v_ag,i  = R_i − (Imp_i + f_t)
v_pas,i = pre_i · (R_i + g_i − f_m)
d_i     = v_pas,i − v_ag,i
```

As médias de `v_ag,i` e `v_pas,i` são exactamente `V_ag` e `V_pas`; a média de `d_i` é `V_pas − V_ag`. O erro padrão é `s_d / √n` com o desvio amostral (precisa de `n ≥ 2`). A diferença é **clara** se `d̄ > 2 · s_d / √n`. Com `regra_exige_margem = sim`, uma escolha passiva que não seja clara fica travada e a rota do livro mantém-se.

### 2.14 Custo medido de cada fill (secção 6.7; `custo_decisao_bps`, `custo_execucao_bps`, `custo_taxa_bps`, `seleccao_adversa_bps`, `medidor._ordens`)

Para um fill com hora da bolsa `t_f`, preço `p`, tamanho `q` e taxa `fee`, com `m₁` o último mid com hora da bolsa estritamente antes de `t_f` (em branco se houve corte):

```
D   = 10⁴ · lado · (m₁ − m₀) / m₀            decisão e latência; só na primeira ordem de cada sinal
E   = 10⁴ · lado · (p − m₁) / m₁              execução; negativo numa passiva que ganha meio spread
F   = 10⁴ · fee / (p · q)                     taxa; negativo é rebate; só com taxa em USDC
A_h = −10⁴ · lado · (m_h − m₁) / m₁           selecção adversa a h = 5, 30, 60 s; positivo se o mid continuou contra
```

Um fill de abertura liga-se ao sinal mais recente do mesmo activo e lado com `t₀ ≤ t_f + 1 s` e `t_f − t₀ ≤ janela_sinal_s` (900 s). Os fills parciais da mesma `oid` juntam-se numa ordem, com cada medida ponderada pelo valor negociado; a ordem é taker se pelo menos metade do valor atravessou. Aberturas, fechos e inversões de posição (`dir`) nunca se misturam no relatório.

### 2.15 Preço indicado no sinal

```
desvio_preco = 10⁴ · lado · (m₀ − p_ind) / p_ind        quanto o mid já estava para lá do preço; positivo = pior
c_preco      = 10⁴ · lado · (p_fill − p_ind) / p_ind     quanto se pagou face a ele; positivo = pior
```

### 2.16 Taxa de acerto de equilíbrio (secção 3; `acerto_equilibrio`)

Com alvo `G`, stop `L`, custo de uma vitória `c_W` e de uma derrota `c_L`, todos em bps:

```
p* = (L + c_L) / (G + L + c_L − c_W)
```

Serve para ler quanto custa a execução em taxa de acerto; não entra em nenhuma decisão.

### 2.17 Liquidações (CoinGlass; `base_da_liquidacao`, `volume_liquidacao`, `lado_liquidacao`, `chave_liquidacao`)

A mesma liquidação pode chegar pelo websocket, nas duas grafias documentadas (`liquidation_orders` com `volume_usd` e `liquidationOrders` com `volUsd`), e pelo REST v4 (`usd_value`). A identidade é `(bolsa, activo, ⌊t / 1000⌋, preço arredondado, lado, usd arredondado a 10)`, e nunca conta duas vezes. `side = 1` é um long liquidado, venda forçada: conta **contra** uma compra e a favor de uma venda; `side = 2` é o espelho. Em cada sinal fresco ficam os dólares liquidados a favor e contra nos 60 s anteriores.

---

## 3. Documentação

### 3.1 Instalar e correr

```
bash arrancar.sh verificar      testa as ligações reais a partir do seu computador
bash arrancar.sh                corre o medidor (Ctrl+C para parar); no Mac impede o sono por inactividade
bash arrancar.sh relatorio      resume o que foi medido
bash arrancar.sh sinal BTC compra [preco]
```

Na primeira vez `arrancar.sh` cria o ambiente `.venv` e instala a única biblioteca externa, `websockets`. Precisa de Python 3.9 ou mais recente.

### 3.2 Comandos de `medidor.py`

```
python3 medidor.py [--config CAMINHO] correr
python3 medidor.py [--config CAMINHO] verificar
python3 medidor.py [--config CAMINHO] relatorio
python3 medidor.py [--config CAMINHO] sinal ATIVO LADO [PRECO] [--nota TEXTO]
python3 medidor.py [--config CAMINHO] bilhete ATIVO LADO [TAMANHO_USD]
```

Sem comando, corre. `LADO` aceita compra/venda, long/short, buy/sell, C/V, B/A/S.

### 3.3 `config.ini`

| Secção | Chave | Defeito | Significado |
| --- | --- | --- | --- |
| geral | `ativos` | BTC, ETH, SOL | Nomes exactos da Hyperliquid |
| geral | `endereco` | vazio | Endereço público `0x…` (42 caracteres) para receber os fills. Nunca uma chave |
| geral | `pasta_dados` | dados | Pasta dos registos, relativa ao `config.ini` |
| geral | `guardar_dias` | 30 | Dias de métricas a guardar; sinais e fills nunca se apagam |
| geral | `rede` | mainnet | `mainnet` ou `testnet` |
| orcamento | `alvo_bps` | 30 | G |
| orcamento | `fraccao_alvo` | 0.15 | φ |
| orcamento | `desconto_livro` | 0.5 | θ |
| orcamento | `tamanho_usd` | 1000 | T de referência |
| orcamento | `taxa_taker_bps` | 4.5 | f_t quando ainda não há 5 ordens taker medidas |
| orcamento | `taxa_maker_bps` | 1.5 | f_m quando ainda não há 5 ordens maker medidas |
| orcamento | `minimo_ordem_usd` | 10 | Valor mínimo por ordem na bolsa; o bilhete avisa abaixo disto |
| detector | `janela_horas` | 24 | Janela do percentil |
| detector | `aquecimento_min` | 30 | Minutos de histórico antes de dar estados |
| detector | `percentil_amarelo` | 80 | |
| detector | `percentil_vermelho` | 95 | |
| detector | `piso_spread_bps` | 1.0 | 0 desliga o piso |
| detector | `piso_profundidade_x` | 20 | Alarma se o livro útil for menor do que isto vezes `tamanho_usd` |
| detector | `piso_vol_razao` | 1.5 | |
| detector | `piso_intensidade_razao` | 2.0 | |
| detector | `fluxo_no_estado` | nao | `sim` põe o fluxo no estado, sem lado |
| detector | `piso_ofi` | 1.0 | |
| detector | `piso_liquidacoes_usd` | 100000 | |
| detector | `piso_mark_oraculo_bps` | 5.0 | |
| sombra | `prazo_s` | 30 | Vida da passiva simulada |
| sombra | `horizontes_s` | 5, 30, 60, 300 | Horizontes de R_h |
| sombra | `horizonte_regra_s` | 300 | h da regra; acrescenta-se sozinho a `horizontes_s` |
| sombra | `horizontes_fill_s` | 5, 30, 60 | Horizontes de A_h |
| sombra | `janela_sinal_s` | 900 | Ligação fill a sinal |
| sombra | `regra_exige_margem` | nao | `sim` exige 2 erros padrão para a medida tirar a travessia |
| painel | `intervalo_s` | 5 | Segundos entre painéis |
| coinglass | `chave` | vazio | Chave da API; vazio desliga as liquidações |
| coinglass | `canal` | liquidation_orders | A outra grafia subscreve-se sozinha |
| coinglass | `rest` | sim | Pede o fluxo no REST v4 se o websocket não entregar |
| coinglass | `rest_exchanges` | Binance, OKX, Bybit | |
| coinglass | `rest_min_usd` | 10000 | |
| coinglass | `rest_intervalo_s` | 10 | |
| avancado | `amostra_s` | 1 | Só para testes |
| avancado | `passo_historico` | 5 | Só para testes |
| avancado | `sem_dados_s` | 10 | Silêncio que vira `SEM_DADOS`; também a idade máxima do estado para o bilhete |
| avancado | `vigia_s` | 30 | Segundos sem dados de mercado antes de religar |
| avancado | `url_ws`, `url_info`, `url_coinglass` | Hyperliquid e CoinGlass | Só para a simulação |

### 3.4 Entrada: `dados/sinais.csv`

```
hora,ativo,lado,preco,alvo_bps,tamanho_usd,nota
,BTC,compra,,,,
2026-10-07T13:42:05Z,ETH,venda,4310.5,40,2500,reversal
```

- Só `ativo` e `lado` são obrigatórios. `hora` vazia é agora; com hora, `2026-10-07T13:42:05Z` em UTC, ou epoch em segundos ou milissegundos; sem fuso é hora local.
- O ficheiro só se acrescenta e cada linha acaba em mudança de linha. O medidor lê a partir do fim que encontrou ao arrancar; sinais escritos com o medidor parado, ou com mais de 2 horas, não são medidos.
- Um sinal com mais de 2 segundos de atraso (`fresco = 0`) mede o resultado e o registo sombra, mas não recebe impacto nem entra na regra, porque o livro desse instante já não existe.
- Uma linha ilegível não impede as seguintes.

### 3.5 Saídas na pasta `dados`

**`registo_sinais.csv`**, uma linha por sinal:
`id, hora_utc, ativo, lado, preco_sinal, desvio_preco_bps, tamanho_usd, alvo_bps, fresco, estado, motivos, mid0, spread_bps, desequilibrio, ofi_lado, liq_favor_usd, liq_contra_usd, imp_bps, imp_viavel, orcamento_bps, p_teto, qmax_usd, qmax_truncado, sombra_preco, sombra_fila, sombra_preenchida, sombra_tempo_s, ganho_passiva_bps, r_5, r_30, r_60, r_300, nota`.

**`registo_fills.csv`**, uma linha por fill:
`hora_utc, ativo, lado, px, sz, valor_usd, taxa, moeda_taxa, taker, dir, oid, tid, mid_antes, e_bps, f_bps, a_5, a_30, a_60, estado, sinal_id, ordem_no_sinal, d_bps, atraso_s, c_preco_bps`.

**`metricas/ATIVO_AAAA-MM-DD.csv`**, uma linha de 5 em 5 segundos:
`hora_utc, local_ms, mid, bid, ask, qb, qa, spread_bps, spread_ticks, prof_util_usd, imp_ref_bps, sigma_curta, sigma_longa, vol_razao, intens_razao, ofi_norm, liq_60s_usd, mark_oraculo_bps, desequilibrio, estado, motivos`. O tempo sem dados também fica registado.

**`estado.json`**, reescrito a cada segundo, para os outros bots lerem:
`hora_utc, ligado, versao, orcamento_bps, tamanho_ref_usd` e, por activo, `estado, motivos, mid, bid, ask, qb, qa, spread_bps, spread_ticks, desequilibrio, prof_util_usd, imp_ref_bps, imp_compra_bps, imp_venda_bps, p_teto_compra, p_teto_venda, qmax_usd_compra, qmax_usd_venda, qmax_truncado, sz_decimals, niveis_compra, niveis_venda, sigma_curta, sigma_longa, vol_razao, intens_razao, ofi_norm, liq_60s_usd, liq_long_usd, liq_short_usd, mark_oraculo_bps, atraso_ms, mid_ponderado, historico, idade_livro_ms`. `niveis_compra` são os níveis que uma compra consome (asks); `niveis_venda`, os bids. Um valor que não se sabe é `null`.

**`relatorio.txt`**: o último relatório. **`medidor.log`**: avisos e erros, com rotação a 5 MB.

Um campo vazio quer dizer "não se sabe". Os CSV abrem-se e fecham-se em cada linha, por isso podem ser copiados ou substituídos sem o medidor ficar a escrever numa cópia; se as colunas mudarem (novos horizontes), o ficheiro migra sozinho e guarda uma cópia `.anterior-…`.

### 3.6 Painel

`ACTIVO, ESTADO, MID, SPREAD (bps), PROF.UTIL (usd no teto, já com θ; `+` se o teto fica para lá dos níveis visíveis), VOL (σ_c/σ_l), INTENS, FLUXO (ofi_norm), MK-OR (bps), IMP.REF (bps do tamanho de referência), MOTIVO`. A primeira linha traz a hora, a ligação, o atraso dos dados, os contadores de sinais e fills e o estado da CoinGlass.

### 3.7 Bilhete

```
BILHETE  AGRESSIVA  compra BTC  (medidor 2.5, livro de 2026-10-07T18:21:58.242Z, ha 0.0 s)
Estado do livro: VERDE
Tipo: IOC    preco: 100.4    tamanho: 100.00 usd    validade: imediata (IOC)
Tamanho em unidades: 0.99900 BTC  (100.30 usd ao preco do bilhete; 5 casas, arredondado para baixo)
Qmax no teto: 501 usd    Imp(Q): 9.99 bps
pior preco 100.2 dentro do teto 100.4. Imp(Q) = 9.99 bps, consequencia, nao criterio.
Medida: ainda nao ha sinais deste activo com impacto. A rota e so a do livro.
Fluxo face a ordem: +0.00 (neutro). Nao muda a rota.
Nao enviado. Quem envia e a pessoa. A Etapa 4 e que tem chave, e ainda nao.
```

Linhas que podem aparecer: `AVISO:` (tamanho a zero, abaixo do mínimo, fotografia do livro velha), `Medida aos 300 s, n=…` com V agressiva, V passiva e a escolha, `V passiva - V agressiva = … erro padrao …` com "diferenca clara" ou "dentro do ruido", `A regra exige margem…` quando a escolha ficou travada, e `Com menos de 100 sinais nao muda a rota. Faltam …`.

### 3.8 Robustez

- **Ligação.** Religa com espera exponencial até 30 s. Um vigia fecha a ligação se passarem `vigia_s` sem dados de mercado, mesmo que os pings respondam.
- **Cortes.** Cada queda ou silêncio fica registado como um intervalo sem dados, no relógio local e no da bolsa. Nenhuma consulta de preço atravessa um corte: o resultado fica em branco.
- **Sinais atrasados** repetem os negócios já vistos; **fills reenviados** depois de religar não contam duas vezes (`tid`) e um fill antigo nunca se liga a um sinal posterior a ele.
- **Fills parciais** juntam-se por `oid`; **taxas noutra moeda** deixam F em branco; **segunda abertura** no mesmo sinal fica fora de D.
- **Paragem a meio** (Ctrl+C) escreve o que estava a meio, com o que falta em branco. O histórico dos percentis recupera-se do disco ao arrancar.
- **Relógio.** Os custos de decisão comparam a hora do computador com a da bolsa; a hora automática tem de estar ligada. Um sinal datado no futuro é lido como agora.

### 3.9 Testes

```
source .venv/bin/activate
python3 testes/teste_nucleo.py      62 testes das fórmulas
python3 testes/teste_medidor.py     44 testes das peças sem rede
python3 testes/simulacao.py         91 conferências contra uma bolsa falsa, cerca de 25 s
```

A simulação fala o formato documentado da Hyperliquid e da CoinGlass, em `127.0.0.1`, e inclui queda de ligação, paragem de dados sem queda, rajada, fills parciais, sinais atrasados, inválidos e a paragem a meio. A versão 2.5 foi conferida em Python 3.13 com `websockets` 17; o código é compatível com 3.9.

### 3.10 Mapa do código

| Fórmula ou regra | Onde |
| --- | --- |
| mid, spread, desequilíbrio, mid ponderado | `nucleo.mid`, `spread_bps`, `desequilibrio`, `mid_ponderado` |
| B, p*, arredondamento, passo, tamanho | `nucleo.orcamento_bps`, `preco_teto`, `arredondar_preco`, `passo_preco`, `arredondar_tamanho` |
| Imp(Q), viável, p_pior, Q_max | `nucleo.impacto`, `tamanho_max`; o livro acertado em `medidor.Ativo.niveis` |
| EWMA, retorno, latência | `nucleo.Ewma`, `lam`, `retorno_bps`, `desvio_latencia_bps` |
| OFI e somas móveis | `nucleo.ofi_evento`, `JanelaSoma` |
| Percentil com empates a meio | `nucleo.JanelaPercentil` |
| Estado do livro | `nucleo.Leitura`, `classificar`; as leituras em `medidor.Ativo.amostrar` |
| Registo sombra | `nucleo.OrdemSombra`; alimentado em `medidor.Medidor._tratar` |
| R_h | `nucleo.resultado_bps`; `medidor.Medidor._avancar_sinais` |
| V_ag, V_pas, escolha, mínimo | `nucleo.valor_agressiva`, `valor_passiva`, `regra_rotas`, `rota_com_medida`, `MINIMO_REGRA` |
| Margem | `nucleo.margem_regra`, `margem_clara`; `medidor.medida_do_activo` |
| D, E, F, A | `nucleo.custo_decisao_bps`, `custo_execucao_bps`, `custo_taxa_bps`, `seleccao_adversa_bps`; `medidor.Medidor._um_fill`, `_avancar_fills`, `_escrever_fill` |
| Rota e bilhete | `nucleo.decidir_rota`, `emitir_bilhete`, `Bilhete`; `medidor.montar_bilhete`, `cmd_bilhete` |
| Cortes e consultas de preço | `medidor.Ativo._chegou`, `desligado`, `em_corte`, `coberto`, `topo_em_local`, `mid_exch` |
| Liquidações | `medidor.base_da_liquidacao`, `volume_liquidacao`, `lado_liquidacao`, `chave_liquidacao`, `Medidor._ingerir_liq`, `_poll_rest` |
| Relatório | `medidor.cmd_relatorio`, `_ordens`, `_pond_valor`, `frase_do_horizonte` |
| Taxa de acerto de equilíbrio | `nucleo.acerto_equilibrio` |

### 3.11 O que falta, por ordem

1. Correr no Mac: `bash arrancar.sh verificar`, e depois os três testes. A Hyperliquid real não foi alcançada a partir do ambiente onde o código foi escrito e revisto.
2. Pôr a chave da CoinGlass e, para medir o custo dos fills, o endereço público. Nunca a chave da conta.
3. Deixar o medidor a correr e os sinais a cair em `sinais.csv`. Os primeiros 30 minutos são aquecimento.
4. Esperar por 100 sinais que entrem na regra e 30 aberturas. Antes disso o relatório é indicativo; a primeira linha diz quantos faltam.
5. Ler quatro números, só nas aberturas: D contra E, a selecção adversa por rota, o custo por estado, e a percentagem de preenchimento. A pergunta é se o vermelho custa dinheiro e se a passiva ganha à agressiva nos vossos sinais, e se essa diferença é clara ou está dentro do ruído.
6. Se a regra há-de valer para o trade de horas, mudar `horizonte_regra_s` para 3600 ou 14400 e voltar a medir.
7. A Etapa 4, enviar a ordem, só depois do ponto 5 estar estável. Ainda não.

---

## 4. Código completo

O código desta versão está no ramo `claude/keen-babbage-0w04g5` do repositório, na pasta `medidor/`:

| Ficheiro | Linhas | Conteúdo |
| --- | --- | --- |
| `nucleo.py` | 590 | Fórmulas puras, sem rede nem ficheiros |
| `medidor.py` | 2117 | Ligações, registos, painel, relatório, bilhete |
| `config.ini` | 91 | Configuração comentada |
| `arrancar.sh` | 25 | Ambiente e arranque |
| `LEIA-ME.md` | 161 | Manual de instalação e leitura |
| `testes/teste_nucleo.py` | 468 | Testes das fórmulas |
| `testes/teste_medidor.py` | 582 | Testes das peças |
| `testes/simulacao.py` | 516 | Simulação de ponta a ponta |

Os mesmos ficheiros seguem em anexo a este documento, individualmente e no arquivo `medidor-2.5.zip`.
