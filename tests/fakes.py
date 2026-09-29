"""Duplos de teste partilhados (nada aqui toca na rede)."""
from __future__ import annotations

import pandas as pd

from zec_bot.binance_futures import BinanceError

H = 3_600_000
M15 = 900_000


class Clock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t


class FakeBinance:
    """Cliente da Binance falso. Por defeito gera funding de 8h e OI de 15m à volta de `now` (segundos);
    ou, com `span_ms=(início, fim)`, cobre esse intervalo (útil para repetir barra a barra)."""

    def __init__(self, now, n_funding=120, step_h=8, funding_last=0.0001, mark=50.0, index=49.9, oi_now=2000.0,
                 span_ms=None, funding_rate=lambda ts: 0.0001, oi_value=None):
        if span_ms is None:
            end = int(now * 1000)
            self.frows = [[end - (n_funding - k) * step_h * H, funding_rate(end)] for k in range(n_funding)]
            self.orows = [[end - (384 - k) * M15, 1000.0 + k if oi_value is None else oi_value,
                           (1000.0 + k) * 50] for k in range(384)]
        else:
            t0, t1 = span_ms
            t0 = t0 - t0 % (step_h * H)
            self.frows = [[t, funding_rate(t)] for t in range(t0, t1, step_h * H)]
            self.orows = [[t, 1000.0 + i if oi_value is None else oi_value, 5e4]
                          for i, t in enumerate(range(t0, t1, M15))]
        self.prem = {"mark": mark, "index": index, "funding_last": funding_last, "next_funding_ms": 0, "time": 0}
        self.oi_now, self.fail = oi_now, set()
        self.calls = {"premium": 0, "oi_now": 0, "funding": 0, "oi_hist": 0}

    def _maybe_fail(self, what):
        self.calls[what] += 1
        if what in self.fail:
            raise BinanceError(f"binance futuros: {what} em baixo")

    def premium_index(self):
        self._maybe_fail("premium")
        return self.prem

    def open_interest(self):
        self._maybe_fail("oi_now")
        return {"oi_coins": self.oi_now, "time": 0}

    def funding_history(self, start_ms):
        self._maybe_fail("funding")
        return self.frows

    def oi_history(self, start_ms):
        self._maybe_fail("oi_hist")
        return self.orows


def ms(ts: str) -> int:
    return int(pd.Timestamp(ts, tz="UTC").timestamp() * 1000)
