"""Estratégia de day trading para ZEC (barras de 15m, contexto de 1h).

Duas configurações, sempre a favor da tendência de 1h:
  * pullback : recuo até à EMA rápida e recuperação (candle de retoma, RSI a virar, lado certo do VWAP)
  * breakout : fecho acima/abaixo do canal de N barras com volume acima da média

Todos os cálculos usam apenas barras FECHADAS e dados passados; a tendência de 1h só fica
disponível depois de a barra de 1h fechar (ver `_htf_trend`).

ATENÇÃO: os parâmetros por defeito são pontos de partida razoáveis, NÃO foram otimizados nem
validados com dados reais. Corre `python -m zec_bot.backtest` antes de confiares neles.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import indicators as ind


@dataclass
class Params:
    ema_fast: int = 21
    ema_slow: int = 55
    htf_minutes: int = 60
    htf_fast: int = 21
    htf_slow: int = 55
    rsi_len: int = 14
    atr_len: int = 14
    adx_len: int = 14
    adx_min: float = 18.0
    donchian: int = 20
    vol_len: int = 20
    vol_mult: float = 1.3
    pb_rsi_min: float = 40.0
    pb_rsi_max: float = 68.0
    bo_rsi_max: float = 78.0
    min_close_pos: float = 0.65  # breakout: fecho na parte alta (long) / baixa (short) da barra
    max_extension_atr: float = 2.5  # não perseguir preço já muito esticado face à EMA
    swing_bars: int = 6
    stop_buffer_atr: float = 0.2
    stop_min_atr: float = 0.8
    stop_max_atr: float = 2.5
    min_risk_pct: float = 0.5  # stops mais curtos que isto são comidos pelas comissões
    tp1_r: float = 1.5
    tp2_r: float = 3.0

    @property
    def warmup_bars(self) -> int:
        """Nº de barras de 15m necessárias para as EMAs (sobretudo a de 1h) estabilizarem."""
        per_htf = self.htf_minutes // 15
        return max(self.ema_slow * 5, self.donchian * 2, self.htf_slow * 3 * per_htf) + 20


@dataclass
class Signal:
    time: pd.Timestamp  # abertura da barra que gerou o sinal
    side: str  # "long" | "short"
    kind: str  # "pullback" | "breakout"
    entry: float
    stop: float
    tp1: float
    tp2: float
    risk: float  # distância entrada-stop (preço)
    atr: float
    rsi: float
    adx: float
    htf: int


def _htf_trend(df: pd.DataFrame, p: Params) -> pd.Series:
    """+1 / -1 / 0 conforme a tendência da barra de 1h MAIS RECENTE JÁ FECHADA."""
    rule = f"{p.htf_minutes}min"
    htf = (
        df.resample(rule)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )
    fast = ind.ema(htf["close"], p.htf_fast)
    slow = ind.ema(htf["close"], p.htf_slow)
    up = (htf["close"] > slow) & (fast > slow)
    down = (htf["close"] < slow) & (fast < slow)
    trend = pd.Series(np.where(up, 1, np.where(down, -1, 0)), index=htf.index, dtype="float64")
    # A barra de 1h rotulada H só existe por inteiro em H+1h: desloca o índice para o fim da barra.
    trend.index = trend.index + pd.Timedelta(minutes=p.htf_minutes)
    aligned = trend.reindex(df.index, method="ffill")
    return aligned.fillna(0).astype(int)


def prepare(df: pd.DataFrame, p: Params | None = None) -> pd.DataFrame:
    """Devolve o DataFrame com indicadores e colunas `sig` (+1/-1/0), `kind` e `risk`."""
    p = p or Params()
    f = df.copy()
    c, o, h, l = f["close"], f["open"], f["high"], f["low"]

    f["ema_f"] = ind.ema(c, p.ema_fast)
    f["ema_s"] = ind.ema(c, p.ema_slow)
    f["rsi"] = ind.rsi(c, p.rsi_len)
    f["atr"] = ind.atr(f, p.atr_len)
    f["adx"] = ind.adx(f, p.adx_len)
    f["vwap"] = ind.session_vwap(f)
    f["vol_ma"] = f["volume"].rolling(p.vol_len).mean().shift(1)
    f["don_hi"] = h.rolling(p.donchian).max().shift(1)
    f["don_lo"] = l.rolling(p.donchian).min().shift(1)
    f["htf"] = _htf_trend(f, p)

    bull, bear = f["htf"] == 1, f["htf"] == -1
    trending = f["adx"] >= p.adx_min
    rng = (h - l).replace(0.0, np.nan)
    close_pos = (c - l) / rng  # 1 = fechou no máximo, 0 = fechou no mínimo
    ext_up = (c - f["ema_f"]) / f["atr"]
    ext_dn = (f["ema_f"] - c) / f["atr"]
    vol_ok = f["volume"] >= p.vol_mult * f["vol_ma"]
    rsi_prev = f["rsi"].shift(1)

    # ---------- LONG ----------
    reclaim_up = (c > f["ema_f"]) & ((l <= f["ema_f"]) | (c.shift(1) <= f["ema_f"].shift(1)))
    long_pb = (
        bull & (f["ema_f"] > f["ema_s"]) & trending & reclaim_up & (c > o)
        & f["rsi"].between(p.pb_rsi_min, p.pb_rsi_max) & (f["rsi"] > rsi_prev)
        & (c > f["vwap"]) & (ext_up <= p.max_extension_atr)
    )
    long_bo = (
        bull & trending & (c > f["don_hi"]) & vol_ok & (c > o)
        & (close_pos >= p.min_close_pos) & (f["rsi"] <= p.bo_rsi_max)
        & (c > f["vwap"]) & (ext_up <= p.max_extension_atr)
    )

    # ---------- SHORT (espelho) ----------
    reclaim_dn = (c < f["ema_f"]) & ((h >= f["ema_f"]) | (c.shift(1) >= f["ema_f"].shift(1)))
    short_pb = (
        bear & (f["ema_f"] < f["ema_s"]) & trending & reclaim_dn & (c < o)
        & f["rsi"].between(100 - p.pb_rsi_max, 100 - p.pb_rsi_min) & (f["rsi"] < rsi_prev)
        & (c < f["vwap"]) & (ext_dn <= p.max_extension_atr)
    )
    short_bo = (
        bear & trending & (c < f["don_lo"]) & vol_ok & (c < o)
        & (close_pos <= 1 - p.min_close_pos) & (f["rsi"] >= 100 - p.bo_rsi_max)
        & (c < f["vwap"]) & (ext_dn <= p.max_extension_atr)
    )

    # ---------- Stop estrutural (swing recente +/- buffer de ATR) ----------
    atr_ = f["atr"]
    risk_long = np.maximum(c - (l.rolling(p.swing_bars).min() - p.stop_buffer_atr * atr_), p.stop_min_atr * atr_)
    risk_short = np.maximum((h.rolling(p.swing_bars).max() + p.stop_buffer_atr * atr_) - c, p.stop_min_atr * atr_)
    ok_long = (risk_long <= p.stop_max_atr * atr_) & (risk_long / c * 100 >= p.min_risk_pct)
    ok_short = (risk_short <= p.stop_max_atr * atr_) & (risk_short / c * 100 >= p.min_risk_pct)

    long_bo, long_pb = long_bo & ok_long, long_pb & ok_long
    short_bo, short_pb = short_bo & ok_short, short_pb & ok_short

    sig = np.zeros(len(f), dtype=int)
    kind = np.full(len(f), "", dtype=object)
    risk = np.full(len(f), np.nan)
    # Ordem de prioridade (o último a escrever ganha): pullback < breakout
    for mask, s, k, r in (
        (long_pb, 1, "pullback", risk_long),
        (short_pb, -1, "pullback", risk_short),
        (long_bo, 1, "breakout", risk_long),
        (short_bo, -1, "breakout", risk_short),
    ):
        m = mask.fillna(False).to_numpy()
        sig[m] = s
        kind[m] = k
        risk[m] = r.to_numpy()[m]
    f["sig"], f["kind"], f["risk"] = sig, kind, risk
    return f


def signal_at(feat: pd.DataFrame, i: int, p: Params | None = None) -> Signal | None:
    """Constrói o Signal da barra `i` (posição inteira) se houver sinal nessa barra."""
    p = p or Params()
    row = feat.iloc[i]
    s = int(row["sig"])
    if s == 0:
        return None
    entry, risk = float(row["close"]), float(row["risk"])
    return Signal(
        time=feat.index[i],
        side="long" if s > 0 else "short",
        kind=str(row["kind"]),
        entry=entry,
        stop=entry - s * risk,
        tp1=entry + s * p.tp1_r * risk,
        tp2=entry + s * p.tp2_r * risk,
        risk=risk,
        atr=float(row["atr"]),
        rsi=float(row["rsi"]),
        adx=float(row["adx"]),
        htf=int(row["htf"]),
    )
