import sys

import numpy as np
import pandas as pd
import pytest
from synthetic import make_candles

from zec_bot import backtest, hl
from zec_bot.backtest import build_deriv, compare, first_valid_bar, run_backtest
from zec_bot.config import Config
from zec_bot.strategy import Params

P = Params()


@pytest.fixture(scope="module")
def df():
    return make_candles(3200, seed=3)  # série com bastantes sinais


def cfg(**kw):
    c = Config(max_signals_per_day=10)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def constant_deriv(df, funding=0.0, oi=0.0):
    return pd.DataFrame({"funding_8h_pct": funding, "oi_change_pct": oi}, index=df.index)


def test_derivs_data_is_ignored_when_filters_are_off(df):
    plain, _ = run_backtest(df, cfg(), P)
    with_data, _ = run_backtest(df, cfg(), P, deriv=constant_deriv(df, funding=9.9, oi=-99.0))
    assert plain and [t["open_time"] for t in plain] == [t["open_time"] for t in with_data]


def test_funding_filter_removes_only_the_crowded_side(df):
    blocked = []
    hot_longs = constant_deriv(df, funding=0.5)  # 0.5%/8h: longs sobrelotados; shorts livres
    trades, _ = run_backtest(df, cfg(funding_filter=True), P, deriv=hot_longs, blocked=blocked)
    assert trades and all(t["side"] == "short" for t in trades)
    assert blocked and all(b["side"] == "long" for b in blocked)
    baseline, _ = run_backtest(df, cfg(), P)
    assert len(trades) < len(baseline)


def test_oi_filter_removes_only_breakouts(df):
    blocked = []
    trades, _ = run_backtest(df, cfg(oi_filter=True), P, deriv=constant_deriv(df, oi=-5.0), blocked=blocked)
    assert all(t["kind"] == "pullback" for t in trades)
    assert blocked and all(b["kind"] == "breakout" for b in blocked)


def test_first_valid_bar_requires_all_enabled_filters():
    idx = pd.date_range("2026-01-01", periods=10, freq="15min", tz="UTC")
    d = pd.DataFrame({"funding_8h_pct": [np.nan] * 2 + [0.01] * 8, "oi_change_pct": [np.nan] * 5 + [0.5] * 5}, index=idx)
    assert first_valid_bar(d, cfg(funding_filter=True), floor=0) == 2
    assert first_valid_bar(d, cfg(oi_filter=True), floor=0) == 5
    assert first_valid_bar(d, cfg(funding_filter=True, oi_filter=True), floor=0) == 5
    assert first_valid_bar(d, cfg(funding_filter=True), floor=4) == 4  # nunca antes do aquecimento
    no_oi = d.assign(oi_change_pct=np.nan)  # filtro de OI ligado mas o Coinalyze não devolveu nada utilizável
    with pytest.raises(SystemExit, match="Sem barras"):
        first_valid_bar(no_oi, cfg(oi_filter=True), floor=0)


def test_compare_report_warns_when_filters_do_not_help(df):
    base, feat = run_backtest(df, cfg(), P)
    text = compare(base, base[: len(base) // 2], [{}] * 3, feat)  # "filtrado" pior de propósito
    assert "sem filtros" in text and "com filtros" in text and "Setups bloqueados pelos filtros: 3" in text
    assert "não há evidência" in text


def test_build_deriv_aligns_funding_and_requires_a_key_for_oi(df, monkeypatch):
    hour = 3_600_000
    t0 = int(df.index[0].timestamp() * 1000) - 3 * hour
    rows = [[t0 + k * hour, 0.00002, 0.0] for k in range(int(len(df) / 4) + 10)]
    monkeypatch.setattr(hl, "fetch_funding_history", lambda coin, start_ms, end_ms=None: rows)
    d = build_deriv(df.index, cfg(), need_oi=False)
    assert d["funding_8h_pct"].notna().all() and d["funding_8h_pct"].iloc[0] == pytest.approx(0.016)
    assert d["oi_change_pct"].isna().all()
    with pytest.raises(SystemExit, match="COINALYZE_API_KEY"):
        build_deriv(df.index, cfg(), need_oi=True)


def test_cli_end_to_end_with_funding_filter(df, monkeypatch, tmp_path, capsys):
    hour = 3_600_000
    t0 = int(df.index[0].timestamp() * 1000) - hour
    rows = [[t0 + k * hour, 0.00012, 0.0] for k in range(int(len(df) / 4) + 5)]  # sempre elevado: 0.096%/8h
    monkeypatch.setattr(hl, "fetch_funding_history", lambda coin, start_ms, end_ms=None: rows)
    monkeypatch.setattr(backtest, "fetch_exchange", lambda *a, **k: df)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["backtest", "--funding-filter", "--trades-csv", str(tmp_path / "t.csv")])
    monkeypatch.setenv("TELEGRAM_TOKEN", "")
    assert backtest.main() == 0
    out = capsys.readouterr().out
    assert "Efeito dos filtros" in out and "Setups bloqueados pelos filtros:" in out and "TOTAL" in out
    assert (tmp_path / "t.csv").exists()


def test_cli_warns_when_hyperliquid_returns_fewer_bars(df, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(backtest, "fetch_exchange", lambda *a, **k: df)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["backtest", "--days", "365", "--trades-csv", str(tmp_path / "t.csv")])
    assert backtest.main() == 0
    out = capsys.readouterr().out
    assert "só há 3200 barras" in out and "5000 barras" in out and "--exchange binance" in out
