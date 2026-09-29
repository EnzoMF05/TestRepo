import numpy as np
import pandas as pd
import pytest
from synthetic import make_candles

from zec_bot.backtest import run_backtest
from zec_bot.config import Config
from zec_bot.engine import summarize
from zec_bot.strategy import Params, prepare, signal_at

P = Params()
COLS = ["ema_f", "ema_s", "rsi", "atr", "adx", "vwap", "vol_ma", "don_hi", "don_lo", "htf", "sig", "risk"]


@pytest.fixture(scope="module")
def candles():
    return make_candles(6000)


@pytest.fixture(scope="module")
def feat(candles):
    return prepare(candles, P)


def test_no_lookahead(candles, feat):
    """Calcular só com dados até à barra k tem de dar exatamente o mesmo que com todos os dados."""
    rng = np.random.default_rng(0)
    sig_bars = list(np.flatnonzero(feat["sig"].to_numpy() != 0)[:30])
    ks = sorted(set(rng.integers(P.warmup_bars, len(candles) - 1, 40).tolist() + sig_bars))
    assert sig_bars, "os dados sintéticos deviam gerar sinais"
    for k in ks:
        cut = prepare(candles.iloc[: k + 1], P).iloc[-1]
        for c in COLS:
            a, b = cut[c], feat.iloc[k][c]
            assert (pd.isna(a) and pd.isna(b)) or np.isclose(a, b, rtol=1e-9, atol=1e-9), (feat.index[k], c, a, b)


def test_htf_trend_only_uses_closed_hours(candles):
    """A tendência de 1h numa barra não pode mudar se alterarmos os candles DESSA hora que ainda não fecharam."""
    k = 3000
    k -= k % 4  # primeira barra de 15m de uma hora
    a = prepare(candles.iloc[: k + 1], P)["htf"].iloc[-1]
    tampered = candles.iloc[: k + 1].copy()
    tampered.iloc[-1, tampered.columns.get_loc("close")] *= 1.5  # mexe na barra corrente
    b = prepare(tampered, P)["htf"].iloc[-1]
    assert a == b


def test_signal_geometry(feat):
    found = {"long": False, "short": False}
    for i in np.flatnonzero(feat["sig"].to_numpy() != 0):
        s = signal_at(feat, int(i), P)
        d = 1 if s.side == "long" else -1
        assert (s.entry - s.stop) * d == pytest.approx(s.risk)
        assert (s.tp1 - s.entry) * d == pytest.approx(P.tp1_r * s.risk)
        assert (s.tp2 - s.entry) * d == pytest.approx(P.tp2_r * s.risk)
        assert P.stop_min_atr * s.atr - 1e-9 <= s.risk <= P.stop_max_atr * s.atr + 1e-9
        assert s.risk / s.entry * 100 >= P.min_risk_pct
        assert s.htf == d  # só opera a favor da tendência de 1h
        found[s.side] = True
    assert all(found.values()), "esperava sinais long e short nos dados sintéticos"


def _random_walk(seed, n=8000):
    r = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    rets = 0.006 * r.standard_normal(n)
    close = 50 * np.exp(np.cumsum(rets))
    open_ = np.r_[close[0], close[:-1]]
    w = np.abs(r.standard_normal((2, n))) * 0.004 * close
    vol = 1000 * np.exp(0.4 * r.standard_normal(n)) * (1 + 40 * np.abs(rets))
    return pd.DataFrame(
        {"open": open_, "high": np.maximum(open_, close) + w[0], "low": np.minimum(open_, close) - w[1],
         "close": close, "volume": vol}, index=idx)


def test_random_walk_has_no_edge():
    """Controlo: sem tendência nem memória, não pode haver lucro (se houver, há lookahead ou bug)."""
    trades = []
    for seed in range(8):
        t, _ = run_backtest(_random_walk(seed), Config(), P)
        trades += t
    s = summarize(trades)
    gross = np.mean([t["r_gross"] for t in trades])
    assert s["n"] > 300
    assert gross < 0.05, f"R bruto médio {gross:.3f} num passeio aleatório é suspeito"
    assert s["total_r"] < 0, "depois de custos, um passeio aleatório tem de perder"
