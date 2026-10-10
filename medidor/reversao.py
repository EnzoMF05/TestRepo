"""Formulas puras do sinalizador de reversao (Etapa 1, especificacao REVOU v2).

Implementa as formulas (1) a (81) de ESTRATEGIA-REVERSAO.md, assinatura a
assinatura conforme docs/reversao-api.txt. Nao abre rede nem ficheiros, nao le
o relogio: tudo o que depende do mundo exterior entra pelos argumentos.

Convencoes
----------
- lado = +1 compra, -1 venda. Custo positivo e custo; resultado positivo e a favor.
- nan = nao se sabe; ValueError = entrada impossivel.
- Precos sao fechos de velas oficiais; x = ln(preco); sigma em unidades de log-preco.
- Toda a aleatoriedade passa por random.Random(semente): mesma semente, mesmo valor.
- Compativel com Python 3.9+. So standard library e nucleo.
"""
from __future__ import annotations

import bisect
import hashlib
import math
import random
import statistics
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Sequence, Set, Tuple

import nucleo as nu

BPS = nu.BPS
VERDE = nu.VERDE
AMARELO = nu.AMARELO
VERMELHO = nu.VERMELHO
DENTRO = "DENTRO"
FORA = "FORA"
NAN = float("nan")
LN2 = math.log(2.0)
EULER_GAMMA = 0.5772156649015329
JANELA_1H_MS = 3_600_000
IMAN_ALCANCE = 0.03        # faixa de precos (3 %) onde se procura o iman (52)
P_CAUDA_NORMAL = 0.954     # P(|z| <= 2) sob OU gaussiano (30)
PASSO_T_CRIT = 240         # (11) t_crit calibrado por patamares de N (velas de 1 h), igual ao vivo e no backtest


def n_calibracao(n: int, n_min: int, n_max: int, passo: int = PASSO_T_CRIT) -> int:
    """(11) N para calibrar t_crit: 0 (sem calibracao, usa-se t_nulo_max) se n < n_min; senao min(n_max, max(n_min, n - n mod passo)).

    Regra unica do sinalizador e do backtest: abaixo de n_min o ajuste nao e valido (4.3) e o
    limiar de fallback chega; acima, patamares de `passo` velas evitam recalibrar a cada hora
    (o quantil de 1000 replicas muda menos do que isso entre patamares).
    """
    if n < n_min or passo < 1:
        return 0
    return int(min(n_max, max(n_min, n - n % passo)))


def n_min_quantil_z(n_z: int, n_min: int) -> int:
    """(30) numero minimo de |z| anteriores para o quantil empirico existir: min(n_z, n_min), igual ao vivo e no backtest."""
    return int(max(1, min(n_z, n_min)))


def _sinal(x: float) -> int:
    """Sinal de x em {-1, 0, +1}; 0 para nan."""
    if x != x:
        return 0
    return 1 if x > 0 else (-1 if x < 0 else 0)


def _finito(x: float) -> bool:
    return x == x and x not in (float("inf"), float("-inf"))


def _quantil(xs: Sequence[float], p: float) -> float:
    """Quantil empirico com interpolacao linear (tipo 7); nan sem amostras ou p fora de [0, 1]."""
    ordenados = sorted(x for x in xs if x == x)
    n = len(ordenados)
    if n == 0 or not (0.0 <= p <= 1.0):
        return NAN
    pos = p * (n - 1)
    i = int(math.floor(pos))
    if i >= n - 1:
        return ordenados[-1]
    frac = pos - i
    return ordenados[i] + frac * (ordenados[i + 1] - ordenados[i])


# --------------------------------------------------------------------------
# 4.1 Ancora e desvio
# --------------------------------------------------------------------------
def lambda_ancora(h_a: float) -> float:
    """(1) lambda_A = 2^(-1/h_A); ValueError se h_a <= 0."""
    if not (h_a == h_a and h_a > 0):
        raise ValueError("h_a tem de ser positivo")
    return nu.lam(h_a)


class Ancora:
    """(2)(3) A_t = EWMA_{h_A}(x)_t com correccao de arranque; juntar devolve d_t = x_t - A_t.

    Sob a nula, d so e o AR(1) estacionario de (5) depois de a ancora estar em regime: um
    teste com passeios aleatorios precisa de aquecer a ancora (varias meias-vidas) antes
    da janela do ajuste, como o sinalizador faz com o historico do arranque.
    """

    def __init__(self, h_a: float):
        self.lam = lambda_ancora(h_a)
        self._ewma = nu.Ewma(h_a)

    def juntar(self, x: float) -> float:
        if not _finito(x):
            raise ValueError("log-preco tem de ser finito")
        return x - self._ewma.juntar(x)

    @property
    def valor(self) -> float:
        return self._ewma.valor

    @property
    def n(self) -> int:
        return self._ewma.n


# --------------------------------------------------------------------------
# 4.3 a 4.5 Ajuste AR(1) sem intercepto, nula, parametros do OU
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class AjusteOU:
    phi_hat: float        # (6)
    phi_c: float          # (7)
    s2: float             # (8)
    se_rob: float         # (9)
    t_nulo: float         # (10)
    theta: float          # (12)
    meia_vida: float      # (12) H em velas de 1 h
    sigma_eq: float       # (12)
    theta_x: float        # (13)
    se_theta: float       # (14)
    n: int                # velas da janela
    valido: bool          # 0 < phi_c < 1 e tudo finito


def _ajuste_nan(n: int) -> AjusteOU:
    return AjusteOU(NAN, NAN, NAN, NAN, NAN, NAN, NAN, NAN, NAN, NAN, n, False)


def ajustar_ar1(d: Sequence[float], lam_a: float) -> AjusteOU:
    """(6)-(14) AR(1) sem intercepto sobre a janela d; N = len(d) velas, N - 1 pares; nan se N < 3.

    s^2 = soma e_t^2 / (N - 1) e a media dos quadrados dos N - 1 residuos. O ajuste
    e valido quando 0 < phi_c < 1; a condicao N >= n_min e verificada no contexto.
    """
    n = len(d)
    if n < 3:
        return _ajuste_nan(n)
    if not (lam_a == lam_a and 0.0 < lam_a < 1.0):
        raise ValueError("lambda_A tem de estar em (0, 1)")
    sxy = 0.0
    sxx = 0.0
    anterior = d[0]
    for x in d[1:]:
        sxy += x * anterior
        sxx += anterior * anterior
        anterior = x
    if not (sxx > 0.0) or not _finito(sxy):
        return _ajuste_nan(n)
    phi_hat = sxy / sxx
    phi_c = phi_hat * (1.0 + 2.0 / n)
    soma_e2 = 0.0
    soma_w = 0.0
    anterior = d[0]
    for x in d[1:]:
        e = x - phi_c * anterior
        e2 = e * e
        soma_e2 += e2
        soma_w += anterior * anterior * e2
        anterior = x
    s2 = soma_e2 / (n - 1)
    se_rob = math.sqrt(soma_w) / sxx
    t_nulo = (phi_c - lam_a) / se_rob if se_rob > 0.0 else NAN
    valido = bool(0.0 < phi_c < 1.0 and _finito(se_rob) and se_rob > 0.0)
    if not valido:
        return AjusteOU(phi_hat, phi_c, s2, se_rob, t_nulo, NAN, NAN, NAN, NAN, NAN, n, False)
    theta = -math.log(phi_c)
    meia_vida = LN2 / theta
    sigma_eq = math.sqrt(s2 / (1.0 - phi_c * phi_c))
    theta_x = math.log(lam_a / phi_c)
    se_theta = se_rob / phi_c
    return AjusteOU(phi_hat, phi_c, s2, se_rob, t_nulo, theta, meia_vida, sigma_eq, theta_x, se_theta, n, True)


def sigma_retorno(r: Sequence[float]) -> float:
    """(15) sigma_r = sqrt(soma r_t^2 / N); nan sem retornos."""
    rs = [x for x in r if x == x]
    if not rs:
        return NAN
    return math.sqrt(sum(x * x for x in rs) / len(rs))


def calibrar_t_crit(n: int, lam_a: float, replicas: int = 1000, alpha: float = 0.001, semente: int = 7) -> float:
    """(11) t_crit = quantil alpha de t_nulo sob a nula simulada com o mesmo N e lambda_A; determinista.

    A nula e simulada pela identidade (5), d_t = lambda_A (d_{t-1} + eps_t), com eps
    gaussiano unitario e d_0 na distribuicao estacionaria: e o passeio aleatorio visto
    por uma ancora ja em regime, sem efeitos de arranque. Quantil com interpolacao
    linear (tipo 7) sobre as replicas ordenadas. Cerca de -3,0 para N = 720 e h_A = 96.
    """
    # NOTA ESPEC: a especificacao diz "cerca de -3,3" (quantil de 600 replicas, que e o
    # minimo da amostra e por isso ruidoso). Com 20 000 replicas desta mesma nula o quantil
    # 0,001 e -2,95 (o 0,0005 e -3,26; media 0,31 e desvio 1,12, como na especificacao).
    # Com 1000 replicas e a semente 7 sai -3,02; entre sementes varia de -2,7 a -3,4. O
    # tipo 6 (quase o minimo) seria menos enviesado para a cauda mas muito mais ruidoso.
    if n < 3:
        raise ValueError("n tem de ser pelo menos 3")
    if not (lam_a == lam_a and 0.0 < lam_a < 1.0):
        raise ValueError("lambda_A tem de estar em (0, 1)")
    if replicas < 1 or not (0.0 < alpha < 1.0):
        raise ValueError("replicas >= 1 e alpha em (0, 1)")
    rng = random.Random(semente)
    gauss = rng.gauss
    escala_0 = lam_a / math.sqrt(1.0 - lam_a * lam_a)
    ts: List[float] = []
    for _ in range(replicas):
        d = [0.0] * n
        anterior = gauss(0.0, 1.0) * escala_0
        d[0] = anterior
        for t in range(1, n):
            anterior = lam_a * (anterior + gauss(0.0, 1.0))
            d[t] = anterior
        aj = ajustar_ar1(d, lam_a)
        if aj.t_nulo == aj.t_nulo:
            ts.append(aj.t_nulo)
    return _quantil(ts, alpha)


# --------------------------------------------------------------------------
# 4.6 Racio de variancias robusto (Lo e MacKinlay)
# --------------------------------------------------------------------------
def racio_variancias(r: Sequence[float], q: int) -> Tuple[float, float]:
    """(16)-(21) VR(q) e z*(q) robusto a heteroscedasticidade; ValueError se q < 2 ou len(r) < 2q.

    Referencia: para um OU com phi = 2^(-1/H), VR(q) = (1 - phi^q) / (q (1 - phi)), ou seja
    0,753 com H = 8 e q = 8 (e abaixo de 0,7 so com H <= 6). Em dados homoscedasticos z*
    e z_simples (22) coincidem a menos do ruido de delta_j, cerca de 3 % com N = 720.
    """
    n = len(r)
    if q < 2:
        raise ValueError("q tem de ser pelo menos 2")
    if n < 2 * q:
        raise ValueError("sao precisos pelo menos 2q retornos")
    if any(x != x for x in r):
        return NAN, NAN
    mu = sum(r) / n
    desvios = [x - mu for x in r]
    soma_a = sum(dv * dv for dv in desvios)
    if soma_a <= 0.0:
        return NAN, NAN
    sigma1 = soma_a / (n - 1)                                     # (16)
    acum = [0.0] * (n + 1)
    for i, x in enumerate(r):
        acum[i + 1] = acum[i] + x
    m = (n - q + 1) * (1.0 - q / n)
    soma_q = 0.0
    qmu = q * mu
    for i in range(q, n + 1):                                     # x_t - x_{t-q}, t = q..N
        dq = acum[i] - acum[i - q] - qmu
        soma_q += dq * dq
    sigma_q = soma_q / m                                           # (17)
    vr = sigma_q / (q * sigma1)                                    # (18)
    a = [dv * dv for dv in desvios]
    theta_q = 0.0
    for j in range(1, q):                                          # (19)(20)
        soma_j = 0.0
        for t in range(j, n):
            soma_j += a[t] * a[t - j]
        delta_j = n * soma_j / (soma_a * soma_a)
        peso = 2.0 * (q - j) / q
        theta_q += peso * peso * delta_j
    z = math.sqrt(n) * (vr - 1.0) / math.sqrt(theta_q) if theta_q > 0.0 else NAN   # (21)
    return vr, z


def z_simples_vr(vr: float, q: int, n: int) -> float:
    """(22) z homoscedastico do VR, so para o teste de implementacao."""
    if q < 2 or n < 1:
        raise ValueError("q >= 2 e n >= 1")
    return (vr - 1.0) / math.sqrt(2.0 * (2 * q - 1) * (q - 1) / (3.0 * q * n))


# --------------------------------------------------------------------------
# 4.7 Volatilidade de Parkinson em velas de 15 m
# --------------------------------------------------------------------------
def parkinson(h: float, l: float) -> float:
    """(23) rp = (ln(h/l))^2 / (4 ln 2); ValueError se h < l ou l <= 0."""
    if not (l == l and h == h) or l <= 0.0 or h < l:
        raise ValueError("vela impossivel: e preciso h >= l > 0")
    lhl = math.log(h / l)
    return lhl * lhl / (4.0 * LN2)


class VolParkinson:
    """(24)(25) EWMA curta e longa de rp; sigma_15 = sqrt(v_l); vol_razao = sqrt(v_c / v_l).

    Sem velas tudo e nan. Com as duas EWMA a zero (velas sem amplitude) vol_razao vale 1:
    nao ha choque sem amplitude.
    """

    def __init__(self, h_curta: float, h_longa: float):
        if not (0 < h_curta <= h_longa):
            raise ValueError("meias-vidas: 0 < h_curta <= h_longa")
        self._vc = nu.Ewma(h_curta)
        self._vl = nu.Ewma(h_longa)

    def juntar(self, h: float, l: float) -> None:
        rp = parkinson(h, l)
        self._vc.juntar(rp)
        self._vl.juntar(rp)

    @property
    def n(self) -> int:
        return self._vl.n

    @property
    def sigma_15(self) -> float:
        if self._vl.n == 0:
            return NAN
        return math.sqrt(self._vl.valor)

    @property
    def vol_razao(self) -> float:
        if self._vl.n == 0:
            return NAN
        vl = self._vl.valor
        vc = self._vc.valor
        if vl > 0.0:
            return math.sqrt(vc / vl)
        return 1.0 if vc == 0.0 else NAN


# --------------------------------------------------------------------------
# 4.8 a 4.10 Veto diario, bandas, quantil empirico, posicionamento
# --------------------------------------------------------------------------
def veto_dia(x_t: float, x_abertura: float, sigma_r: float, k_dia: float) -> bool:
    """(26)(27) |x_t - x_00UTC| > k_dia sigma_r sqrt(24); k_dia <= 0 desliga; nan nao veta."""
    if not (k_dia == k_dia and k_dia > 0.0):
        return False
    if not (_finito(x_t) and _finito(x_abertura) and _finito(sigma_r)):
        return False
    return abs(x_t - x_abertura) > k_dia * sigma_r * math.sqrt(24.0)


def z_score(ln_c: float, a: float, sigma_eq: float) -> float:
    """(28)(36) z = (ln c - A) / sigma_eq; nan se sigma_eq nao e positivo."""
    if not (sigma_eq == sigma_eq and sigma_eq > 0.0) or not (_finito(ln_c) and _finito(a)):
        return NAN
    return (ln_c - a) / sigma_eq


def niveis(a: float, sigma_eq: float, z: float) -> Tuple[float, float]:
    """(29) (exp(A - z sigma_eq), exp(A + z sigma_eq)); ValueError com sigma_eq ou z negativos."""
    if sigma_eq != sigma_eq or sigma_eq < 0.0 or z != z or z < 0.0:
        raise ValueError("sigma_eq e z tem de ser nao negativos")
    return math.exp(a - z * sigma_eq), math.exp(a + z * sigma_eq)


def quantil_abs_z(zs: Sequence[float], p: float = P_CAUDA_NORMAL) -> float:
    """(30) quantil empirico p de |z_s| (tipo 7) sobre velas anteriores; o chamador exclui a vela corrente."""
    return _quantil([abs(z) for z in zs if z == z], p)


def z_in_efectivo(z_in: float, q_z: float, z_in_emp_max: float) -> float:
    """(31) z_in_ef = z_in se q_z <= z_in_emp_max (ou q_z desconhecido), senao q_z."""
    if q_z != q_z or q_z <= z_in_emp_max:
        return z_in
    return q_z


def z_robusto(x: float, historico: Sequence[float]) -> float:
    """(32) (x - mediana) / MAD; nan se MAD = 0, historico vazio ou x desconhecido."""
    if x != x:
        return NAN
    med = nu.mediana(historico)
    if med != med:
        return NAN
    mad = nu.mediana([abs(y - med) for y in historico if y == y])
    if not (mad > 0.0):
        return NAN
    return (x - med) / mad


def oi_usd(open_interest: float, mark_px: float) -> float:
    """(33) OI = openInterest x markPx (H3); ValueError com OI negativo ou mark nao positivo."""
    if open_interest < 0.0 or mark_px <= 0.0:
        raise ValueError("openInterest >= 0 e markPx > 0")
    return open_interest * mark_px


def premio_bps(mark: float, oraculo: float) -> float:
    """(34) pm = BPS (markPx - oraclePx) / oraclePx; ValueError se oraclePx <= 0."""
    if oraculo <= 0.0:
        raise ValueError("oraclePx tem de ser positivo")
    return BPS * (mark - oraculo) / oraculo


# --------------------------------------------------------------------------
# 4.11 Estado do contexto
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ParametrosContexto:
    t_amarelo: float = -2.0
    vr_max_verde: float = 1.0
    vr_veto: float = 1.2
    zvr_veto: float = 2.0
    h_min: float = 3.0
    h_max: float = 24.0
    vol_razao_max: float = 2.0
    n_min: int = 480
    z_veto: float = 4.0
    desloc_alvo_max: float = 0.5   # (60) deslocacao do alvo pela ancora reestimada, em sigma_0


@dataclass(frozen=True)
class Contexto:
    estado: str
    motivos: Tuple[str, ...]
    aj: AjusteOU
    vr: float
    z_vr: float
    vol_razao: float
    veto_dia: bool
    t_crit: float


def classificar_contexto(aj: AjusteOU, vr: float, z_vr: float, vol_razao: float, veto_dia: bool,
                         t_crit: float, cfg: ParametrosContexto) -> Contexto:
    """(35) VERDE, AMARELO ou VERMELHO com os motivos (nulo, vr, meia_vida, vol, dia, ajuste); nan falha."""
    motivos: List[str] = []
    amarelo = False
    if aj.t_nulo == aj.t_nulo and t_crit == t_crit and aj.t_nulo <= t_crit:
        pass
    elif aj.t_nulo == aj.t_nulo and t_crit == t_crit and t_crit < aj.t_nulo <= cfg.t_amarelo:
        amarelo = True
        motivos.append("nulo")
    else:
        motivos.append("nulo")
    if not (vr == vr and vr < cfg.vr_max_verde):
        motivos.append("vr")
    if not (aj.meia_vida == aj.meia_vida and cfg.h_min <= aj.meia_vida <= cfg.h_max):
        motivos.append("meia_vida")
    if not (vol_razao == vol_razao and vol_razao <= cfg.vol_razao_max):
        motivos.append("vol")
    if veto_dia:
        motivos.append("dia")
    if not aj.valido or aj.n < cfg.n_min:
        motivos.append("ajuste")
    if not motivos:
        estado = VERDE
    elif amarelo and motivos == ["nulo"]:
        estado = AMARELO
    else:
        estado = VERMELHO
    return Contexto(estado, tuple(motivos), aj, vr, z_vr, vol_razao, bool(veto_dia), t_crit)


# --------------------------------------------------------------------------
# 5. Gatilho no fecho da vela de 15 m
# --------------------------------------------------------------------------
def k_maximo(meia_vida: float, k_h: float, tecto: int) -> int:
    """(37) k_max = min(tecto, round(8 k_H H_0)) em velas de 15 m; ValueError com H nao positivo."""
    if not (meia_vida == meia_vida and meia_vida > 0.0) or k_h <= 0.0 or tecto < 1:
        raise ValueError("meia_vida > 0, k_h > 0 e tecto >= 1")
    return int(min(tecto, int(round(8.0 * k_h * meia_vida))))


class Excursao:
    """Seccao 5.2: maquina DENTRO/FORA por activo com ext, k_fora, lado_exc e oi_inicio.

    k_fora conta os fechos FORA, o da saida incluido. A excursao termina no primeiro fecho
    com |z| < z_in_ef: terminou fica True nesse fecho e terminou_mesmo_lado diz se acabou do
    lado por onde saiu. estado_prev e z_ant sao o estado e o z registados no fecho anterior.
    Um z ou z_in_ef desconhecido (nan) devolve a maquina a DENTRO sem sinal.
    """

    def __init__(self) -> None:
        self.estado = DENTRO
        self.estado_prev = DENTRO
        self.ext = 0.0
        self.k_fora = 0
        self.lado_exc = 0
        self.oi_inicio = NAN
        self.z = NAN
        self.z_ant = NAN
        self.terminou = False
        self.terminou_mesmo_lado = False

    def actualizar(self, z: float, z_in_ef: float, oi: float = NAN) -> None:
        self.estado_prev = self.estado
        self.z_ant = self.z
        self.z = z
        self.terminou = False
        self.terminou_mesmo_lado = False
        if z != z or z_in_ef != z_in_ef:
            self.estado = DENTRO
            return
        if abs(z) >= z_in_ef:
            if self.estado_prev != FORA:
                self.lado_exc = _sinal(z)
                self.oi_inicio = oi
                self.k_fora = 0
                self.ext = abs(z)
            self.k_fora += 1
            self.ext = max(self.ext, abs(z))
            self.estado = FORA
        else:
            if self.estado_prev == FORA:
                self.terminou = True
                self.terminou_mesmo_lado = _sinal(z) == self.lado_exc
            self.estado = DENTRO


@dataclass(frozen=True)
class ParametrosGatilho:
    z_out: float = 0.5
    z_min_resto: float = 0.75
    z_veto: float = 4.0
    k_choque: float = 4.0
    vol_razao_max: float = 2.0


def gatilho_reentrada(estado_prev: str, lado_exc: int, ext: float, k_fora: int, k_max: int, z_prev: float,
                      z_k: float, z_in_ef: float, r_k: float, sigma_15: float, vol_razao_15: float,
                      cfg: ParametrosGatilho) -> Tuple[int, str]:
    """G2 a G5 no fecho k: (lado = -sign(z_k), 'ok') ou (0, primeira condicao falhada em G2..G5); nan falha."""
    za = abs(z_k) if z_k == z_k else NAN
    if not (estado_prev == FORA and za == za and za < z_in_ef and _sinal(z_k) == lado_exc
            and z_prev == z_prev and za < abs(z_prev)):
        return 0, "G2"
    if not (za >= cfg.z_out + cfg.z_min_resto):
        return 0, "G3"
    if not (ext == ext and ext < cfg.z_veto and k_fora <= k_max):
        return 0, "G4"
    if not (r_k == r_k and sigma_15 == sigma_15 and abs(r_k) <= cfg.k_choque * sigma_15
            and vol_razao_15 == vol_razao_15 and vol_razao_15 <= cfg.vol_razao_max):
        return 0, "G5"
    return -_sinal(z_k), "ok"


# --------------------------------------------------------------------------
# 6.1 Fluxo agressor (canal trades)
# --------------------------------------------------------------------------
def lado_agressor(side: str) -> int:
    """s_i = +1 se side = B, -1 se side = A; ValueError para outro texto."""
    t = (side or "").strip().upper()
    if t == "B":
        return 1
    if t == "A":
        return -1
    raise ValueError("side desconhecido: %r" % side)


def agressor(users: Sequence[str], s: int) -> Tuple[str, str]:
    """(agressor, passivo) com users = [comprador, vendedor] (H2): users[0] se s = +1, users[1] se s = -1."""
    if s not in (1, -1):
        raise ValueError("s tem de ser +1 ou -1")
    if not users or len(users) < 2:
        return "", ""
    comprador = str(users[0] or "")
    vendedor = str(users[1] or "")
    return (comprador, vendedor) if s > 0 else (vendedor, comprador)


def hash_endereco(endereco: str) -> str:
    """h = sha256(endereco em minusculas)[:10]; o endereco inteiro nunca se guarda."""
    texto = (endereco or "").strip().lower()
    if not texto:
        return ""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:10]


class JanelaSomaCausal(nu.JanelaSoma):
    """nucleo.JanelaSoma cuja soma(agora) so conta os itens com agora - duracao < t <= agora.

    O JanelaSoma do nucleo descarta o que e anterior a janela mas conta tudo o que ja foi
    junto, mesmo com t posterior a `agora`; aqui o que chegou depois do instante pedido (os
    negocios da folga entre T_k e a avaliacao, por exemplo) fica de fora, como pede a
    seccao 5.4: as janelas de fluxo terminam em T_k. Os itens chegam por ordem de tempo.
    """

    def soma(self, agora_ms: int) -> float:
        total = super().soma(agora_ms)
        for t, v in reversed(self._itens):
            if t <= agora_ms:
                break
            total -= v
        return total


class FluxoAgressor:
    """(38)(39) V_B e V_A moveis (JanelaSomaCausal sobre nucleo.JanelaSoma) e OFI = (V_B - V_A) / (V_B + V_A); nan sem volume."""

    def __init__(self, janela_ms: int):
        if janela_ms <= 0:
            raise ValueError("janela_ms tem de ser positiva")
        self._vb = JanelaSomaCausal(janela_ms)
        self._va = JanelaSomaCausal(janela_ms)

    def negocio(self, t_ms: int, s: int, ntl: float) -> None:
        if ntl != ntl or ntl < 0.0:
            raise ValueError("notional tem de ser nao negativo")
        if s > 0:
            self._vb.juntar(t_ms, ntl)
        elif s < 0:
            self._va.juntar(t_ms, ntl)
        else:
            raise ValueError("s tem de ser +1 ou -1")

    def v_b(self, agora_ms: int) -> float:
        return self._vb.soma(agora_ms)

    def v_a(self, agora_ms: int) -> float:
        return self._va.soma(agora_ms)

    def ofi(self, agora_ms: int) -> float:
        vb, va = self.v_b(agora_ms), self.v_a(agora_ms)
        total = vb + va
        if total <= 0.0:
            return NAN
        return (vb - va) / total


# --------------------------------------------------------------------------
# 6.2 e 6.4 Negocios grandes, insistencia, rajadas, TWAP, HHI
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ParametrosTwap:
    min_negocios: int = 4
    janela_s: float = 600.0
    dt_min_s: float = 10.0
    dt_max_s: float = 50.0
    usar_hash: bool = True     # H5 confirmada: so negocios com hash a zeros; senao so a cadencia (twapsrc=cad)


class _PercentilComQuantil(nu.JanelaPercentil):
    """nucleo.JanelaPercentil com o quantil p das amostras guardadas (tipo 7)."""

    def quantil(self, p: float) -> float:
        n = len(self._ord)
        if n == 0:
            return NAN
        pos = p * (n - 1)
        i = int(math.floor(pos))
        if i >= n - 1:
            return self._ord[-1]
        return self._ord[i] + (pos - i) * (self._ord[i + 1] - self._ord[i])


# registo de um negocio: (t_ms, s, ntl, h_agr, h_pas, px, hash_zero, grande)
_Negocio = Tuple[int, int, float, str, str, float, bool, bool]


class NegociosGrandes:
    """(41)-(44),(47)-(49) Q99 em n_max amostras, FLX e REP na hora, rajadas, TWAP e HHI passivo.

    Um negocio e grande face ao quantil p das amostras anteriores a ele; antes de haver
    ceil(1/(1-p)) amostras nao ha grandes. Os negocios ficam em memoria janela_24h_ms
    (e no maximo n_max), o que chega para as janelas de 1 h, 600 s e 60 s das medidas.
    """

    def __init__(self, n_max: int, p: float, janela_24h_ms: int):
        if n_max <= 0 or not (0.0 < p < 1.0) or janela_24h_ms <= 0:
            raise ValueError("n_max > 0, p em (0, 1) e janela_24h_ms > 0")
        self.p = p
        self.janela_ms = janela_24h_ms
        self.n_min = int(math.ceil(1.0 / (1.0 - p)))
        self._ntl = _PercentilComQuantil(n_max)
        self._neg: Deque[_Negocio] = deque(maxlen=n_max)

    def negocio(self, t_ms: int, s: int, ntl: float, h_agr: str, h_pas: str, px: float, hash_zero: bool) -> None:
        if s not in (1, -1) or ntl != ntl or ntl < 0.0 or px != px or px <= 0.0:
            raise ValueError("negocio impossivel")
        q = self.q99()
        grande = bool(q == q and ntl >= q)
        self._ntl.juntar(ntl)
        self._neg.append((int(t_ms), s, ntl, h_agr or "", h_pas or "", px, bool(hash_zero), grande))
        limite = int(t_ms) - self.janela_ms
        while self._neg and self._neg[0][0] <= limite:
            self._neg.popleft()

    def q99(self) -> float:
        """(41) quantil p do notional; nan com menos de ceil(1/(1-p)) amostras."""
        if len(self._ntl) < self.n_min:
            return NAN
        return self._ntl.quantil(self.p)

    def _desde(self, limite_ms: int, agora_ms: int) -> List[_Negocio]:
        return [n for n in self._neg if limite_ms < n[0] <= agora_ms]

    def n_grandes(self, t0_ms: int, t1_ms: int) -> int:
        """(41) numero de negocios grandes com t0 <= time <= t1 (a vela [t, T]); os posteriores a T nao contam."""
        return sum(1 for n in self._neg if n[7] and t0_ms <= n[0] <= t1_ms)

    def flx(self, agora_ms: int) -> float:
        """(42) FLX_1h = soma_{grandes} s ntl / soma_{grandes} ntl na ultima hora; nan sem grandes."""
        liquido = 0.0
        bruto = 0.0
        for n in self._desde(agora_ms - JANELA_1H_MS, agora_ms):
            if n[7]:
                liquido += n[1] * n[2]
                bruto += n[2]
        return liquido / bruto if bruto > 0.0 else NAN

    def repetidos(self, agora_ms: int, excluidos: Set[str]) -> int:
        """(43) REP_1h = n_rep+ - n_rep-: agressores com >= 3 grandes na hora, sinal pelo liquido, sem MM nem excluidos."""
        contagem: Dict[str, int] = {}
        liquido: Dict[str, float] = {}
        bruto: Dict[str, float] = {}
        for n in self._desde(agora_ms - JANELA_1H_MS, agora_ms):
            h = n[3]
            if not h or h in excluidos:
                continue
            liquido[h] = liquido.get(h, 0.0) + n[1] * n[2]
            bruto[h] = bruto.get(h, 0.0) + n[2]
            if n[7]:
                contagem[h] = contagem.get(h, 0) + 1
        rep = 0
        for h, c in contagem.items():
            if c < 3:
                continue
            if abs(liquido[h]) < 0.1 * bruto[h]:   # market maker: liquido pequeno face ao bruto
                continue
            rep += 1 if liquido[h] > 0.0 else -1
        return rep

    def rajadas(self, agora_ms: int, silencio_s: float, dt_ms: int, amp_bps: float) -> List[Tuple[int, int, str]]:
        """(47) rajadas (raj_ms, s_raj, hash do agressor) acabadas em [agora - silencio_s, agora]: blocos do mesmo agressor e lado com intervalos <= dt_ms, soma ntl >= Q99 e amplitude >= amp_bps; por ordem de tempo."""
        q = self.q99()
        if q != q:
            return []
        amp_min = amp_bps / BPS
        limite = agora_ms - int(silencio_s * 1000)
        # bloco aberto por agressor: [t_ultimo, px_primeiro, px_ultimo, s, soma_ntl]
        blocos: Dict[str, List[float]] = {}
        saida: List[Tuple[int, int, str]] = []

        def qualifica(b: List[float]) -> bool:
            return b[4] >= q and abs(math.log(b[2] / b[1])) >= amp_min and limite <= b[0] <= agora_ms

        for n in self._neg:
            t, s, ntl, h, _, px = n[0], n[1], n[2], n[3], n[4], n[5]
            if t > agora_ms or not h:
                continue
            b = blocos.get(h)
            if b is not None and t - b[0] <= dt_ms and b[3] == s:
                b[0] = t
                b[2] = px
                b[4] += ntl
                continue
            if b is not None and qualifica(b):
                saida.append((int(b[0]), int(b[3]), h))
            blocos[h] = [t, px, px, s, ntl]
        for h, b in blocos.items():
            if qualifica(b):
                saida.append((int(b[0]), int(b[3]), h))
        saida.sort()
        return saida

    def rajada_contra(self, agora_ms: int, lado: int, silencio_s: float, dt_ms: int, amp_bps: float) -> bool:
        """(47)(48) existe bloco do mesmo agressor e lado (intervalos <= dt_ms) com soma ntl >= Q99 e amplitude >= amp_bps, do lado -lado, acabado ha menos de silencio_s."""
        if lado not in (1, -1):
            raise ValueError("lado tem de ser +1 ou -1")
        return any(s == -lado for _, s, _ in self.rajadas(agora_ms, silencio_s, dt_ms, amp_bps))

    def lado_rajada(self, agora_ms: int, silencio_s: float, dt_ms: int, amp_bps: float) -> int:
        """(47) lado s em {-1, 0, +1} da ultima rajada acabada ha menos de silencio_s (0 sem rajada): a coluna raj das velas, independente do sinal."""
        lista = self.rajadas(agora_ms, silencio_s, dt_ms, amp_bps)
        return lista[-1][1] if lista else 0

    def twap(self, agora_ms: int, lado: int, cfg: ParametrosTwap) -> Tuple[int, str]:
        """(49) -1 se ha agressor com >= min_negocios fatias em cadencia [dt_min, dt_max] s na janela do lado -lado, +1 do lado lado, 0 senao; fonte 'hash' ou 'cad'.

        Uma fatia e o conjunto dos negocios do mesmo agressor e lado no mesmo instante (uma
        ordem que cruza varios niveis do livro gera varios negocios com o mesmo time): conta-se
        e mede-se a cadencia sobre os instantes distintos.
        """
        if lado not in (1, -1):
            raise ValueError("lado tem de ser +1 ou -1")
        fonte = "hash" if cfg.usar_hash else "cad"
        tempos: Dict[Tuple[str, int], Set[int]] = {}
        for n in self._desde(agora_ms - int(cfg.janela_s * 1000), agora_ms):
            if cfg.usar_hash and not n[6]:
                continue
            if not n[3]:
                continue
            tempos.setdefault((n[3], n[1]), set()).add(n[0])
        dt_min = cfg.dt_min_s * 1000.0
        dt_max = cfg.dt_max_s * 1000.0
        lados: Set[int] = set()
        for (_, s), instantes in tempos.items():
            ts = sorted(instantes)
            if len(ts) < cfg.min_negocios:
                continue
            if all(dt_min <= ts[i] - ts[i - 1] <= dt_max for i in range(1, len(ts))):
                lados.add(s)
        if -lado in lados:
            return -1, fonte
        if lado in lados:
            return 1, fonte
        return 0, fonte

    def hhi_passivo(self, p_inf: float, p_sup: float, t0_ms: int, t1_ms: int) -> float:
        """(44) HHI do notional passivo nos negocios de [t0, t1] com px <= p_inf ou px >= p_sup; nan sem negocios."""
        por_endereco: Dict[str, float] = {}
        for n in self._neg:
            if not (t0_ms <= n[0] <= t1_ms):
                continue
            if n[5] <= p_inf or n[5] >= p_sup:
                por_endereco[n[4]] = por_endereco.get(n[4], 0.0) + n[2]
        total = sum(por_endereco.values())
        if total <= 0.0:
            return NAN
        return sum((v / total) ** 2 for v in por_endereco.values())


# --------------------------------------------------------------------------
# 6.3 Absorcao residual
# --------------------------------------------------------------------------
def absorcao_residual(r_hist: Sequence[float], u_hist: Sequence[float], r_k: float, u_k: float,
                      lado: int) -> Tuple[float, float, float]:
    """(45)(46) beta = soma r u / soma u^2, s_e^2 = soma (r - beta u)^2 / (n - 1), a_k = lado (r_k - beta u_k) / s_e; nan com menos de 24 pares."""
    if len(r_hist) != len(u_hist):
        raise ValueError("r_hist e u_hist tem de ter o mesmo comprimento")
    pares = [(r, u) for r, u in zip(r_hist, u_hist) if r == r and u == u]
    n = len(pares)
    if n < 24:
        return NAN, NAN, NAN
    suu = sum(u * u for _, u in pares)
    if suu <= 0.0:
        return NAN, NAN, NAN
    beta = sum(r * u for r, u in pares) / suu
    s_e2 = sum((r - beta * u) ** 2 for r, u in pares) / (n - 1)
    s_e = math.sqrt(s_e2)
    if s_e <= 0.0 or r_k != r_k or u_k != u_k:
        return beta, s_e, NAN
    return beta, s_e, lado * (r_k - beta * u_k) / s_e


# --------------------------------------------------------------------------
# 6.5 e 6.6 Posicoes das baleias e liquidacoes
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Posicao:
    coin: str
    szi: float               # positivo = long, negativo = short
    position_value: float    # USD
    liquidation_px: float    # nan se nao houver


def posicoes_baleias(posicoes: Sequence[Posicao], preco: float, lado: int, pct_fuel: float, oi_usd: float,
                     faixa_bps: float, p_min_exc: float, p_max_exc: float) -> Tuple[float, float, int]:
    """(50)-(52) (POS liquido, FUEL/OI, iman); (nan, nan, 0) sem posicoes.

    FUEL segue a convencao de (53): o lado contrario ao sinal e o lado cujas liquidacoes
    sao fluxo contra a entrada, ou seja, as posicoes com o sinal do proprio lado (longs
    numa compra), cujo liquidationPx fica a menos de pct_fuel do preco na direccao do
    stop. iman: a faixa de faixa_bps com maior positionValue por liquidationPx, ate
    IMAN_ALCANCE do preco, foi tocada pela excursao [p_min_exc, p_max_exc] e o fecho de
    reentrada ficou para la dela do lado da ancora.
    """
    if lado not in (1, -1):
        raise ValueError("lado tem de ser +1 ou -1")
    if not (preco == preco and preco > 0.0):
        raise ValueError("preco tem de ser positivo")
    if not posicoes:
        return NAN, NAN, 0
    # NOTA ESPEC: (51) diz "posicoes do lado contrario ao sinal ... na direccao do stop". Um
    # short tem liquidationPx acima do preco e nunca na direccao do stop de uma compra; so as
    # posicoes com o sinal do proprio lado (longs numa compra) podem ser liquidadas a caminho do
    # stop, e e esse o fluxo contrario a entrada, na mesma convencao de (53). Implementa-se isso.
    pos = 0.0
    fuel = 0.0
    faixas: Dict[int, float] = {}
    largura = preco * faixa_bps / BPS
    for p in posicoes:
        if p.szi > 0.0:
            pos += p.position_value
        elif p.szi < 0.0:
            pos -= p.position_value
        lq = p.liquidation_px
        if not (lq == lq and lq > 0.0):
            continue
        if _sinal(p.szi) == lado:
            if lado > 0 and preco * (1.0 - pct_fuel) <= lq <= preco:
                fuel += p.position_value
            elif lado < 0 and preco <= lq <= preco * (1.0 + pct_fuel):
                fuel += p.position_value
        if abs(lq / preco - 1.0) <= IMAN_ALCANCE and largura > 0.0:
            i = int(math.floor((lq - preco) / largura))
            faixas[i] = faixas.get(i, 0.0) + p.position_value
    fuel_oi = fuel / oi_usd if oi_usd == oi_usd and oi_usd > 0.0 else NAN
    iman = 0
    if faixas and p_min_exc == p_min_exc and p_max_exc == p_max_exc:
        i_max = max(sorted(faixas), key=lambda k: faixas[k])
        f_lo = preco + i_max * largura
        f_hi = f_lo + largura
        tocou = p_min_exc <= f_hi and p_max_exc >= f_lo
        de_volta = preco > f_hi if lado > 0 else preco < f_lo
        iman = 1 if tocou and de_volta else 0
    return pos, fuel_oi, iman


def racio_liquidacoes(liq_contra_15m: float, max_excursao: float) -> float:
    """(53) liq_k = LIQ_contra_k / max LIQ_contra na excursao; nan sem chave ou sem maximo positivo."""
    if liq_contra_15m != liq_contra_15m or max_excursao != max_excursao or max_excursao <= 0.0:
        return NAN
    return liq_contra_15m / max_excursao


# --------------------------------------------------------------------------
# 6.7 Pontuacao e factor de tamanho
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ParametrosBaleias:
    limiar_flx: float = 0.3
    limiar_rep: int = 2
    limiar_fz: float = 1.0
    limiar_liq: float = 0.5
    liq_alto: float = 0.8
    limiar_abs: float = 1.0
    pct_fuel: float = 0.02
    pos_alto: float = 0.8
    pos_baixo: float = 0.1
    b_veto: int = -2
    fb_baixo: float = 0.5
    fb_alto: float = 1.25
    b_alto: int = 3


def pontuar_baleias(lado: int, flx: float, pos_flx: float, rep: int, z_f: float, doi_exc: float, liq: float,
                    dpos: float, pos_dpos: float, twap: int, a_k: float, u_k: float, pos_doi8: float,
                    cfg: ParametrosBaleias) -> Tuple[int, Dict[str, int]]:
    """(54) B_k = T1 + ... + T8, cada termo em {-1, 0, +1}; nan numa entrada da 0 nesse termo."""
    if lado not in (1, -1):
        raise ValueError("lado tem de ser +1 ou -1")
    t: Dict[str, int] = {}
    # T1 absorcao pelos grandes
    t1 = 0
    if flx == flx:
        if lado * flx >= cfg.limiar_flx and pos_flx == pos_flx and pos_flx >= cfg.pos_alto:
            t1 = 1
        elif lado * flx <= -cfg.limiar_flx:
            t1 = -1
    t["T1"] = t1
    # T2 insistencia
    t2 = 0
    if rep == rep:
        if lado * rep >= cfg.limiar_rep:
            t2 = 1
        elif lado * rep <= -cfg.limiar_rep:
            t2 = -1
    t["T2"] = t2
    # T3 multidao do lado errado
    t3 = 0
    if z_f == z_f:
        if lado * z_f <= -cfg.limiar_fz and doi_exc == doi_exc and doi_exc > 0.0:
            t3 = 1
        elif lado * z_f >= cfg.limiar_fz:
            t3 = -1
    t["T3"] = t3
    # T4 cascata esgotada (so com chave)
    t4 = 0
    if liq == liq:
        if liq <= cfg.limiar_liq:
            t4 = 1
        elif liq > cfg.liq_alto:
            t4 = -1
    t["T4"] = t4
    # T5 posicionamento das baleias
    t5 = 0
    if dpos == dpos and pos_dpos == pos_dpos and pos_dpos >= cfg.pos_alto:
        if lado * dpos > 0.0:
            t5 = 1
        elif lado * dpos < 0.0:
            t5 = -1
    t["T5"] = t5
    # T6 TWAP contra
    t["T6"] = -1 if twap == -1 else 0
    # T7 absorcao residual
    t7 = 0
    if a_k == a_k:
        if a_k >= cfg.limiar_abs and u_k == u_k and lado * u_k <= 0.0:
            t7 = 1
        elif a_k <= -cfg.limiar_abs:
            t7 = -1
    t["T7"] = t7
    # T8 desalavancagem
    t["T8"] = 1 if pos_doi8 == pos_doi8 and pos_doi8 <= cfg.pos_baixo else 0
    return sum(t.values()), t


def factor_tamanho(b: int, fuel_oi: float, cfg: ParametrosBaleias) -> float:
    """(55) f_B = fb_baixo se B <= 0, 1 se 1 <= B < b_alto, fb_alto se B >= b_alto; min(f_B, fb_baixo) se FUEL/OI > pct_fuel."""
    if b <= 0:
        f = cfg.fb_baixo
    elif b >= cfg.b_alto:
        f = cfg.fb_alto
    else:
        f = 1.0
    if fuel_oi == fuel_oi and fuel_oi > cfg.pct_fuel:
        f = min(f, cfg.fb_baixo)
    return f


# --------------------------------------------------------------------------
# 7. Risco e saida
# --------------------------------------------------------------------------
def tempo_maximo(meia_vida: float, n_h: float, tmax_h: float) -> Tuple[float, int]:
    """(56) tau_max = min(n_H H_0, tmax_h) em velas de 1 h e tmax_15 = round(4 tau_max); ValueError com H nao positivo."""
    if not (meia_vida == meia_vida and meia_vida > 0.0) or n_h <= 0.0 or tmax_h <= 0.0:
        raise ValueError("meia_vida, n_h e tmax_h tem de ser positivos")
    tau = min(n_h * meia_vida, tmax_h)
    return tau, int(round(4.0 * tau))


def factor_captura(phi: float, lam_a: float) -> float:
    """(57) rho = 1 - phi (1 - lambda_A) / (lambda_A (1 - phi)); exactamente 0 sob a nula phi = lambda_A."""
    if not (phi == phi and 0.0 < phi < 1.0) or not (lam_a == lam_a and 0.0 < lam_a < 1.0):
        raise ValueError("phi e lambda_A tem de estar em (0, 1)")
    return 1.0 - phi * (1.0 - lam_a) / (lam_a * (1.0 - phi))


def alvo_bps(d0: float, sigma0: float, phi0: float, lam_a: float, tau_max: float, z_out: float) -> float:
    """(58) G = BPS min(rho |d_0| (1 - phi_0^tau_max), |d_0| - z_out sigma_0); pode ser negativo (G8 recusa)."""
    if not (sigma0 == sigma0 and sigma0 > 0.0) or tau_max <= 0.0 or z_out < 0.0:
        raise ValueError("sigma_0 > 0, tau_max > 0 e z_out >= 0")
    if d0 != d0:
        return NAN
    rho = factor_captura(phi0, lam_a)
    esperado = rho * abs(d0) * (1.0 - phi0 ** tau_max)
    ate_banda = abs(d0) - z_out * sigma0
    return BPS * min(esperado, ate_banda)


def stop_bps(z0: float, sigma0: float, z_stop: float) -> float:
    """(59) L = BPS (z_stop - |z_0|) sigma_0; ValueError se |z_0| >= z_stop."""
    if z0 != z0 or abs(z0) >= z_stop:
        raise ValueError("a entrada tem de ficar aquem do stop: |z_0| < z_stop")
    if not (sigma0 == sigma0 and sigma0 > 0.0):
        raise ValueError("sigma_0 tem de ser positivo")
    return BPS * (z_stop - abs(z0)) * sigma0


def preco_stop(a0: float, sigma0: float, lado: int, z_stop: float) -> float:
    """(59) P_stop = exp(A_0 - lado z_stop sigma_0)."""
    if lado not in (1, -1):
        raise ValueError("lado tem de ser +1 ou -1")
    if not (sigma0 == sigma0 and sigma0 > 0.0) or z_stop <= 0.0:
        raise ValueError("sigma_0 e z_stop tem de ser positivos")
    return math.exp(a0 - lado * z_stop * sigma0)


def preco_alvo(a0: float, sigma0: float, lado: int, z_out: float) -> float:
    """(58)(59) P_alvo = exp(A_0 - lado z_out sigma_0): banda z_out do MESMO lado da entrada (a ultima meia sigma antes da ancora)."""
    # NOTA ESPEC: a seccao 7.5 escreve P_alvo = exp(A_0 + lado z_out sigma_0), que e a banda do lado
    # OPOSTO da ancora (numa compra com z_0 = -1,9 seria z = +0,5, a 2,4 sigma da entrada) e contradiz
    # (58), que limita G a |d_0| - z_out sigma_0, (62), que e a probabilidade de |z| tocar z_out antes de
    # z_stop do mesmo lado, G3 (caminho |z_k| - z_out) e a definicao de z_out como a ultima meia sigma
    # antes da ancora. Implementa-se o mesmo lado, na convencao de sinal de preco_stop.
    if lado not in (1, -1):
        raise ValueError("lado tem de ser +1 ou -1")
    if not (sigma0 == sigma0 and sigma0 > 0.0) or z_out < 0.0:
        raise ValueError("sigma_0 tem de ser positivo e z_out nao negativo")
    return math.exp(a0 - lado * z_out * sigma0)


def invalidar(t_nulo: float, vr: float, z_vr: float, z_abs: float, desloc_alvo_sigma: float,
              cfg: ParametrosContexto) -> Optional[str]:
    """(60) primeiro motivo de invalidacao em (nulo, vr, z_veto, ancora) ou None; nan nao invalida.

    desloc_alvo_sigma = -lado (A_t - A_0) / sigma_0: deslocacao do alvo (banda z_out do mesmo lado,
    preco_alvo) pela ancora reestimada, em sigma_0, positiva quando a ancora se afastou do lado em
    que o trade aposta (numa compra, a ancora a descer baixa o destino esperado da reversao).
    """
    if t_nulo == t_nulo and t_nulo > cfg.t_amarelo:
        return "nulo"
    if vr == vr and z_vr == z_vr and vr > cfg.vr_veto and z_vr > cfg.zvr_veto:
        return "vr"
    if z_abs == z_abs and abs(z_abs) >= cfg.z_veto:
        return "z_veto"
    if desloc_alvo_sigma == desloc_alvo_sigma and desloc_alvo_sigma > cfg.desloc_alvo_max:
        return "ancora"
    return None


def funcao_escala(z: float, passos: int = 200) -> float:
    """(61) S(z) = integral de 0 a z de exp(u^2/2) du por Simpson com `passos` intervalos (par); S(-z) = -S(z)."""
    if z != z:
        return NAN
    if passos < 2:
        raise ValueError("passos tem de ser pelo menos 2")
    if passos % 2:
        passos += 1
    a = abs(z)
    if a == 0.0:
        return 0.0
    h = a / passos
    soma = math.exp(0.0) + math.exp(a * a / 2.0)
    for i in range(1, passos):
        u = i * h
        soma += (4.0 if i % 2 else 2.0) * math.exp(u * u / 2.0)
    valor = soma * h / 3.0
    return valor if z > 0.0 else -valor


def prob_alvo_antes_stop(z0: float, z_out: float, z_stop: float) -> float:
    """(62) P_teo = (S(z_stop) - S(|z_0|)) / (S(z_stop) - S(z_out)); ValueError fora de z_out <= |z_0| <= z_stop."""
    if z0 != z0 or z_out < 0.0 or z_stop <= z_out or not (z_out <= abs(z0) <= z_stop):
        raise ValueError("geometria impossivel: 0 <= z_out <= |z_0| <= z_stop e z_out < z_stop")
    s_stop = funcao_escala(z_stop)
    return (s_stop - funcao_escala(abs(z0))) / (s_stop - funcao_escala(z_out))


@dataclass(frozen=True)
class Vela:
    t: int                 # inicio (ms)
    T: int                 # fim (ms)
    o: float
    h: float
    l: float
    c: float
    v: float               # volume em unidades do activo
    n: int                 # numero de negocios
    completa: bool = True


@dataclass(frozen=True)
class Saida:
    motivo: str            # alvo, stop, tempo, invalidacao
    preco: float
    velas: int
    r_bruto_bps: float     # (77)
    funding_bps: float     # (77) BPS lado soma F_h
    mae_bps: float         # pior excursao adversa, >= 0
    mfe_bps: float         # melhor excursao favoravel, >= 0
    t_ms: int              # fim da vela da saida


@dataclass(frozen=True)
class Sinal:
    hora_ms: int
    ativo: str
    lado: int
    preco: float
    alvo_bps: float
    tamanho_usd: float
    nota: str


class TradeVirtual:
    """Seccao 7.5 e (77): trade virtual avancado a cada fecho de 15 m.

    Ordem no fecho: stop se a minima/maxima (ou o mark) toca P_stop, preenchido no nivel
    com deslize imp_bps contra OU na abertura da vela se ela ja abriu para la do stop (o
    pior dos dois: num salto o stop nao preenche ao nivel); senao alvo se o fecho passa
    P_alvo, preenchido em P_alvo (ordem passiva no nivel); senao tempo ao fecho da vela
    tmax_15. f_hora e o funding cobrado nesta vela (0 quando nenhuma hora inteira fechou
    dentro do trade).
    """

    def __init__(self, lado: int, p_entrada: float, p_alvo: float, p_stop: float, tmax_15: int, imp_bps: float):
        if lado not in (1, -1):
            raise ValueError("lado tem de ser +1 ou -1")
        if not (p_entrada > 0.0 and p_alvo > 0.0 and p_stop > 0.0):
            raise ValueError("precos tem de ser positivos")
        if lado * (p_alvo - p_entrada) <= 0.0 or lado * (p_entrada - p_stop) <= 0.0:
            raise ValueError("alvo e stop tem de ficar de lados opostos da entrada")
        if tmax_15 < 1:
            raise ValueError("tmax_15 tem de ser pelo menos 1")
        if imp_bps != imp_bps or imp_bps < 0.0:
            raise ValueError("imp_bps tem de ser nao negativo")
        self.lado = lado
        self.p_entrada = p_entrada
        self.p_alvo = p_alvo
        self.p_stop = p_stop
        self.tmax_15 = tmax_15
        self.imp_bps = imp_bps
        self.velas = 0
        self.funding_bps = 0.0
        self._mae = 0.0
        self._mfe = 0.0
        self.saida: Optional[Saida] = None

    @property
    def mae_bps(self) -> float:
        return self._mae

    @property
    def mfe_bps(self) -> float:
        return self._mfe

    @property
    def fechado(self) -> bool:
        return self.saida is not None

    def _fechar(self, motivo: str, preco: float, t_ms: int) -> Saida:
        r_bruto = BPS * self.lado * math.log(preco / self.p_entrada)
        self.saida = Saida(motivo, preco, self.velas, r_bruto, self.funding_bps, self._mae, self._mfe, t_ms)
        return self.saida

    def avancar(self, vela: Vela, mark_min: float = NAN, mark_max: float = NAN, f_hora: float = 0.0) -> Optional[Saida]:
        if self.saida is not None:
            raise ValueError("o trade virtual ja fechou")
        if vela.l <= 0.0 or vela.h < vela.l or not (vela.l <= vela.c <= vela.h):
            raise ValueError("vela impossivel")
        self.velas += 1
        if f_hora == f_hora:
            self.funding_bps += self.lado * f_hora * BPS
        lo = min(vela.l, mark_min) if mark_min == mark_min and mark_min > 0.0 else vela.l
        hi = max(vela.h, mark_max) if mark_max == mark_max and mark_max > 0.0 else vela.h
        if self.lado > 0:
            favoravel = BPS * math.log(hi / self.p_entrada)
            adverso = BPS * math.log(self.p_entrada / lo)
            tocou_stop = lo <= self.p_stop
            passou_alvo = vela.c >= self.p_alvo
        else:
            favoravel = BPS * math.log(self.p_entrada / lo)
            adverso = BPS * math.log(hi / self.p_entrada)
            tocou_stop = hi >= self.p_stop
            passou_alvo = vela.c <= self.p_alvo
        self._mfe = max(self._mfe, favoravel)
        self._mae = max(self._mae, adverso)
        if tocou_stop:
            p_nivel = self.p_stop * (1.0 - self.lado * self.imp_bps / BPS)
            # salto por cima do stop: a ordem preenche na abertura, nao no nivel
            if vela.o > 0.0 and self.lado * (self.p_stop - vela.o) > 0.0:
                p_nivel = vela.o
            return self._fechar("stop", p_nivel, vela.T)
        if passou_alvo:
            return self._fechar("alvo", self.p_alvo, vela.T)
        if self.velas >= self.tmax_15:
            return self._fechar("tempo", vela.c, vela.T)
        return None

    def invalidar(self, motivo: str, p_fecho: float, t_ms: int = 0) -> Saida:
        if self.saida is not None:
            raise ValueError("o trade virtual ja fechou")
        if not (p_fecho > 0.0):
            raise ValueError("preco de fecho tem de ser positivo")
        return self._fechar(motivo or "invalidacao", p_fecho, t_ms)


# --------------------------------------------------------------------------
# 7.6 Disjuntores
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ParametrosDisjuntores:
    perdas_dia_x_g: float = 3.0
    perdas_seguidas: int = 5
    cvar_x_l: float = 2.0
    cvar_fraccao: float = 0.05
    cvar_n: int = 100


def disjuntores(r_liq_dia: Sequence[float], stops_seguidos: int, r_liq_ultimos: Sequence[float],
                g_med: float, l_med: float, cfg: ParametrosDisjuntores) -> Optional[str]:
    """(63) primeiro disjuntor disparado em (perdas_dia, seguidas, cvar) ou None; o regime fica no sinalizador.

    cvar: media dos ceil(cvar_fraccao n) piores r_liq dos ultimos cvar_n (pelo menos 1).
    """
    dia = [x for x in r_liq_dia if x == x]
    if dia and g_med == g_med and sum(dia) <= -cfg.perdas_dia_x_g * g_med:
        return "perdas_dia"
    if stops_seguidos >= cfg.perdas_seguidas:
        return "seguidas"
    ultimos = [x for x in r_liq_ultimos if x == x][-cfg.cvar_n:]
    if ultimos and l_med == l_med:
        k = max(1, int(math.ceil(cfg.cvar_fraccao * len(ultimos))))
        piores = sorted(ultimos)[:k]
        if sum(piores) / k <= -cfg.cvar_x_l * l_med:
            return "cvar"
    return None


# --------------------------------------------------------------------------
# 8. Dimensionamento
# --------------------------------------------------------------------------
def funding_esperado_bps(lado: int, f_hora: float, tau_max_h: float) -> float:
    """(64) f_esp = lado F BPS tau_max; positivo = custo (F > 0: os longs pagam)."""
    if lado not in (1, -1):
        raise ValueError("lado tem de ser +1 ou -1")
    return lado * f_hora * BPS * tau_max_h


def custos_defeito(taxa_taker: float, taxa_maker: float, imp_bps: float, f_esp: float) -> Tuple[float, float]:
    """(65) c_W = t_t + imp + t_m + f_esp ; c_L = t_t + imp + t_t + imp + f_esp."""
    c_w = taxa_taker + imp_bps + taxa_maker + f_esp
    c_l = taxa_taker + imp_bps + taxa_taker + imp_bps + f_esp
    return c_w, c_l


def wilson_inferior(p_hat: float, n: int, z: float = 1.96) -> float:
    """(68) limite inferior de Wilson; nan se n = 0 ou p_hat fora de [0, 1]."""
    if n <= 0 or p_hat != p_hat or not (0.0 <= p_hat <= 1.0):
        return NAN
    z2 = z * z
    centro = p_hat + z2 / (2.0 * n)
    raio = z * math.sqrt(p_hat * (1.0 - p_hat) / n + z2 / (4.0 * n * n))
    return (centro - raio) / (1.0 + z2 / n)


def margem_decisao(p_hat: float, n: int, m_min: float = 0.05, z: float = 1.96) -> float:
    """(69) m = max(m_min, z sqrt(p (1 - p) / n)); inf sem trades (incerteza sem limite)."""
    if n <= 0:
        return float("inf")
    if p_hat != p_hat or not (0.0 <= p_hat <= 1.0):
        return NAN
    return max(m_min, z * math.sqrt(p_hat * (1.0 - p_hat) / n))


def kelly_fraccao(p_inf: float, g_bps: float, l_bps: float, c_w: float, c_l: float) -> float:
    """(70) b = (G - c_W) / (L + c_L), f* = (p_inf (b + 1) - 1) / b; f* > 0 sse p_inf > p*; -1 se b <= 0."""
    if l_bps + c_l <= 0.0:
        raise ValueError("L + c_L tem de ser positivo")
    if p_inf != p_inf:
        return NAN
    b = (g_bps - c_w) / (l_bps + c_l)
    if b <= 0.0:
        return -1.0
    return (p_inf * (b + 1.0) - 1.0) / b


@dataclass(frozen=True)
class ParametrosTamanho:
    k_kelly: float = 0.25
    risco_por_trade: float = 0.005
    vol_alvo_dia: float = 0.01
    liq_fraccao: float = 0.01
    tamanho_base: float = 1000.0
    tamanho_max_frac: float = 0.25
    minimo_ordem_usd: float = 10.0
    n_cal: int = 30
    m_min: float = 0.05
    z: float = 1.96


@dataclass(frozen=True)
class Tamanho:
    usd: float
    n_kelly: float
    n_risco: float
    n_vol: float
    n_liq: float
    p_estrela: float
    p_inf: float
    margem: float
    f_estrela: float
    fase: str              # cal, op, sombra
    cap: str               # base, kelly, risco, vol, liq, max, min, sombra


def dimensionar(e_usd: float, g_bps: float, l_bps: float, c_w: float, c_l: float, p_hat: float, n: int,
                sigma_r: float, f_b: float, day_ntl_vlm: float, qmax_usd: float, cfg: ParametrosTamanho) -> Tamanho:
    """(66)-(76) tamanho = clamp(f_B min(N_kelly, N_risco, N_vol, N_liq), minimo, tamanho_max_frac E); cal da tamanho_base; sombra da 0.

    Um limite desconhecido (nan) nao manda; N_liq = min(liq_fraccao dayNtlVlm / 96, qmax_usd)
    sobre os termos conhecidos. cap diz qual dos limites mandou.
    """
    if not (e_usd == e_usd and e_usd > 0.0):
        raise ValueError("capital E tem de ser positivo")
    if l_bps + c_l <= 0.0:
        raise ValueError("L + c_L tem de ser positivo")
    p_estrela = nu.acerto_equilibrio(g_bps, l_bps, c_w, c_l)                       # (66)
    p_inf = wilson_inferior(p_hat, n, cfg.z)                                        # (68)
    margem = margem_decisao(p_hat, n, cfg.m_min, cfg.z)                             # (69)
    f_estrela = kelly_fraccao(p_inf, g_bps, l_bps, c_w, c_l)                        # (70)
    custo_stop = l_bps + c_l
    n_kelly = cfg.k_kelly * max(f_estrela, 0.0) * e_usd * BPS / custo_stop if f_estrela == f_estrela else NAN
    n_risco = cfg.risco_por_trade * e_usd * BPS / custo_stop                        # (72)
    n_vol = cfg.vol_alvo_dia * e_usd / (sigma_r * math.sqrt(24.0)) if sigma_r == sigma_r and sigma_r > 0.0 else NAN
    liq_termos = []
    if day_ntl_vlm == day_ntl_vlm and day_ntl_vlm > 0.0:
        liq_termos.append(cfg.liq_fraccao * day_ntl_vlm / 96.0)
    if qmax_usd == qmax_usd and qmax_usd > 0.0:
        liq_termos.append(qmax_usd)
    n_liq = min(liq_termos) if liq_termos else NAN                                   # (74)
    if n < cfg.n_cal:                                                               # (76) cal
        return Tamanho(cfg.tamanho_base, n_kelly, n_risco, n_vol, n_liq, p_estrela, p_inf, margem, f_estrela,
                       "cal", "base")
    if not (p_inf == p_inf and p_estrela == p_estrela and p_inf > p_estrela + margem):   # (76) sombra
        return Tamanho(0.0, n_kelly, n_risco, n_vol, n_liq, p_estrela, p_inf, margem, f_estrela, "sombra", "sombra")
    candidatos = [("kelly", n_kelly), ("risco", n_risco), ("vol", n_vol), ("liq", n_liq)]
    cap, base = min((c for c in candidatos if c[1] == c[1]), key=lambda c: c[1])
    usd = f_b * base if f_b == f_b else base
    tecto = cfg.tamanho_max_frac * e_usd
    if usd > tecto:
        usd, cap = tecto, "max"
    if usd < cfg.minimo_ordem_usd:
        usd, cap = cfg.minimo_ordem_usd, "min"
    return Tamanho(usd, n_kelly, n_risco, n_vol, n_liq, p_estrela, p_inf, margem, f_estrela, "op", cap)


def alavancagem_maxima(l_bps: float, max_leverage: float) -> float:
    """lev_max = 1 / (5 L / BPS + 1 / (2 maxLeverage)); ValueError com maxLeverage nao positivo."""
    if max_leverage <= 0.0 or l_bps < 0.0:
        raise ValueError("maxLeverage > 0 e L >= 0")
    return 1.0 / (5.0 * l_bps / BPS + 1.0 / (2.0 * max_leverage))


# --------------------------------------------------------------------------
# 9. A nota
# --------------------------------------------------------------------------
def _texto_nota(v: object) -> str:
    """Valor de um campo da nota: nan ou None viram na; floats com 2 a 4 casas; texto so ASCII sem virgulas, aspas, espacos nem sinais de igual."""
    if v is None:
        return "na"
    if isinstance(v, bool):
        return "on" if v else "off"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return "na"
        inteiro, casas = ("%.4f" % v).split(".")
        casas = casas.rstrip("0")
        if len(casas) < 2:
            casas = (casas + "00")[:2]
        txt = inteiro + "." + casas
        return "0.00" if txt == "-0.00" else txt
    txt = "_".join(str(v).replace("=", ":").split())
    txt = "".join(ch for ch in txt if 32 < ord(ch) < 127 and ch not in ",\"'")
    return txt or "na"


def hash_variante(params: Dict[str, float]) -> str:
    """6 caracteres do sha256 dos parametros ordenados por nome, numeros com 6 casas (nome=valor;...)."""
    # NOTA ESPEC: a API fala em "json ordenado", mas json nao esta na lista de imports
    # permitidos; o texto "nome=valor" ordenado por nome, com 6 casas, e tao determinista.
    partes = []
    for k in sorted(params):
        v = params[k]
        if isinstance(v, bool):
            txt = "1" if v else "0"
        elif isinstance(v, (int, float)):
            txt = "%.6f" % v
        else:
            txt = str(v)
        partes.append("%s=%s" % (k, txt))
    return hashlib.sha256(";".join(partes).encode("utf-8")).hexdigest()[:6]


def nota_sinal(campos: Dict[str, object]) -> str:
    """Seccao 9: 'revou v=2 chave=valor ...' em ASCII, sem virgulas, aspas nem quebras; nan vira na."""
    tokens = ["revou", "v=2"]
    for k, v in campos.items():
        chave = "".join(ch for ch in str(k) if ch.isalnum() or ch == "_")
        if not chave or chave == "v":
            continue
        tokens.append("%s=%s" % (chave, _texto_nota(v)))
    return " ".join(tokens)


def interpretar_nota(nota: str) -> Dict[str, str]:
    """Seccao 9: inverso de nota_sinal; dicionario vazio se o primeiro token nao for revou."""
    tokens = (nota or "").split()
    if not tokens or tokens[0] != "revou":
        return {}
    campos: Dict[str, str] = {}
    for tk in tokens[1:]:
        if "=" not in tk:
            continue
        k, v = tk.split("=", 1)
        if k:
            campos[k] = v
    return campos


# --------------------------------------------------------------------------
# Velas e resultado
# --------------------------------------------------------------------------
def vela_fechada(t_ms: int, T_ms: int, agora_ms: int, atraso_ms: int, folga_ms: int) -> bool:
    """Seccao 5.1: a vela [t, T] esta fechada se agora - atraso >= T + folga; ValueError se T < t."""
    if T_ms < t_ms:
        raise ValueError("T tem de ser maior ou igual a t")
    return agora_ms - atraso_ms >= T_ms + folga_ms


def agregar_1h(velas_15m: Sequence[Vela]) -> Vela:
    """Quatro velas de 15 m contiguas e alinhadas a hora numa vela de 1 h: o = o_1, h = max, l = min, c = c_4, v e n somados."""
    if len(velas_15m) != 4:
        raise ValueError("sao precisas exactamente 4 velas de 15 m")
    if velas_15m[0].t % JANELA_1H_MS != 0:
        raise ValueError("a primeira vela tem de comecar a hora certa")
    for i in range(1, 4):
        if velas_15m[i].t - velas_15m[i - 1].t != 900_000:
            raise ValueError("as velas tem de ser contiguas de 15 m")
    return Vela(velas_15m[0].t, velas_15m[-1].T, velas_15m[0].o, max(v.h for v in velas_15m),
                min(v.l for v in velas_15m), velas_15m[-1].c, sum(v.v for v in velas_15m),
                sum(v.n for v in velas_15m), all(v.completa for v in velas_15m))


def resultado_trade(lado: int, p_entrada: float, p_saida: float, funding_bps: float,
                    custo_bps: float) -> Tuple[float, float]:
    """(77) r_bruto = BPS lado ln(p_saida / p_entrada) ; r_liq = r_bruto - custos - funding_bps."""
    if lado not in (1, -1):
        raise ValueError("lado tem de ser +1 ou -1")
    if not (p_entrada > 0.0 and p_saida > 0.0):
        raise ValueError("precos tem de ser positivos")
    r_bruto = BPS * lado * math.log(p_saida / p_entrada)
    return r_bruto, r_bruto - custo_bps - funding_bps


# --------------------------------------------------------------------------
# 11.5 e 11.6 Metricas de validacao, nulos
# --------------------------------------------------------------------------
def phi_normal(x: float) -> float:
    """Phi(x) = (1 + erf(x / sqrt 2)) / 2."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def phi_inversa(p: float) -> float:
    """Inversa de Phi por bisseccao (1e-10); ValueError se p nao esta em (0, 1)."""
    if not (p == p and 0.0 < p < 1.0):
        raise ValueError("p tem de estar em (0, 1)")
    lo, hi = -40.0, 40.0
    while hi - lo > 1e-10:
        meio = 0.5 * (lo + hi)
        if phi_normal(meio) < p:
            lo = meio
        else:
            hi = meio
    return 0.5 * (lo + hi)


def sharpe_trade(r: Sequence[float]) -> Tuple[float, float, float]:
    """(78) (SR = media / sd amostral, assimetria g3, curtose bruta g4) por trade; nan com n < 3 ou sd = 0."""
    xs = [x for x in r if x == x]
    n = len(xs)
    if n < 3:
        return NAN, NAN, NAN
    mu = statistics.fmean(xs)
    sd = statistics.stdev(xs)
    if sd <= 0.0:
        return NAN, NAN, NAN
    m2 = sum((x - mu) ** 2 for x in xs) / n
    m3 = sum((x - mu) ** 3 for x in xs) / n
    m4 = sum((x - mu) ** 4 for x in xs) / n
    return mu / sd, m3 / m2 ** 1.5, m4 / (m2 * m2)


def psr(sr: float, sr_ref: float, n: int, g3: float, g4: float) -> float:
    """(79) PSR(SR*) = Phi[(SR - SR*) sqrt(n - 1) / sqrt(1 - g3 SR + (g4 - 1) SR^2 / 4)]; nan se n < 2 ou a raiz nao existe."""
    if n < 2 or any(v != v for v in (sr, sr_ref, g3, g4)):
        return NAN
    den = 1.0 - g3 * sr + (g4 - 1.0) * sr * sr / 4.0
    if den <= 0.0:
        return NAN
    return phi_normal((sr - sr_ref) * math.sqrt(n - 1.0) / math.sqrt(den))


def sharpe_max_esperado(m: int, var_sr: float) -> float:
    """(80) SR* = sqrt(V) [(1 - gamma) Phi^-1(1 - 1/M) + gamma Phi^-1(1 - 1/(M e))]; 0 com M <= 1."""
    if var_sr != var_sr or var_sr < 0.0:
        raise ValueError("a variancia dos SR tem de ser nao negativa")
    if m <= 1 or var_sr == 0.0:
        return 0.0
    return math.sqrt(var_sr) * ((1.0 - EULER_GAMMA) * phi_inversa(1.0 - 1.0 / m)
                                + EULER_GAMMA * phi_inversa(1.0 - 1.0 / (m * math.e)))


def pbo_cscv(matriz: Sequence[Sequence[float]], s: int = 16) -> float:
    """(81) PBO por CSCV: S blocos contiguos de trades, somas por bloco, metade IS e metade OOS em todas as combinacoes; PBO = fraccao de lambda = ln(w / (1 - w)) <= 0.

    w e o rank OOS (ascendente, 1 = pior) da configuracao melhor em IS dividido por N + 1,
    o que mantem lambda finito; nos empates conta-se o rank medio das empatadas (uma matriz
    em que nada distingue as configuracoes da w = 0,5, lambda = 0 e PBO = 1, nunca 0).
    """
    if s < 2 or s % 2:
        raise ValueError("s tem de ser par e pelo menos 2")
    t = len(matriz)
    if t < s:
        raise ValueError("sao precisas pelo menos s linhas (trades)")
    n_cfg = len(matriz[0])
    if n_cfg < 1 or any(len(linha) != n_cfg for linha in matriz):
        raise ValueError("a matriz tem de ser rectangular com pelo menos uma configuracao")
    blocos: List[List[float]] = []
    for b in range(s):
        ini = b * t // s
        fim = (b + 1) * t // s
        soma = [0.0] * n_cfg
        for linha in matriz[ini:fim]:
            for j, v in enumerate(linha):
                soma[j] += v
        blocos.append(soma)
    total = [sum(bl[j] for bl in blocos) for j in range(n_cfg)]
    metade = s // 2
    n_comb = 0
    n_sobre = 0
    for mascara in range(1 << s):
        if bin(mascara).count("1") != metade:
            continue
        n_comb += 1
        linhas_is = [blocos[b] for b in range(s) if mascara >> b & 1]
        perf_is = [sum(col) for col in zip(*linhas_is)]
        melhor = max(range(n_cfg), key=lambda j: perf_is[j])
        perf_oos = [total[j] - perf_is[j] for j in range(n_cfg)]
        ordenado = sorted(perf_oos)
        v = perf_oos[melhor]
        rank = 0.5 * (bisect.bisect_left(ordenado, v) + bisect.bisect_right(ordenado, v) + 1)
        w = rank / (n_cfg + 1.0)
        if math.log(w / (1.0 - w)) <= 0.0:
            n_sobre += 1
    return n_sobre / n_comb


def simular_gbm(n: int, sigma_r: float, semente: int) -> List[float]:
    """Trajectoria de n log-precos x_t = x_{t-1} + sigma_r eps_t com x_0 = 0 e random.Random(semente)."""
    if n < 1:
        raise ValueError("n tem de ser pelo menos 1")
    if sigma_r != sigma_r or sigma_r < 0.0:
        raise ValueError("sigma_r tem de ser nao negativo")
    rng = random.Random(semente)
    x = [0.0] * n
    for t in range(1, n):
        x[t] = x[t - 1] + sigma_r * rng.gauss(0.0, 1.0)
    return x


def p_nulo_rotulo(g_bps: float, l_bps: float, tmax_15: int, sigma_15: float, n_caminhos: int, semente: int) -> float:
    """Seccao 11.6: fraccao de caminhos GBM de 15 m em que o fecho passa +G antes de passar -L dentro de tmax_15; tempo esgotado nao conta."""
    if g_bps <= 0.0 or l_bps <= 0.0 or tmax_15 < 1 or n_caminhos < 1:
        raise ValueError("G > 0, L > 0, tmax_15 >= 1 e n_caminhos >= 1")
    if sigma_15 != sigma_15 or sigma_15 < 0.0:
        raise ValueError("sigma_15 tem de ser nao negativo")
    rng = random.Random(semente)
    gauss = rng.gauss
    alvo = g_bps / BPS
    stop = -l_bps / BPS
    acertos = 0
    for _ in range(n_caminhos):
        x = 0.0
        for _ in range(tmax_15):
            x += sigma_15 * gauss(0.0, 1.0)
            if x <= stop:
                break
            if x >= alvo:
                acertos += 1
                break
    return acertos / n_caminhos


def sessao_utc(ms: int) -> Tuple[str, int, int]:
    """(sessao, dia da semana 0 = segunda, hora UTC): asia 00-07 e 22-24, europa 07-13, eua 13-22; sabado e domingo = fds."""
    if ms < 0:
        raise ValueError("ms tem de ser nao negativo")
    dias = ms // 86_400_000
    hora = int((ms // 3_600_000) % 24)
    dow = int((dias + 3) % 7)          # 1970-01-01 foi quinta-feira (3)
    if dow >= 5:
        return "fds", dow, hora
    if 7 <= hora < 13:
        return "europa", dow, hora
    if 13 <= hora < 22:
        return "eua", dow, hora
    return "asia", dow, hora
