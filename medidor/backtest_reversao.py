#!/usr/bin/env python3
"""Backtest por eventos do sinalizador de reversao (Etapa 1, REVOU v2, seccao 11).

Percorre as velas fechadas de 1 h e de 15 m com as mesmas funcoes de reversao.py do
processo ao vivo e a mesma ordem de actualizacao (contexto de 1 h antes do gatilho
de 15 m), sem logica duplicada: a vela corrente nunca entra na ancora, no ajuste nem
no quantil empirico; z_prev e o valor registado no fecho anterior.

Comandos:
    python3 backtest_reversao.py --ativo BTC                        variante A, uma passagem
    python3 backtest_reversao.py --ativo BTC --walk --grelha        walk-forward com a grelha
    python3 backtest_reversao.py --ativo BTC --walk --nulo 1000 --placebo 10
    python3 backtest_reversao.py --ativo BTC --degradada-1h --walk  variante 1h-degradada

Convencoes
----------
- lado = +1 compra, -1 venda; custo positivo e custo; resultado positivo e a favor.
- nan = nao se sabe; ValueError = entrada impossivel.
- Toda a aleatoriedade passa por random.Random(semente): dois backtests com os mesmos
  dados e semente produzem ficheiros identicos (ensaios.csv e um diario e traz a data).
- Compativel com Python 3.9+. So standard library, nucleo, reversao e medidor.
- Nunca escreve nos ficheiros do medidor nem do sinalizador: so em dados/backtest/ e
  dados/ensaios.csv.
"""
from __future__ import annotations

import argparse
import configparser
import csv
import dataclasses
import itertools
import math
import os
import random
import statistics
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Sequence, Set, Tuple

import medidor as md
import nucleo as nu
import reversao as rv

VERSAO = "1.0"
BPS = nu.BPS
NAN = float("nan")
Vela = rv.Vela
MS_15M = 900_000
MS_1H = 3_600_000
MS_DIA = 86_400_000
CAMPOS_GRELHA = ("z_in", "z_out", "z_stop", "n_h", "z_min_resto", "k_h")
# seccao 11.4: 3 x 2 x 3 x 3 x 2 x 2 = 216 configuracoes
GRELHA_DECLARADA: Dict[str, Tuple[float, ...]] = {
    "z_in": (1.8, 2.0, 2.2),
    "z_out": (0.25, 0.5),
    "z_stop": (2.75, 3.0, 3.5),
    "n_h": (1.5, 2.0, 3.0),
    "z_min_resto": (0.5, 0.75),
    "k_h": (0.75, 1.0),
}
MOTIVOS_SAIDA = ("alvo", "stop", "tempo", "invalidacao")
ROTAS = ("agressiva", "passiva")
PASSO_T_CRIT = rv.PASSO_T_CRIT   # t_crit calibrado por patamares de N (velas de 1 h), a mesma regra do sinalizador
HORAS_NULO = 480            # horas de negociacao por caminho do nulo GBM, depois do aquecimento
SUB_PASSOS = 4              # passos do passeio por vela sintetica (o stop toca entre fechos)
N_CAMINHOS_P_NULO = 10_000  # seccao 11.6
VARIANTE_A = "A"
VARIANTE_B = "B"
VARIANTE_1H = "1h-degradada"
COLUNAS_ENRIQUECIDAS = ("v_b", "v_a", "n_grandes", "flx", "rep", "abs", "hhi", "raj", "twap", "oi_usd",
                        "funding", "premium", "liq_long", "liq_short", "pos", "fuel", "iman", "atraso_ms", "corte")


def _sinal(x: float) -> int:
    """Sinal de x em {-1, 0, +1}; 0 para nan."""
    if x != x:
        return 0
    return 1 if x > 0 else (-1 if x < 0 else 0)


def _finito(x: float) -> bool:
    return x == x and x not in (float("inf"), float("-inf"))


def quantil(xs: Sequence[float], p: float) -> float:
    """Quantil empirico com interpolacao linear (tipo 7, o mesmo de reversao); nan sem amostras."""
    ordenados = sorted(x for x in xs if x == x)
    n = len(ordenados)
    if n == 0 or not (0.0 <= p <= 1.0):
        return NAN
    pos = p * (n - 1)
    i = int(math.floor(pos))
    if i >= n - 1:
        return ordenados[-1]
    return ordenados[i] + (pos - i) * (ordenados[i + 1] - ordenados[i])


def _verdade(txt: str) -> bool:
    return (txt or "").strip().lower() in ("sim", "s", "yes", "true", "1")


def intervalo_ms(intervalo: str) -> int:
    """Duracao de um intervalo da Hyperliquid (1m, 5m, 15m, 1h, 4h, 1d, 1w) em ms; ValueError se desconhecido."""
    txt = (intervalo or "").strip().lower()
    unidades = {"m": 60_000, "h": MS_1H, "d": MS_DIA, "w": 7 * MS_DIA}
    if len(txt) < 2 or txt[-1] not in unidades or not txt[:-1].isdigit() or int(txt[:-1]) < 1:
        raise ValueError("intervalo desconhecido: %r" % intervalo)
    return int(txt[:-1]) * unidades[txt[-1]]


# ==========================================================================
# Parametros da grelha, custos, trade e dados (seccao 11.4)
# ==========================================================================
@dataclass(frozen=True)
class Params:
    """Parametros da regra: os seis da grelha primeiro; os restantes vem de config.ini e ficam fora da grelha.

    Cada campo entra em hash(): mudar um parametro fora da grelha e outro ensaio (seccao 12).
    """
    z_in: float = 2.0
    z_out: float = 0.5
    z_stop: float = 3.0
    n_h: float = 2.0
    z_min_resto: float = 0.75
    k_h: float = 1.0
    h_a: float = 96.0
    n_ajuste: int = 720
    n_min: int = 480
    n_max: int = 2160
    calibrar_nula: bool = True
    alpha_nula: float = 0.001
    replicas_nula: int = 1000
    semente_nula: int = 7
    t_nulo_max: float = -3.0
    t_amarelo: float = -2.0
    q_vr: int = 8
    vr_max_verde: float = 1.0
    vr_veto: float = 1.2
    zvr_veto: float = 2.0
    h_min: float = 3.0
    h_max: float = 24.0
    h_vc: float = 8.0
    h_vl: float = 96.0
    vol_razao_max: float = 2.0
    k_choque: float = 4.0
    k_dia: float = 2.0
    z_veto: float = 4.0
    n_z: int = 720
    z_in_emp_max: float = 2.5
    k_max_tecto: int = 64
    tmax_h: float = 16.0
    desloc_alvo_max: float = 0.5
    idade_ctx_max_min: float = 75.0
    arrefecimento_velas: int = 2
    g_min_x_custo: float = 3.0

    def hash(self) -> str:
        """hash_variante dos parametros (6 caracteres); muda com qualquer campo."""
        return rv.hash_variante(dataclasses.asdict(self))

    def grelha(self) -> Tuple[float, ...]:
        """Os seis valores da grelha, pela ordem de CAMPOS_GRELHA."""
        return tuple(float(getattr(self, c)) for c in CAMPOS_GRELHA)


def validar_params(p: Params) -> None:
    """ValueError se a geometria for impossivel: 0 <= z_out < z_in < z_stop <= z_veto, meias-vidas e janelas positivas."""
    if not (0.0 <= p.z_out < p.z_in < p.z_stop <= p.z_veto):
        raise ValueError("bandas impossiveis: e preciso 0 <= z_out < z_in < z_stop <= z_veto")
    if p.h_a <= 0 or p.h_vc <= 0 or p.h_vl < p.h_vc or p.n_h <= 0 or p.tmax_h <= 0 or p.k_h <= 0:
        raise ValueError("h_a, h_vc, h_vl, n_h, tmax_h e k_h tem de ser positivos (h_vl >= h_vc)")
    if not (0 < p.n_min <= p.n_max) or p.n_ajuste < 1 or p.n_z < 1 or p.k_max_tecto < 1:
        raise ValueError("janelas: 0 < n_min <= n_max, n_ajuste >= 1, n_z >= 1, k_max_tecto >= 1")
    if not (0 < p.h_min <= p.h_max) or p.z_min_resto < 0 or p.arrefecimento_velas < 0:
        raise ValueError("0 < h_min <= h_max, z_min_resto >= 0 e arrefecimento_velas >= 0")


def parametros_contexto(p: Params) -> rv.ParametrosContexto:
    return rv.ParametrosContexto(p.t_amarelo, p.vr_max_verde, p.vr_veto, p.zvr_veto, p.h_min, p.h_max,
                                 p.vol_razao_max, p.n_min, p.z_veto, p.desloc_alvo_max)


def parametros_gatilho(p: Params) -> rv.ParametrosGatilho:
    return rv.ParametrosGatilho(p.z_out, p.z_min_resto, p.z_veto, p.k_choque, p.vol_razao_max)


def grelha_declarada(base: Params, eixos: Optional[Dict[str, Sequence[float]]] = None) -> List[Params]:
    """As 216 configuracoes da seccao 11.4 (ou outros eixos) sobre os parametros fixos de `base`; a primeira e `base`."""
    eixos = dict(GRELHA_DECLARADA if eixos is None else eixos)
    nomes = [c for c in CAMPOS_GRELHA if c in eixos]
    saida: List[Params] = [base]
    vistos = {base}
    for valores in itertools.product(*[eixos[c] for c in nomes]):
        p = dataclasses.replace(base, **dict(zip(nomes, valores)))
        try:
            validar_params(p)
        except ValueError:
            continue
        if p not in vistos:
            vistos.add(p)
            saida.append(p)
    return saida


@dataclass(frozen=True)
class Custos:
    """Custos de defeito da seccao 11.2 em bps; substituidos pelos medidos pelo medidor quando existem."""
    taxa_taker: float = 4.5
    taxa_maker: float = 1.5
    imp_bps: float = 2.0
    pi_passiva: float = 0.6
    meio_spread_bps: float = 1.0

    def custo_entrada(self, rota: str) -> float:
        """Entrada agressiva (ou passiva que passou a taker): taker + imp + meio spread; passiva preenchida: maker."""
        if rota == "passiva":
            return self.taxa_maker
        if rota in ("agressiva", "passiva_taker"):
            return self.taxa_taker + self.imp_bps + self.meio_spread_bps
        raise ValueError("rota desconhecida: %r" % rota)

    def custo_saida(self, motivo: str) -> float:
        """Saida no alvo a maker (ordem passiva no nivel); stop, tempo e invalidacao a taker + imp."""
        if motivo == "alvo":
            return self.taxa_maker
        if motivo in MOTIVOS_SAIDA:
            return self.taxa_taker + self.imp_bps
        raise ValueError("motivo desconhecido: %r" % motivo)

    def c_w_c_l(self, f_esp: float = 0.0) -> Tuple[float, float]:
        """(65) com estes custos."""
        return rv.custos_defeito(self.taxa_taker, self.taxa_maker, self.imp_bps, f_esp)


@dataclass(frozen=True)
class Trade:
    """Um trade virtual fechado pelo motor (seccao 7.5 e (77)).

    O deslize do stop entra em custo_bps (taker + imp) e nao no preco de saida, para que
    r_bruto seja so o caminho do preco, como (77) escreve, e o nulo GBM o meca; em r_liq e o
    mesmo a menos de ln(1 - imp/BPS) contra imp/BPS. i_entrada e i_saida sao indices das
    velas avaliadas (15 m, ou 1 h na variante degradada).
    """
    i_entrada: int
    i_saida: int
    t_entrada_ms: int
    t_saida_ms: int
    lado: int
    rota: str                 # agressiva, passiva, passiva_taker
    p_entrada: float
    p_saida: float
    p_alvo: float
    p_stop: float
    a_0: float
    sigma_0: float
    phi_0: float
    h_0: float
    z_0: float
    d_0: float
    ext: float
    k_fora: int
    g_bps: float
    l_bps: float
    tmax_velas: int
    p_teo: float
    sigma_15: float
    motivo: str
    velas: int
    mae_bps: float
    mfe_bps: float
    r_bruto_bps: float
    funding_bps: float
    custo_bps: float
    r_liq_bps: float
    b_k: int = 0
    f_b: float = NAN
    t_nulo: float = NAN
    vr: float = NAN
    inval: str = ""           # motivo de (60) numa saida por invalidacao: nulo, vr, z_veto, ancora, fim_dados

    @property
    def ganhou(self) -> bool:
        return self.r_liq_bps > 0.0

    @property
    def dist_alvo_bps(self) -> float:
        """Distancia da entrada ao nivel do alvo em bps: o rotulo alvo-antes-de-stop do p_nulo (11.6)."""
        return BPS * abs(math.log(self.p_alvo / self.p_entrada))


@dataclass
class Dados:
    """Velas e funding de um activo para o walk-forward, o placebo e o relatorio."""
    ativo: str
    velas_1h: List[Vela]
    velas_15m: List[Vela]
    funding: Dict[int, float]
    colunas_baleias: Optional[List[Dict[str, float]]] = None
    degradada_1h: bool = False
    rota: str = "agressiva"
    semente: int = 7

    @property
    def velas(self) -> List[Vela]:
        return self.velas_1h if self.degradada_1h else self.velas_15m

    @property
    def velas_por_hora(self) -> int:
        return 1 if self.degradada_1h else 4

    @property
    def variante(self) -> str:
        if self.degradada_1h:
            return VARIANTE_1H
        return VARIANTE_B if self.colunas_baleias else VARIANTE_A


# ==========================================================================
# 11.3 Dados: velas e funding em disco
# ==========================================================================
def _ficheiros_velas(pasta: str, ativo: str, intervalo: str) -> List[str]:
    """Ficheiros <ATIVO>_<intervalo>_<AAAA-MM-DD>.csv da pasta, por ordem de dia."""
    prefixo = "%s_%s_" % (ativo, intervalo)
    if not os.path.isdir(pasta):
        return []
    nomes = [f for f in os.listdir(pasta)
             if f.startswith(prefixo) and f.endswith(".csv") and len(f) == len(prefixo) + 14]
    return [os.path.join(pasta, f) for f in sorted(nomes)]


def interpretar_vela(linha: Dict[str, Any], dt_ms: int) -> Optional[Vela]:
    """Vela do formato do canal candle/candleSnapshot (numeros ou texto); None sem t ou com precos impossiveis.

    T em falta vale t + dt; v e n em falta valem 0; a coluna corte (15 m enriquecido) marca completa = False.
    """
    t = md.flt(linha.get("t"))
    if not _finito(t):
        return None
    big_t = md.flt(linha.get("T"))
    o, h, l, c = (md.flt(linha.get(k)) for k in ("o", "h", "l", "c"))
    if not all(_finito(x) for x in (o, h, l, c)) or l <= 0.0 or h < l or not (l <= c <= h) or not (l <= o <= h):
        return None
    v = md.flt(linha.get("v"))
    n = md.flt(linha.get("n"))
    corte = _verdade(str(linha.get("corte") or ""))
    return Vela(int(t), int(big_t) if _finito(big_t) else int(t) + dt_ms, o, h, l, c,
                v if _finito(v) else 0.0, int(n) if _finito(n) else 0, not corte)


def _velas_e_linhas(pasta: str, ativo: str, intervalo: str) -> List[Tuple[Vela, Dict[str, Any]]]:
    dt = intervalo_ms(intervalo)
    por_t: Dict[int, Tuple[Vela, Dict[str, Any]]] = {}
    for caminho in _ficheiros_velas(pasta, ativo, intervalo):
        with open(caminho, "r", encoding="utf-8", newline="") as f:
            for ln in csv.DictReader(f):
                v = interpretar_vela(ln, dt)
                if v is not None:
                    por_t[v.t] = (v, ln)          # repetida: fica a ultima escrita
    saida: List[Tuple[Vela, Dict[str, Any]]] = []
    anterior: Optional[Vela] = None
    for t in sorted(por_t):
        v, ln = por_t[t]
        if anterior is not None and v.t != anterior.t + dt and v.completa:
            v = dataclasses.replace(v, completa=False)   # buraco antes desta vela
        saida.append((v, ln))
        anterior = v
    return saida


def carregar_velas(pasta: str, ativo: str, intervalo: str) -> List[Vela]:
    """Le dados/velas/<ATIVO>_<intervalo>_<dia>.csv: ordena por t, remove repetidas e marca buracos.

    A primeira vela depois de um buraco (t != T da anterior) e qualquer vela com corte ficam
    com completa = False; o motor nao entra nas 4 velas seguintes (G0) e nao reinicia a ancora.
    """
    return [v for v, _ in _velas_e_linhas(pasta, ativo, intervalo)]


def carregar_colunas_baleias(pasta: str, ativo: str) -> List[Dict[str, float]]:
    """Colunas enriquecidas das velas de 15 m, alinhadas com carregar_velas(pasta, ativo, '15m'); vazio vira nan."""
    saida = []
    for _, ln in _velas_e_linhas(pasta, ativo, "15m"):
        saida.append({k: md.flt(ln.get(k)) for k in COLUNAS_ENRIQUECIDAS})
    return saida


def contar_buracos(velas: Sequence[Vela]) -> int:
    """Velas marcadas como incompletas (buraco antes ou corte)."""
    return sum(1 for v in velas if not v.completa)


def carregar_funding(pasta: str, ativo: str) -> Dict[int, float]:
    """Le dados/funding/<ATIVO>.csv (time, fundingRate, premium) em {hora_ms: taxa por hora}; hora = time arredondado a hora."""
    caminho = os.path.join(pasta, ativo + ".csv")
    if not os.path.exists(caminho):
        return {}
    saida: Dict[int, float] = {}
    with open(caminho, "r", encoding="utf-8", newline="") as f:
        for ln in csv.DictReader(f):
            t = md.flt(ln.get("time"))
            taxa = md.flt(ln.get("fundingRate"))
            if _finito(t) and _finito(taxa):
                hora = int(t) - int(t) % MS_1H
                saida[hora] = taxa
    return saida


def filtrar_periodo(velas: Sequence[Vela], de_ms: Optional[int], ate_ms: Optional[int]) -> List[Vela]:
    """Velas com t >= de e T <= ate (limites ausentes nao filtram)."""
    return [v for v in velas if (de_ms is None or v.t >= de_ms) and (ate_ms is None or v.T <= ate_ms)]


def agregar_1h_de_15m(velas_15m: Sequence[Vela]) -> List[Vela]:
    """Velas de 1 h a partir de grupos de 4 velas de 15 m contiguas e alinhadas a hora (rv.agregar_1h); grupos incompletos ficam de fora."""
    saida: List[Vela] = []
    i = 0
    n = len(velas_15m)
    while i + 4 <= n:
        v = velas_15m[i]
        if v.t % MS_1H == 0 and all(velas_15m[i + j].t == v.t + j * MS_15M for j in range(4)):
            saida.append(rv.agregar_1h(velas_15m[i:i + 4]))
            i += 4
        else:
            i += 1
    return saida


def velas_de_trajectoria(x: Sequence[float], sub: int, t0_ms: int, dt_ms: int = MS_15M,
                         preco_base: float = 100.0) -> List[Vela]:
    """Velas a partir de um caminho de log-preco com `sub` passos por vela: o = primeiro ponto, c = ultimo, h e l = extremos do troco; T = t + dt - 1 como na Hyperliquid."""
    if sub < 1 or preco_base <= 0.0 or dt_ms <= 0:
        raise ValueError("sub >= 1, preco_base > 0 e dt_ms > 0")
    n = (len(x) - 1) // sub
    velas: List[Vela] = []
    for j in range(n):
        troco = x[j * sub: (j + 1) * sub + 1]
        velas.append(Vela(t0_ms + j * dt_ms, t0_ms + (j + 1) * dt_ms - 1, preco_base * math.exp(troco[0]),
                          preco_base * math.exp(max(troco)), preco_base * math.exp(min(troco)),
                          preco_base * math.exp(troco[-1]), 1.0, sub, True))
    return velas


# ==========================================================================
# Seccao 4: contexto de 1 h, calculado uma vez por serie e partilhado pela grelha
# ==========================================================================
@dataclass(frozen=True)
class Contexto1h:
    """Valores em vigor depois do fecho de uma vela de 1 h, lidos pelas velas de 15 m ate ao fecho seguinte."""
    t_ms: int              # T da vela de 1 h
    estado: str
    motivos: Tuple[str, ...]
    a: float               # A_ult (A_{t-u} no placebo)
    sigma_eq: float
    phi: float
    meia_vida: float
    t_nulo: float
    t_crit: float
    vr: float
    z_vr: float
    vol_razao: float
    z: float               # z_t com os valores em vigor
    q_z: float             # (30) quantil empirico de |z| ate t - 1
    f_hora: float          # funding por hora em vigor (nan sem historico)
    sigma_r: float
    n: int
    valido: bool


def _t_crit(p: Params, lam_a: float, n: int, cache: Dict[int, float]) -> float:
    """(11) t_crit calibrado para rv.n_calibracao(N) (patamares de PASSO_T_CRIT velas); t_nulo_max com a calibracao desligada ou N < n_min."""
    n_cal = rv.n_calibracao(n, p.n_min, p.n_max)
    if not p.calibrar_nula or n_cal == 0:
        return p.t_nulo_max
    if n_cal not in cache:
        cache[n_cal] = rv.calibrar_t_crit(n_cal, lam_a, p.replicas_nula, p.alpha_nula, p.semente_nula)
    return cache[n_cal]


def contexto_1h(params: Params, velas_1h: Sequence[Vela], velas_15m: Sequence[Vela] = (),
                funding: Optional[Dict[int, float]] = None, degradada_1h: bool = False,
                desloc_ancora: int = 0) -> List[Contexto1h]:
    """Contexto de 1 h (seccao 4) vela a vela: ancora, AR(1) sobre N = min(velas depois do aquecimento, n_max), VR, veto diario, estado (35), z_t e q_z.

    Como no sinalizador (ActivoSinal.fecho_1h), o desvio d so entra na janela do AR(1) depois
    de ceil(4 h_A) velas de aquecimento da ancora (o transitorio da correccao de arranque
    comprime os primeiros desvios); N cresce depois ate n_max e o ajuste so e valido com
    N >= n_min (4.3). vol_razao_15 e o estimador de Parkinson com as velas de 15 m fechadas
    antes do instante do fecho de 1 h (a quarta vela da hora ainda nao entrou, como no processo
    ao vivo); na variante degradada usa as velas de 1 h anteriores. q_z (30) usa os |z| dos fechos
    de 1 h anteriores, nunca o corrente, e so com rv.n_min_quantil_z(n_z, n_min) valores.
    sigma_eq, phi e H ficam em vigor ate ao proximo ajuste valido; a ancora e sempre a corrente.
    f_hora e a taxa de fundingHistory da hora que esta vela fecha (t + 1 h; T e t + 1 h - 1 ms).
    Com desloc_ancora = u > 0 a ancora usada em z e nos niveis e A_{t-u} (placebo da seccao
    11.6); o ajuste fica sobre o desvio verdadeiro. t_crit calibra-se por rv.n_calibracao
    (patamares de PASSO_T_CRIT velas de N, a regra do sinalizador).
    """
    validar_params(params)
    if desloc_ancora < 0:
        raise ValueError("desloc_ancora tem de ser nao negativo")
    lam_a = rv.lambda_ancora(params.h_a)
    cfg_ctx = parametros_contexto(params)
    funding = funding or {}
    anc = rv.Ancora(params.h_a)
    aquecimento = int(math.ceil(4.0 * params.h_a))            # como ActivoSinal.aquecimento
    min_z = rv.n_min_quantil_z(params.n_z, params.n_min)
    vol = rv.VolParkinson(params.h_vc, params.h_vl)
    ds: List[float] = []
    xs: List[float] = []
    rs: List[float] = []
    historico_a: Deque[float] = deque(maxlen=desloc_ancora + 1)
    zs: Deque[float] = deque(maxlen=params.n_z)
    cache_t_crit: Dict[int, float] = {}
    sigma_ult = phi_ult = h_ult = NAN
    x_abertura = NAN
    f_hora = NAN
    saida: List[Contexto1h] = []
    i15 = 0
    n15 = len(velas_15m)
    for vela in velas_1h:
        if not degradada_1h:
            while i15 < n15 and velas_15m[i15].T < vela.T:      # velas de 15 m fechadas antes deste instante
                vol.juntar(velas_15m[i15].h, velas_15m[i15].l)
                i15 += 1
        vol_razao = vol.vol_razao
        ln_c = math.log(vela.c)
        if vela.t % MS_DIA == 0:
            x_abertura = math.log(vela.o)                       # (26) abertura das 00:00 UTC
        d = anc.juntar(ln_c)
        historico_a.append(anc.valor)
        if anc.n > aquecimento:
            ds.append(d)
        if xs:
            rs.append(ln_c - xs[-1])
        xs.append(ln_c)
        n = min(len(ds), params.n_max)
        aj = rv.ajustar_ar1(ds[-n:], lam_a)
        janela_r = rs[-n:]
        sigma_r = rv.sigma_retorno(janela_r) if janela_r else NAN
        vr = z_vr = NAN
        if len(janela_r) >= 2 * params.q_vr:
            vr, z_vr = rv.racio_variancias(janela_r, params.q_vr)
        veto = rv.veto_dia(ln_c, x_abertura, sigma_r, params.k_dia)
        t_crit = _t_crit(params, lam_a, n, cache_t_crit)
        ctx = rv.classificar_contexto(aj, vr, z_vr, vol_razao, veto, t_crit, cfg_ctx)
        valido = bool(aj.valido and aj.sigma_eq == aj.sigma_eq and aj.sigma_eq > 0.0)
        if valido:
            sigma_ult, phi_ult, h_ult = aj.sigma_eq, aj.phi_c, aj.meia_vida
        a_ult = historico_a[0] if len(historico_a) > desloc_ancora else NAN
        z_t = rv.z_score(ln_c, a_ult, sigma_ult)
        q_z = rv.quantil_abs_z(list(zs)) if len(zs) >= min_z else NAN
        if z_t == z_t:
            zs.append(abs(z_t))
        f_hora = funding.get(vela.t + MS_1H, f_hora)              # fim da hora pela abertura
        if degradada_1h:
            vol.juntar(vela.h, vela.l)
        saida.append(Contexto1h(vela.T, ctx.estado, ctx.motivos, a_ult, sigma_ult, phi_ult, h_ult, aj.t_nulo,
                                t_crit, vr, z_vr, vol_razao, z_t, q_z, f_hora, sigma_r, n, valido))
    return saida


# ==========================================================================
# Seccoes 5 a 7: motor por eventos
# ==========================================================================
def _col(col: Optional[Dict[str, float]], nome: str) -> float:
    if not col:
        return NAN
    v = col.get(nome)
    if v is None:
        return NAN
    return md.flt(v)


class _Baleias:
    """Variante B: termos T1 a T8 e G6 a partir das colunas enriquecidas gravadas ao vivo (seccao 11.1).

    Leitura das colunas, que o sinalizador grava sem conhecer o lado do sinal futuro (a mesma
    convencao de ActivoSinal.fecho_15m): flx, rep e abs sao FLX_1h, REP_1h e o residuo
    (r_k - beta u_k)/s_e sem o factor lado; raj e twap sao o lado s em {-1, 0, +1} da ultima rajada
    dentro do silencio e do TWAP detectado (contra o sinal quando s = -lado); oi_usd, funding,
    liq_long, liq_short e pos como na seccao 6; fuel (ja dividido por OI) e calculado ao vivo para
    lado_med = -sign(z_k), que na vela de reentrada e o lado do sinal. Vazio vale nan e o termo 0.
    dt_ms e a duracao das velas lidas (15 m, ou 1 h na variante degradada): a hora fecha quando
    t + dt e multiplo de 1 h.
    """

    def __init__(self, dt_ms: int = MS_15M) -> None:
        self.dt_ms = dt_ms
        self.pos_flx = nu.JanelaPercentil(672)       # 7 dias de velas de 15 m
        self.pos_dpos = nu.JanelaPercentil(672)
        self.pos_doi8 = nu.JanelaPercentil(2880)
        self.ewma_vol = nu.Ewma(96.0)
        self.ewma_ant = NAN
        self.oi_hist: Deque[float] = deque(maxlen=8)
        self.pos_hist: Deque[float] = deque(maxlen=17)
        self.funding_hist: Deque[float] = deque(maxlen=720)
        self.max_liq = [NAN, NAN]
        self.u_k = NAN
        self.pos_flx_k = NAN
        self.pos_dpos_k = NAN
        self.pos_doi8_k = NAN
        self.dpos_k = NAN
        self.z_f = NAN

    def vela(self, vela: Vela, col: Optional[Dict[str, float]]) -> None:
        """Actualiza os acumuladores no fecho k; as posicoes percentis usam so velas anteriores."""
        vb, va = _col(col, "v_b"), _col(col, "v_a")
        if vb == vb and va == va:
            self.u_k = (vb - va) / self.ewma_ant if self.ewma_ant == self.ewma_ant and self.ewma_ant > 0.0 else NAN
            self.ewma_ant = self.ewma_vol.juntar(vb + va)
        else:
            self.u_k = NAN
        flx = _col(col, "flx")
        self.pos_flx_k = self.pos_flx.posicao(abs(flx)) if flx == flx else NAN
        if flx == flx:
            self.pos_flx.juntar(abs(flx))
        oi = _col(col, "oi_usd")
        doi8 = NAN
        if oi == oi and oi > 0.0 and len(self.oi_hist) == 8:
            maximo = max(self.oi_hist)
            doi8 = math.log(oi / maximo) if maximo > 0.0 else NAN
        self.pos_doi8_k = self.pos_doi8.posicao(doi8) if len(self.pos_doi8) >= 960 else NAN
        if doi8 == doi8:
            self.pos_doi8.juntar(doi8)
        if oi == oi:
            self.oi_hist.append(oi)
        pos = _col(col, "pos")
        if pos == pos:
            self.pos_hist.append(pos)
        self.dpos_k = (self.pos_hist[-1] - self.pos_hist[0]) if len(self.pos_hist) == 17 else NAN
        rel = abs(self.dpos_k) / oi if self.dpos_k == self.dpos_k and oi == oi and oi > 0.0 else NAN
        self.pos_dpos_k = self.pos_dpos.posicao(rel) if rel == rel else NAN
        if rel == rel:
            self.pos_dpos.juntar(rel)
        f = _col(col, "funding")
        self.z_f = rv.z_robusto(f, list(self.funding_hist)) if f == f else NAN
        if f == f and (vela.t + self.dt_ms) % MS_1H == 0:
            self.funding_hist.append(f)

    def excursao(self, exc: rv.Excursao, col: Optional[Dict[str, float]]) -> None:
        """Maximo de liquidacoes por lado desde o inicio da excursao (53)."""
        ll, ls = _col(col, "liq_long"), _col(col, "liq_short")
        if exc.estado == rv.FORA and exc.k_fora == 1:
            self.max_liq = [ll, ls]
        elif exc.estado == rv.FORA:
            for i, v in enumerate((ll, ls)):
                if v == v:
                    self.max_liq[i] = v if self.max_liq[i] != self.max_liq[i] else max(self.max_liq[i], v)

    def avaliar(self, lado: int, col: Optional[Dict[str, float]], exc: rv.Excursao,
                cfg_b: rv.ParametrosBaleias) -> Tuple[bool, int, float]:
        """(G6, B_k, f_B) no fecho da reentrada."""
        raj = _col(col, "raj")
        if raj == raj and int(raj) == -lado:
            return False, 0, NAN
        liq_contra = _col(col, "liq_long") if lado > 0 else _col(col, "liq_short")
        liq = rv.racio_liquidacoes(liq_contra, self.max_liq[0] if lado > 0 else self.max_liq[1])
        oi = _col(col, "oi_usd")
        doi_exc = oi / exc.oi_inicio - 1.0 if oi == oi and exc.oi_inicio == exc.oi_inicio and exc.oi_inicio > 0 else NAN
        s_twap = _col(col, "twap")
        twap = 0
        if s_twap == s_twap and int(s_twap) != 0:
            twap = -1 if int(s_twap) == -lado else 1
        a_col = _col(col, "abs")
        a_k = lado * a_col if a_col == a_col else NAN
        rep = _col(col, "rep")
        b_k, _ = rv.pontuar_baleias(lado, _col(col, "flx"), self.pos_flx_k, int(rep) if rep == rep else NAN,
                                    self.z_f, doi_exc, liq, self.dpos_k, self.pos_dpos_k, twap, a_k, self.u_k,
                                    self.pos_doi8_k, cfg_b)
        if b_k <= cfg_b.b_veto:
            return False, b_k, NAN
        return True, b_k, rv.factor_tamanho(b_k, _col(col, "fuel"), cfg_b)


class _Motor:
    """Estado de um activo no backtest: o que ActivoSinal guarda ao vivo, sem rede nem relogio."""

    def __init__(self, params: Params, custos: Custos, funding: Dict[int, float], contexto: Sequence[Contexto1h],
                 velas: Sequence[Vela], colunas: Optional[Sequence[Dict[str, float]]], degradada_1h: bool,
                 portao: bool, rota: str, semente: int, cfg_baleias: Optional[rv.ParametrosBaleias]):
        self.p = params
        self.custos = custos
        self.funding = funding
        self.contexto = list(contexto)
        self.velas = list(velas)
        self.colunas = list(colunas) if colunas is not None else None
        self.degradada = degradada_1h
        self.portao = portao
        self.rota = rota
        self.lam_a = rv.lambda_ancora(params.h_a)
        self.cfg_ctx = parametros_contexto(params)
        self.cfg_g = parametros_gatilho(params)
        self.cfg_b = cfg_baleias or rv.ParametrosBaleias()
        self.vol = rv.VolParkinson(params.h_vc, params.h_vl)
        self.exc = rv.Excursao()
        self.rng = random.Random(semente)
        self.dt_ms = MS_1H if degradada_1h else MS_15M
        self.baleias = _Baleias(self.dt_ms) if self.colunas is not None else None
        self.trades: List[Trade] = []
        self.motivos_recusa: Dict[str, int] = {}
        self.ctx: Optional[Contexto1h] = None
        self.tv: Optional[rv.TradeVirtual] = None
        self.aberto: Optional[Dict[str, Any]] = None
        self.pend_inval: Optional[str] = None
        self.pend_passiva: Optional[int] = None
        self.bloqueio_ate = -1
        self.z_prev = NAN
        self.ln_c_prev = NAN

    # ---- ciclo ----------------------------------------------------------------
    def correr(self) -> List[Trade]:
        i_ctx = 0
        n_ctx = len(self.contexto)
        ultima = -1
        for k, vela in enumerate(self.velas):
            while i_ctx < n_ctx and self.contexto[i_ctx].t_ms <= vela.T:   # 1 h antes de 15 m
                self._fecho_1h(self.contexto[i_ctx])
                i_ctx += 1
            self._fecho_vela(k, vela)
            ultima = k
        if self.tv is not None and ultima >= 0:                             # fim dos dados: sai ao ultimo fecho
            vela = self.velas[ultima]
            self._fechar(self.tv.invalidar("invalidacao", vela.c, vela.T), ultima, "fim_dados")
        return self.trades

    def _fecho_1h(self, ctx: Contexto1h) -> None:
        self.ctx = ctx
        if self.tv is None or self.pend_inval is not None or self.aberto is None:
            return
        ab = self.aberto
        # (60) o alvo e a banda z_out do mesmo lado da entrada, P_alvo = exp(A - lado z_out sigma_0): a ancora
        # a afastar-se do lado em que o trade aposta (a descer numa compra) encurta o destino esperado;
        # deslocacao contra o trade = -lado (A_t - A_0) / sigma_0, positiva quando e contra (rv.invalidar)
        desloc = -ab["lado"] * (ctx.a - ab["a_0"]) / ab["sigma_0"] if ctx.a == ctx.a else NAN
        motivo = rv.invalidar(ctx.t_nulo if self.portao else NAN, ctx.vr if self.portao else NAN, ctx.z_vr,
                              ctx.z, desloc, self.cfg_ctx)
        if motivo:
            self.pend_inval = motivo

    def _fecho_vela(self, k: int, vela: Vela) -> None:
        ln_c = math.log(vela.c)
        r_k = ln_c - self.ln_c_prev if self.ln_c_prev == self.ln_c_prev else NAN
        col = self.colunas[k] if self.colunas is not None else None
        if self.tv is not None and self.aberto is not None:                 # 1. o trade virtual avanca
            f_hora = 0.0
            fim = vela.t + self.dt_ms                                        # fim pela abertura (T e t + dt - 1)
            if fim % MS_1H == 0 and self.aberto["t_entrada"] < fim:
                f_hora = self.funding.get(fim, 0.0)                         # marca de hora com a posicao aberta (como ao vivo)
            s = self.tv.avancar(vela, NAN, NAN, f_hora)
            inval = ""
            if s is None and self.pend_inval is not None:                   # (60) sai ao fecho seguinte
                inval = self.pend_inval
                s = self.tv.invalidar("invalidacao", vela.c, vela.T)
            if s is not None:
                self._fechar(s, k, inval)
        self.vol.juntar(vela.h, vela.l)                                     # 2. acumuladores de 15 m
        if self.baleias is not None:
            self.baleias.vela(vela, col)
        ctx = self.ctx
        if ctx is None or not (ctx.a == ctx.a and ctx.sigma_eq == ctx.sigma_eq and ctx.sigma_eq > 0.0):
            self.z_prev = NAN
            self.ln_c_prev = ln_c
            self.pend_passiva = None
            return
        z_k = rv.z_score(ln_c, ctx.a, ctx.sigma_eq)                          # (36) com A_ult registado
        z_in_ef = rv.z_in_efectivo(self.p.z_in, ctx.q_z, self.p.z_in_emp_max)
        prev = (self.exc.estado, self.exc.lado_exc, self.exc.ext, self.exc.k_fora)
        self.exc.actualizar(z_k, z_in_ef, _col(col, "oi_usd"))
        if self.baleias is not None:
            self.baleias.excursao(self.exc, col)
        if self.pend_passiva is not None:                                   # 3. passiva sem preenchimento
            lado = self.pend_passiva
            self.pend_passiva = None
            if self.tv is None:
                geo = self._condicao_mantida(lado, k, vela, z_k, z_in_ef, r_k)
                if geo is not None:
                    self._entrar(k, vela, lado, "passiva_taker", geo, 0, NAN)
        elif self.tv is None:                                               # 4. gatilho
            self._avaliar(k, vela, z_k, z_in_ef, prev, r_k, col)
        self.z_prev = z_k
        self.ln_c_prev = ln_c

    # ---- condicoes G0 a G8 ---------------------------------------------------
    def _recusar(self, motivo: str) -> None:
        self.motivos_recusa[motivo] = self.motivos_recusa.get(motivo, 0) + 1

    def _g0(self, k: int, vela: Vela) -> bool:
        """G0: 4 velas completas (sem buraco nem corte), n >= 1 e contexto com menos de idade_ctx_max_min."""
        if k < 3 or vela.n < 1 or self.ctx is None:
            return False
        if any(not self.velas[j].completa for j in range(k - 3, k + 1)):
            return False
        return vela.T - self.ctx.t_ms <= self.p.idade_ctx_max_min * 60_000

    def _k_max(self) -> int:
        assert self.ctx is not None
        k_max = rv.k_maximo(self.ctx.meia_vida, self.p.k_h, self.p.k_max_tecto)
        return max(1, int(round(k_max / 4.0))) if self.degradada else k_max

    def _geometria(self, lado: int, z_k: float, vela: Vela) -> Optional[Dict[str, float]]:
        """Niveis e custos congelados na entrada (seccao 7); None se |z_0| nao fica entre z_out e z_stop."""
        ctx = self.ctx
        assert ctx is not None
        p = self.p
        if not (p.z_out <= abs(z_k) < p.z_stop):
            # NOTA ESPEC: (31) pode levar z_in_ef acima de z_stop com caudas pesadas e a reentrada ficaria
            # para la do stop; segue-se stop_bps (ValueError com |z_0| >= z_stop): sem sinal nesse fecho.
            return None
        d0 = math.log(vela.c) - ctx.a
        tau, tmax_15 = rv.tempo_maximo(ctx.meia_vida, p.n_h, p.tmax_h)
        tmax = max(1, int(round(tau))) if self.degradada else max(1, tmax_15)
        f_ult = ctx.f_hora if ctx.f_hora == ctx.f_hora else 0.0
        f_esp = rv.funding_esperado_bps(lado, f_ult, tau)
        c_w, c_l = self.custos.c_w_c_l(f_esp)
        return {
            "d_0": d0, "tau": tau, "tmax": tmax,
            "g_bps": rv.alvo_bps(d0, ctx.sigma_eq, ctx.phi, self.lam_a, tau, p.z_out),
            "l_bps": rv.stop_bps(z_k, ctx.sigma_eq, p.z_stop),
            "c_w": c_w, "c_l": c_l,
            "p_alvo": rv.preco_alvo(ctx.a, ctx.sigma_eq, lado, p.z_out),       # banda z_out do mesmo lado
            "p_stop": rv.preco_stop(ctx.a, ctx.sigma_eq, lado, p.z_stop),
            "p_teo": rv.prob_alvo_antes_stop(z_k, p.z_out, p.z_stop),
        }

    def _g8(self, geo: Dict[str, float]) -> bool:
        return (not self.portao) or geo["g_bps"] >= self.p.g_min_x_custo * geo["c_l"]

    def _avaliar(self, k: int, vela: Vela, z_k: float, z_in_ef: float, prev: Tuple[str, int, float, int],
                 r_k: float, col: Optional[Dict[str, float]]) -> None:
        """G0 a G8 no fecho k, pela ordem da seccao 5.3; a primeira que falha fica em motivos_recusa."""
        ctx = self.ctx
        assert ctx is not None
        if not self.exc.terminou:                      # so ha reentrada possivel quando uma excursao acaba
            return
        if not self._g0(k, vela):
            self._recusar("G0")
            return
        if self.portao and ctx.estado != rv.VERDE:
            self._recusar("G1")
            return
        lado, motivo = rv.gatilho_reentrada(prev[0], prev[1], prev[2], prev[3], self._k_max(), self.z_prev, z_k,
                                            z_in_ef, r_k, self.vol.sigma_15, self.vol.vol_razao, self.cfg_g)
        if lado == 0:
            self._recusar(motivo)
            return
        b_k, f_b = 0, NAN
        if self.baleias is not None:
            ok, b_k, f_b = self.baleias.avaliar(lado, col, self.exc, self.cfg_b)
            if not ok:
                self._recusar("G6")
                return
        if k <= self.bloqueio_ate:                     # G7: arrefecimento apos um stop
            self._recusar("G7")
            return
        geo = self._geometria(lado, z_k, vela)
        if geo is None or not self._g8(geo):
            self._recusar("G8")
            return
        if self.rota == "passiva":
            if self.rng.random() < self.custos.pi_passiva:
                self._entrar(k, vela, lado, "passiva", geo, b_k, f_b)
            else:
                self.pend_passiva = lado               # tenta a taker na vela seguinte
            return
        self._entrar(k, vela, lado, "agressiva", geo, b_k, f_b)

    def _condicao_mantida(self, lado: int, k: int, vela: Vela, z_k: float, z_in_ef: float,
                          r_k: float) -> Optional[Dict[str, float]]:
        """Seccao 11.2: a passiva nao preencheu; entra a taker em k + 1 se a condicao se mantiver.

        Mantem-se se a vela continua dentro da banda do mesmo lado, com caminho ate z_out (G3),
        sem choque (G5), com dados (G0), contexto VERDE (G1) e geometria que paga (G8).
        """
        p = self.p
        za = abs(z_k)
        if not (za < z_in_ef and _sinal(z_k) == -lado and za >= p.z_out + p.z_min_resto):
            return None
        if not (r_k == r_k and self.vol.sigma_15 == self.vol.sigma_15 and abs(r_k) <= p.k_choque * self.vol.sigma_15
                and self.vol.vol_razao == self.vol.vol_razao and self.vol.vol_razao <= p.vol_razao_max):
            return None
        if not self._g0(k, vela) or (self.portao and self.ctx is not None and self.ctx.estado != rv.VERDE):
            return None
        geo = self._geometria(lado, z_k, vela)
        if geo is None or not self._g8(geo):
            return None
        return geo

    # ---- entrada e saida ------------------------------------------------------
    def _entrar(self, k: int, vela: Vela, lado: int, rota: str, geo: Dict[str, float], b_k: int, f_b: float) -> None:
        ctx = self.ctx
        assert ctx is not None
        self.tv = rv.TradeVirtual(lado, vela.c, geo["p_alvo"], geo["p_stop"], int(geo["tmax"]), 0.0)
        self.aberto = {
            "k": k, "t_entrada": vela.T, "lado": lado, "rota": rota, "p_entrada": vela.c,
            "p_alvo": geo["p_alvo"], "p_stop": geo["p_stop"],
            "a_0": ctx.a, "sigma_0": ctx.sigma_eq, "phi_0": ctx.phi, "h_0": ctx.meia_vida,
            "z_0": rv.z_score(math.log(vela.c), ctx.a, ctx.sigma_eq), "d_0": geo["d_0"],
            "ext": self.exc.ext, "k_fora": self.exc.k_fora, "g_bps": geo["g_bps"], "l_bps": geo["l_bps"],
            "tmax": int(geo["tmax"]), "p_teo": geo["p_teo"], "sigma_15": self.vol.sigma_15,
            "b_k": b_k, "f_b": f_b, "t_nulo": ctx.t_nulo, "vr": ctx.vr,
            "custo_entrada": self.custos.custo_entrada(rota),
        }
        self.pend_inval = None

    def _fechar(self, s: rv.Saida, k: int, inval: str = "") -> None:
        ab = self.aberto
        assert ab is not None
        custo = ab["custo_entrada"] + self.custos.custo_saida(s.motivo)
        r_bruto, r_liq = rv.resultado_trade(ab["lado"], ab["p_entrada"], s.preco, s.funding_bps, custo)
        self.trades.append(Trade(
            ab["k"], k, ab["t_entrada"], s.t_ms, ab["lado"], ab["rota"], ab["p_entrada"], s.preco, ab["p_alvo"],
            ab["p_stop"], ab["a_0"], ab["sigma_0"], ab["phi_0"], ab["h_0"], ab["z_0"], ab["d_0"], ab["ext"],
            ab["k_fora"], ab["g_bps"], ab["l_bps"], ab["tmax"], ab["p_teo"], ab["sigma_15"], s.motivo, s.velas,
            s.mae_bps, s.mfe_bps, r_bruto, s.funding_bps, custo, r_liq, ab["b_k"], ab["f_b"], ab["t_nulo"], ab["vr"],
            inval))
        if s.motivo == "stop":
            self.bloqueio_ate = k + self.p.arrefecimento_velas
        self.tv = None
        self.aberto = None
        self.pend_inval = None


def simular(params: Params, velas_1h: List[Vela], velas_15m: List[Vela], funding: Dict[int, float], custos: Custos,
            colunas_baleias: Optional[List[dict]] = None, degradada_1h: bool = False, portao: bool = True,
            rota: str = "agressiva", semente: int = 7, desloc_ancora: int = 0,
            contexto: Optional[Sequence[Contexto1h]] = None,
            cfg_baleias: Optional[rv.ParametrosBaleias] = None) -> List[Trade]:
    """Motor por eventos (seccoes 4 a 7 e 11.2) com as funcoes de reversao.py e a ordem 1 h antes de 15 m.

    Convencoes conservadoras: entrada ao fecho da vela de reentrada; alvo so se o fecho passa o nivel
    (preco_alvo, banda z_out do mesmo lado); stop se a minima ou maxima toca, e alvo e stop na mesma vela
    contam stop; tempo ao fecho da vela tmax_15; invalidacao (60) detectada no fecho de 1 h e executada
    ao fecho seguinte; funding em cada marca de hora (t + dt multiplo de 1 h) posterior a entrada com a
    posicao aberta, com o sinal (64), como no sinalizador. Rota passiva: preenche ao fecho com probabilidade pi
    (random.Random(semente)), senao entra a taker na vela seguinte se a condicao se mantiver.
    portao=False (so para o nulo GBM) desliga G1, G8 e a invalidacao por nulo e por VR, e deixa
    o resto da regra. Sem velas_1h, agregam-se das de 15 m. Com degradada_1h o gatilho avalia-se
    nos fechos de 1 h (z_k -> z_t) e tmax e k_max passam a velas de 1 h. desloc_ancora = u e o placebo
    da seccao 11.6; contexto permite partilhar o contexto de 1 h entre as configuracoes da grelha.
    """
    validar_params(params)
    if rota not in ROTAS:
        raise ValueError("rota tem de ser agressiva ou passiva")
    funding = funding or {}
    velas_1h = list(velas_1h)
    velas_15m = list(velas_15m)
    if not velas_1h and velas_15m:
        velas_1h = agregar_1h_de_15m(velas_15m)
    velas = velas_1h if degradada_1h else velas_15m
    if colunas_baleias is not None and len(colunas_baleias) != len(velas):
        raise ValueError("colunas_baleias tem de ter uma linha por vela avaliada")
    if contexto is None:
        contexto = contexto_1h(params, velas_1h, velas_15m, funding, degradada_1h, desloc_ancora)
    motor = _Motor(params, custos, funding, contexto, velas, colunas_baleias, degradada_1h, portao, rota, semente,
                   cfg_baleias)
    return motor.correr()


def simular_com_recusas(params: Params, dados: Dados, custos: Custos, portao: bool = True, desloc_ancora: int = 0,
                        contexto: Optional[Sequence[Contexto1h]] = None) -> Tuple[List[Trade], Dict[str, int]]:
    """simular sobre Dados, devolvendo tambem a contagem dos motivos de recusa (G0..G8) para o relatorio."""
    velas_1h = dados.velas_1h or agregar_1h_de_15m(dados.velas_15m)
    if contexto is None:
        contexto = contexto_1h(params, velas_1h, dados.velas_15m, dados.funding, dados.degradada_1h, desloc_ancora)
    motor = _Motor(params, custos, dados.funding, contexto, dados.velas, dados.colunas_baleias, dados.degradada_1h,
                   portao, dados.rota, dados.semente, None)
    trades = motor.correr()
    return trades, dict(motor.motivos_recusa)


# ==========================================================================
# 11.5 Metricas por trade (sem anualizacao)
# ==========================================================================
def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Correlacao de Spearman com empates a meio; nan com menos de 3 pares ou sem variacao."""
    pares = [(x, y) for x, y in zip(xs, ys) if x == x and y == y]
    n = len(pares)
    if n < 3:
        return NAN

    def ranks(vs: List[float]) -> List[float]:
        ordem = sorted(range(n), key=lambda i: vs[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vs[ordem[j + 1]] == vs[ordem[i]]:
                j += 1
            meio = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[ordem[k]] = meio
            i = j + 1
        return r

    rx = ranks([p[0] for p in pares])
    ry = ranks([p[1] for p in pares])
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx <= 0.0 or syy <= 0.0:
        return NAN
    return sxy / math.sqrt(sxx * syy)


def _calibracao(trades: Sequence[Trade], largura: float = 0.25) -> Tuple[float, float]:
    """(ECE, Brier) de P_teo contra o rotulo alvo por caixas de |z_0| de `largura`; nan sem trades."""
    pares = [(t.p_teo, 1.0 if t.motivo == "alvo" else 0.0, abs(t.z_0)) for t in trades if t.p_teo == t.p_teo]
    n = len(pares)
    if n == 0:
        return NAN, NAN
    brier = sum((p - y) ** 2 for p, y, _ in pares) / n
    caixas: Dict[int, List[Tuple[float, float]]] = {}
    for p, y, z in pares:
        caixas.setdefault(int(math.floor(z / largura)), []).append((p, y))
    ece = 0.0
    for grupo in caixas.values():
        m = len(grupo)
        ece += m / n * abs(sum(y for _, y in grupo) / m - sum(p for p, _ in grupo) / m)
    return ece, brier


def metricas(trades: Sequence[Trade], c_w: float, c_l: float) -> dict:
    """Seccao 11.5: n, expectancia e EP de r_liq, p_hat, p_inf, p* (G e L medianos com c_w e c_l), margem, PF, ganho/perda, quantis, MAE, MFE, DD, perdas seguidas, duracao, motivos, SR, g3, g4, Spearman, ECE, Brier."""
    n = len(trades)
    met: Dict[str, Any] = {
        "n": n, "expectancia": NAN, "ep": NAN, "t": NAN, "exp_bruta": NAN, "ep_bruta": NAN, "custo_medio": NAN,
        "funding_medio": NAN, "p_hat": NAN, "p_inf": NAN, "p_estrela": NAN, "margem": NAN, "pf": NAN,
        "ganho_medio": NAN, "perda_media": NAN, "razao_ganho_perda": NAN, "mediana": NAN, "q05": NAN, "q95": NAN,
        "mae": NAN, "mfe": NAN, "dd_max": NAN, "perdas_seguidas": 0, "duracao_media": NAN,
        "motivos": {m: NAN for m in MOTIVOS_SAIDA}, "sinais_por_dia": NAN, "custo_por_dia": NAN, "dias": NAN,
        "sr": NAN, "g3": NAN, "g4": NAN, "spearman_z0": NAN, "ece": NAN, "brier": NAN,
        "g_mediano": NAN, "l_mediano": NAN, "tmax_mediano": NAN, "sigma_15_mediano": NAN, "dist_alvo_mediana": NAN,
        "por_rota": {},
    }
    if n == 0:
        return met
    r = [t.r_liq_bps for t in trades]
    brutos = [t.r_bruto_bps for t in trades]
    media = statistics.fmean(r)
    sd = statistics.stdev(r) if n >= 2 else NAN
    ep = sd / math.sqrt(n) if sd == sd else NAN
    ganhos = [x for x in r if x > 0.0]
    perdas = [x for x in r if x <= 0.0]
    p_hat = len(ganhos) / n
    g_med = nu.mediana([t.g_bps for t in trades])
    l_med = nu.mediana([t.l_bps for t in trades])
    acum = 0.0
    pico = 0.0
    dd = 0.0
    seguidas = 0
    pior_serie = 0
    for x in r:
        acum += x
        pico = max(pico, acum)
        dd = max(dd, pico - acum)
        seguidas = seguidas + 1 if x <= 0.0 else 0
        pior_serie = max(pior_serie, seguidas)
    t_ini = min(t.t_entrada_ms for t in trades)
    t_fim = max(t.t_saida_ms for t in trades)
    dias = max((t_fim - t_ini) / MS_DIA, 1.0 / 96.0)
    ece, brier = _calibracao(trades)
    sr, g3, g4 = rv.sharpe_trade(r)
    met.update({
        "expectancia": media, "ep": ep, "t": media / ep if ep == ep and ep > 0.0 else NAN,
        "exp_bruta": statistics.fmean(brutos),
        "ep_bruta": statistics.stdev(brutos) / math.sqrt(n) if n >= 2 else NAN,
        "custo_medio": statistics.fmean([t.custo_bps for t in trades]),
        "funding_medio": statistics.fmean([t.funding_bps for t in trades]),
        "p_hat": p_hat, "p_inf": rv.wilson_inferior(p_hat, n), "margem": rv.margem_decisao(p_hat, n),
        "p_estrela": nu.acerto_equilibrio(g_med, l_med, c_w, c_l),
        "pf": (sum(ganhos) / -sum(perdas)) if perdas and sum(perdas) < 0.0 else (float("inf") if ganhos else NAN),
        "ganho_medio": statistics.fmean(ganhos) if ganhos else NAN,
        "perda_media": statistics.fmean(perdas) if perdas else NAN,
        "razao_ganho_perda": (statistics.fmean(ganhos) / -statistics.fmean(perdas))
        if ganhos and perdas and statistics.fmean(perdas) < 0.0 else NAN,
        "mediana": nu.mediana(r), "q05": quantil(r, 0.05), "q95": quantil(r, 0.95),
        "mae": statistics.fmean([t.mae_bps for t in trades]), "mfe": statistics.fmean([t.mfe_bps for t in trades]),
        "dd_max": dd, "perdas_seguidas": pior_serie,
        "duracao_media": statistics.fmean([t.velas for t in trades]),
        "motivos": {m: sum(1 for t in trades if t.motivo == m) / n for m in MOTIVOS_SAIDA},
        "sinais_por_dia": n / dias, "custo_por_dia": sum(t.custo_bps + t.funding_bps for t in trades) / dias,
        "dias": dias, "sr": sr, "g3": g3, "g4": g4,
        "spearman_z0": _spearman([abs(t.z_0) for t in trades], r),
        "ece": ece, "brier": brier, "g_mediano": g_med, "l_mediano": l_med,
        "tmax_mediano": nu.mediana([float(t.tmax_velas) for t in trades]),
        "sigma_15_mediano": nu.mediana([t.sigma_15 for t in trades]),
        "dist_alvo_mediana": nu.mediana([t.dist_alvo_bps for t in trades]),
        "por_rota": {rt: sum(1 for t in trades if t.rota == rt) for rt in sorted({t.rota for t in trades})},
    })
    return met


def consistencia_4_trocos(trades: Sequence[Trade], n_trocos: int = 4) -> List[float]:
    """Seccao 11.7 (7): expectancia de r_liq por troco cronologico (pela entrada); nan num troco vazio."""
    if n_trocos < 1:
        raise ValueError("n_trocos tem de ser pelo menos 1")
    ordenados = sorted(trades, key=lambda t: (t.t_entrada_ms, t.i_entrada))
    n = len(ordenados)
    saida: List[float] = []
    for j in range(n_trocos):
        troco = ordenados[j * n // n_trocos:(j + 1) * n // n_trocos]
        saida.append(statistics.fmean([t.r_liq_bps for t in troco]) if troco else NAN)
    return saida


# ==========================================================================
# 11.3 e 11.4 Walk-forward, grelha e registo de ensaios
# ==========================================================================
def janelas_walk_forward(n_velas: int, is_velas: int, oos_velas: int, purga_velas: int,
                         embargo_frac: float) -> List[Tuple[int, int, int, int]]:
    """Janelas rolantes (is_ini, is_fim, oos_ini, oos_fim), indices [ini, fim) de velas.

    oos_ini = is_fim + purga + embargo, com embargo = ceil(embargo_frac x n_velas); o passo entre
    janelas e oos_velas, logo as OOS encadeiam-se sem sobreposicao. A ultima OOS pode ficar truncada
    desde que tenha pelo menos metade de oos_velas (208 dias de 1 h dao as 4 janelas da seccao 11.3).
    """
    if is_velas < 1 or oos_velas < 1 or purga_velas < 0 or not (0.0 <= embargo_frac < 1.0) or n_velas < 0:
        raise ValueError("is_velas >= 1, oos_velas >= 1, purga_velas >= 0 e embargo_frac em [0, 1)")
    embargo = int(math.ceil(embargo_frac * n_velas))
    janelas: List[Tuple[int, int, int, int]] = []
    j = 0
    minimo = max(1, (oos_velas + 1) // 2)
    while True:
        is_ini = j * oos_velas
        is_fim = is_ini + is_velas
        oos_ini = is_fim + purga_velas + embargo
        oos_fim = min(oos_ini + oos_velas, n_velas)
        if oos_fim - oos_ini < minimo:
            break
        janelas.append((is_ini, is_fim, oos_ini, oos_fim))
        if oos_ini + oos_velas >= n_velas:
            break
        j += 1
    return janelas


def _vizinhos(p: Params, eixos: Dict[str, List[float]], existentes: Dict[Params, float]) -> List[Params]:
    """Configuracoes a um passo de p em cada eixo da grelha que existem nos resultados."""
    saida: List[Params] = []
    for campo, valores in eixos.items():
        v = float(getattr(p, campo))
        if v not in valores:
            continue
        i = valores.index(v)
        for j in (i - 1, i + 1):
            if 0 <= j < len(valores):
                q = dataclasses.replace(p, **{campo: valores[j]})
                if q in existentes:
                    saida.append(q)
    return saida


def patamar(resultados: Dict[Params, float]) -> Params:
    """Seccao 11.4: centro de um patamar, nunca o maximo.

    Candidatas: configuracoes com expectancia positiva cujos vizinhos a um passo em cada eixo da
    grelha (os que existem) tambem sao positivos; escolhe-se a de maior media da vizinhanca
    (a propria mais os vizinhos). Sem patamar, a maior media da vizinhanca; empates pelo hash.
    """
    if not resultados:
        raise ValueError("sem resultados para escolher")
    eixos: Dict[str, List[float]] = {}
    for campo in CAMPOS_GRELHA:
        valores = sorted({float(getattr(p, campo)) for p in resultados})
        if len(valores) > 1:
            eixos[campo] = valores
    pontuados: List[Tuple[float, str, Params]] = []
    candidatas: List[Tuple[float, str, Params]] = []
    for p, r in resultados.items():
        viz = _vizinhos(p, eixos, resultados)
        valores = [r] + [resultados[q] for q in viz]
        conhecidos = [v for v in valores if v == v]
        media = statistics.fmean(conhecidos) if conhecidos else NAN
        chave = (media if media == media else float("-inf"), p.hash(), p)
        pontuados.append(chave)
        if r == r and r > 0.0 and all(resultados[q] == resultados[q] and resultados[q] > 0.0 for q in viz):
            candidatas.append(chave)
    escolha = candidatas or pontuados
    return max(escolha, key=lambda c: (c[0], -int(c[1], 16)))[2]


COLUNAS_ENSAIOS = (["data", "ativo", "variante", "rota", "hash"] + list(CAMPOS_GRELHA)
                   + ["h_a", "n_ajuste", "n", "expectancia_bps", "ep_bps", "sr", "p_hat", "periodo"])


def chave_ensaio(hash_: str, ativo: str, variante: str, rota: str, periodo: str) -> Tuple[str, str, str, str, str]:
    """Identidade de um ensaio em ensaios.csv: a mesma configuracao sobre o mesmo activo, variante, rota e periodo conta uma vez."""
    return (str(hash_ or ""), str(ativo or ""), str(variante or ""), str(rota or ""), str(periodo or ""))


def ler_ensaios(caminho: str) -> List[Dict[str, str]]:
    """Linhas de dados/ensaios.csv (as do backtest e as do sinalizador, que nao tem ativo nem periodo); vazio sem ficheiro."""
    if not os.path.exists(caminho):
        return []
    with open(caminho, "r", encoding="utf-8", newline="") as f:
        return [dict(ln) for ln in csv.DictReader(f)]


def ensaios_unicos(linhas: Sequence[Dict[str, str]], excluir: Optional[Set[Tuple[str, ...]]] = None) -> List[Dict[str, str]]:
    """Primeira linha de cada chave_ensaio, fora das chaves em `excluir`."""
    vistas: Set[Tuple[str, ...]] = set(excluir or ())
    saida: List[Dict[str, str]] = []
    for ln in linhas:
        k = chave_ensaio(ln.get("hash", ""), ln.get("ativo", ""), ln.get("variante", ""), ln.get("rota", ""), ln.get("periodo", ""))
        if k in vistas:
            continue
        vistas.add(k)
        saida.append(ln)
    return saida


def registar_ensaio(caminho: str, params: Params, met: dict) -> bool:
    """Acrescenta a dados/ensaios.csv (modo a, cabecalho so quando vazio) uma linha por configuracao corrida, descartada ou nao: M do DSR.

    A mesma chave_ensaio (hash, activo, variante, rota, periodo) nao se repete: correr duas vezes o
    mesmo backtest nao duplica M. Devolve True se escreveu.
    """
    chave = chave_ensaio(params.hash(), met.get("ativo", ""), met.get("variante", ""), met.get("rota", ""), met.get("periodo", ""))
    for ln in ler_ensaios(caminho):
        if chave_ensaio(ln.get("hash", ""), ln.get("ativo", ""), ln.get("variante", ""), ln.get("rota", ""), ln.get("periodo", "")) == chave:
            return False
    linha: Dict[str, Any] = {
        "data": md.iso_utc(md.agora_ms()), "ativo": met.get("ativo", ""), "variante": met.get("variante", ""),
        "rota": met.get("rota", ""), "hash": params.hash(), "h_a": md.num(params.h_a, 3), "n_ajuste": params.n_ajuste,
        "n": met.get("n", 0), "expectancia_bps": md.num(met.get("expectancia"), 4), "ep_bps": md.num(met.get("ep"), 4),
        "sr": md.num(met.get("sr"), 6), "p_hat": md.num(met.get("p_hat"), 4), "periodo": met.get("periodo", ""),
    }
    for c in CAMPOS_GRELHA:
        linha[c] = md.num(float(getattr(params, c)), 4)
    md.Registo(caminho, COLUNAS_ENSAIOS).escrever(linha)
    return True


def ler_sr_ensaios(caminho: str) -> List[float]:
    """SR de todas as linhas de ensaios.csv (as que o tem); vazio sem ficheiro."""
    if not os.path.exists(caminho):
        return []
    saida: List[float] = []
    with open(caminho, "r", encoding="utf-8", newline="") as f:
        for ln in csv.DictReader(f):
            sr = md.flt(ln.get("sr"))
            if _finito(sr):
                saida.append(sr)
    return saida


def matriz_pbo(trades_por_cfg: Dict[Params, Sequence[Trade]], configs: Sequence[Params],
               t_ini_ms: int, t_fim_ms: int) -> List[List[float]]:
    """Matriz (dias UTC x configuracoes) com a soma de r_liq dos trades entrados em cada dia, para pbo_cscv."""
    d0 = t_ini_ms // MS_DIA
    d1 = t_fim_ms // MS_DIA
    if d1 < d0:
        return []
    linhas = [[0.0] * len(configs) for _ in range(d1 - d0 + 1)]
    for j, p in enumerate(configs):
        for t in trades_por_cfg.get(p, ()):
            d = t.t_entrada_ms // MS_DIA
            if d0 <= d <= d1:
                linhas[d - d0][j] += t.r_liq_bps
    return linhas


def periodo_texto(velas: Sequence[Vela]) -> str:
    if not velas:
        return ""
    return "%s a %s" % (md.iso_utc(velas[0].t)[:10], md.iso_utc(velas[-1].T)[:10])


def p_nulo_de(met: dict, semente: int, n_caminhos: int = N_CAMINHOS_P_NULO) -> float:
    """p_nulo (11.6) para a geometria mediana dos trades: distancia ao alvo, L, tmax_15 e sigma_15 medianos."""
    g = met.get("dist_alvo_mediana", NAN)
    l_bps = met.get("l_mediano", NAN)
    tmax = met.get("tmax_mediano", NAN)
    sig = met.get("sigma_15_mediano", NAN)
    if not all(x == x for x in (g, l_bps, tmax, sig)) or g <= 0.0 or l_bps <= 0.0 or sig < 0.0:
        return NAN
    return rv.p_nulo_rotulo(g, l_bps, max(1, int(round(tmax))), sig, n_caminhos, semente)


def walk_forward(grelha: List[Params], dados: Dados, cfg: Any) -> dict:
    """Seccao 11.4: por janela escolhe-se no IS o centro de um patamar e mede-se no OOS; serie OOS encadeada.

    A grelha so corre com n >= n_grelha_min (200) trades da configuracao de partida (grelha[0]) no
    periodo; senao os valores de partida contam como um unico ensaio. O contexto de 1 h e partilhado
    por todas as configuracoes (nao depende da grelha). Sem uma janela completa (52 dias de 15 m) o
    periodo inteiro e um ensaio de sanidade de uma janela: IS vazio e OOS = tudo, com grelha[0].
    cfg le-se por atributo com os defeitos da seccao 11.3: is_dias 90, oos_dias 30, embargo_frac 0,01,
    n_grelha_min 200, c_w_bps 8, c_l_bps 13, custos, ensaios_csv (M do DSR), semente.
    """
    if not grelha:
        raise ValueError("a grelha precisa de pelo menos uma configuracao")
    base = grelha[0]
    custos: Custos = getattr(cfg, "custos", None) or Custos()
    c_w = float(getattr(cfg, "c_w_bps", 8.0))
    c_l = float(getattr(cfg, "c_l_bps", 13.0))
    is_dias = float(getattr(cfg, "is_dias", 90.0))
    oos_dias = float(getattr(cfg, "oos_dias", 30.0))
    embargo_frac = float(getattr(cfg, "embargo_frac", 0.01))
    n_grelha_min = int(getattr(cfg, "n_grelha_min", 200))
    semente = int(getattr(cfg, "semente", dados.semente))
    velas = dados.velas
    n = len(velas)
    por_hora = dados.velas_por_hora
    velas_1h = dados.velas_1h or agregar_1h_de_15m(dados.velas_15m)
    contextos: Dict[Tuple[Any, ...], List[Contexto1h]] = {}

    def contexto_de(p: Params) -> List[Contexto1h]:
        """O contexto de 1 h so depende dos parametros fora da grelha: partilha-se entre configuracoes."""
        chave = tuple(v for k, v in dataclasses.asdict(p).items() if k not in CAMPOS_GRELHA)
        if chave not in contextos:
            contextos[chave] = contexto_1h(p, velas_1h, dados.velas_15m, dados.funding, dados.degradada_1h)
        return contextos[chave]

    contexto = contexto_de(base)
    trades_base, recusas = simular_com_recusas(base, dados, custos, contexto=contexto)
    grelha_corrida = len(trades_base) >= n_grelha_min and len(grelha) > 1
    configs = list(grelha) if grelha_corrida else [base]
    trades_por_cfg: Dict[Params, List[Trade]] = {base: trades_base}
    for p in configs:
        if p not in trades_por_cfg:
            trades_por_cfg[p] = simular(p, velas_1h, dados.velas_15m, dados.funding, custos, dados.colunas_baleias,
                                        dados.degradada_1h, True, dados.rota, dados.semente, 0, contexto_de(p))
    purga = int(math.ceil(base.tmax_h * por_hora))
    janelas = janelas_walk_forward(n, int(is_dias * 24 * por_hora), int(oos_dias * 24 * por_hora), purga, embargo_frac)
    sanidade = not janelas
    if sanidade:
        janelas = [(0, 0, 0, n)]
    janelas_res: List[Dict[str, Any]] = []
    oos: List[Trade] = []
    is_escolhidos: List[Trade] = []
    for (is_ini, is_fim, oos_ini, oos_fim) in janelas:
        res_is: Dict[Params, float] = {}
        for p in configs:
            r_is = [t.r_liq_bps for t in trades_por_cfg[p] if is_ini <= t.i_entrada < is_fim]
            res_is[p] = statistics.fmean(r_is) if r_is else NAN
        escolhido = base if sanidade else patamar(res_is)
        t_is = [t for t in trades_por_cfg[escolhido] if is_ini <= t.i_entrada < is_fim]
        t_oos = [t for t in trades_por_cfg[escolhido] if oos_ini <= t.i_entrada < oos_fim]
        oos.extend(t_oos)
        is_escolhidos.extend(t_is)
        janelas_res.append({
            "is": (is_ini, is_fim), "oos": (oos_ini, oos_fim), "escolhido": escolhido.hash(), "params": escolhido,
            "n_is": len(t_is), "exp_is": res_is[escolhido], "n_oos": len(t_oos),
            "exp_oos": statistics.fmean([t.r_liq_bps for t in t_oos]) if t_oos else NAN,
        })
    exp_janelas = [j["exp_oos"] for j in janelas_res]
    conhecidas = [x for x in exp_janelas if x == x]
    met_oos = metricas(oos, c_w, c_l)
    met_total = metricas(trades_base, c_w, c_l)
    exp_is_total = statistics.fmean([t.r_liq_bps for t in is_escolhidos]) if is_escolhidos else NAN
    wfe = (met_oos["expectancia"] / exp_is_total
           if exp_is_total == exp_is_total and exp_is_total > 0.0 and met_oos["expectancia"] == met_oos["expectancia"]
           else NAN)
    ensaios: List[Tuple[Params, dict]] = []
    periodo = periodo_texto(velas)
    for p in configs:
        m = metricas(trades_por_cfg[p], c_w, c_l)
        m.update({"ativo": dados.ativo, "variante": dados.variante, "rota": dados.rota, "periodo": periodo})
        ensaios.append((p, m))
    # M do DSR (11.4): os ensaios desta corrida mais os ja registados em ensaios.csv com outra chave
    # (os desta corrida, se ja la estiverem de uma corrida anterior, nao contam duas vezes); as linhas
    # do sinalizador (sem SR) contam para M mas nao para a variancia dos SR
    chaves_corrida = {chave_ensaio(p.hash(), m.get("ativo", ""), m.get("variante", ""), m.get("rota", ""), m.get("periodo", ""))
                      for p, m in ensaios}
    caminho_ensaios = getattr(cfg, "ensaios_csv", "")
    outros = ensaios_unicos(ler_ensaios(caminho_ensaios), chaves_corrida) if caminho_ensaios else []
    sr_lista = [m["sr"] for _, m in ensaios] + [md.flt(ln.get("sr")) for ln in outros]
    m_ensaios = len(ensaios) + len(outros)
    matriz = matriz_pbo(trades_por_cfg, configs, velas[0].t, velas[-1].T) if velas else []
    pbo_val = NAN
    if grelha_corrida and len(matriz) >= 16:
        pbo_val = pbo(matriz, 16)
    dsr_val = dsr(sr_lista, met_oos["sr"], met_oos["n"], met_oos["g3"], met_oos["g4"], m_ensaios)
    return {
        "ativo": dados.ativo, "variante": dados.variante, "rota": dados.rota, "params": base, "hash": base.hash(),
        "periodo": periodo, "n_velas": n, "n_velas_1h": len(velas_1h), "buracos": contar_buracos(velas),
        "t_crit": contexto[-1].t_crit if contexto else NAN,
        "grelha_corrida": grelha_corrida, "n_configs": len(configs), "n_total": len(trades_base),
        "recusas": recusas, "trades": trades_base, "met_total": met_total,
        "sanidade": sanidade, "janelas": janelas_res, "oos_trades": oos, "met_oos": met_oos,
        "mediana_oos": nu.mediana(conhecidas) if conhecidas else NAN,
        "fraccao_positiva": (sum(1 for x in conhecidas if x > 0.0) / len(janelas_res)) if janelas_res else NAN,
        "wfe": wfe, "exp_is": exp_is_total, "ensaios": ensaios, "sr_lista": sr_lista, "m_ensaios": m_ensaios,
        "matriz_pbo": matriz, "pbo": pbo_val, "dsr": dsr_val,
        "trocos": consistencia_4_trocos(oos), "p_nulo": p_nulo_de(met_oos, semente),
        "gbm": None, "placebo": None, "vivo": None, "purga_velas": purga,
        "embargo_velas": int(math.ceil(embargo_frac * n)),
    }


# ==========================================================================
# 11.5 e 11.6 DSR, PBO, nulos e placebo
# ==========================================================================
def dsr(sr_lista: Sequence[float], sr_escolhido: float, n: int, g3: float, g4: float,
        m: Optional[int] = None) -> float:
    """(79)-(81) DSR = PSR(SR*) com SR* de sharpe_max_esperado(M, var dos SR conhecidos); M = numero de ensaios (defeito: os SR finitos)."""
    srs = [s for s in sr_lista if s == s]
    m = len(srs) if m is None else max(int(m), len(srs))
    var = statistics.variance(srs) if len(srs) >= 2 else 0.0
    sr_estrela = rv.sharpe_max_esperado(m, var)
    return rv.psr(sr_escolhido, sr_estrela, n, g3, g4)


def pbo(matriz: Sequence[Sequence[float]], s: int = 16) -> float:
    """(81) PBO por CSCV (reversao.pbo_cscv) sobre a matriz trades (ou dias) x configuracoes."""
    return rv.pbo_cscv(matriz, s)


def nulo_gbm(params: Params, sigma_r: float, sigma_15: float, n_caminhos: int, semente: int, custos: Custos,
             rota: str = "agressiva", horas: int = HORAS_NULO) -> dict:
    """Seccao 11.6: a regra inteira com o portao desligado sobre caminhos GBM de 15 m; a expectancia bruta deve ser zero.

    Cada caminho tem ceil(4 h_A) + n_min horas de aquecimento (as mesmas do processo ao vivo e de
    contexto_1h: 4 h_A para a ancora, n_min para o ajuste) mais `horas` de negociacao, SUB_PASSOS passos
    por vela de 15 m com desvio sigma_15 por vela (sigma_r / 2 se sigma_15 for desconhecido), e uma
    semente propria tirada de random.Random(semente). Devolve a expectancia bruta e liquida (pooled,
    com erro padrao), a distribuicao por caminho (quantil 0,95 para o criterio 6) e p_nulo do rotulo
    para a geometria mediana dos trades gerados. Com N = 720 cada caminho demora cerca de 2 s.
    """
    if n_caminhos < 1:
        raise ValueError("n_caminhos tem de ser pelo menos 1")
    if not (sigma_r == sigma_r and sigma_r >= 0.0):
        raise ValueError("sigma_r tem de ser nao negativo")
    if not (sigma_15 == sigma_15 and sigma_15 > 0.0):
        sigma_15 = sigma_r / 2.0
    aquecimento_h = int(math.ceil(4.0 * params.h_a)) + params.n_min
    n_15 = 4 * (aquecimento_h + max(1, int(horas)))
    rng = random.Random(semente)
    t0 = 1_700_000_000_000 - 1_700_000_000_000 % MS_DIA
    todos: List[Trade] = []
    por_caminho_bruta: List[float] = []
    por_caminho_liq: List[float] = []
    caminhos_com_trades = 0
    for _ in range(n_caminhos):
        s_i = rng.randrange(1 << 30)
        x = rv.simular_gbm(n_15 * SUB_PASSOS + 1, sigma_15 / math.sqrt(SUB_PASSOS), s_i)
        velas_15m = velas_de_trajectoria(x, SUB_PASSOS, t0)
        velas_1h = agregar_1h_de_15m(velas_15m)
        trades = simular(params, velas_1h, velas_15m, {}, custos, None, False, False, rota, s_i)
        todos.extend(trades)
        if trades:
            caminhos_com_trades += 1
            por_caminho_bruta.append(statistics.fmean([t.r_bruto_bps for t in trades]))
            por_caminho_liq.append(statistics.fmean([t.r_liq_bps for t in trades]))
    n = len(todos)
    brutos = [t.r_bruto_bps for t in todos]
    liquidos = [t.r_liq_bps for t in todos]
    met = metricas(todos, custos.c_w_c_l()[0], custos.c_w_c_l()[1])
    return {
        "n_caminhos": n_caminhos, "caminhos_com_trades": caminhos_com_trades, "n": n,
        "horas_por_caminho": aquecimento_h + int(horas), "sigma_15": sigma_15,
        "exp_bruta": statistics.fmean(brutos) if n else NAN,
        "ep_bruta": statistics.stdev(brutos) / math.sqrt(n) if n >= 2 else NAN,
        "exp_liq": statistics.fmean(liquidos) if n else NAN,
        "ep_liq": statistics.stdev(liquidos) / math.sqrt(n) if n >= 2 else NAN,
        "custo_medio": statistics.fmean([t.custo_bps for t in todos]) if n else NAN,
        "p_hat": met["p_hat"], "q95_bruta": quantil(por_caminho_bruta, 0.95), "q95_liq": quantil(por_caminho_liq, 0.95),
        "por_caminho_liq": por_caminho_liq, "motivos": met["motivos"], "p_nulo": p_nulo_de(met, semente),
    }


def placebo_ancora(params: Params, dados: Dados, n_real: int, semente: int, custos: Custos) -> dict:
    """Seccao 11.6: a mesma regra com A_t substituida por A_{t-u}, u uniforme em [48, 240] velas de 1 h, em n_real realizacoes.

    Devolve as expectancias por realizacao, a expectancia e o erro padrao de todos os trades do placebo
    juntos e o numero de trades; os u saem de random.Random(semente).
    """
    if n_real < 1:
        raise ValueError("n_real tem de ser pelo menos 1")
    rng = random.Random(semente)
    us: List[int] = []
    por_real: List[float] = []
    todos: List[Trade] = []
    for _ in range(n_real):
        u = rng.randint(48, 240)
        us.append(u)
        trades, _ = simular_com_recusas(params, dados, custos, True, u)
        todos.extend(trades)
        por_real.append(statistics.fmean([t.r_liq_bps for t in trades]) if trades else NAN)
    n = len(todos)
    r = [t.r_liq_bps for t in todos]
    return {
        "n_real": n_real, "us": us, "por_realizacao": por_real, "n": n,
        "exp": statistics.fmean(r) if n else NAN, "ep": statistics.stdev(r) / math.sqrt(n) if n >= 2 else NAN,
        "exp_bruta": statistics.fmean([t.r_bruto_bps for t in todos]) if n else NAN,
    }


# ==========================================================================
# 11.7 Criterio de aceitacao
# ==========================================================================
def aceitacao(resultados: dict, cfg: Any = None) -> Tuple[bool, List[str]]:
    """Os 8 criterios da seccao 11.7 sobre o OOS encadeado; devolve (aceite, motivos das falhas).

    Motivos: n_oos, p_inf, t, mediana_oos, fraccao_positiva, wfe, pbo, dsr, gbm, placebo, trocos,
    vivo (criterio 8: com menos de 100 sinais medidos ao vivo fica pendente e conta como falha).
    Limiares por atributo de cfg: n_oos_min 100, margem_p 0,05, t_min 3, frac_pos_min 0,6, wfe_min 0,5,
    pbo_max 0,2, dsr_min 0,95, n_vivo_min 100.
    """
    n_oos_min = int(getattr(cfg, "n_oos_min", 100))
    margem_p = float(getattr(cfg, "margem_p", 0.05))
    t_min = float(getattr(cfg, "t_min", 3.0))
    frac_min = float(getattr(cfg, "frac_pos_min", 0.6))
    wfe_min = float(getattr(cfg, "wfe_min", 0.5))
    pbo_max = float(getattr(cfg, "pbo_max", 0.2))
    dsr_min = float(getattr(cfg, "dsr_min", 0.95))
    n_vivo_min = int(getattr(cfg, "n_vivo_min", 100))
    met = resultados.get("met_oos") or {}
    motivos: List[str] = []

    def num(chave: str, de: Optional[dict] = None) -> float:
        v = (met if de is None else de).get(chave)
        return md.flt(v) if v is not None else NAN

    n = int(met.get("n", 0) or 0)
    if n < n_oos_min:
        motivos.append("n_oos")
    p_inf, p_est, p_nulo = num("p_inf"), num("p_estrela"), md.flt(resultados.get("p_nulo", NAN))
    referencia = max(x for x in (p_est, p_nulo) if x == x) if (p_est == p_est or p_nulo == p_nulo) else NAN
    if not (p_inf == p_inf and referencia == referencia and p_inf > referencia + margem_p):
        motivos.append("p_inf")
    t = num("t")
    if not (t == t and t >= t_min):
        motivos.append("t")
    med = md.flt(resultados.get("mediana_oos", NAN))
    if not (med == med and med > 0.0):
        motivos.append("mediana_oos")
    frac = md.flt(resultados.get("fraccao_positiva", NAN))
    if not (frac == frac and frac >= frac_min):
        motivos.append("fraccao_positiva")
    wfe = md.flt(resultados.get("wfe", NAN))
    if not (wfe == wfe and wfe >= wfe_min):
        motivos.append("wfe")
    pbo_v = md.flt(resultados.get("pbo", NAN))
    if not (pbo_v == pbo_v and pbo_v < pbo_max):
        motivos.append("pbo")
    dsr_v = md.flt(resultados.get("dsr", NAN))
    if not (dsr_v == dsr_v and dsr_v >= dsr_min):
        motivos.append("dsr")
    exp, ep = num("expectancia"), num("ep")
    gbm = resultados.get("gbm") or {}
    q95 = md.flt(gbm.get("q95_liq", NAN)) if gbm else NAN
    if not (exp == exp and q95 == q95 and exp > q95):
        motivos.append("gbm")
    plc = resultados.get("placebo") or {}
    exp_p, ep_p = (md.flt(plc.get("exp", NAN)), md.flt(plc.get("ep", NAN))) if plc else (NAN, NAN)
    if not (exp == exp and exp_p == exp_p and ep == ep and ep_p == ep_p
            and exp - exp_p > 2.0 * math.sqrt(ep * ep + ep_p * ep_p)):
        motivos.append("placebo")
    trocos = [md.flt(x) for x in (resultados.get("trocos") or [])]
    if not trocos or not all(x == x and x > 0.0 for x in trocos):
        motivos.append("trocos")
    vivo = resultados.get("vivo") or {}
    n_vivo = int(md.flt(vivo.get("n", 0))) if vivo and vivo.get("n") is not None else 0
    if n_vivo < n_vivo_min:
        motivos.append("vivo")
    else:
        r_vivo = md.flt(vivo.get("r_3600_medio", NAN)) - md.flt(vivo.get("custos", 0.0))
        if not (r_vivo == r_vivo and exp == exp and ep == ep and abs(r_vivo - exp) <= 2.0 * ep):
            motivos.append("vivo")
    return (not motivos), motivos


# ==========================================================================
# Relatorio e ficheiros
# ==========================================================================
def _f(v: Any, casas: int = 2) -> str:
    if isinstance(v, bool):
        return "sim" if v else "nao"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v:
            return "-"
        if v in (float("inf"), float("-inf")):
            return "inf" if v > 0 else "-inf"
        return ("%." + str(casas) + "f") % v
    return str(v)


def _linhas_metricas(w: Any, met: dict, prefixo: str = "   ") -> None:
    if not met or not met.get("n"):
        w(prefixo + "sem trades")
        return
    w(prefixo + "n=%d  expectancia %s bps (EP %s, t %s)  bruta %s  custo medio %s  funding %s" % (
        met["n"], _f(met["expectancia"]), _f(met["ep"]), _f(met["t"]), _f(met["exp_bruta"]),
        _f(met["custo_medio"]), _f(met["funding_medio"])))
    w(prefixo + "p_hat %s  p_inf %s  p* %s  margem %s  PF %s  ganho/perda %s  SR %s (g3 %s, g4 %s)" % (
        _f(met["p_hat"], 3), _f(met["p_inf"], 3), _f(met["p_estrela"], 3), _f(met["margem"], 3), _f(met["pf"]),
        _f(met["razao_ganho_perda"]), _f(met["sr"], 3), _f(met["g3"]), _f(met["g4"])))
    w(prefixo + "mediana %s  q05 %s  q95 %s  MAE %s  MFE %s  DD max %s  perdas seguidas %d  duracao %s velas" % (
        _f(met["mediana"]), _f(met["q05"]), _f(met["q95"]), _f(met["mae"]), _f(met["mfe"]), _f(met["dd_max"]),
        met["perdas_seguidas"], _f(met["duracao_media"], 1)))
    mot = met.get("motivos") or {}
    w(prefixo + "saidas: " + "  ".join("%s %s" % (m, _f(100.0 * mot.get(m, NAN), 0) + "%") for m in MOTIVOS_SAIDA)
      + "  |  sinais/dia %s  custo/dia %s bps  Spearman(|z0|, r) %s  ECE %s  Brier %s" % (
          _f(met["sinais_por_dia"]), _f(met["custo_por_dia"]), _f(met["spearman_z0"], 3), _f(met["ece"], 3),
          _f(met["brier"], 3)))
    w(prefixo + "G mediano %s  L mediano %s  tmax mediano %s  rotas %s" % (
        _f(met["g_mediano"], 1), _f(met["l_mediano"], 1), _f(met["tmax_mediano"], 0), met.get("por_rota") or {}))


def relatorio_texto(resultados: dict) -> str:
    """Relatorio em texto por activo e variante; aceita o dicionario de walk_forward de um activo ou {'ativos': [...]}."""
    lista = resultados.get("ativos") if isinstance(resultados.get("ativos"), list) else [resultados]
    out: List[str] = []
    w = out.append
    w("BACKTEST DO SINALIZADOR DE REVERSAO %s (REVOU v2, seccao 11)" % VERSAO)
    for r in lista:
        p: Params = r.get("params") or Params()
        w("")
        w("ACTIVO %s  variante %s  rota %s  periodo %s  velas %d (1 h %d, incompletas %d)" % (
            r.get("ativo", "?"), r.get("variante", "?"), r.get("rota", "?"), r.get("periodo", "?"),
            r.get("n_velas", 0), r.get("n_velas_1h", 0), r.get("buracos", 0)))
        w("Parametros var=%s  %s  h_a=%s n_ajuste=%d t_crit=%s" % (
            p.hash(), " ".join("%s=%s" % (c, _f(float(getattr(p, c)))) for c in CAMPOS_GRELHA), _f(p.h_a, 0),
            p.n_ajuste, _f(r.get("t_crit", NAN))))
        w("1. PERIODO INTEIRO (configuracao de partida)")
        _linhas_metricas(w, r.get("met_total") or {})
        rec = r.get("recusas") or {}
        if rec:
            w("   recusas: " + "  ".join("%s %d" % kv for kv in sorted(rec.items())))
        w("2. WALK-FORWARD  grelha corrida %s (%d configuracoes, M=%d ensaios)  purga %d  embargo %d velas%s" % (
            _f(bool(r.get("grelha_corrida"))), r.get("n_configs", 0), r.get("m_ensaios", 0), r.get("purga_velas", 0),
            r.get("embargo_velas", 0), "  ENSAIO DE SANIDADE: sem janela completa" if r.get("sanidade") else ""))
        for i, j in enumerate(r.get("janelas") or []):
            w("   janela %d: IS [%d, %d) var=%s n=%d exp %s  |  OOS [%d, %d) n=%d exp %s" % (
                i + 1, j["is"][0], j["is"][1], j["escolhido"], j["n_is"], _f(j["exp_is"]), j["oos"][0], j["oos"][1],
                j["n_oos"], _f(j["exp_oos"])))
        w("   OOS encadeado:")
        _linhas_metricas(w, r.get("met_oos") or {}, "      ")
        w("   mediana OOS por janela %s  fraccao positiva %s  WFE %s (IS %s)  PBO %s  DSR %s  p_nulo %s" % (
            _f(r.get("mediana_oos", NAN)), _f(r.get("fraccao_positiva", NAN)), _f(r.get("wfe", NAN)),
            _f(r.get("exp_is", NAN)), _f(r.get("pbo", NAN), 3), _f(r.get("dsr", NAN), 3), _f(r.get("p_nulo", NAN), 3)))
        w("   4 trocos: " + "  ".join(_f(x) for x in (r.get("trocos") or [])))
        gbm = r.get("gbm")
        if gbm:
            w("3. NULO GBM (portao desligado)  caminhos %d (%d com trades)  n=%d  bruta %s (EP %s)  liquida %s (EP %s)"
              "  custo %s  q95 por caminho %s  p_hat %s  p_nulo %s" % (
                  gbm["n_caminhos"], gbm["caminhos_com_trades"], gbm["n"], _f(gbm["exp_bruta"]), _f(gbm["ep_bruta"]),
                  _f(gbm["exp_liq"]), _f(gbm["ep_liq"]), _f(gbm["custo_medio"]), _f(gbm["q95_liq"]),
                  _f(gbm["p_hat"], 3), _f(gbm["p_nulo"], 3)))
        else:
            w("3. NULO GBM: nao corrido (--nulo N)")
        plc = r.get("placebo")
        if plc:
            w("4. PLACEBO DA ANCORA  realizacoes %d  u %s  n=%d  exp %s (EP %s)  por realizacao %s" % (
                plc["n_real"], plc["us"], plc["n"], _f(plc["exp"]), _f(plc["ep"]),
                " ".join(_f(x) for x in plc["por_realizacao"])))
        else:
            w("4. PLACEBO: nao corrido (--placebo N)")
        vivo = r.get("vivo")
        w("5. AO VIVO: " + ("n=%s r_3600 medio %s custos %s" % (vivo.get("n"), _f(md.flt(vivo.get("r_3600_medio"))),
                                                               _f(md.flt(vivo.get("custos"))))
                            if vivo else "sem 100 sinais medidos pelo medidor (criterio 8 pendente)"))
        ok, motivos = r.get("aceite"), r.get("motivos")
        if ok is None or motivos is None:
            ok, motivos = aceitacao(r)
        w("ACEITACAO: %s%s" % ("SIM" if ok else "NAO", "" if ok else "  (falham: %s)" % ", ".join(motivos)))
    w("")
    w("Nada disto prova a estrategia ao vivo: so os 100 sinais medidos pelo medidor o dizem (seccao 11.7, 8).")
    return "\n".join(out)


COLUNAS_TRADES = [f.name for f in dataclasses.fields(Trade)]


def escrever_trades(caminho: str, trades: Sequence[Trade]) -> None:
    """Escreve os trades em CSV (uma coluna por campo; floats com 10 algarismos significativos): determinista."""
    os.makedirs(os.path.dirname(os.path.abspath(caminho)), exist_ok=True)
    with open(caminho, "w", encoding="utf-8", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(COLUNAS_TRADES)
        for t in trades:
            linha = []
            for c in COLUNAS_TRADES:
                v = getattr(t, c)
                if isinstance(v, float):
                    linha.append("" if v != v else "%.10g" % v)
                else:
                    linha.append(v)
            wr.writerow(linha)


def ler_trades(caminho: str) -> List[Trade]:
    """Inverso de escrever_trades."""
    tipos = {f.name: f.type for f in dataclasses.fields(Trade)}
    saida: List[Trade] = []
    with open(caminho, "r", encoding="utf-8", newline="") as f:
        for ln in csv.DictReader(f):
            kw: Dict[str, Any] = {}
            for c in COLUNAS_TRADES:
                txt = ln.get(c, "")
                tipo = str(tipos[c])
                if "int" in tipo:
                    kw[c] = int(float(txt)) if txt else 0
                elif "float" in tipo:
                    kw[c] = md.flt(txt) if txt else NAN
                else:
                    kw[c] = txt
            saida.append(Trade(**kw))
    return saida


# ==========================================================================
# Configuracao e linha de comandos
# ==========================================================================
class ConfigBacktest:
    """[geral], [orcamento] e [sinalizador] de config.ini sem importar o sinalizador; SystemExit em portugues se invalido."""

    def __init__(self, caminho: str):
        self.med = md.Config(caminho)
        cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        cp.read(caminho, encoding="utf-8")

        def g(chave: str, defeito: str) -> str:
            return cp.get("sinalizador", chave, fallback=defeito).strip()

        def fl(chave: str, defeito: str) -> float:
            try:
                return float(g(chave, defeito))
            except ValueError:
                raise SystemExit("config.ini: [sinalizador] %s tem de ser um numero" % chave)

        self.ativos = self.med.ativos
        self.pasta = self.med.pasta
        pasta_velas = g("pasta_velas", "dados/velas")
        self.pasta_velas = pasta_velas if os.path.isabs(pasta_velas) else os.path.join(self.med.base, pasta_velas)
        self.pasta_funding = os.path.join(self.pasta, "funding")
        self.pasta_backtest = os.path.join(self.pasta, "backtest")
        self.ensaios_csv = os.path.join(self.pasta, "ensaios.csv")
        self.params = Params(
            z_in=fl("z_in", "2.0"), z_out=fl("z_out", "0.5"), z_stop=fl("z_stop", "3.0"), n_h=fl("n_h", "2"),
            z_min_resto=fl("z_min_resto", "0.75"), k_h=fl("k_h", "1"), h_a=fl("h_a", "96"),
            n_ajuste=int(fl("n_ajuste", "720")), n_min=int(fl("n_min", "480")), n_max=int(fl("n_max", "2160")),
            calibrar_nula=_verdade(g("calibrar_nula", "sim")), alpha_nula=fl("alpha_nula", "0.001"),
            replicas_nula=int(fl("replicas_nula", "1000")), semente_nula=int(fl("semente_nula", "7")),
            t_nulo_max=fl("t_nulo_max", "-3.0"), t_amarelo=fl("t_amarelo", "-2.0"), q_vr=int(fl("q_vr", "8")),
            vr_max_verde=fl("vr_max_verde", "1.0"), vr_veto=fl("vr_veto", "1.2"), zvr_veto=fl("zvr_veto", "2.0"),
            h_min=fl("h_min", "3"), h_max=fl("h_max", "24"), h_vc=fl("h_vc", "8"), h_vl=fl("h_vl", "96"),
            vol_razao_max=fl("vol_razao_max", "2.0"), k_choque=fl("k_choque", "4"), k_dia=fl("k_dia", "2"),
            z_veto=fl("z_veto", "4.0"), n_z=int(fl("n_z", "720")), z_in_emp_max=fl("z_in_emp_max", "2.5"),
            k_max_tecto=int(fl("k_max_tecto", "64")), tmax_h=fl("tmax_h", "16"),
            idade_ctx_max_min=fl("idade_ctx_max_min", "75"), arrefecimento_velas=int(fl("arrefecimento_velas", "2")),
            g_min_x_custo=fl("g_min_x_custo", "3"))
        try:
            validar_params(self.params)
        except ValueError as e:
            raise SystemExit("config.ini: [sinalizador] %s" % e)
        self.custos = Custos(self.med.taxa_taker, self.med.taxa_maker, fl("imp_defeito_bps", "2"), 0.6, 1.0)
        self.c_w_bps = fl("c_w_bps", "8")
        self.c_l_bps = fl("c_l_bps", "13")
        self.capital_usd = fl("capital_usd", "20000")
        if self.capital_usd <= 0:
            raise SystemExit("config.ini: [sinalizador] capital_usd tem de ser positivo")
        self.is_dias = 90.0
        self.oos_dias = 30.0
        self.embargo_frac = 0.01
        self.n_grelha_min = 200
        self.semente = int(fl("semente_nula", "7"))
        self.n_oos_min = 100
        self.margem_p = 0.05
        self.t_min = 3.0
        self.frac_pos_min = 0.6
        self.wfe_min = 0.5
        self.pbo_max = 0.2
        self.dsr_min = 0.95
        self.n_vivo_min = 100
        self.horizonte_vivo = self.med.h_regra


def custos_do_medidor(cfg: ConfigBacktest, ativo: str, minimo: int = 30) -> Tuple[Custos, int]:
    """Custos a partir do registo_sinais.csv do medidor (so leitura): imp mediano, pi da sombra e meio spread mediano com >= minimo sinais; senao os de defeito."""
    base = cfg.custos
    caminho = cfg.med.reg_sinais
    if not os.path.exists(caminho):
        return base, 0
    with open(caminho, "r", encoding="utf-8", newline="") as f:
        linhas = [ln for ln in csv.DictReader(f) if (ln.get("ativo") or "").upper() == ativo.upper() and ln.get("mid0")]
    imps = [md.flt(ln.get("imp_bps")) for ln in linhas if ln.get("imp_bps")]
    imps = [x for x in imps if _finito(x)]
    sombras = [ln.get("sombra_preenchida") for ln in linhas if ln.get("sombra_preenchida") in ("0", "1")]
    spreads = [md.flt(ln.get("spread_bps")) for ln in linhas if ln.get("spread_bps")]
    spreads = [x for x in spreads if _finito(x)]
    n = min(len(imps), len(sombras))
    if n < minimo:
        return base, n
    return Custos(base.taxa_taker, base.taxa_maker, nu.mediana(imps), sum(1 for s in sombras if s == "1") / len(sombras),
                  0.5 * nu.mediana(spreads) if spreads else base.meio_spread_bps), n


def resultado_vivo(cfg: ConfigBacktest, ativo: str) -> Optional[dict]:
    """Criterio 8: r_<horizonte_regra> medio dos sinais do medidor com nota revou deste activo; None sem registo."""
    caminho = cfg.med.reg_sinais
    if not os.path.exists(caminho):
        return None
    col = "r_%d" % cfg.horizonte_vivo
    rs: List[float] = []
    with open(caminho, "r", encoding="utf-8", newline="") as f:
        for ln in csv.DictReader(f):
            if (ln.get("ativo") or "").upper() != ativo.upper() or not (ln.get("nota") or "").startswith("revou"):
                continue
            v = md.flt(ln.get(col))
            if _finito(v):
                rs.append(v)
    if not rs:
        return None
    n = len(rs)
    c_w, c_l = cfg.custos.c_w_c_l()
    return {"n": n, "r_3600_medio": statistics.fmean(rs), "ep": statistics.stdev(rs) / math.sqrt(n) if n >= 2 else NAN,
            "custos": 0.5 * (c_w + c_l), "horizonte_s": cfg.horizonte_vivo}


def _sigma_das_velas(velas: Sequence[Vela], n: int) -> float:
    """Desvio padrao do retorno log das ultimas n velas (15)."""
    xs = [math.log(v.c) for v in velas[-(n + 1):]]
    return rv.sigma_retorno([b - a for a, b in zip(xs, xs[1:])]) if len(xs) >= 2 else NAN


DIAS_MIN_VARIANTE_B = 60


def correr_activo(cfg: ConfigBacktest, ativo: str, de_ms: Optional[int], ate_ms: Optional[int], grelha: bool,
                  walk: bool, n_nulo: int, n_placebo: int, degradada_1h: bool, rota: str) -> List[dict]:
    """Um activo: variante A (ou 1h-degradada) e, com colunas enriquecidas em pelo menos 60 dias, tambem a B.

    Carrega, simula, valida, regista os ensaios e escreve dados/backtest/<ATIVO>_<variante>_<rota>_trades.csv
    e _oos.csv; devolve um resultado por variante corrida.
    """
    velas_1h = filtrar_periodo(carregar_velas(cfg.pasta_velas, ativo, "1h"), de_ms, ate_ms)
    velas_15m = filtrar_periodo(carregar_velas(cfg.pasta_velas, ativo, "15m"), de_ms, ate_ms)
    funding = carregar_funding(cfg.pasta_funding, ativo)
    if not velas_1h and not velas_15m:
        raise SystemExit("%s: nao ha velas em %s. Corra antes  python3 sinalizador.py historico" % (ativo, cfg.pasta_velas))
    if not velas_15m and not degradada_1h:
        print("%s: sem velas de 15 m; corre a variante 1h-degradada" % ativo)
        degradada_1h = True
    custos, n_custos = custos_do_medidor(cfg, ativo)
    cfg.custos = custos
    variantes = [Dados(ativo, velas_1h, velas_15m, funding, None, degradada_1h, rota, cfg.semente)]
    if not degradada_1h:
        colunas = carregar_colunas_baleias(cfg.pasta_velas, ativo)
        por_t = {v.t: c for v, c in zip(carregar_velas(cfg.pasta_velas, ativo, "15m"), colunas)}
        alinhadas = [por_t.get(v.t, {}) for v in velas_15m]
        com_dados = sum(1 for c in alinhadas if c.get("flx", NAN) == c.get("flx", NAN))
        if com_dados >= DIAS_MIN_VARIANTE_B * 96:
            variantes.append(Dados(ativo, velas_1h, velas_15m, funding, alinhadas, False, rota, cfg.semente))
    saida = []
    for dados in variantes:
        saida.append(_correr_variante(cfg, dados, custos, n_custos, grelha, walk, n_nulo, n_placebo))
    return saida


def _correr_variante(cfg: ConfigBacktest, dados: Dados, custos: Custos, n_custos: int, grelha: bool, walk: bool,
                     n_nulo: int, n_placebo: int) -> dict:
    ativo, rota = dados.ativo, dados.rota
    lista = grelha_declarada(cfg.params) if grelha else [cfg.params]
    if not walk:
        cfg_simples = _CfgSimples(cfg, 10 ** 9)
        res = walk_forward([cfg.params], dados, cfg_simples)
        res["janelas"] = []
        res["sanidade"] = True
    else:
        res = walk_forward(lista, dados, cfg)
    res["custos"] = custos
    res["n_custos_medidos"] = n_custos
    if n_nulo > 0:
        sigma_r = _sigma_das_velas(dados.velas_1h, cfg.params.n_ajuste)
        sigma_15 = _sigma_das_velas(dados.velas_15m, 4 * cfg.params.n_ajuste) if dados.velas_15m else NAN
        res["gbm"] = nulo_gbm(cfg.params, sigma_r, sigma_15, n_nulo, cfg.semente, custos, rota)
    if n_placebo > 0:
        res["placebo"] = placebo_ancora(cfg.params, dados, n_placebo, cfg.semente, custos)
    res["vivo"] = resultado_vivo(cfg, ativo)
    res["aceite"], res["motivos"] = aceitacao(res, cfg)
    for p, met in res["ensaios"]:
        registar_ensaio(cfg.ensaios_csv, p, met)
    os.makedirs(cfg.pasta_backtest, exist_ok=True)
    nome = "%s_%s_%s" % (ativo, dados.variante, rota)
    escrever_trades(os.path.join(cfg.pasta_backtest, nome + "_trades.csv"), res["trades"])
    escrever_trades(os.path.join(cfg.pasta_backtest, nome + "_oos.csv"), res["oos_trades"])
    return res


class _CfgSimples:
    """cfg para uma passagem sem walk-forward: janela unica (IS vazio, OOS = tudo)."""

    def __init__(self, cfg: ConfigBacktest, is_dias: float):
        self.custos = cfg.custos
        self.c_w_bps = cfg.c_w_bps
        self.c_l_bps = cfg.c_l_bps
        self.is_dias = is_dias
        self.oos_dias = cfg.oos_dias
        self.embargo_frac = cfg.embargo_frac
        self.n_grelha_min = cfg.n_grelha_min
        self.semente = cfg.semente
        self.ensaios_csv = cfg.ensaios_csv


def main(argv: Optional[List[str]] = None) -> None:
    aqui = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Backtest por eventos do sinalizador de reversao (REVOU v2).")
    ap.add_argument("--config", default=os.path.join(aqui, "config.ini"), help="caminho do config.ini")
    ap.add_argument("--ativo", action="append", help="activo (repetivel); por defeito todos os de [geral] ativos")
    ap.add_argument("--de", default="", help="inicio do periodo (ISO ou epoch)")
    ap.add_argument("--ate", default="", help="fim do periodo (ISO ou epoch)")
    ap.add_argument("--grelha", action="store_true", help="corre as 216 configuracoes (so com n >= 200 trades)")
    ap.add_argument("--walk", action="store_true", help="walk-forward IS 90 d / OOS 30 d com purga e embargo")
    ap.add_argument("--nulo", nargs="?", const=1000, default=0, type=int, metavar="N",
                    help="nulo GBM com o portao desligado, N caminhos (1000 por defeito; cerca de 2 s por caminho)")
    ap.add_argument("--placebo", nargs="?", const=10, default=0, type=int, metavar="N",
                    help="placebo da ancora deslocada, N realizacoes (10 por defeito)")
    ap.add_argument("--degradada-1h", action="store_true", help="gatilho avaliado nos fechos de 1 h (208 dias)")
    ap.add_argument("--rota", default="agressiva", choices=ROTAS, help="rota de entrada")
    args = ap.parse_args(argv)
    cfg = ConfigBacktest(args.config)
    try:
        de_ms = md.interpretar_hora(args.de, 0) if args.de else None
        ate_ms = md.interpretar_hora(args.ate, 0) if args.ate else None
    except ValueError as e:
        raise SystemExit("hora invalida: %s" % e)
    ativos = args.ativo or cfg.ativos
    desconhecidos = [a for a in ativos if a not in cfg.ativos]
    if desconhecidos:
        raise SystemExit("activos fora de [geral] ativos: %s" % ", ".join(desconhecidos))
    resultados = []
    for ativo in ativos:
        resultados.extend(correr_activo(cfg, ativo, de_ms, ate_ms, args.grelha, args.walk, args.nulo, args.placebo,
                                        args.degradada_1h, args.rota))
    texto = relatorio_texto({"ativos": resultados})
    print(texto)
    os.makedirs(cfg.pasta_backtest, exist_ok=True)
    with open(os.path.join(cfg.pasta_backtest, "relatorio_backtest.txt"), "w", encoding="utf-8") as f:
        f.write(texto + "\n")


if __name__ == "__main__":
    main()
