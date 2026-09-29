import sys

import numpy as np
import pandas as pd
import pytest
from fakes import FakeBinance, ms
from synthetic import crowd_frame, informed_funding, make_candles

from zec_bot import backtest
from zec_bot.backtest import build_deriv, compare, first_valid_bar, run_backtest
from zec_bot.binance_futures import BinanceError
from zec_bot.config import Config
from zec_bot.strategy import Params

P = Params()  # tendência
H8 = 8 * 3_600_000


@pytest.fixture(scope="module")
def df():
    return make_candles(3200, seed=3)  # série com bastantes sinais de tendência


def cfg(**kw):
    c = Config(max_signals_per_day=10)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


# ------------------------------------------------------------------ filtros da estratégia de tendência
def test_derivs_data_is_ignored_when_filters_are_off(df):
    plain, _ = run_backtest(df, cfg(), P)
    with_data, _ = run_backtest(df, cfg(), P, deriv=crowd_frame(df.index, funding=9.9, oi_change=-99.0))
    assert plain and [t["open_time"] for t in plain] == [t["open_time"] for t in with_data]


def test_funding_filter_removes_only_the_crowded_side(df):
    blocked = []
    hot_longs = crowd_frame(df.index, funding=0.5)  # 0.5%/8h: longs sobrelotados; shorts livres
    trades, _ = run_backtest(df, cfg(funding_filter=True), P, deriv=hot_longs, blocked=blocked)
    assert trades and all(t["side"] == "short" for t in trades)
    assert blocked and all(b["side"] == "long" for b in blocked)
    baseline, _ = run_backtest(df, cfg(), P)
    assert len(trades) < len(baseline)


def test_oi_filter_removes_only_breakouts(df):
    blocked = []
    trades, _ = run_backtest(df, cfg(oi_filter=True), P, deriv=crowd_frame(df.index, oi_change=-5.0), blocked=blocked)
    assert all(t["kind"] == "pullback" for t in trades)
    assert blocked and all(b["kind"] == "breakout" for b in blocked)


def test_compare_report_warns_when_filters_do_not_help(df):
    base, feat = run_backtest(df, cfg(), P)
    text = compare(base, base[: len(base) // 2], [{}] * 3, feat)  # "filtrado" pior de propósito
    assert "sem filtros" in text and "com filtros" in text and "Setups bloqueados pelos filtros: 3" in text
    assert "não há evidência" in text


# ------------------------------------------------------------------ primeira barra com dados suficientes
def _frame(n=10, pctl_from=2, oi_rev_from=5, fund_from=1, oi_from=6):
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    nan = lambda k: [np.nan] * k + [1.0] * (n - k)
    return pd.DataFrame({"funding_pctl": nan(pctl_from), "oi_rev_pct": nan(oi_rev_from),
                         "funding_8h_pct": nan(fund_from), "oi_change_pct": nan(oi_from)}, index=idx)


def test_first_valid_bar_follows_what_the_strategy_needs():
    d = _frame()
    assert first_valid_bar(d, cfg(strategy="reversal"), floor=0) == 2  # precisa do percentil do funding
    assert first_valid_bar(d, cfg(strategy="reversal", rev_use_oi=True), floor=0) == 5  # + OI de 24h
    assert first_valid_bar(d, cfg(strategy="trend", funding_filter=True), floor=0) == 1
    assert first_valid_bar(d, cfg(strategy="trend", oi_filter=True), floor=0) == 6
    assert first_valid_bar(d, cfg(strategy="both", funding_filter=True, oi_filter=True), floor=0) == 6
    assert first_valid_bar(d, cfg(strategy="reversal"), floor=4) == 4  # nunca antes do aquecimento dos indicadores


def test_first_valid_bar_fails_loudly_when_data_is_missing():
    d = _frame().assign(oi_rev_pct=np.nan)
    with pytest.raises(SystemExit, match="Sem barras"):
        first_valid_bar(d, cfg(strategy="reversal", rev_use_oi=True), floor=0)


# ------------------------------------------------------------------ dados da Binance
def patch_binance(monkeypatch, fake):
    calls = {"funding": [], "oi": []}

    def funding(symbol, start_ms, end_ms=None, **k):
        calls["funding"].append((symbol, start_ms))
        return fake.frows

    def oi(symbol, start_ms, end_ms=None, period="15m", **k):
        calls["oi"].append((symbol, start_ms, period))
        return fake.orows

    monkeypatch.setattr(backtest.bf, "fetch_funding_history", funding)
    monkeypatch.setattr(backtest.bf, "fetch_oi_hist", oi)
    return calls


def test_build_deriv_asks_binance_for_enough_history_and_builds_the_frame(df, monkeypatch):
    fake = FakeBinance(0, span_ms=(ms(str(df.index[0] - pd.Timedelta(days=40))), ms(str(df.index[-1] + pd.Timedelta(days=1)))))
    calls = patch_binance(monkeypatch, fake)
    c = cfg()
    fr = build_deriv(df.index, c, need_oi=True)
    (symbol, start_ms), = calls["funding"]
    assert symbol == "ZECUSDT"
    # tem de recuar a janela do percentil (30d) + margem antes da 1ª barra, senão as primeiras semanas ficam sem percentil
    assert start_ms <= ms(str(df.index[0])) - 33 * 86_400_000
    assert calls["oi"][0][2] == "15m"
    assert list(fr.columns) == ["funding_8h_pct", "funding_pctl", "oi_change_pct", "oi_rev_pct"]
    assert fr["funding_pctl"].notna().all() and fr["oi_rev_pct"].iloc[-1] > 0


def test_build_deriv_skips_oi_when_not_needed(df, monkeypatch):
    fake = FakeBinance(0, span_ms=(ms(str(df.index[0] - pd.Timedelta(days=40))), ms(str(df.index[-1] + pd.Timedelta(days=1)))))
    calls = patch_binance(monkeypatch, fake)
    fr = build_deriv(df.index, cfg(), need_oi=False)
    assert calls["oi"] == [] and fr["oi_rev_pct"].isna().all() and fr["funding_pctl"].notna().all()


def test_build_deriv_fails_with_a_readable_message(df, monkeypatch):
    def boom(*a, **k):
        raise BinanceError("binance futuros: HTTP 451 (região bloqueada)")

    monkeypatch.setattr(backtest.bf, "fetch_funding_history", boom)
    with pytest.raises(SystemExit, match="451"):
        build_deriv(df.index, cfg(), need_oi=False)
    monkeypatch.setattr(backtest.bf, "fetch_funding_history", lambda *a, **k: [])
    with pytest.raises(SystemExit, match="não devolveu funding"):
        build_deriv(df.index, cfg(), need_oi=False)


# ------------------------------------------------------------------ comando completo
@pytest.fixture
def cli(monkeypatch, tmp_path):
    """Corre `python -m zec_bot.backtest <args>` com candles e Binance simulados."""
    candles = make_candles(2400, seed=4)
    rates = informed_funding(candles)
    fake = FakeBinance(0, span_ms=(ms(str(candles.index[0] - pd.Timedelta(days=40))),
                                   ms(str(candles.index[-1] + pd.Timedelta(days=1)))),
                       funding_rate=lambda t: rates.get(t - t % H8, 0.0001))
    calls = patch_binance(monkeypatch, fake)
    monkeypatch.setattr(backtest, "fetch_exchange", lambda *a, **k: candles)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STRATEGY", raising=False)

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["backtest", "--trades-csv", str(tmp_path / "t.csv"), *args])
        return backtest.main()

    run.calls, run.path = calls, tmp_path / "t.csv"
    return run


def test_cli_defaults_to_the_reversal_strategy(cli, capsys):
    assert cli() == 0
    out = capsys.readouterr().out
    assert "Estratégia: reversal" in out and "TOTAL" in out and "setup: reversal" in out
    assert "setup: pullback" not in out and "setup: breakout" not in out  # modo reversão não faz trades de tendência
    assert "lado: long" in out and "lado: short" in out
    assert cli.calls["funding"] and cli.calls["oi"] == []  # sem REV_USE_OI não pede o OI
    trades = pd.read_csv(cli.path)
    assert len(trades) > 5 and set(trades["kind"]) == {"reversal"} and {"tp1_r", "tp2_r"} <= set(trades.columns)
    assert set(trades["tp1_r"]) == {1.0} and set(trades["tp2_r"]) == {2.0}


def test_cli_strategy_both_reports_each_type_separately(cli, capsys):
    assert cli("--strategy", "both") == 0
    out = capsys.readouterr().out
    assert "Estratégia: both" in out and "setup: reversal" in out
    assert "setup: pullback" in out or "setup: breakout" in out


def test_cli_trend_with_funding_filter_compares_with_and_without(cli, capsys):
    assert cli("--strategy", "trend", "--funding-filter") == 0
    out = capsys.readouterr().out
    assert "Efeito dos filtros" in out and "Setups bloqueados pelos filtros:" in out and "TOTAL" in out


def test_cli_use_oi_asks_binance_for_oi_and_starts_when_it_exists(cli, capsys):
    assert cli("--use-oi") == 0
    assert cli.calls["oi"], "com --use-oi tem de pedir o histórico de OI"
    assert "Estratégia: reversal" in capsys.readouterr().out


def test_cli_side_and_cost_flags(cli, capsys):
    assert cli("--side", "short", "--fee", "0.1", "--slippage", "0.05") == 0
    out = capsys.readouterr().out
    assert "0.1% comissão + 0.05% derrapagem" in out and "lado: long" not in out
    assert set(pd.read_csv(cli.path)["side"]) == {"short"}


def test_cli_warns_when_hyperliquid_returns_fewer_bars(cli, capsys):
    assert cli("--days", "365") == 0
    out = capsys.readouterr().out
    assert "só há 2400 barras" in out and "5000 barras" in out and "--exchange binance" in out


def test_cli_stops_cleanly_without_enough_data(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(backtest, "fetch_exchange", lambda *a, **k: make_candles(300, seed=1))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["backtest"])
    assert backtest.main() == 1 and "Dados insuficientes" in capsys.readouterr().out
