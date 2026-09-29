"""Contexto de derivados (funding e open interest) para os sinais e para o backtest.

Live: funding e OI atuais vêm da API gratuita da Hyperliquid. A variação de OI calcula-se a partir de
amostras que o próprio robô vai guardando (persistidas no ficheiro de estado) ou, se tiveres
COINALYZE_API_KEY, do histórico do Coinalyze (útil nas primeiras horas, antes de haver amostras).

Backtest: o funding tem histórico fundo na Hyperliquid; o OI só existe via Coinalyze e só ~2-3 semanas.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import numpy as np
import pandas as pd

from . import hl
from .coinalyze import Coinalyze, CoinalyzeError
from .config import Config

log = logging.getLogger("zec_bot.derivs")

SAMPLE_EVERY_S = 300  # 1 amostra de OI de 5 em 5 minutos
KEEP_S = 30 * 3600  # guarda 30h de amostras


def funding_8h_pct(funding_hr: float) -> float:
    """Taxa horária (fração) -> equivalente a 8h em % (convenção das CEX; a Hyperliquid cobra de hora a hora)."""
    return funding_hr * 8 * 100


def interpret(price_chg_pct: Optional[float], oi_chg_pct: Optional[float],
              oi_flat: float = 0.5, px_flat: float = 0.2) -> Optional[str]:
    """Leitura clássica preço + OI. É uma heurística de contexto, não uma previsão."""
    if price_chg_pct is None or oi_chg_pct is None:
        return None
    if abs(oi_chg_pct) < oi_flat:
        return "OI estável"
    if abs(price_chg_pct) < px_flat:
        return "OI a subir sem direção (posições a acumular)" if oi_chg_pct > 0 else "OI a cair sem direção (posições a fechar)"
    if price_chg_pct > 0:
        return "preço↑ + OI↑: entram longs novos" if oi_chg_pct > 0 else "preço↑ + OI↓: sobe por fecho de shorts (mais fraco)"
    return "preço↓ + OI↑: entram shorts novos" if oi_chg_pct > 0 else "preço↓ + OI↓: fecho de longs / liquidações"


class DerivsMonitor:
    def __init__(self, cfg: Config, fetch_ctx: Optional[Callable[[], dict]] = None,
                 coinalyze: Optional[Coinalyze] = None, now: Optional[Callable[[], float]] = None):
        self.cfg = cfg
        self.fetch_ctx = fetch_ctx or (lambda: hl.fetch_asset_ctx(cfg.base))
        self.coinalyze = coinalyze if coinalyze is not None else Coinalyze(cfg.coinalyze_api_key, cfg.coinalyze_symbol, cfg.base)
        self.now = now or time.time
        self.samples: list[list[float]] = []  # [ts_s, oi_coins, mark]
        self.last_ctx: Optional[dict] = None
        self.last_error = ""
        self._cz: tuple[float, Optional[pd.DataFrame]] = (0.0, None)

    # ------------------------------------------------------------------ recolha
    def refresh(self, force: bool = False) -> Optional[dict]:
        """Contexto atual da Hyperliquid (com cache de 5 min). None se a chamada falhar."""
        t = self.now()
        if not force and self.last_ctx is not None and t - self.last_ctx["_ts"] < SAMPLE_EVERY_S:
            return self.last_ctx
        try:
            ctx = dict(self.fetch_ctx())
        except Exception as e:  # noqa: BLE001 — derivados são contexto: nunca podem derrubar o robô
            self.last_error = str(e)
            log.warning("Sem dados de derivados: %s", e)
            return None
        ctx["_ts"] = t
        self.last_ctx = ctx
        if not self.samples or t - self.samples[-1][0] >= SAMPLE_EVERY_S * 0.9:
            self.samples.append([t, ctx["oi_coins"], ctx["mark"]])
        cutoff = t - KEEP_S
        self.samples = [s for s in self.samples if s[0] >= cutoff]
        return ctx

    def _coinalyze_oi(self, hours: float) -> Optional[pd.DataFrame]:
        t = self.now()
        if t - self._cz[0] < SAMPLE_EVERY_S and self._cz[1] is not None:
            return self._cz[1]
        df = self.coinalyze.oi_history("15min", hours=max(hours + 1, 8.0), now=t)
        self._cz = (t, df)
        return df

    def oi_change_pct(self, hours: float) -> tuple[Optional[float], Optional[str]]:
        """(variação % do OI nas últimas `hours`, fonte). Fonte: 'hl' (amostras próprias) ou 'coinalyze'."""
        if len(self.samples) >= 2:
            latest = self.samples[-1]
            target = latest[0] - hours * 3600
            best = min(self.samples, key=lambda s: abs(s[0] - target))
            if best is not latest and abs(best[0] - target) <= max(600.0, hours * 3600 * 0.15) and best[1] > 0:
                return (latest[1] / best[1] - 1) * 100, "hl"
        if self.coinalyze.enabled:
            try:
                c = self._coinalyze_oi(hours)["c"]
                base = c.asof(c.index[-1] - pd.Timedelta(hours=hours))
                if pd.notna(base) and base > 0:
                    return (float(c.iloc[-1]) / float(base) - 1) * 100, "coinalyze"
            except CoinalyzeError as e:
                self.last_error = str(e)
                log.warning("Coinalyze: %s", e)
        return None, None

    # ------------------------------------------------------------------ resultado
    def snapshot(self, price_change_1h_pct: Optional[float] = None) -> Optional[dict]:
        """Tudo o que os sinais precisam. Chaves `funding_8h_pct` e `oi_change_pct` alimentam os filtros."""
        ctx = self.refresh(force=True)
        if ctx is None:
            return None
        oi1, src1 = self.oi_change_pct(1.0)
        oi4, src4 = self.oi_change_pct(4.0)
        look = self.cfg.oi_lookback_hours
        oi_f = oi1 if look == 1.0 else self.oi_change_pct(look)[0]
        return {
            "funding_hr_pct": ctx["funding_hr"] * 100,
            "funding_8h_pct": funding_8h_pct(ctx["funding_hr"]),
            "premium_pct": ctx["premium"] * 100,
            "oi_coins": ctx["oi_coins"],
            "oi_usd": ctx["oi_coins"] * ctx["mark"],
            "oi_change_1h_pct": oi1,
            "oi_change_4h_pct": oi4,
            "oi_change_pct": oi_f,
            "oi_source": src1 or src4,
            "mark": ctx["mark"],
            "max_leverage": ctx.get("max_leverage", 0.0),
            "day_vol_usd": ctx.get("day_vol_usd", 0.0),
            "price_change_1h_pct": price_change_1h_pct,
            "reading": interpret(price_change_1h_pct, oi1),
        }


# ---------------------------------------------------------------------------- backtest
def funding_series(rows: list[list], index: pd.DatetimeIndex, interval_min: int) -> pd.Series:
    """Funding (8h-equivalente, %) conhecido no FECHO de cada barra: usa só registos com time <= fecho."""
    if not rows:
        return pd.Series(np.nan, index=index)
    s = pd.Series([r[1] * 8 * 100 for r in rows],
                  index=pd.to_datetime([r[0] for r in rows], unit="ms", utc=True)).sort_index()
    s = s[~s.index.duplicated()]
    close_times = index + pd.Timedelta(minutes=interval_min)
    out = s.asof(close_times)
    out.index = index
    out[close_times < s.index[0]] = np.nan
    return out


def oi_change_series(oi: pd.DataFrame, index: pd.DatetimeIndex, interval_min: int, hours: float) -> pd.Series:
    """Variação % do OI nas últimas `hours` no fecho de cada barra, só onde há histórico do Coinalyze.

    A barra de OI que começa em T (15min) fecha em T+15min, o mesmo instante em que a barra de preço de
    abertura T fecha; por isso usa-se o `c` da barra com t == T e nada posterior.
    """
    if interval_min != 15:
        raise ValueError("o alinhamento do OI do Coinalyze está feito para barras de 15m")
    c = oi["c"].sort_index()
    now = c.asof(index)
    base = c.asof(index - pd.Timedelta(hours=hours))
    chg = (now.to_numpy() / base.to_numpy() - 1) * 100
    out = pd.Series(chg, index=index)
    out[(index > c.index[-1]) | ((index - pd.Timedelta(hours=hours)) < c.index[0])] = np.nan
    return out
