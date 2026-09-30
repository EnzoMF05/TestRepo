"""Gerador de candles sintéticos (regimes de tendência/lateral) só para testar o código.

ATENÇÃO: isto NÃO diz nada sobre a rentabilidade da estratégia em ZEC real.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def make_candles(n: int = 4000, seed: int = 7, start: str = "2026-01-01", interval_min: int = 15) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq=f"{interval_min}min", tz="UTC")

    # regimes com duração média ~ 300 barras: alta, baixa, lateral
    drift = np.zeros(n)
    i = 0
    while i < n:
        length = int(rng.integers(120, 500))
        drift[i:i + length] = rng.choice([0.0006, -0.0006, 0.0])
        i += length
    vol = 0.006 * np.exp(0.5 * rng.standard_normal(n).cumsum() / np.sqrt(n) * 3)
    rets = drift + vol * rng.standard_normal(n)
    close = 50 * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[close[0]], close[:-1]])
    wick = np.abs(rng.standard_normal((2, n))) * vol * close * 0.6
    high = np.maximum(open_, close) + wick[0]
    low = np.minimum(open_, close) - wick[1]
    volume = 1000 * np.exp(0.4 * rng.standard_normal(n)) * (1 + 40 * np.abs(rets))
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)
    df.index.name = "time"
    return df


def reversal_scenario(direction: int = -1, n: int = 1000, seed: int = 3):
    """Cenário à mão para a estratégia de reversão. Devolve (candles, índice da barra de rejeição).

    direction=-1: rampa de 12 barras para cima (preço "caro") e candle de rejeição de topo -> reversão SHORT.
    direction=+1: rampa para baixo (preço "barato") e candle de rejeição de fundo -> reversão LONG.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    close = 50 + rng.normal(0, 0.06, n)
    d = -direction  # +1 = rampa para cima
    for k in range(12):
        close[n - 14 + k] = 50 + d * 0.25 * (k + 1)
    o = np.r_[close[0], close[:-1]]
    h = np.maximum(o, close) + 0.04
    l = np.minimum(o, close) - 0.04
    k = n - 2
    top = close[k - 1]
    if d > 0:
        o[k], h[k], l[k], close[k] = top + 0.10, top + 0.45, top - 0.20, top - 0.10
    else:
        o[k], h[k], l[k], close[k] = top - 0.10, top + 0.20, top - 0.45, top + 0.10
    o[n - 1], close[n - 1] = close[k], close[k] - d * 0.02
    h[n - 1], l[n - 1] = max(o[n - 1], close[n - 1]) + 0.03, min(o[n - 1], close[n - 1]) - 0.03
    df = pd.DataFrame({"open": o, "high": h, "low": l, "close": close, "volume": np.full(n, 1000.0)}, index=idx)
    df.index.name = "time"
    return df, k


def crowd_frame(index, funding=0.01, pctl=50.0, oi_rev=0.0, oi_change=0.0) -> pd.DataFrame:
    """Features de funding/OI constantes (formato de `derivs.build_frame`)."""
    return pd.DataFrame({"funding_8h_pct": funding, "funding_pctl": pctl, "oi_change_pct": oi_change,
                         "oi_rev_pct": oi_rev}, index=index)


def informed_funding(df, threshold: float = 4.0) -> dict:
    """{início da janela de 8h em ms: taxa} que acompanha o preço: funding extremo quando o preço está muito
    esticado (positivo em cima, negativo em baixo). Só serve para exercitar os DOIS lados da reversão nos testes:
    não diz nada sobre a relação real entre funding e preço."""
    from zec_bot.strategy import Params, prepare
    base = prepare(df, Params(mode="trend"))
    win = pd.Series(df.index.floor("8h"), index=df.index)
    hi = base["stretch_up"].fillna(0).groupby(win).max()
    lo = base["stretch_dn"].fillna(0).groupby(win).max()
    return {int(w.timestamp() * 1000): (-0.0006 if lo[w] >= threshold else (0.0008 if hi[w] >= threshold else 0.0001))
            for w in hi.index}


def sweep_scenario(direction: int = +1, n: int = 400, seed: int = 11, level: float | None = None,
                   hour: tuple | None = None):
    """15m à volta de 100 (ATR 1H ~2) cuja ÚLTIMA hora varre um nível. Devolve (candles, info).

    direction=+1: varre 98.5 por baixo (low 97.0), fecha de volta acima -> LONG; -1: varre 101.5 por cima -> SHORT.
    `hour` = ((o,h,l,c) x 4) troca as 4 barras da hora varrida; `level` troca o nível.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-03-01 00:00", periods=n, freq="15min", tz="UTC")
    close = 100 + rng.normal(0, 0.6, n)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.25, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.25, n))
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1000.0}, index=idx)
    if hour is None:
        hour = ((100.0, 100.3, 99.4, 99.6), (99.6, 99.7, 97.0, 97.4), (97.4, 99.2, 97.3, 99.0), (99.0, 100.4, 98.9, 99.9)) \
            if direction > 0 else \
            ((100.0, 100.6, 99.7, 100.4), (100.4, 103.0, 100.3, 102.6), (102.6, 102.7, 100.8, 101.0), (101.0, 101.1, 99.6, 100.1))
    df.iloc[-4:, :4] = np.array(hour)
    df.index.name = "time"
    lvl = level if level is not None else (98.5 if direction > 0 else 101.5)
    return df, {"level": lvl, "last": n - 1, "candle_open": idx[-4], "close_ts": idx[-1] + pd.Timedelta(minutes=15),
                "direction": direction}


def append_bars(df: pd.DataFrame, bars: list) -> pd.DataFrame:
    """Acrescenta barras de 15m (o,h,l,c) a seguir ao fim de `df`."""
    idx = pd.date_range(df.index[-1] + pd.Timedelta(minutes=15), periods=len(bars), freq="15min", tz="UTC")
    extra = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=idx).assign(volume=1000.0)
    out = pd.concat([df, extra])
    out.index.name = "time"
    return out
