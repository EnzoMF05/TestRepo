"""Estratégia de day trading para ZEC (barras de 15m, contexto de 1h).

Modo "reversal" (o teu estilo): contra o excesso, com três condições em simultâneo
  1. mercado CARREGADO: funding da Binance extremo para este ativo (percentil + mínimo absoluto)
       - longs sobrelotados (funding alto)    -> só SHORT
       - shorts sobrelotados (funding muito negativo) -> só LONG
  2. preço ESTICADO ("caro"/"barato"): afastado da EMA55 em ATR e RSI quente/frio nas últimas 3 barras
  3. GATILHO de rejeição: candle contra o excesso (pavio de rejeição ou fecho abaixo/acima da mínima/máxima
     anterior, com o RSI a virar). Nunca entra só porque "está caro": espera a viragem.

Modo "trend": a favor da tendência de 1h, duas configurações:
  * pullback : recuo até à EMA rápida e recuperação (candle de retoma, RSI a virar, lado certo do VWAP)
  * breakout : fecho acima/abaixo do canal de N barras com volume acima da média
  Com `avoid_expensive_longs` não abre longs quando o preço já está "caro".

Todos os cálculos usam apenas barras FECHADAS e dados passados; a tendência de 1h só fica
disponível depois de a barra de 1h fechar (ver `_htf_trend`).

ATENÇÃO: os parâmetros por defeito são pontos de partida razoáveis, NÃO foram otimizados nem
validados com dados reais. Corre `python -m zec_bot.backtest` antes de confiares neles.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import indicators as ind
from .config import Config


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

    # --- modo e "caro" (aplica-se à estratégia de tendência) ---
    mode: str = "trend"  # trend | reversal | both | sweep   (Config.strategy escolhe o do utilizador)
    avoid_expensive_longs: bool = False
    avoid_ext_atr: float = 2.0  # "caro" = a mais de 2 ATR acima da EMA55...
    avoid_rsi: float = 70.0  # ... ou RSI >= 70

    # --- reversão ---
    rev_short_min_pctl: float = 90.0
    rev_short_min_funding_8h: float = 0.03
    rev_long_max_pctl: float = 5.0
    rev_long_max_funding_8h: float = -0.02
    rev_use_oi: bool = False
    rev_oi_min_pct: float = 3.0
    rev_ext_atr: float = 2.5  # esticado: >= 2.5 ATR da EMA55 em alguma das últimas 3 barras
    rev_rsi: float = 65.0  # RSI >= 65 (short) / <= 35 (long) em alguma das últimas 3 barras
    rev_wick: float = 0.35  # pavio de rejeição >= 35% da barra
    rev_swing_bars: int = 8
    rev_stop_max_atr: float = 3.0
    rev_tp1_r: float = 1.0  # reversão: alvos mais curtos (regressão à média), taxa de acerto mais alta
    rev_tp2_r: float = 2.0

    @classmethod
    def from_cfg(cls, cfg: Config) -> "Params":
        return cls(
            mode=cfg.strategy, avoid_expensive_longs=cfg.avoid_expensive_longs,
            rev_short_min_pctl=cfg.rev_short_min_pctl, rev_short_min_funding_8h=cfg.rev_short_min_funding_8h,
            rev_long_max_pctl=cfg.rev_long_max_pctl, rev_long_max_funding_8h=cfg.rev_long_max_funding_8h,
            rev_use_oi=cfg.rev_use_oi, rev_oi_min_pct=cfg.rev_oi_min_pct,
        )

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
    tp1_r: float = 1.5
    tp2_r: float = 3.0
    info: dict = field(default_factory=dict)  # contexto do sinal para a mensagem (reversão)


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


def prepare(df: pd.DataFrame, p: Params | None = None, deriv: pd.DataFrame | None = None) -> pd.DataFrame:
    """Devolve o DataFrame com indicadores e colunas `sig` (+1/-1/0), `kind`, `risk`, `tp1_r`, `tp2_r`.

    `deriv`: features de funding/OI por barra (`derivs.build_frame`). Sem elas, a reversão não gera sinais.
    """
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
    for col in ("funding_8h_pct", "funding_pctl", "oi_rev_pct"):
        f[col] = deriv[col].reindex(f.index).to_numpy() if deriv is not None and col in deriv else np.nan

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

    # ---------- "caro": sem longs de tendência quando o preço já subiu demasiado ----------
    ext_slow_up = (c - f["ema_s"]) / f["atr"]
    ext_slow_dn = (f["ema_s"] - c) / f["atr"]
    if p.avoid_expensive_longs:
        expensive = (ext_slow_up >= p.avoid_ext_atr) | (f["rsi"] >= p.avoid_rsi)
        long_pb, long_bo = long_pb & ~expensive, long_bo & ~expensive
    if p.mode in ("reversal", "sweep"):
        long_pb = long_bo = short_pb = short_bo = pd.Series(False, index=f.index)

    # ---------- REVERSÃO: mercado carregado + preço esticado + gatilho de rejeição ----------
    stretch_up = ext_slow_up.rolling(3).max()
    stretch_dn = ext_slow_dn.rolling(3).max()
    f["stretch_up"], f["stretch_dn"] = stretch_up, stretch_dn
    f["rsi_hi3"], f["rsi_lo3"] = f["rsi"].rolling(3).max(), f["rsi"].rolling(3).min()
    body_hi, body_lo = np.maximum(o, c), np.minimum(o, c)
    up_wick, lo_wick = (h - body_hi) / rng, (body_lo - l) / rng

    crowd_long = (f["funding_8h_pct"] >= p.rev_short_min_funding_8h) & (f["funding_pctl"] >= p.rev_short_min_pctl)
    crowd_short = (f["funding_8h_pct"] <= p.rev_long_max_funding_8h) & (f["funding_pctl"] <= p.rev_long_max_pctl)
    if p.rev_use_oi:  # posições a acumular: OI a subir; sem dados de OI => sem sinal (nunca adivinha)
        oi_grew = f["oi_rev_pct"] >= p.rev_oi_min_pct
        crowd_long, crowd_short = crowd_long & oi_grew, crowd_short & oi_grew

    rev_short = (
        crowd_long & (stretch_up >= p.rev_ext_atr) & (f["rsi_hi3"] >= p.rev_rsi)
        & (c < o) & ((up_wick >= p.rev_wick) | (c < l.shift(1))) & (f["rsi"] < rsi_prev)
    )
    rev_long = (
        crowd_short & (stretch_dn >= p.rev_ext_atr) & (f["rsi_lo3"] <= 100 - p.rev_rsi)
        & (c > o) & ((lo_wick >= p.rev_wick) | (c > h.shift(1))) & (f["rsi"] > rsi_prev)
    )
    if p.mode in ("trend", "sweep") or deriv is None:
        rev_short = rev_long = pd.Series(False, index=f.index)

    # stop além do extremo recente (reversão): mais folga que na tendência
    swing = p.rev_swing_bars
    rrisk_long = np.maximum(c - (l.rolling(swing).min() - p.stop_buffer_atr * atr_), p.stop_min_atr * atr_)
    rrisk_short = np.maximum((h.rolling(swing).max() + p.stop_buffer_atr * atr_) - c, p.stop_min_atr * atr_)
    rev_long = rev_long & (rrisk_long <= p.rev_stop_max_atr * atr_) & (rrisk_long / c * 100 >= p.min_risk_pct)
    rev_short = rev_short & (rrisk_short <= p.rev_stop_max_atr * atr_) & (rrisk_short / c * 100 >= p.min_risk_pct)

    sig = np.zeros(len(f), dtype=int)
    kind = np.full(len(f), "", dtype=object)
    risk = np.full(len(f), np.nan)
    tp1_r = np.full(len(f), np.nan)
    tp2_r = np.full(len(f), np.nan)
    # Ordem de prioridade (o último a escrever ganha): pullback < breakout < reversão
    for mask, s_, k, r, t1, t2 in (
        (long_pb, 1, "pullback", risk_long, p.tp1_r, p.tp2_r),
        (short_pb, -1, "pullback", risk_short, p.tp1_r, p.tp2_r),
        (long_bo, 1, "breakout", risk_long, p.tp1_r, p.tp2_r),
        (short_bo, -1, "breakout", risk_short, p.tp1_r, p.tp2_r),
        (rev_long, 1, "reversal", rrisk_long, p.rev_tp1_r, p.rev_tp2_r),
        (rev_short, -1, "reversal", rrisk_short, p.rev_tp1_r, p.rev_tp2_r),
    ):
        m = mask.fillna(False).to_numpy()
        sig[m] = s_
        kind[m] = k
        risk[m] = r.to_numpy()[m]
        tp1_r[m], tp2_r[m] = t1, t2
    f["sig"], f["kind"], f["risk"], f["tp1_r"], f["tp2_r"] = sig, kind, risk, tp1_r, tp2_r
    return f


def signal_at(feat: pd.DataFrame, i: int, p: Params | None = None) -> Signal | None:
    """Constrói o Signal da barra `i` (posição inteira) se houver sinal nessa barra."""
    p = p or Params()
    row = feat.iloc[i]
    s = int(row["sig"])
    if s == 0:
        return None
    entry, risk = float(row["close"]), float(row["risk"])
    t1, t2 = float(row["tp1_r"]), float(row["tp2_r"])
    info = {}
    if row["kind"] == "reversal":
        up = s < 0  # short: o excesso foi para cima
        info = {
            "stretch_atr": float(row["stretch_up"] if up else row["stretch_dn"]),
            "rsi_extreme": float(row["rsi_hi3"] if up else row["rsi_lo3"]),
            "funding_8h_pct": float(row["funding_8h_pct"]),
            "funding_pctl": float(row["funding_pctl"]),
            "oi_rev_pct": None if pd.isna(row["oi_rev_pct"]) else float(row["oi_rev_pct"]),
        }
    return Signal(
        time=feat.index[i],
        side="long" if s > 0 else "short",
        kind=str(row["kind"]),
        entry=entry,
        stop=entry - s * risk,
        tp1=entry + s * t1 * risk,
        tp2=entry + s * t2 * risk,
        risk=risk,
        atr=float(row["atr"]),
        rsi=float(row["rsi"]),
        adx=float(row["adx"]),
        htf=int(row["htf"]),
        tp1_r=t1,
        tp2_r=t2,
        info=info,
    )
