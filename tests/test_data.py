"""Testes dos parsers e da paginação com respostas SIMULADAS no formato documentado de cada API.

Nota: não foi possível testar contra as APIs reais a partir do ambiente de desenvolvimento
(acesso às exchanges bloqueado). Corre `python -m zec_bot --check` no teu Mac para confirmar.
"""
import pandas as pd
import pytest

from zec_bot import data
from zec_bot.data import (DataError, _parse_binance, _parse_bybit, _parse_kraken, _parse_okx, drop_open_bar,
                          fetch_exchange, to_frame)

T0 = 1_700_000_000_000
STEP = 15 * 60 * 1000


def test_parse_binance():
    raw = [[T0, "30.1", "31.0", "29.5", "30.5", "1200.5", T0 + STEP - 1, "36000", 500, "600", "18000", "0"]]
    assert _parse_binance(raw) == [[T0, 30.1, 31.0, 29.5, 30.5, 1200.5]]


def test_parse_bybit_sorts_ascending_and_checks_error():
    ok = {"retCode": 0, "result": {"list": [[str(T0 + STEP), "2", "3", "1", "2.5", "10", "25"],
                                            [str(T0), "1", "2", "0.5", "1.5", "20", "30"]]}}
    rows = _parse_bybit(ok)
    assert [r[0] for r in rows] == [T0, T0 + STEP] and rows[0][1:] == [1.0, 2.0, 0.5, 1.5, 20.0]
    with pytest.raises(DataError):
        _parse_bybit({"retCode": 10001, "retMsg": "params error"})


def test_parse_okx_sorts_ascending_and_checks_error():
    ok = {"code": "0", "data": [[str(T0 + STEP), "2", "3", "1", "2.5", "10", "25", "25", "1"],
                                [str(T0), "1", "2", "0.5", "1.5", "20", "30", "30", "1"]]}
    rows = _parse_okx(ok)
    assert [r[0] for r in rows] == [T0, T0 + STEP] and rows[1][5] == 10.0
    with pytest.raises(DataError):
        _parse_okx({"code": "51001", "msg": "Instrument ID doesn't exist"})


def test_parse_kraken_converts_seconds_and_uses_volume_column():
    ok = {"error": [], "result": {"XZECZUSD": [[1700000000, "30", "31", "29", "30.5", "30.2", "12.5", 7]], "last": 1}}
    assert _parse_kraken(ok) == [[1700000000000, 30.0, 31.0, 29.0, 30.5, 12.5]]
    with pytest.raises(DataError):
        _parse_kraken({"error": ["EQuery:Unknown asset pair"], "result": {}})


def test_drop_open_bar():
    df = to_frame([[T0 + k * STEP, 1, 1, 1, 1, 1] for k in range(5)])
    now = pd.Timestamp(T0 + 3 * STEP + 60_000, unit="ms", tz="UTC")  # 1 min depois de abrir a barra 3
    out = drop_open_bar(df, 15, now=now)
    assert len(out) == 3 and out.index[-1] == pd.Timestamp(T0 + 2 * STEP, unit="ms", tz="UTC")


def test_fetch_exchange_paginates_backwards(monkeypatch):
    series = [[T0 + k * STEP, 1.0, 2.0, 0.5, 1.5, 10.0] for k in range(1000)]
    calls = []

    def fake(base, quote, interval, end_ms, limit=100):
        calls.append(end_ms)
        page = [r for r in series if end_ms is None or r[0] <= end_ms][-100:]
        return page

    monkeypatch.setitem(data._FETCHERS, "binance", fake)
    monkeypatch.setattr(data.time, "sleep", lambda s: None)
    df = fetch_exchange("binance", "ZEC", "USDT", 15, 350)
    assert len(df) == 350 and df.index.is_monotonic_increasing and df.index.is_unique
    assert df.index[-1] == pd.Timestamp(series[-1][0], unit="ms", tz="UTC")
    assert len(calls) == 4  # 4 páginas de 100 para chegar a 350


def test_get_candles_falls_back_to_next_exchange(monkeypatch):
    rows = [[T0 + k * STEP, 1.0, 2.0, 0.5, 1.5, 10.0] for k in range(10)]

    def boom(*a, **k):
        raise DataError("bloqueado")

    monkeypatch.setitem(data._FETCHERS, "binance", boom)
    monkeypatch.setitem(data._FETCHERS, "bybit", lambda *a, **k: rows)
    df, ex = data.get_candles("binance", "ZEC", "USDT", 15, 5)
    assert ex == "bybit" and len(df) == 5


def test_get_candles_raises_when_everything_fails(monkeypatch):
    def boom(*a, **k):
        raise DataError("x")

    for name in list(data._FETCHERS):
        monkeypatch.setitem(data._FETCHERS, name, boom)
    with pytest.raises(DataError):
        data.get_candles("binance", "ZEC", "USDT", 15, 5)
