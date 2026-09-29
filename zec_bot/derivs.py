"""Funding e open interest: medir "quão carregado" está o mercado.

Fonte de referência: perp USDT da Binance (o mercado que o Coinalyze mostra), em dados públicos sem chave.
O funding da Hyperliquid (onde operas) aparece só como informação, junto com a alavancagem máxima do ativo.

Os mesmos features (`build_frame`) alimentam o robô live e o backtest, sempre só com informação já conhecida
no fecho de cada barra.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import numpy as np
import pandas as pd

from . import hl
from .binance_futures import BinanceFutures, interval_hours
from .config import Config

log = logging.getLogger("zec_bot.derivs")

FUNDING_TTL_S = 1800  # o funding só muda de 1h a 8h: 30 min de cache chegam
OI_TTL_S = 120
STALE_FUNDING_S = 6 * 3600  # com a Binance em baixo, o funding em cache ainda vale até 6h
STALE_OI_S = 1800
OI_PERIOD_MIN = 15
OI_FETCH_DAYS = 4  # 4 dias de OI de 15m cabem numa só chamada (≤ 500 registos) e cobrem a janela de 24h


def funding_8h_pct(rate: float, interval_h: float = 1.0) -> float:
    """Taxa por intervalo de funding (fração) -> % equivalente a 8h. Hyperliquid: interval_h=1; Binance: o real."""
    return rate * (8.0 / interval_h) * 100


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


# ---------------------------------------------------------------------------- features por barra
def funding_features(rows: list[list], index: pd.DatetimeIndex, interval_min: int,
                     window_days: float = 30.0, min_records: int = 20) -> pd.DataFrame:
    """Por barra: funding (%/8h) e o seu PERCENTIL dentro dos últimos `window_days`, conhecidos no fecho da barra.

    O percentil diz quão extremo é o funding *para este ativo, agora* (0 = o mais negativo do período,
    100 = o mais positivo), sem eu ter de adivinhar o que é "alto" no ZEC.
    """
    out = pd.DataFrame(np.nan, index=index, columns=["funding_8h_pct", "funding_pctl"])
    if not rows:
        return out
    times = [r[0] for r in rows]
    f8 = pd.Series([r[1] * (8.0 / h) * 100 for r, h in zip(rows, interval_hours(times))],
                   index=pd.to_datetime(times, unit="ms", utc=True))
    f8 = f8[~f8.index.duplicated()].sort_index()
    pctl = f8.rolling(f"{int(window_days * 24)}h", min_periods=min_records).apply(
        lambda a: (a <= a[-1]).mean() * 100, raw=True)
    close_times = index + pd.Timedelta(minutes=interval_min)
    out["funding_8h_pct"] = f8.asof(close_times).to_numpy()
    out["funding_pctl"] = pctl.asof(close_times).to_numpy()
    return out


def oi_change_series(oi_rows: list[list], index: pd.DatetimeIndex, interval_min: int, hours: float,
                     period_min: int = OI_PERIOD_MIN) -> pd.Series:
    """Variação % do OI nas últimas `hours` no fecho de cada barra (NaN onde não há histórico).

    Conservador: a Binance dá um valor por período de 15m com o `timestamp` dessa barra; só o uso a partir de
    timestamp + período, o que exclui qualquer hipótese de olhar para o futuro.
    """
    out = pd.Series(np.nan, index=index)
    if not oi_rows:
        return out
    s = pd.Series([r[1] for r in oi_rows],
                  index=pd.to_datetime([r[0] + period_min * 60_000 for r in oi_rows], unit="ms", utc=True))
    s = s[~s.index.duplicated()].sort_index()
    close_times = index + pd.Timedelta(minutes=interval_min)
    then_times = close_times - pd.Timedelta(hours=hours)
    chg = (s.asof(close_times).to_numpy() / s.asof(then_times).to_numpy() - 1) * 100
    out = pd.Series(chg, index=index)
    out[(close_times > s.index[-1] + pd.Timedelta(minutes=2 * period_min)) | (then_times < s.index[0])] = np.nan
    return out


def build_frame(index: pd.DatetimeIndex, funding_rows: list[list], oi_rows: list[list], cfg: Config,
                window_days: float = 30.0) -> pd.DataFrame:
    """Colunas por barra: funding_8h_pct, funding_pctl, oi_change_pct (filtros), oi_rev_pct (reversão)."""
    f = funding_features(funding_rows, index, cfg.interval_min, window_days)
    f["oi_change_pct"] = oi_change_series(oi_rows, index, cfg.interval_min, cfg.oi_lookback_hours)
    f["oi_rev_pct"] = oi_change_series(oi_rows, index, cfg.interval_min, cfg.rev_oi_hours)
    return f


# ---------------------------------------------------------------------------- live
class DerivsMonitor:
    """Vai buscar (com cache) o funding e o OI da Binance e o funding/alavancagem máxima da Hyperliquid."""

    def __init__(self, cfg: Config, binance: Optional[BinanceFutures] = None,
                 venue: Optional[Callable[[], dict]] = None, now: Optional[Callable[[], float]] = None):
        self.cfg = cfg
        self.binance = binance or BinanceFutures(cfg.derivs_symbol)
        self.venue = venue if venue is not None else (lambda: hl.fetch_asset_ctx(cfg.base))
        self.now = now or time.time
        self.last_error = ""
        self._funding: tuple[float, list] = (-1e18, [])
        self._oi: tuple[float, list] = (-1e18, [])

    # ------------------------------------------------------------------ dados com cache
    def funding_rows(self) -> list[list]:
        t = self.now()
        if t - self._funding[0] >= FUNDING_TTL_S:
            start = int((t - (self.cfg.crowd_window_days + 3) * 86400) * 1000)
            try:
                self._funding = (t, self.binance.funding_history(start))
            except Exception:  # noqa: BLE001 — o funding muda de hora a hora: dados de há <6h ainda servem
                if not self._funding[1] or t - self._funding[0] > STALE_FUNDING_S:
                    raise
                log.warning("Binance indisponível: a usar funding em cache de há %.0f min", (t - self._funding[0]) / 60)
        return self._funding[1]

    def oi_rows(self) -> list[list]:
        t = self.now()
        if t - self._oi[0] >= OI_TTL_S:
            try:
                self._oi = (t, self.binance.oi_history(int((t - OI_FETCH_DAYS * 86400) * 1000)))
            except Exception:  # noqa: BLE001
                if not self._oi[1] or t - self._oi[0] > STALE_OI_S:
                    raise
        return self._oi[1]

    def frame(self, index: pd.DatetimeIndex) -> Optional[pd.DataFrame]:
        """Features por barra para a estratégia. None se o funding não estiver disponível (a reversão não corre)."""
        try:
            funding = self.funding_rows()
        except Exception as e:  # noqa: BLE001 — derivados nunca derrubam o robô
            self.last_error = str(e)
            log.warning("Sem funding da Binance: %s", e)
            return None
        try:
            oi = self.oi_rows()
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            log.warning("Sem histórico de OI da Binance: %s", e)
            oi = []
        return build_frame(index, funding, oi, self.cfg, self.cfg.crowd_window_days)

    # ------------------------------------------------------------------ para as mensagens
    def _oi_change_from_now(self, oi_now: float, hours: float) -> Optional[float]:
        rows = self.oi_rows()
        if not rows:
            return None
        target = (self.now() - hours * 3600) * 1000
        # só valores já disponíveis nesse instante (timestamp + período <= alvo)
        past = [r for r in rows if r[0] + OI_PERIOD_MIN * 60_000 <= target]
        if not past or oi_now <= 0 or past[-1][1] <= 0:
            return None
        return (oi_now / past[-1][1] - 1) * 100

    def snapshot(self, price_change_1h_pct: Optional[float] = None, frame_row: Optional[pd.Series] = None) -> Optional[dict]:
        """Números para a mensagem do sinal. None se a Binance não responder."""
        try:
            prem = self.binance.premium_index()
            oi_now = self.binance.open_interest()["oi_coins"]
            rows = self.funding_rows()
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            log.warning("Sem dados de derivados da Binance: %s", e)
            return None
        interval_h = interval_hours([r[0] for r in rows])[-1] if rows else 8.0
        oi1, oi4 = None, None
        try:
            oi1, oi4 = self._oi_change_from_now(oi_now, 1.0), self._oi_change_from_now(oi_now, 4.0)
            oi_f = self._oi_change_from_now(oi_now, self.cfg.oi_lookback_hours)
            oi_rev = self._oi_change_from_now(oi_now, self.cfg.rev_oi_hours)
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            oi_f = oi_rev = None
        venue = {}
        try:
            venue = self.venue() or {}
        except Exception as e:  # noqa: BLE001 — a Hyperliquid é só informação extra
            log.info("Sem contexto da Hyperliquid: %s", e)
        get = (lambda k: None if frame_row is None or pd.isna(frame_row.get(k)) else float(frame_row[k]))
        return {
            "source": "binance",
            "funding_8h_pct": funding_8h_pct(prem["funding_last"], interval_h),
            "funding_pctl": get("funding_pctl"),
            "premium_pct": (prem["mark"] / prem["index"] - 1) * 100 if prem["index"] else 0.0,
            "oi_coins": oi_now,
            "oi_usd": oi_now * prem["mark"],
            "oi_change_1h_pct": oi1,
            "oi_change_4h_pct": oi4,
            "oi_change_pct": oi_f,
            "oi_rev_pct": oi_rev,
            "mark": prem["mark"],
            "hl_funding_8h_pct": funding_8h_pct(venue["funding_hr"], 1.0) if "funding_hr" in venue else None,
            "max_leverage": venue.get("max_leverage", 0.0),
            "price_change_1h_pct": price_change_1h_pct,
            "reading": interpret(price_change_1h_pct, oi1),
        }
