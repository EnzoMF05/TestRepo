"""Motor de sinais: gestão do trade virtual e regras de risco diário.

É o MESMO código que o modo live e o backtest usam, para os resultados serem comparáveis.

Gestão do trade (por sinal):
  * stop inicial = -1R
  * TP1 (1.5R): fecha 50% e o stop passa a breakeven
  * TP2 (3R): fecha os restantes 50%
  * se uma barra tocar em stop e alvo, assume-se o pior caso (stop primeiro)
  * saída a mercado ao fim de `max_hold_bars`
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .config import Config
from .strategy import Params, Signal, signal_at


@dataclass
class Trade:
    id: int
    side: str
    kind: str
    entry: float
    stop: float  # stop atual (passa a breakeven depois do TP1)
    stop0: float  # stop inicial
    tp1: float
    tp2: float
    risk: float
    open_time: str
    cost_r: float  # comissões+derrapagem expressas em R
    state: str = "open"  # open | tp1 | closed
    bars_held: int = 0
    realized_r: float = 0.0  # R bruto já garantido (metade do TP1)
    exit_reason: str = ""
    close_time: str = ""
    r_gross: float = 0.0
    r_net: float = 0.0

    @property
    def d(self) -> int:
        return 1 if self.side == "long" else -1

    def _stop_hit(self, high: float, low: float) -> bool:
        return low <= self.stop if self.d > 0 else high >= self.stop

    def _reached(self, price: float, high: float, low: float) -> bool:
        return high >= price if self.d > 0 else low <= price

    def _close(self, r_gross: float, reason: str, time: str) -> None:
        self.state = "closed"
        self.exit_reason = reason
        self.close_time = time
        self.r_gross = r_gross
        self.r_net = r_gross - self.cost_r

    def step(self, high: float, low: float, close: float, time: str, p: Params, max_hold: int) -> list[str]:
        """Processa uma barra FECHADA. Devolve os eventos ocorridos: 'tp1' e/ou 'closed'."""
        if self.state == "closed":
            return []
        events: list[str] = []
        self.bars_held += 1

        if self.state == "open":
            if self._stop_hit(high, low):
                self._close(-1.0, "stop", time)
                return ["closed"]
            if self._reached(self.tp1, high, low):
                self.state = "tp1"
                self.realized_r = 0.5 * p.tp1_r
                self.stop = self.entry
                events.append("tp1")

        if self.state == "tp1":
            # Nesta barra o TP1 pode ter sido atingido antes do retorno ao breakeven: pior caso.
            if self._stop_hit(high, low):
                self._close(self.realized_r, "breakeven", time)
                events.append("closed")
                return events
            if self._reached(self.tp2, high, low):
                self._close(self.realized_r + 0.5 * p.tp2_r, "tp2", time)
                events.append("closed")
                return events

        if self.bars_held >= max_hold:
            remaining = 0.5 if self.state == "tp1" else 1.0
            unrealized = self.d * (close - self.entry) / self.risk
            self._close(self.realized_r + remaining * unrealized, "tempo", time)
            events.append("closed")
        return events


@dataclass
class Event:
    kind: str  # signal | tp1 | closed | daily_stop | day_summary | blocked
    trade: Optional[Trade] = None
    data: dict = field(default_factory=dict)


def summarize(trades: list[dict]) -> dict:
    """Estatísticas de uma lista de trades fechados (dicts com r_net)."""
    n = len(trades)
    if n == 0:
        return {"n": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_r": 0.0, "avg_r": 0.0, "profit_factor": 0.0}
    rs = [t["r_net"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_loss = -sum(losses)
    return {
        "n": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / n * 100,
        "total_r": sum(rs),
        "avg_r": sum(rs) / n,
        "profit_factor": (sum(wins) / gross_loss) if gross_loss > 0 else float("inf"),
    }


class Engine:
    def __init__(self, cfg: Config, params: Params | None = None):
        self.cfg = cfg
        self.p = params or Params()
        self.trade: Optional[Trade] = None
        self.closed: list[dict] = []
        self.day = ""
        self.day_signals = 0
        self.day_r = 0.0
        self.day_stopped = False
        self.last_signal_time = ""
        self.last_bar = ""
        self.paused = False
        self.seq = 0

    # ------------------------------------------------------------------ regras
    def _can_signal(self, ts: pd.Timestamp) -> bool:
        c = self.cfg
        if self.paused or self.trade is not None or self.day_stopped:
            return False
        if self.day_signals >= c.max_signals_per_day:
            return False
        if self.last_signal_time:
            gap = ts - pd.Timestamp(self.last_signal_time)
            if gap < pd.Timedelta(minutes=c.interval_min * c.cooldown_bars):
                return False
        start, end = c.hours_window()
        return start <= ts.hour < end

    def _side_allowed(self, side: str) -> bool:
        return self.cfg.side == "both" or self.cfg.side == side

    def _deriv_block(self, sig: Signal, deriv: Optional[dict]) -> Optional[str]:
        """Motivo para bloquear o sinal por funding/OI (só com os filtros ligados). Sem dados: não bloqueia."""
        c = self.cfg
        if not deriv:
            return None
        f = deriv.get("funding_8h_pct")
        if c.funding_filter and f is not None and not np.isnan(f):
            if sig.side == "long" and f > c.funding_limit_8h_pct:
                return f"funding {f:+.3f}%/8h > {c.funding_limit_8h_pct:g}% (longs sobrelotados)"
            if sig.side == "short" and f < -c.funding_limit_8h_pct:
                return f"funding {f:+.3f}%/8h < -{c.funding_limit_8h_pct:g}% (shorts sobrelotados)"
        oi = deriv.get("oi_change_pct")
        if c.oi_filter and sig.kind == "breakout" and oi is not None and not np.isnan(oi):
            if oi < c.oi_min_change_pct:
                return f"OI {oi:+.2f}% < {c.oi_min_change_pct:g}% (rutura sem posições novas)"
        return None

    # ------------------------------------------------------------------ barra
    def on_bar(self, feat: pd.DataFrame, i: int, evaluate: bool = True, deriv: Optional[dict] = None) -> list[Event]:
        """Processa a barra fechada `i`. `evaluate=False` só atualiza o trade aberto (barras em atraso).

        `deriv`: contexto de funding/OI dessa barra (chaves `funding_8h_pct`, `oi_change_pct`), opcional.
        """
        ts = feat.index[i]
        iso = ts.isoformat()
        events: list[Event] = []

        day = ts.strftime("%Y-%m-%d")
        if day != self.day:
            if self.day:
                events.append(Event("day_summary", data=self._day_stats(self.day)))
            self.day, self.day_signals, self.day_r, self.day_stopped = day, 0, 0.0, False

        row = feat.iloc[i]
        if self.trade is not None:
            for kind in self.trade.step(
                float(row["high"]), float(row["low"]), float(row["close"]), iso, self.p, self.cfg.max_hold_bars
            ):
                events.append(Event(kind, self.trade))
            if self.trade.state == "closed":
                self.day_r += self.trade.r_net
                self.closed.append(asdict(self.trade))
                self.closed = self.closed[-2000:]
                self.trade = None

        if not self.day_stopped and self.day_r <= -self.cfg.daily_stop_r:
            self.day_stopped = True
            events.append(Event("daily_stop", data={"day_r": self.day_r}))

        self.last_bar = iso

        if evaluate and self._can_signal(ts):
            sig = signal_at(feat, i, self.p)
            reason = self._deriv_block(sig, deriv) if sig is not None and self._side_allowed(sig.side) else None
            if reason:  # bloqueado: não conta para o cooldown nem para o limite diário
                events.append(Event("blocked", data={"reason": reason, "side": sig.side, "kind": sig.kind, "time": iso}))
            elif sig is not None and self._side_allowed(sig.side):
                self.trade = self._open(sig, iso)
                self.day_signals += 1
                self.last_signal_time = iso
                events.append(Event("signal", self.trade, data={"htf": sig.htf, "rsi": sig.rsi, "adx": sig.adx}))
        return events

    def _open(self, sig: Signal, iso: str) -> Trade:
        self.seq += 1
        cost_r = (self.cfg.round_trip_cost_pct / 100.0) * sig.entry / sig.risk
        return Trade(
            id=self.seq, side=sig.side, kind=sig.kind, entry=sig.entry, stop=sig.stop, stop0=sig.stop,
            tp1=sig.tp1, tp2=sig.tp2, risk=sig.risk, open_time=iso, cost_r=cost_r,
        )

    # ------------------------------------------------------------------ estatísticas
    def _day_stats(self, day: str) -> dict:
        trades = [t for t in self.closed if t["close_time"][:10] == day]
        s = summarize(trades)
        s.update({"date": day, "signals": self.day_signals, "day_r": self.day_r})
        return s

    def stats(self, last_n_days: int | None = None) -> dict:
        trades = self.closed
        if last_n_days and self.last_bar:
            cutoff = (pd.Timestamp(self.last_bar) - pd.Timedelta(days=last_n_days)).isoformat()
            trades = [t for t in trades if t["close_time"] >= cutoff]
        return summarize(trades)

    # ------------------------------------------------------------------ persistência
    def to_dict(self) -> dict:
        return {
            "trade": asdict(self.trade) if self.trade else None,
            "closed": self.closed,
            "day": self.day,
            "day_signals": self.day_signals,
            "day_r": self.day_r,
            "day_stopped": self.day_stopped,
            "last_signal_time": self.last_signal_time,
            "last_bar": self.last_bar,
            "paused": self.paused,
            "seq": self.seq,
        }

    def load(self, d: dict) -> None:
        self.trade = Trade(**d["trade"]) if d.get("trade") else None
        self.closed = d.get("closed", [])
        self.day = d.get("day", "")
        self.day_signals = d.get("day_signals", 0)
        self.day_r = d.get("day_r", 0.0)
        self.day_stopped = d.get("day_stopped", False)
        self.last_signal_time = d.get("last_signal_time", "")
        self.last_bar = d.get("last_bar", "")
        self.paused = d.get("paused", False)
        self.seq = d.get("seq", 0)
