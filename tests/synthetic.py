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
