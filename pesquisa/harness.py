# -*- coding: utf-8 -*-
"""Harness comum de investigação: dados, características causais, simulação, métricas e registo.

Convenções
----------
* Todas as séries estão indexadas pelo instante de ABERTURA da vela, em UTC. A coluna
  ``fecho_em`` guarda o instante em que a vela fecha (abertura + duração). Uma característica
  calculada na vela k só pode usar dados até ``fecho_em[k]``; é nesse instante que o sinal nasce.
* A execução é manual: a entrada faz-se na ABERTURA da vela seguinte ao fecho que gera o sinal.
* R = distância da entrada ao stop. Os resultados vêm em múltiplos de R, líquidos de custos.
* Períodos: IS = 2023-01-01 a 2025-03-31; OOS = 2025-04-01 a 2026-09-09. O OOS avalia-se UMA vez.
* Nada aqui olha para o futuro: ``teste_harness.py`` verifica a causalidade de cada característica
  perturbando a cauda da série e confirmando que o passado não muda.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------------------------

AQUI = os.path.dirname(os.path.abspath(__file__))
DADOS = os.environ.get(
    "PESQUISA_DADOS",
    "/tmp/claude-0/-home-user-TestRepo/401fa93a-318c-525f-aa0e-ce261d9dea8b/scratchpad/pesquisa/dados",
)
ENSAIOS = os.path.join(AQUI, "ensaios")

SIMBOLOS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "LTC", "LINK", "DOT", "AVAX", "NEAR",
            "TRX", "BCH", "APT", "ARB", "OP", "SUI"]

PERIODOS = {
    "IS": ("2023-01-01", "2025-03-31 23:59:59"),
    "IS_preco": ("2020-09-01", "2025-03-31 23:59:59"),   # famílias só de preço podem usar 2020-2021
    "OOS": ("2025-04-01", "2026-09-09 23:59:59"),
}

CUSTO_BPS = 6.5           # por lado
VELAS_POR = {"15m": 1, "1h": 4, "4h": 16, "1d": 96}
MINUTOS_POR = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}
_FREQ_PANDAS = {"1h": "1h", "4h": "4h", "1d": "1D"}

COLUNAS_BRUTAS = [
    "open", "high", "low", "close", "volume_base", "volume_quote", "trade_count",
    "taker_buy_vol_btc", "taker_sell_vol_btc", "taker_buy_count", "taker_sell_count",
    "funding_rate_pct", "basis_usd", "open_interest_usd", "oi_change_pct", "long_liq_usd",
    "short_liq_usd", "ls_ratio_global", "ls_ratio_top", "top_account_ratio", "spot_close",
    "index_close", "is_imputed_metrics",
]
COLUNAS_METRICAS = ["funding_rate_pct", "basis_usd", "open_interest_usd", "oi_change_pct",
                    "long_liq_usd", "short_liq_usd", "ls_ratio_global", "ls_ratio_top",
                    "top_account_ratio"]

ArrayLike = Union[pd.Series, np.ndarray, Sequence[float]]


def _utc(x) -> Optional[pd.Timestamp]:
    """Converte string/Timestamp para Timestamp UTC (None fica None)."""
    if x is None:
        return None
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


# ---------------------------------------------------------------------------------------------
# 1. Dados
# ---------------------------------------------------------------------------------------------

def caminho(simbolo: str, pasta: Optional[str] = None) -> str:
    """Caminho do parquet de 15 m de um activo ("BTC" ou "BTCUSDT")."""
    s = simbolo.upper()
    if not s.endswith("USDT"):
        s += "USDT"
    return os.path.join(pasta or DADOS, f"{s}_15m.parquet")


def carregar(simbolo: str, de=None, ate=None, pasta: Optional[str] = None) -> pd.DataFrame:
    """Lê as velas de 15 m de um activo e devolve um DataFrame indexado pela abertura (UTC).

    Colunas: as brutas do parquet, mais ``fecho_em`` (abertura + 15 min), ``imputado`` (bool,
    1 = métricas imputadas; nunca usar essas velas para características de métricas) e ``activo``.
    ``de``/``ate`` filtram pela abertura (inclusivo). O índice é único e crescente, sem buracos.
    """
    df = pd.read_parquet(caminho(simbolo, pasta))
    df = df.sort_values("open_time_ms").drop_duplicates("open_time_ms")
    idx = pd.to_datetime(df["open_time_ms"].to_numpy(), unit="ms", utc=True)
    df.index = pd.DatetimeIndex(idx, name="abertura_em")
    df["fecho_em"] = df.index + pd.Timedelta(minutes=15)
    df["imputado"] = df["is_imputed_metrics"].astype(int).astype(bool)
    df["activo"] = simbolo.upper().replace("USDT", "")
    df = df.drop(columns=["open_time_ms", "close_time_ms"])
    if de is not None:
        df = df[df.index >= _utc(de)]
    if ate is not None:
        df = df[df.index <= _utc(ate)]
    return df


def agregar(df: pd.DataFrame, intervalo: str) -> pd.DataFrame:
    """Agrega velas de 15 m para "1h", "4h" ou "1d", alinhado a UTC e SEM lookahead.

    Só devolve velas superiores COMPLETAS (4, 16 ou 96 velas de 15 m presentes). ``fecho_em`` é o
    fecho da última vela de 15 m contida, e é o único instante a partir do qual a vela superior pode
    ser usada (ver ``ultima_superior_fechada``). Agregação: OHLC, soma de volumes, trade_count e
    volumes taker, soma das liquidações, ÚLTIMO funding, OI, basis, ls_ratio, spot e índice;
    ``oi_change_pct`` é a soma (aproximação do composto); ``imputado`` é verdadeiro se alguma vela
    de 15 m estava imputada.
    """
    if intervalo == "15m":
        return df.copy()
    freq = _FREQ_PANDAS[intervalo]
    esperado = VELAS_POR[intervalo]
    regras: Dict[str, str] = {"open": "first", "high": "max", "low": "min", "close": "last"}
    for c in ("volume_base", "volume_quote", "trade_count", "taker_buy_vol_btc", "taker_sell_vol_btc",
              "taker_buy_count", "taker_sell_count", "long_liq_usd", "short_liq_usd", "oi_change_pct"):
        if c in df:
            regras[c] = "sum"
    for c in ("funding_rate_pct", "basis_usd", "open_interest_usd", "ls_ratio_global", "ls_ratio_top",
              "top_account_ratio", "spot_close", "index_close", "fecho_em", "activo"):
        if c in df:
            regras[c] = "last"
    if "imputado" in df:
        regras["imputado"] = "max"
    g = df.resample(freq, label="left", closed="left")
    out = g.agg(regras)
    conta = g["close"].count()
    out = out[conta == esperado].copy()
    if "imputado" in out:
        out["imputado"] = out["imputado"].astype(bool)
    out.index.name = "abertura_em"
    return out


def ultima_superior_fechada(fecho_sup: ArrayLike, instantes: ArrayLike) -> np.ndarray:
    """Posição (0..n-1) da última vela superior cujo ``fecho_em`` <= instante; -1 se nenhuma.

    ``fecho_sup`` tem de estar ordenado (é o ``fecho_em`` de ``agregar``). Uma vela de 1 h que fecha
    às 01:00 só fica disponível para a vela de 15 m cujo fecho é >= 01:00.
    """
    fs = pd.DatetimeIndex(pd.Series(fecho_sup)).as_unit("ns").asi8
    ins = pd.DatetimeIndex(pd.Series(instantes)).as_unit("ns").asi8
    return np.searchsorted(fs, ins, side="right") - 1


def projectar_superior(df15: pd.DataFrame, df_sup: pd.DataFrame,
                       colunas: Optional[Iterable[str]] = None, sufixo: str = "") -> pd.DataFrame:
    """Leva colunas da vela superior para a grelha de 15 m usando só velas superiores já fechadas.

    Para cada vela de 15 m k usa a última vela superior com ``fecho_em`` <= ``fecho_em[k]``.
    Onde não existe, fica NaN. Devolve um DataFrame alinhado ao índice de ``df15``.
    """
    cols = list(colunas) if colunas is not None else [c for c in df_sup.columns if c not in ("fecho_em", "activo")]
    pos = ultima_superior_fechada(df_sup["fecho_em"], df15["fecho_em"])
    ok = pos >= 0
    out = pd.DataFrame(index=df15.index)
    for c in cols:
        vals = df_sup[c].to_numpy()
        v = np.full(len(df15), np.nan, dtype=float if np.issubdtype(np.asarray(vals).dtype, np.number) else object)
        v[ok] = vals[pos[ok]]
        out[c + sufixo] = v
    return out


def mascarar_imputado(df: pd.DataFrame, coluna: str) -> pd.Series:
    """Série da coluna com NaN nas velas imputadas (obrigatório antes de qualquer z-score de métricas)."""
    s = df[coluna].astype(float)
    if "imputado" in df:
        s = s.where(~df["imputado"].astype(bool))
    return s


# ---------------------------------------------------------------------------------------------
# 2. Características causais
# ---------------------------------------------------------------------------------------------

def retorno_log(close: pd.Series, n: int = 1) -> pd.Series:
    """Retorno logarítmico a n velas: log(close_k / close_{k-n}). Causal (só usa k e k-n)."""
    c = close.astype(float)
    return np.log(c) - np.log(c.shift(n))


def vol_realizada_ewma(ret: pd.Series, meia_vida: float, min_periodos: Optional[int] = None) -> pd.Series:
    """Volatilidade realizada por vela: raiz da média EWMA de ret^2 com a meia-vida dada (em velas).

    Causal: a EWMA em k só usa retornos até k. Não anualiza; multiplicar por sqrt(velas) para
    escalar a outro horizonte.
    """
    r2 = ret.astype(float) ** 2
    mp = min_periodos if min_periodos is not None else int(np.ceil(meia_vida))
    return np.sqrt(r2.ewm(halflife=meia_vida, min_periods=mp, adjust=True).mean())


def vol_parkinson_ewma(high: pd.Series, low: pd.Series, meia_vida: float,
                       min_periodos: Optional[int] = None) -> pd.Series:
    """Volatilidade de Parkinson por vela: sqrt(EWMA(ln(high/low)^2) / (4 ln 2)). Causal."""
    hl2 = np.log(high.astype(float) / low.astype(float)) ** 2 / (4.0 * np.log(2.0))
    mp = min_periodos if min_periodos is not None else int(np.ceil(meia_vida))
    return np.sqrt(hl2.ewm(halflife=meia_vida, min_periods=mp, adjust=True).mean())


def atr(df: pd.DataFrame, n: int = 14, metodo: str = "ewma") -> pd.Series:
    """Average True Range em preço. True range = max(h-l, |h-c_prev|, |l-c_prev|). Causal.

    ``metodo`` "ewma" (alpha = 1/n, estilo Wilder) ou "sma" (média simples de n velas).
    """
    h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    cp = c.shift(1)
    tr = pd.concat([h - l, (h - cp).abs(), (l - cp).abs()], axis=1).max(axis=1)
    if metodo == "sma":
        return tr.rolling(n, min_periods=n).mean()
    return tr.ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean()


def vwap_movel(df: pd.DataFrame, janela: int) -> pd.Series:
    """VWAP das últimas ``janela`` velas: soma(volume_quote) / soma(volume_base). Exacto e causal."""
    vq = df["volume_quote"].astype(float).rolling(janela, min_periods=janela).sum()
    vb = df["volume_base"].astype(float).rolling(janela, min_periods=janela).sum()
    return vq / vb.replace(0.0, np.nan)


def zscore_ancorado(df: pd.DataFrame, janela: int, ancora: str = "vwap",
                    escala: Optional[pd.Series] = None) -> pd.Series:
    """z-score do fecho a uma âncora móvel de ``janela`` velas: (close - âncora) / escala.

    ``ancora``: "vwap" (VWAP móvel), "media" (média simples do fecho) ou "ewma" (meia-vida =
    janela/2). ``escala``: por omissão o desvio-padrão móvel do fecho na mesma janela; pode ser uma
    Series (p.ex. ATR ou sigma em preço). Causal: janelas só para trás.
    """
    c = df["close"].astype(float)
    if ancora == "vwap":
        a = vwap_movel(df, janela)
    elif ancora == "media":
        a = c.rolling(janela, min_periods=janela).mean()
    elif ancora == "ewma":
        a = c.ewm(halflife=janela / 2.0, min_periods=janela).mean()
    else:
        raise ValueError("ancora tem de ser 'vwap', 'media' ou 'ewma'")
    e = escala.astype(float) if escala is not None else c.rolling(janela, min_periods=janela).std()
    return (c - a) / e.replace(0.0, np.nan)


def extremos_moveis(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Máximo e mínimo das n velas ANTERIORES (exclui a vela actual), em preço.

    Colunas: ``max_anterior``, ``min_anterior``. Causal (rolling + shift(1)).
    """
    hi = df["high"].astype(float).rolling(n, min_periods=n).max().shift(1)
    lo = df["low"].astype(float).rolling(n, min_periods=n).min().shift(1)
    return pd.DataFrame({"max_anterior": hi, "min_anterior": lo}, index=df.index)


def varrimento(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Detecção de varrimento de extremos de n velas anteriores.

    ``varrimento`` = +1 quando a mínima fura o mínimo anterior e o fecho volta ACIMA dele
    (varrimento de mínimos, viés comprador); -1 quando a máxima fura o máximo anterior e o fecho
    volta ABAIXO; 0 caso contrário. ``nivel`` é o extremo varrido (útil para o stop) e ``excesso``
    quanto furou, em fracção do preço. Causal.
    """
    ex = extremos_moveis(df, n)
    lo, hi, c = df["low"].astype(float), df["high"].astype(float), df["close"].astype(float)
    alta = (lo < ex["min_anterior"]) & (c > ex["min_anterior"])
    baixa = (hi > ex["max_anterior"]) & (c < ex["max_anterior"])
    v = pd.Series(0, index=df.index, dtype=int)
    v[alta] = 1
    v[baixa & ~alta] = -1
    nivel = pd.Series(np.nan, index=df.index)
    nivel[v == 1] = ex["min_anterior"][v == 1]
    nivel[v == -1] = ex["max_anterior"][v == -1]
    excesso = pd.Series(np.nan, index=df.index)
    excesso[v == 1] = (ex["min_anterior"] - lo)[v == 1] / c[v == 1]
    excesso[v == -1] = (hi - ex["max_anterior"])[v == -1] / c[v == -1]
    return pd.DataFrame({"varrimento": v, "nivel": nivel, "excesso": excesso}, index=df.index)


def posicao_no_intervalo(df: pd.DataFrame, n: int) -> pd.Series:
    """Posição do fecho no intervalo das últimas n velas (inclui a actual): 0 = no mínimo, 1 = no máximo."""
    hi = df["high"].astype(float).rolling(n, min_periods=n).max()
    lo = df["low"].astype(float).rolling(n, min_periods=n).min()
    return (df["close"].astype(float) - lo) / (hi - lo).replace(0.0, np.nan)


def desequilibrio_taker(df: pd.DataFrame, janela: int = 1) -> pd.Series:
    """Desequilíbrio taker (compra - venda) / (compra + venda) sobre ``janela`` velas. Em [-1, 1]. Causal."""
    b = df["taker_buy_vol_btc"].astype(float)
    s = df["taker_sell_vol_btc"].astype(float)
    if janela > 1:
        b = b.rolling(janela, min_periods=janela).sum()
        s = s.rolling(janela, min_periods=janela).sum()
    return (b - s) / (b + s).replace(0.0, np.nan)


def cvd(df: pd.DataFrame, janela: Optional[int] = None) -> pd.Series:
    """Delta de volume acumulado (taker buy - taker sell), em unidades do activo.

    ``janela`` None = acumulado desde o início da série; inteiro = soma móvel. Causal.
    """
    d = df["taker_buy_vol_btc"].astype(float) - df["taker_sell_vol_btc"].astype(float)
    if janela is None:
        return d.cumsum()
    return d.rolling(janela, min_periods=janela).sum()


def z_robusto(s: pd.Series, janela: int, min_periodos: Optional[int] = None,
              escala_min: float = 0.0) -> pd.Series:
    """z-score robusto móvel: (x - mediana móvel) / max(1.4826 * MAD móvel, escala_min).

    O MAD é aproximado pela mediana móvel de |x - mediana móvel| (rápido; a mediana usada no desvio
    é a da janela que termina em cada ponto, não a de cada janela histórica). ``escala_min`` evita
    z explosivos quando a série fica presa num valor (p.ex. funding em 0,01 %). NaN propagam-se;
    passe ``mascarar_imputado`` antes para métricas. Causal.
    """
    x = s.astype(float)
    mp = min_periodos if min_periodos is not None else max(10, janela // 2)
    med = x.rolling(janela, min_periods=mp).median()
    mad = (x - med).abs().rolling(janela, min_periods=mp).median() * 1.4826
    if escala_min > 0:
        mad = mad.clip(lower=escala_min)
    return (x - med) / mad.replace(0.0, np.nan)


def percentil_movel(s: pd.Series, janela: int, min_periodos: Optional[int] = None) -> pd.Series:
    """Percentil (0..1) do valor actual dentro das últimas ``janela`` velas (pandas rolling rank). Causal."""
    mp = min_periodos if min_periodos is not None else max(10, janela // 2)
    return s.astype(float).rolling(janela, min_periods=mp).rank(pct=True)


def racio_variancias(ret: ArrayLike, q: int) -> Tuple[float, float, int]:
    """Rácio de variâncias de Lo-MacKinlay (1988) a q períodos, com z robusto à heteroscedasticidade.

    VR(q) = Var(soma de q retornos) / (q Var(retorno)), com correcção de enviesamento. VR > 1 indica
    persistência (momentum), VR < 1 reversão. z = (VR - 1) / sqrt(theta), com theta a estimativa
    M2 de Lo-MacKinlay (robusta a heteroscedasticidade). Devolve (VR, z, n). NaN são removidos.
    """
    r = np.asarray(pd.Series(ret).dropna(), dtype=float)
    n = len(r)
    if n < 4 * q + 2:
        return float("nan"), float("nan"), n
    mu = r.mean()
    d = r - mu
    sa2 = (d ** 2).sum() / (n - 1)
    # soma de q retornos consecutivos
    cs = np.concatenate([[0.0], np.cumsum(r)])
    rq = cs[q:] - cs[:-q]            # n-q+1 somas
    m = q * (n - q + 1) * (1.0 - q / n)
    sc2 = ((rq - q * mu) ** 2).sum() / m
    vr = sc2 / sa2 if sa2 > 0 else float("nan")
    d2 = d ** 2
    den = d2.sum() ** 2
    theta = 0.0
    for j in range(1, q):
        delta = (d2[j:] * d2[:-j]).sum() / den
        theta += (2.0 * (q - j) / q) ** 2 * delta
    z = (vr - 1.0) / np.sqrt(theta) if theta > 0 else float("nan")
    return float(vr), float(z), n


def racio_variancias_movel(ret: pd.Series, q: int, janela: int, passo: int = 1) -> pd.DataFrame:
    """VR(q) e z calculados em janelas móveis de ``janela`` velas, de ``passo`` em ``passo``. Causal.

    Devolve DataFrame indexado pela vela onde a janela termina, colunas ``vr`` e ``z``.
    """
    r = ret.astype(float)
    idx, vrs, zs = [], [], []
    vals = r.to_numpy()
    for fim in range(janela, len(r) + 1, passo):
        vr, z, _ = racio_variancias(vals[fim - janela:fim], q)
        idx.append(r.index[fim - 1])
        vrs.append(vr)
        zs.append(z)
    return pd.DataFrame({"vr": vrs, "z": zs}, index=pd.DatetimeIndex(idx, name=r.index.name))


def autocorrelacao(ret: ArrayLike, lag: int = 1) -> Tuple[float, float, int]:
    """Autocorrelação amostral ao ``lag`` e erro padrão robusto (Bartlett simples 1/sqrt(n)). Devolve (rho, ep, n)."""
    r = pd.Series(ret).dropna().astype(float)
    n = len(r)
    if n < lag + 10:
        return float("nan"), float("nan"), n
    rho = float(r.autocorr(lag))
    return rho, 1.0 / np.sqrt(n), n


# ---------------------------------------------------------------------------------------------
# 3. Simulação
# ---------------------------------------------------------------------------------------------

def _as_array(x, n: int, index) -> np.ndarray:
    if isinstance(x, pd.Series):
        return x.reindex(index).to_numpy(dtype=float)
    if np.isscalar(x):
        return np.full(n, float(x))
    return np.asarray(x, dtype=float)


def simular(df15: pd.DataFrame, sinais: pd.Series, stop, alvo, n_max: int, custo_bps: float = CUSTO_BPS,
            escala: Optional[pd.Series] = None, alvo_em_R: bool = False, uma_posicao: bool = True,
            activo: Optional[str] = None) -> pd.DataFrame:
    """Simula trades com as regras comuns e devolve um DataFrame de trades.

    Regras (iguais para todas as famílias):
    * ``sinais`` é uma Series de lado (+1 compra, -1 venda, 0 nada) alinhada ao FECHO da vela k.
    * Entrada na ABERTURA da vela k+1 (execução manual).
    * ``stop``/``alvo``: Series de PREÇOS alinhadas a k, ou números = múltiplos de ``escala`` (Series
      em preço, p.ex. ATR ou sigma, lida em k) a partir da entrada. Com ``alvo_em_R=True`` o alvo
      numérico é um múltiplo de R (distância entrada-stop).
    * R = |entrada - stop|. Sinais com R <= 0 (stop do lado errado) são ignorados.
    * Stop tocado quando a mínima (compra) ou a máxima (venda) cruza o nível; preenche no nível ou
      na abertura da vela se esta já saltou o stop (o pior dos dois). Testa-se já na vela de entrada.
    * Alvo preenchido no nível quando a máxima/mínima o toca. Alvo e stop na mesma vela = STOP.
    * Saída por tempo ao fecho da vela n_max (a vela de entrada é a 1.ª).
    * Custo ``custo_bps`` por lado sobre o nocional de entrada e de saída, convertido a R.
    * ``uma_posicao``: ignora sinais enquanto há posição aberta (um trade de cada vez por activo).

    Colunas devolvidas: activo, sinal_em (fecho da vela do sinal), entrada_em, saida_em (fecho da vela
    de saída), lado, entrada, stop, alvo, saida, motivo (stop|alvo|tempo|fim_dados), R_bruto,
    R_liquido, duracao (velas), duracao_h, R_preco (R em preço). ``.attrs["ignorados"]`` conta sinais
    descartados (sem vela seguinte, R inválido ou posição aberta).
    """
    n = len(df15)
    idx = df15.index
    o = df15["open"].to_numpy(dtype=float)
    h = df15["high"].to_numpy(dtype=float)
    l = df15["low"].to_numpy(dtype=float)
    c = df15["close"].to_numpy(dtype=float)
    fecho = df15["fecho_em"].to_numpy() if "fecho_em" in df15 else (idx + pd.Timedelta(minutes=15)).to_numpy()
    sin = sinais.reindex(idx).fillna(0).to_numpy(dtype=float)
    stop_a = _as_array(stop, n, idx)
    alvo_a = _as_array(alvo, n, idx)
    stop_e_preco = isinstance(stop, (pd.Series, np.ndarray, list, tuple))
    alvo_e_preco = isinstance(alvo, (pd.Series, np.ndarray, list, tuple))
    esc = _as_array(escala, n, idx) if escala is not None else None
    if (not stop_e_preco or (not alvo_e_preco and not alvo_em_R)) and esc is None:
        raise ValueError("stop/alvo numéricos exigem 'escala' (Series em preço, p.ex. ATR)")
    nome = activo if activo is not None else (str(df15["activo"].iloc[0]) if "activo" in df15 else "?")
    cfrac = custo_bps / 1e4
    trades: List[dict] = []
    ignorados = 0
    livre_a_partir = 0  # posição da primeira vela em que se pode voltar a entrar
    for k in np.flatnonzero(sin != 0):
        lado = 1.0 if sin[k] > 0 else -1.0
        e = k + 1
        if e >= n:
            ignorados += 1
            continue
        if uma_posicao and e < livre_a_partir:
            ignorados += 1
            continue
        entrada = o[e]
        if stop_e_preco:
            s_px = stop_a[k]
        else:
            s_px = entrada - lado * stop_a[k] * esc[k]
        if not np.isfinite(s_px):
            ignorados += 1
            continue
        R = lado * (entrada - s_px)
        if not (R > 0):
            ignorados += 1
            continue
        if alvo_e_preco:
            a_px = alvo_a[k]
        elif alvo_em_R:
            a_px = entrada + lado * alvo_a[k] * R
        else:
            a_px = entrada + lado * alvo_a[k] * esc[k]
        if not np.isfinite(a_px) or lado * (a_px - entrada) <= 0:
            ignorados += 1
            continue
        fim = min(e + n_max - 1, n - 1)
        hh, ll, oo = h[e:fim + 1], l[e:fim + 1], o[e:fim + 1]
        if lado > 0:
            toca_stop = ll <= s_px
            toca_alvo = hh >= a_px
        else:
            toca_stop = hh >= s_px
            toca_alvo = ll <= a_px
        js = int(np.argmax(toca_stop)) if toca_stop.any() else -1
        ja = int(np.argmax(toca_alvo)) if toca_alvo.any() else -1
        if js >= 0 and (ja < 0 or js <= ja):
            j = js
            # pior entre o nível e a abertura da vela (abertura já saltou o stop)
            saida = min(s_px, oo[j]) if lado > 0 else max(s_px, oo[j])
            motivo = "stop"
        elif ja >= 0:
            j = ja
            saida = a_px
            motivo = "alvo"
        else:
            j = fim - e
            saida = c[fim]
            motivo = "tempo"
        if motivo == "tempo" and fim < e + n_max - 1:
            motivo = "fim_dados"
        R_bruto = lado * (saida - entrada) / R
        custo_R = (entrada + saida) * cfrac / R
        trades.append(dict(
            activo=nome, sinal_em=pd.Timestamp(fecho[k]), entrada_em=idx[e], saida_em=pd.Timestamp(fecho[e + j]),
            lado=int(lado), entrada=entrada, stop=s_px, alvo=a_px, saida=float(saida), motivo=motivo,
            R_bruto=float(R_bruto), R_liquido=float(R_bruto - custo_R), duracao=int(j + 1),
            duracao_h=float((j + 1) * 0.25), R_preco=float(R),
        ))
        livre_a_partir = e + j + 1
    cols = ["activo", "sinal_em", "entrada_em", "saida_em", "lado", "entrada", "stop", "alvo", "saida", "motivo",
            "R_bruto", "R_liquido", "duracao", "duracao_h", "R_preco"]
    out = pd.DataFrame(trades, columns=cols)
    out.attrs["ignorados"] = ignorados
    return out


def sinais_aleatorios(df15: pd.DataFrame, n_sinais: int, semente: int = 0,
                      de=None, ate=None) -> pd.Series:
    """Sinais aleatórios (lado +1/-1 ao acaso) em ``n_sinais`` velas escolhidas ao acaso: a nula."""
    rng = np.random.default_rng(semente)
    idx = df15.index
    mask = np.ones(len(idx), dtype=bool)
    if de is not None:
        mask &= idx >= _utc(de)
    if ate is not None:
        mask &= idx <= _utc(ate)
    cand = np.flatnonzero(mask)[:-1]
    esc = rng.choice(cand, size=min(n_sinais, len(cand)), replace=False)
    s = pd.Series(0, index=idx, dtype=int)
    s.iloc[esc] = rng.choice([-1, 1], size=len(esc))
    return s


# ---------------------------------------------------------------------------------------------
# 4. Métricas e critérios
# ---------------------------------------------------------------------------------------------

def _resumo(t: pd.DataFrame) -> pd.Series:
    r = t["R_liquido"].to_numpy(dtype=float) if len(t) else np.array([])
    n = len(r)
    ganhos = r[r > 0].sum() if n else 0.0
    perdas = -r[r < 0].sum() if n else 0.0
    pf = ganhos / perdas if perdas > 0 else (float("inf") if ganhos > 0 else float("nan"))
    dd = float("nan")
    if n:
        ordem = t.sort_values("saida_em")["R_liquido"].to_numpy(dtype=float)
        cum = np.cumsum(ordem)
        dd = float(np.max(np.maximum.accumulate(np.concatenate([[0.0], cum])) - np.concatenate([[0.0], cum])))
    return pd.Series({
        "n": n,
        "media": float(r.mean()) if n else float("nan"),
        "erro_padrao": float(r.std(ddof=1) / np.sqrt(n)) if n > 1 else float("nan"),
        "mediana": float(np.median(r)) if n else float("nan"),
        "soma": float(r.sum()) if n else 0.0,
        "PF": float(pf),
        "taxa_acerto": float((r > 0).mean()) if n else float("nan"),
        "dd_max": dd,
        "R_bruto_medio": float(t["R_bruto"].mean()) if n else float("nan"),
        "duracao_h_media": float(t["duracao_h"].mean()) if n else float("nan"),
        "frac_stop": float((t["motivo"] == "stop").mean()) if n else float("nan"),
        "frac_alvo": float((t["motivo"] == "alvo").mean()) if n else float("nan"),
    })


def metricas(trades: pd.DataFrame, de=None, ate=None) -> Dict[str, object]:
    """Métricas de um conjunto de trades (R líquido): global, por activo, por metade do período, por ano.

    Devolve dict com ``global`` (Series: n, media, erro_padrao, mediana, soma, PF, taxa_acerto,
    dd_max, ...), ``por_activo``, ``por_metade`` e ``por_ano`` (DataFrames). A metade divide-se no
    ponto médio entre ``de`` e ``ate`` (por omissão, primeiro e último sinal). DD máximo em R, sobre a
    curva acumulada ordenada pela saída, todos os activos juntos.
    """
    t = trades.copy()
    if len(t) == 0:
        vazio = pd.DataFrame()
        return {"global": _resumo(t), "por_activo": vazio, "por_metade": vazio, "por_ano": vazio}
    t["sinal_em"] = pd.to_datetime(t["sinal_em"], utc=True)
    t["saida_em"] = pd.to_datetime(t["saida_em"], utc=True)
    d0 = _utc(de) if de is not None else t["sinal_em"].min()
    d1 = _utc(ate) if ate is not None else t["sinal_em"].max()
    meio = d0 + (d1 - d0) / 2
    t["metade"] = np.where(t["sinal_em"] < meio, "1.a", "2.a")
    t["ano"] = t["sinal_em"].dt.year
    return {
        "global": _resumo(t),
        "por_activo": t.groupby("activo", sort=True).apply(_resumo, include_groups=False),
        "por_metade": t.groupby("metade", sort=True).apply(_resumo, include_groups=False),
        "por_ano": t.groupby("ano", sort=True).apply(_resumo, include_groups=False),
    }


def criterios(trades: pd.DataFrame, de, ate) -> pd.DataFrame:
    """Critérios H1 a H6 do utilizador sobre os trades com sinal em [de, ate].

    H1 n >= 100; H2 média líquida >= +0,15 R; H3 PF >= 1,3; H4 positivo (soma de R) em >= 3 activos;
    H5 DD máximo <= 10 R; H6 as duas metades do período positivas. Devolve DataFrame com
    ``valor``, ``limite``, ``passa`` e ``.attrs["passa_tudo"]``.
    """
    t = trades.copy()
    if len(t):
        t["sinal_em"] = pd.to_datetime(t["sinal_em"], utc=True)
        t = t[(t["sinal_em"] >= _utc(de)) & (t["sinal_em"] <= _utc(ate))]
    m = metricas(t, de, ate)
    g = m["global"]
    n_pos = int((m["por_activo"]["soma"] > 0).sum()) if len(t) else 0
    metades = m["por_metade"]["soma"] if len(t) else pd.Series(dtype=float)
    duas_pos = bool(len(metades) == 2 and (metades > 0).all())
    linhas = [
        ("H1", "n >= 100", g["n"], 100, g["n"] >= 100),
        ("H2", "media liquida >= 0.15 R", g["media"], 0.15, bool(g["media"] >= 0.15)),
        ("H3", "PF >= 1.3", g["PF"], 1.3, bool(g["PF"] >= 1.3)),
        ("H4", "activos positivos >= 3", n_pos, 3, n_pos >= 3),
        ("H5", "DD maximo <= 10 R", g["dd_max"], 10, bool(g["dd_max"] <= 10)),
        ("H6", "duas metades positivas", float(metades.min()) if len(metades) else float("nan"), 0, duas_pos),
    ]
    out = pd.DataFrame(linhas, columns=["criterio", "descricao", "valor", "limite", "passa"]).set_index("criterio")
    out.attrs["passa_tudo"] = bool(out["passa"].all())
    return out


# ---------------------------------------------------------------------------------------------
# 5. Registo de ensaios
# ---------------------------------------------------------------------------------------------

_CAMPOS_ENSAIO = ["data", "familia", "periodo", "parametros", "n", "media", "erro_padrao", "PF", "taxa_acerto",
                  "dd_max", "n_activos_positivos", "por_activo", "nota"]


def registar_ensaio(familia: str, parametros: dict, metr: Dict[str, object], periodo: str = "IS",
                    nota: str = "", pasta: Optional[str] = None) -> str:
    """Acrescenta uma linha a ``ensaios/<familia>.csv`` com data, parâmetros (JSON), n, média, PF e
    resultado por activo (JSON com a média R de cada um). TODA a configuração experimentada,
    inclusive as descartadas, fica registada; o relatório conta as linhas (M). Devolve o caminho.
    """
    pasta = pasta or ENSAIOS
    os.makedirs(pasta, exist_ok=True)
    p = os.path.join(pasta, f"{familia}.csv")
    g = metr["global"]
    pa = metr.get("por_activo")
    por_activo = {}
    n_pos = 0
    if isinstance(pa, pd.DataFrame) and len(pa):
        por_activo = {str(a): round(float(v), 4) for a, v in pa["media"].items()}
        n_pos = int((pa["soma"] > 0).sum())
    linha = {
        "data": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "familia": familia, "periodo": periodo,
        "parametros": json.dumps(parametros, sort_keys=True, ensure_ascii=False, default=str),
        "n": int(g["n"]), "media": round(float(g["media"]), 5) if np.isfinite(g["media"]) else "",
        "erro_padrao": round(float(g["erro_padrao"]), 5) if np.isfinite(g["erro_padrao"]) else "",
        "PF": round(float(g["PF"]), 4) if np.isfinite(g["PF"]) else "",
        "taxa_acerto": round(float(g["taxa_acerto"]), 4) if np.isfinite(g["taxa_acerto"]) else "",
        "dd_max": round(float(g["dd_max"]), 3) if np.isfinite(g["dd_max"]) else "",
        "n_activos_positivos": n_pos,
        "por_activo": json.dumps(por_activo, sort_keys=True), "nota": nota,
    }
    novo = not os.path.exists(p)
    with open(p, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_CAMPOS_ENSAIO)
        if novo:
            w.writeheader()
        w.writerow(linha)
    return p


def contar_ensaios(familia: str, pasta: Optional[str] = None) -> int:
    """Número M de configurações registadas para a família (linhas do CSV)."""
    p = os.path.join(pasta or ENSAIOS, f"{familia}.csv")
    if not os.path.exists(p):
        return 0
    return len(pd.read_csv(p))


# ---------------------------------------------------------------------------------------------
# 6. Estudo de eventos
# ---------------------------------------------------------------------------------------------

def estudo_de_eventos(df: pd.DataFrame, eventos: pd.Series, horizontes: Sequence[int],
                      desde_abertura_seguinte: bool = True, espacamento_min: int = 0) -> pd.DataFrame:
    """Retorno médio e mediano (em bps, log) nos ``horizontes`` (em velas de ``df``) após cada evento.

    ``eventos``: Series booleana (retorno sem sinal) ou de lado +1/-1/0 (retorno multiplicado pelo
    lado, isto é, "a favor do evento"). O retorno mede-se da ABERTURA da vela seguinte (execução
    manual; ``desde_abertura_seguinte=False`` usa o fecho do evento) até ao FECHO da vela k+h.
    ``espacamento_min`` > 0 descarta eventos a menos de tantas velas do último evento retido, para
    reduzir a sobreposição (o erro padrão assume independência). Devolve DataFrame indexado por
    horizonte: n, media_bps, erro_padrao_bps, t, mediana_bps, frac_pos.
    """
    ev = eventos.reindex(df.index).fillna(0)
    if ev.dtype == bool:
        lado = ev.astype(float).to_numpy()
    else:
        lado = np.sign(ev.to_numpy(dtype=float))
    pos = np.flatnonzero(lado != 0)
    if espacamento_min > 0 and len(pos):
        retidos = [pos[0]]
        for p in pos[1:]:
            if p - retidos[-1] >= espacamento_min:
                retidos.append(p)
        pos = np.asarray(retidos)
    c = np.log(df["close"].to_numpy(dtype=float))
    o = np.log(df["open"].to_numpy(dtype=float))
    n = len(df)
    linhas = []
    for hzt in horizontes:
        ok = pos[pos + hzt < n]
        base = o[ok + 1] if desde_abertura_seguinte else c[ok]
        r = (c[ok + hzt] - base) * lado[ok] * 1e4
        m = len(r)
        media = float(r.mean()) if m else float("nan")
        ep = float(r.std(ddof=1) / np.sqrt(m)) if m > 1 else float("nan")
        linhas.append({"horizonte": hzt, "n": m, "media_bps": media, "erro_padrao_bps": ep,
                       "t": media / ep if (m > 1 and ep > 0) else float("nan"),
                       "mediana_bps": float(np.median(r)) if m else float("nan"),
                       "frac_pos": float((r > 0).mean()) if m else float("nan")})
    return pd.DataFrame(linhas).set_index("horizonte")


# ---------------------------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------------------------

def recortar(df: pd.DataFrame, periodo: str) -> pd.DataFrame:
    """Recorta o DataFrame a um dos ``PERIODOS`` ("IS", "IS_preco", "OOS") pela abertura."""
    de, ate = PERIODOS[periodo]
    return df[(df.index >= _utc(de)) & (df.index <= _utc(ate))]


def carregar_todos(simbolos: Iterable[str] = SIMBOLOS, de=None, ate=None,
                   pasta: Optional[str] = None) -> Dict[str, pd.DataFrame]:
    """Carrega vários activos num dict {activo: df15}."""
    return {s: carregar(s, de, ate, pasta) for s in simbolos}
