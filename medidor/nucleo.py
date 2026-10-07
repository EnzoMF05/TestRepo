"""Nucleo matematico do medidor de execucao (Etapa 2).

Funcoes puras: nao abrem ligacoes nem ficheiros. Cada funcao implementa uma
definicao do documento "Scalping na Hyperliquid: execucao e controlo de
slippage" (seccoes 3, 5 e 6); o numero da seccao vem no comentario.

Convencoes
----------
- Precos e tamanhos sao float. Custos saem em bps (1 bp = 0,01 %).
- lado = +1 para compra, -1 para venda.
- Custo positivo = custo; resultado positivo = a favor.
- Compativel com Python 3.9+.
"""
from __future__ import annotations

import bisect
import math
from collections import deque
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Deque, Iterable, List, Optional, Sequence, Tuple

BPS = 1e4

VERDE = "VERDE"
AMARELO = "AMARELO"
VERMELHO = "VERMELHO"
AQUECIMENTO = "AQUECIMENTO"
SEM_DADOS = "SEM_DADOS"

Nivel = Tuple[float, float]  # (preco, tamanho)


# --------------------------------------------------------------------------
# 6.1 Mid, spread e desequilibrio
# --------------------------------------------------------------------------
def mid(b: float, a: float) -> float:
    """Ponto a meio entre a melhor compra (b) e a melhor venda (a)."""
    return (a + b) / 2.0


def spread_bps(b: float, a: float) -> float:
    return BPS * (a - b) / mid(b, a)


def desequilibrio(qb: float, qa: float) -> float:
    """I entre -1 e +1; positivo = mais tamanho na compra."""
    total = qb + qa
    if total <= 0:
        return 0.0
    return (qb - qa) / total


def mid_ponderado(b: float, a: float, qb: float, qa: float) -> float:
    """m_w = m + (a - b)/2 * I."""
    return mid(b, a) + (a - b) / 2.0 * desequilibrio(qb, qa)


# --------------------------------------------------------------------------
# 6.2 Impacto previsto, teto de preco e tamanho maximo
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Impacto:
    viavel: bool          # o livro descontado chega para o tamanho pedido?
    preco_medio: float    # preco medio de execucao previsto (nan se inviavel)
    imp_bps: float        # custo face ao mid, ja com meio spread (inf se inviavel)
    preco_pior: float     # ultimo nivel tocado
    niveis: int           # niveis consumidos
    preenchido: float     # tamanho que o livro descontado aguenta, ate ao pedido


def impacto(niveis: Sequence[Nivel], q: float, m: float, lado: int,
            theta: float = 1.0) -> Impacto:
    """Percorre o livro do lado consumido (melhor preco primeiro).

    niveis: asks para uma compra, bids para uma venda.
    theta:  fraccao do tamanho visivel que se assume disponivel.
    """
    if q <= 0 or m <= 0:
        raise ValueError("q e m tem de ser positivos")
    resto = q
    custo = 0.0
    usados = 0
    pior = float("nan")
    for px, sz in niveis:
        if resto <= 1e-15:
            break
        x = min(theta * sz, resto)
        if x <= 0:
            continue
        custo += px * x
        resto -= x
        usados += 1
        pior = px
    feito = q - max(resto, 0.0)
    if resto > q * 1e-9:  # o livro nao chega: a ordem nao cabe
        return Impacto(False, float("nan"), float("inf"), pior, usados, feito)
    medio = custo / feito
    return Impacto(True, medio, BPS * lado * (medio - m) / m, pior, usados, feito)


def orcamento_bps(alvo_bps: float, fraccao: float) -> float:
    """B = phi * G."""
    return fraccao * alvo_bps


def preco_teto(m: float, lado: int, b_bps: float) -> float:
    """p_teto = m * (1 + lado * B / 1e4). Limite por unidade."""
    return m * (1.0 + lado * b_bps / BPS)


@dataclass(frozen=True)
class TamanhoMax:
    q: float              # tamanho em unidades do activo
    usd: float            # o mesmo em dolares
    truncado: bool        # o teto fica para la dos niveis visiveis: q e um minimo


def tamanho_max(niveis: Sequence[Nivel], p_teto: float, lado: int,
                theta: float = 1.0) -> TamanhoMax:
    """Q_max = soma de theta*q_i nos niveis com preco dentro do teto."""
    q = 0.0
    usd = 0.0
    dentro = 0
    for px, sz in niveis:
        if lado * px <= lado * p_teto:
            q += theta * sz
            usd += theta * sz * px
            dentro += 1
        else:
            break
    truncado = len(niveis) > 0 and dentro == len(niveis)
    return TamanhoMax(q, usd, truncado)


# --------------------------------------------------------------------------
# 6.9 Arredondamento valido na Hyperliquid (perps)
# --------------------------------------------------------------------------
def arredondar_preco(px: float, sz_decimals: int, para_baixo: bool) -> float:
    """No maximo 5 algarismos significativos e 6 - szDecimals casas decimais.

    Precos inteiros sao sempre validos. para_baixo=True para o teto de uma
    compra, False para o teto de uma venda.
    """
    if px <= 0:
        raise ValueError("preco tem de ser positivo")
    d = Decimal(repr(float(px)))
    casas_max = max(0, 6 - int(sz_decimals))
    casas_sig = 4 - d.adjusted()          # casas permitidas por 5 algarismos
    casas = max(0, min(casas_max, casas_sig))
    passo = Decimal(1).scaleb(-casas)
    modo = ROUND_FLOOR if para_baixo else ROUND_CEILING
    return float(d.quantize(passo, rounding=modo))


def passo_preco(px: float, sz_decimals: int) -> float:
    """Menor incremento de preco valido junto de px."""
    d = Decimal(repr(float(px)))
    casas = max(0, min(max(0, 6 - int(sz_decimals)), 4 - d.adjusted()))
    return float(Decimal(1).scaleb(-casas))


def arredondar_tamanho(q: float, sz_decimals: int) -> float:
    """Tamanho com szDecimals casas, sempre para baixo."""
    passo = Decimal(1).scaleb(-int(sz_decimals))
    return float(Decimal(repr(float(q))).quantize(passo, rounding=ROUND_FLOOR))


# --------------------------------------------------------------------------
# 6.3 Volatilidade (EWMA) e custo da latencia
# --------------------------------------------------------------------------
def lam(meia_vida: float) -> float:
    """lambda = 2^(-1/h), h em numero de amostras."""
    return 2.0 ** (-1.0 / meia_vida)


class Ewma:
    """Media exponencial com correccao do arranque.

    s <- lambda*s + (1-lambda)*x ; valor = s / peso. A correccao so importa
    nas primeiras amostras; depois coincide com a formula do documento.
    """

    def __init__(self, meia_vida: float):
        self.lam = lam(meia_vida)
        self._s = 0.0
        self._w = 0.0
        self.n = 0

    def juntar(self, x: float) -> float:
        self._s = self.lam * self._s + (1.0 - self.lam) * x
        self._w = self.lam * self._w + (1.0 - self.lam)
        self.n += 1
        return self.valor

    @property
    def valor(self) -> float:
        return self._s / self._w if self._w > 0 else float("nan")


def retorno_bps(m_agora: float, m_antes: float) -> float:
    return BPS * math.log(m_agora / m_antes)


def desvio_latencia_bps(sigma_bps_raiz_s: float, tau_s: float) -> float:
    """E|delta| = sigma * sqrt(tau) * sqrt(2/pi)."""
    return sigma_bps_raiz_s * math.sqrt(tau_s) * math.sqrt(2.0 / math.pi)


# --------------------------------------------------------------------------
# 6.4 Fluxo de ordens (OFI), Cont, Kukanov e Stoikov
# --------------------------------------------------------------------------
def ofi_evento(b0: float, qb0: float, a0: float, qa0: float,
               b1: float, qb1: float, a1: float, qa1: float) -> float:
    """Contribuicao de uma actualizacao do melhor preco (0 = antes, 1 = depois)."""
    e = 0.0
    if b1 >= b0:
        e += qb1
    if b1 <= b0:
        e -= qb0
    if a1 <= a0:
        e -= qa1
    if a1 >= a0:
        e += qa0
    return e


class JanelaSoma:
    """Soma movel de valores nos ultimos `duracao_ms` milissegundos."""

    def __init__(self, duracao_ms: int):
        self.duracao_ms = duracao_ms
        self._itens: Deque[Tuple[int, float]] = deque()
        self._soma = 0.0

    def juntar(self, t_ms: int, v: float) -> None:
        self._itens.append((t_ms, v))
        self._soma += v

    def soma(self, agora_ms: int) -> float:
        limite = agora_ms - self.duracao_ms
        while self._itens and self._itens[0][0] <= limite:
            self._soma -= self._itens.popleft()[1]
        if not self._itens:
            self._soma = 0.0  # evita acumular erro de arredondamento
        return self._soma


# --------------------------------------------------------------------------
# 6.8 Percentil de cada medida (com empates a contar metade)
# --------------------------------------------------------------------------
class JanelaPercentil:
    """Ultimas N amostras de uma medida e a posicao de um valor nelas.

    posicao(x) = (n. de amostras < x + metade das iguais a x) / N, entre 0 e 1.
    Os empates contam metade para que um valor muito repetido (por exemplo um
    spread quase sempre no minimo) fique a meio e nao no topo.
    """

    def __init__(self, n_max: int):
        if n_max <= 0:
            raise ValueError("n_max tem de ser positivo")
        self.n_max = n_max
        self._ordem: Deque[float] = deque()
        self._ord: List[float] = []

    def __len__(self) -> int:
        return len(self._ord)

    def juntar(self, x: float) -> None:
        if x != x:  # nan
            return
        self._ordem.append(x)
        bisect.insort(self._ord, x)
        if len(self._ordem) > self.n_max:
            velho = self._ordem.popleft()
            i = bisect.bisect_left(self._ord, velho)
            del self._ord[i]

    def posicao(self, x: float) -> float:
        n = len(self._ord)
        if n == 0 or x != x:
            return float("nan")
        menos = bisect.bisect_left(self._ord, x)
        iguais = bisect.bisect_right(self._ord, x) - menos
        return (menos + 0.5 * iguais) / n

    def mediana(self) -> float:
        n = len(self._ord)
        if n == 0:
            return float("nan")
        if n % 2:
            return self._ord[n // 2]
        return 0.5 * (self._ord[n // 2 - 1] + self._ord[n // 2])


# --------------------------------------------------------------------------
# 5. Detector: raro (percentil) e relevante (piso)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Leitura:
    nome: str
    valor: float
    posicao: float        # 0..1 no historico da propria medida (nan = sem historico)
    relevante: bool       # passa o piso minimo de relevancia?
    invertida: bool = False  # True quando o perigo esta nos valores baixos


def classificar(leituras: Iterable[Leitura], p_amarelo: float = 0.80,
                p_vermelho: float = 0.95) -> Tuple[str, List[str]]:
    """Estado do livro a partir das leituras.

    Uma medida so da alarme se for rara no seu historico E relevante em valor
    absoluto. Vermelho: alguma acima de p_vermelho. Amarelo: alguma acima de
    p_amarelo. Nas medidas invertidas (profundidade) le-se 1 - posicao.
    """
    estado = VERDE
    motivos: List[str] = []
    for lt in leituras:
        if lt.posicao != lt.posicao or not lt.relevante:
            continue
        pos = 1.0 - lt.posicao if lt.invertida else lt.posicao
        if pos >= p_vermelho:
            estado = VERMELHO
            motivos.append("%s p%d" % (lt.nome, round(100 * lt.posicao)))
        elif pos >= p_amarelo:
            if estado != VERMELHO:
                estado = AMARELO
            motivos.append("%s p%d" % (lt.nome, round(100 * lt.posicao)))
    return estado, motivos


# --------------------------------------------------------------------------
# 6.6 Registo sombra: uma ordem passiva simulada
# --------------------------------------------------------------------------
class OrdemSombra:
    """Ordem passiva simulada ao melhor preco do proprio lado.

    Compra a `preco` com `fila` a frente (pior caso: e a ultima da fila).
    Preenche se sair um negocio a preco melhor do que o seu (abaixo, numa
    compra) ou se o volume negociado exactamente ao seu preco exceder a fila
    inicial mais o seu tamanho. Para vendas, o espelho.
    """

    def __init__(self, lado: int, preco: float, fila: float, tamanho: float,
                 t0_ms: int, prazo_ms: int):
        self.lado = lado
        self.preco = preco
        self.fila = fila
        self.tamanho = tamanho
        self.t0_ms = t0_ms
        self.fim_ms = t0_ms + prazo_ms
        self.volume_no_preco = 0.0
        self.preenchida = False
        self.t_fill_ms: Optional[int] = None

    def negocio(self, t_ms: int, px: float, sz: float) -> bool:
        """Processa um negocio; devolve True se a ordem ficou preenchida."""
        if self.preenchida or t_ms < self.t0_ms or t_ms > self.fim_ms:
            return self.preenchida
        tol = abs(self.preco) * 1e-12
        if self.lado * (self.preco - px) > tol:      # negocio atravessou o seu preco
            self._fechar(t_ms)
        elif abs(px - self.preco) <= tol:
            self.volume_no_preco += sz
            if self.volume_no_preco >= self.fila + self.tamanho - 1e-12:
                self._fechar(t_ms)
        return self.preenchida

    def _fechar(self, t_ms: int) -> None:
        self.preenchida = True
        self.t_fill_ms = t_ms

    def expirada(self, agora_ms: int) -> bool:
        return self.preenchida or agora_ms > self.fim_ms


# --------------------------------------------------------------------------
# 6.5 Resultado do sinal e regra passiva / agressiva
# --------------------------------------------------------------------------
def resultado_bps(lado: int, m_h: float, m_0: float) -> float:
    """R_h = 1e4 * lado * (m_h - m_0) / m_0."""
    return BPS * lado * (m_h - m_0) / m_0


def valor_agressiva(r_medio: float, imp_bps: float, taxa_taker_bps: float) -> float:
    """V_ag = R medio - (Imp + f_t)."""
    return r_medio - (imp_bps + taxa_taker_bps)


def valor_passiva(pi: float, r_medio_preenchidos: float, ganho_spread_bps: float,
                  taxa_maker_bps: float) -> float:
    """V_pas = pi * (R medio nos preenchidos + s/2 - f_m)."""
    return pi * (r_medio_preenchidos + ganho_spread_bps - taxa_maker_bps)


MINIMO_REGRA = 100


def regra_rotas(r_medio: float, imp_bps: float, pi: float, r_preenchidos: float,
                ganho_spread_bps: float, taxa_taker_bps: float, taxa_maker_bps: float) -> Tuple[float, float, str]:
    """V_ag e V_pas da seccao 6.5, e a rota com o valor maior. Empate fica na agressiva."""
    v_ag = valor_agressiva(r_medio, imp_bps, taxa_taker_bps)
    v_pas = (valor_passiva(pi, r_preenchidos, ganho_spread_bps, taxa_maker_bps) if pi > 0 else 0.0)
    return v_ag, v_pas, ("passiva" if v_pas > v_ag else "agressiva")


def rota_com_medida(rota_livro: str, n: int, escolha: str, minimo: int = MINIMO_REGRA) -> str:
    """A medida so tira uma travessia. Nao atravessa o que o livro proibiu, e abaixo do minimo nao mexe."""
    if rota_livro != "agressiva" or n < minimo or escolha != "passiva":
        return rota_livro
    return "passiva"


def margem_regra(r: Sequence[float], imp_bps: Sequence[float], preenchida: Sequence[bool],
                 ganho_spread_bps: Sequence[float], taxa_taker_bps: float,
                 taxa_maker_bps: float) -> Tuple[float, float]:
    """V_pas - V_ag calculado sinal a sinal, e o erro padrao dessa diferenca.

    Para cada sinal i: v_ag_i = R_i - (Imp_i + f_t) e v_pas_i = 1[preenchida_i] (R_i + g_i - f_m),
    com g_i o meio spread ganho pela passiva. As medias de v_ag_i e v_pas_i sao exactamente
    V_ag e V_pas da regra 6.5; a media de d_i = v_pas_i - v_ag_i e V_pas - V_ag, e o erro
    padrao e desvio(d) / sqrt(n), com o desvio amostral. Com menos de 2 sinais nao ha erro
    padrao (nan). Sem sinais, tudo nan.
    """
    n = len(r)
    if not (n == len(imp_bps) == len(preenchida) == len(ganho_spread_bps)):
        raise ValueError("as listas tem de ter o mesmo comprimento")
    if n == 0:
        return float("nan"), float("nan")
    d = []
    for ri, ii, cheia, gi in zip(r, imp_bps, preenchida, ganho_spread_bps):
        v_ag = ri - (ii + taxa_taker_bps)
        v_pas = (ri + gi - taxa_maker_bps) if cheia else 0.0
        d.append(v_pas - v_ag)
    media_d = sum(d) / n
    if n < 2:
        return media_d, float("nan")
    var = sum((x - media_d) ** 2 for x in d) / (n - 1)
    return media_d, math.sqrt(var / n)


def margem_clara(diferenca: float, erro_padrao: float, k: float = 2.0) -> bool:
    """A passiva so e claramente melhor se V_pas - V_ag passa k erros padrao."""
    return diferenca == diferenca and erro_padrao == erro_padrao and diferenca > k * erro_padrao


# --------------------------------------------------------------------------
# Etapa 3. Bilhete: a ordem pronta, sem a enviar
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Bilhete:
    rota: str             # passiva, agressiva, aguardar, sem_dados
    tipo: str             # ALO, IOC, ou vazio (sem_dados e aguardar: nao ha ordem)
    preco: float
    tamanho_usd: float
    nota: str
    qmax_usd: float = float("nan")
    imp_bps: float = float("nan")


def _livro_serve(estado: str, bid: float, ask: float) -> bool:
    return estado not in ("", SEM_DADOS) and bid == bid and ask == ask and bid > 0 and ask > bid


def decidir_rota(lado: int, tamanho_usd: float, orcamento_bps: float, estado: str,
                 bid: float, ask: float, niveis: Sequence[Nivel], sz_decimals: int,
                 theta: float = 1.0) -> Bilhete:
    """A rota a partir do livro. Nao envia a ordem.

    p* = mid * (1 + lado * B / 1e4), arredondado para dentro do orcamento.
    Q = T / mid unidades, com o desconto theta na profundidade.
    IOC em p* se, e so se, o pior preco de Q nao passa de p* e o estado nao e vermelho.
    O impacto medio e consequencia dessa conta, nao um segundo criterio.
    """
    if not _livro_serve(estado, bid, ask) or not niveis:
        return Bilhete("sem_dados", "", float("nan"), tamanho_usd, "sem livro: nao ha bilhete")
    m = mid(bid, ask)
    toque = bid if lado > 0 else ask
    teto = arredondar_preco(preco_teto(m, lado, orcamento_bps), sz_decimals, lado > 0)
    imp = impacto(list(niveis), tamanho_usd / m, m, lado, theta)
    qm = tamanho_max(niveis, teto, lado, theta)
    if estado == AQUECIMENTO:  # sem estado nao ha rota, nem a passiva: o bilhete nao traz ordem
        return Bilhete("aguardar", "", float("nan"), tamanho_usd,
                       "aquecimento: o estado ainda nao existe, nao ha rota", qm.usd, imp.imp_bps)
    if estado == VERMELHO:
        return Bilhete("passiva", "ALO", toque, tamanho_usd,
                       "vermelho: nao atravessa o livro; fluxo a favor nao muda isto", qm.usd, imp.imp_bps)
    tol = 1e-9 * max(1.0, abs(teto))
    cabe = bool(imp.viavel and imp.preco_pior == imp.preco_pior and lado * imp.preco_pior <= lado * teto + tol)
    dolares_chegam = qm.usd == qm.usd and qm.usd + 1e-6 >= tamanho_usd
    aviso = "" if cabe == dolares_chegam else " A profundidade em dolares e outra conta; a rota segue Q = T/mid."
    if cabe:
        return Bilhete(
            "agressiva", "IOC", teto, tamanho_usd,
            "pior preco %.8g dentro do teto %.8g. Imp(Q) = %.2f bps, consequencia, nao criterio.%s"
            % (imp.preco_pior, teto, imp.imp_bps, aviso),
            qm.usd, imp.imp_bps)
    texto_q = " Qmax %.0f usd." % qm.usd if qm.usd == qm.usd else ""
    return Bilhete("passiva", "ALO", toque, tamanho_usd,
                   "Q nao cabe no teto; fica passiva no toque." + texto_q + aviso, qm.usd, imp.imp_bps)


def emitir_bilhete(lado: int, tamanho_usd: float, orcamento_bps: float, estado: str,
                   bid: float, ask: float, imp_bps: float, qmax_usd: float,
                   p_teto: float) -> Bilhete:
    """Rota sem os niveis do livro: Q <= Qmax em dolares, no teto ja arredondado.

    imp_bps fica registado e nao decide. Com os niveis, use decidir_rota.
    """
    if not _livro_serve(estado, bid, ask):
        return Bilhete("sem_dados", "", float("nan"), tamanho_usd, "sem livro: nao ha bilhete", qmax_usd, imp_bps)
    toque = bid if lado > 0 else ask
    if estado == AQUECIMENTO:  # sem estado nao ha rota, nem a passiva: o bilhete nao traz ordem
        return Bilhete("aguardar", "", float("nan"), tamanho_usd,
                       "aquecimento: o estado ainda nao existe, nao ha rota", qmax_usd, imp_bps)
    if estado == VERMELHO:
        return Bilhete("passiva", "ALO", toque, tamanho_usd,
                       "vermelho: nao atravessa o livro; fluxo a favor nao muda isto", qmax_usd, imp_bps)
    cabe = qmax_usd == qmax_usd and qmax_usd + 1e-9 >= tamanho_usd and p_teto == p_teto and p_teto > 0
    if cabe:
        return Bilhete("agressiva", "IOC", p_teto, tamanho_usd,
                       "sem os niveis: Q <= Qmax em dolares no teto. O impacto de referencia nao decide.",
                       qmax_usd, imp_bps)
    extra = ""
    if qmax_usd == qmax_usd and 0 < qmax_usd < tamanho_usd:
        extra = " Ate %.0f usd caberiam no teto." % qmax_usd
    return Bilhete("passiva", "ALO", toque, tamanho_usd,
                   "o tamanho nao cabe no teto; fica passiva no toque." + extra, qmax_usd, imp_bps)


# --------------------------------------------------------------------------
# 6.7 Custo medido de cada fill
# --------------------------------------------------------------------------
def custo_decisao_bps(lado: int, m1: float, m0: float) -> float:
    """D: quanto o mid andou contra si entre o sinal (m0) e o fill (m1)."""
    return BPS * lado * (m1 - m0) / m0


def custo_execucao_bps(lado: int, px: float, m1: float) -> float:
    """E: preco do fill face ao ultimo mid antes dele."""
    return BPS * lado * (px - m1) / m1


def custo_taxa_bps(fee: float, px: float, sz: float) -> float:
    """F: taxa paga em bps do valor negociado (negativo = rebate)."""
    return BPS * fee / (px * sz)


def seleccao_adversa_bps(lado: int, m_h: float, m1: float) -> float:
    """A_h: positivo se o mid continuou contra si h segundos depois."""
    return -BPS * lado * (m_h - m1) / m1


# --------------------------------------------------------------------------
# 3. Taxa de acerto de equilibrio
# --------------------------------------------------------------------------
def acerto_equilibrio(alvo_bps: float, stop_bps: float, custo_ganha_bps: float,
                      custo_perde_bps: float) -> float:
    """p* = (L + c_L) / (G + L + c_L - c_W)."""
    den = alvo_bps + stop_bps + custo_perde_bps - custo_ganha_bps
    if den <= 0:
        return float("inf")
    return (stop_bps + custo_perde_bps) / den


# --------------------------------------------------------------------------
# Utilitarios de leitura
# --------------------------------------------------------------------------
def interpretar_lado(texto: str) -> int:
    """Aceita compra/venda, long/short, buy/sell, C/V, B/A/S. Devolve +1 ou -1."""
    t = (texto or "").strip().lower()
    if t in ("compra", "comprar", "long", "buy", "c", "b", "l", "+1", "1"):
        return 1
    if t in ("venda", "vender", "short", "sell", "v", "s", "a", "-1"):
        return -1
    raise ValueError("lado desconhecido: %r" % texto)


def media(xs: Sequence[float]) -> float:
    xs = [x for x in xs if x == x]
    return sum(xs) / len(xs) if xs else float("nan")


def mediana(xs: Sequence[float]) -> float:
    xs = sorted(x for x in xs if x == x)
    n = len(xs)
    if n == 0:
        return float("nan")
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])
