"""Cliente da Hyperliquid com respostas SIMULADAS no formato documentado.

Nota: não foi possível testar contra a API real a partir do ambiente de desenvolvimento.
Corre `python -m zec_bot --check` no teu Mac para confirmar.
"""
import pandas as pd
import pytest

from zec_bot import data, hl
from zec_bot.data import DataError

T0 = 1_700_000_000_000
STEP = 15 * 60 * 1000


def candle(t, o=30.1, h=31.0, l=29.5, c=30.5, v=1200.5):
    return {"t": t, "T": t + STEP - 1, "s": "ZEC", "i": "15m", "o": str(o), "c": str(c), "h": str(h), "l": str(l),
            "v": str(v), "n": 100}


def test_parse_candles_maps_fields_in_ohlcv_order_and_sorts():
    rows = hl.parse_candles([candle(T0 + STEP, o=2, h=3, l=1, c=2.5, v=10), candle(T0)])
    assert rows[0] == [T0, 30.1, 31.0, 29.5, 30.5, 1200.5]  # o, h, l, c, v (a API manda c antes de h/l)
    assert rows[1] == [T0 + STEP, 2.0, 3.0, 1.0, 2.5, 10.0]


@pytest.mark.parametrize("bad", [{"error": "x"}, "null", None, [{"t": 1}]])
def test_parse_candles_rejects_unexpected_shapes(bad):
    with pytest.raises(hl.HLError):
        hl.parse_candles(bad)


def test_fetch_candles_builds_the_documented_request(monkeypatch):
    seen = {}
    monkeypatch.setattr(hl, "post_info", lambda payload: seen.update(payload) or [candle(T0)])
    hl.fetch_candles("ZEC", 15, end_ms=T0 + 10 * STEP, limit=100)
    assert seen["type"] == "candleSnapshot"
    assert seen["req"] == {"coin": "ZEC", "interval": "15m", "startTime": T0 + 10 * STEP - 100 * STEP,
                           "endTime": T0 + 10 * STEP}


def test_fetch_candles_caps_window_at_5000_bars(monkeypatch):
    seen = {}
    monkeypatch.setattr(hl, "post_info", lambda payload: seen.update(payload) or [])
    hl.fetch_candles("ZEC", 15, end_ms=T0, limit=99999)
    assert seen["req"]["endTime"] - seen["req"]["startTime"] == 5000 * STEP
    with pytest.raises(hl.HLError):
        hl.fetch_candles("ZEC", 7)


def _ctx_payload():
    universe = [{"name": "BTC", "maxLeverage": 40}, {"name": "ZEC", "maxLeverage": 5},
                {"name": "OLD", "maxLeverage": 3, "isDelisted": True}]
    ctxs = [
        {"funding": "0.00001", "openInterest": "1", "markPx": "60000", "oraclePx": "60001", "premium": "0"},
        {"funding": "0.0000125", "openInterest": "812345.6", "markPx": "50.5", "oraclePx": "50.4",
         "premium": "0.0003", "dayNtlVlm": "23400000.5"},
        {"funding": "0", "openInterest": "0", "markPx": "1", "oraclePx": "1"},
    ]
    return [{"universe": universe}, ctxs]


def test_parse_asset_ctx_picks_the_right_row_by_position():
    c = hl.parse_asset_ctx(_ctx_payload(), "ZEC")
    assert c["funding_hr"] == 0.0000125 and c["oi_coins"] == 812345.6 and c["mark"] == 50.5
    assert c["oracle"] == 50.4 and c["premium"] == 0.0003 and c["day_vol_usd"] == 23400000.5
    assert c["max_leverage"] == 5.0


def test_parse_asset_ctx_errors():
    with pytest.raises(hl.HLError, match="não encontrada"):
        hl.parse_asset_ctx(_ctx_payload(), "DOGE")
    with pytest.raises(hl.HLError, match="não encontrada"):
        hl.parse_asset_ctx(_ctx_payload(), "OLD")  # delisted
    with pytest.raises(hl.HLError, match="formato"):
        hl.parse_asset_ctx({"oops": 1}, "ZEC")
    bad = _ctx_payload()
    del bad[1][1]["openInterest"]
    with pytest.raises(hl.HLError, match="formato"):
        hl.parse_asset_ctx(bad, "ZEC")


def test_hyperliquid_is_a_pageable_source_that_stops_at_its_5000_limit(monkeypatch):
    """Pede mais do que a Hyperliquid guarda: tem de devolver o que há, sem ciclar."""
    available = [candle(T0 + k * STEP) for k in range(300)]
    calls = []

    def fake_post(payload):
        calls.append(payload["req"]["endTime"])
        end = payload["req"]["endTime"]
        return [c for c in available if c["t"] <= end]

    monkeypatch.setattr(hl, "post_info", fake_post)
    monkeypatch.setattr(data.time, "sleep", lambda s: None)
    monkeypatch.setattr(hl.time, "time", lambda: (T0 + 400 * STEP) / 1000)
    df = data.fetch_exchange("hyperliquid", "ZEC", "USDT", 15, 8000)
    assert len(df) == 300 and df.index.is_unique and df.index.is_monotonic_increasing
    assert len(calls) <= 3


def test_hyperliquid_errors_become_data_errors(monkeypatch):
    def boom(payload):
        raise hl.HLError("hyperliquid: ProxyError")

    monkeypatch.setattr(hl, "post_info", boom)
    with pytest.raises(DataError, match="ProxyError"):
        data.fetch_exchange("hyperliquid", "ZEC", "USDT", 15, 100)
    monkeypatch.setattr(hl, "post_info", lambda p: [])
    with pytest.raises(DataError, match="sem dados"):
        data.fetch_exchange("hyperliquid", "ZEC", "USDT", 15, 100)


def test_open_bar_from_hyperliquid_is_dropped(monkeypatch):
    """A API devolve a barra em formação; o robô só pode ver barras fechadas."""
    rows = [candle(T0 + k * STEP) for k in range(10)]
    monkeypatch.setattr(hl, "post_info", lambda p: rows)
    monkeypatch.setattr(data.time, "sleep", lambda s: None)
    real_now = pd.Timestamp.now
    monkeypatch.setattr(pd.Timestamp, "now", staticmethod(lambda tz=None: pd.Timestamp(T0 + 9 * STEP + 60_000, unit="ms", tz="UTC")))
    try:
        df = data.fetch_exchange("hyperliquid", "ZEC", "USDT", 15, 5)
    finally:
        monkeypatch.setattr(pd.Timestamp, "now", real_now)
    assert df.index[-1] == pd.Timestamp(T0 + 8 * STEP, unit="ms", tz="UTC")  # a 10ª barra ainda está aberta
