import numpy as np
import pandas as pd
import pytest

from zec_bot.config import Config
from zec_bot.engine import Engine, Trade
from zec_bot.strategy import Params

P = Params()  # tp1 = 1.5R, tp2 = 3R


def mk(side="long", entry=100.0, risk=2.0, cost_r=0.0) -> Trade:
    d = 1 if side == "long" else -1
    return Trade(id=1, side=side, kind="pullback", entry=entry, stop=entry - d * risk, stop0=entry - d * risk,
                 tp1=entry + d * 1.5 * risk, tp2=entry + d * 3 * risk, risk=risk, open_time="t0", cost_r=cost_r)


def step(t, high, low, close=None, max_hold=100):
    return t.step(high, low, low if close is None else close, "t", P, max_hold)


# ------------------------------------------------------------------ Trade (long)
def test_long_stop():
    t = mk(cost_r=0.1)
    assert step(t, 101, 97.9) == ["closed"]
    assert (t.exit_reason, t.r_gross, t.r_net) == ("stop", -1.0, pytest.approx(-1.1))


def test_long_tp1_then_breakeven():
    t = mk()
    assert step(t, 103.5, 100.5) == ["tp1"]
    assert (t.state, t.stop) == ("tp1", 100.0)
    assert step(t, 101, 99.9) == ["closed"]
    assert (t.exit_reason, t.r_gross) == ("breakeven", pytest.approx(0.75))


def test_long_tp1_then_tp2():
    t = mk()
    step(t, 103.5, 100.5)
    assert step(t, 106.1, 102) == ["closed"]
    assert (t.exit_reason, t.r_gross) == ("tp2", pytest.approx(0.75 + 1.5))


def test_same_bar_stop_and_tp1_assumes_stop_first():
    t = mk()
    assert step(t, 104, 97) == ["closed"]
    assert (t.exit_reason, t.r_gross) == ("stop", -1.0)


def test_same_bar_tp1_and_tp2():
    t = mk()
    assert step(t, 107, 101) == ["tp1", "closed"]
    assert t.r_gross == pytest.approx(2.25)


def test_same_bar_tp1_then_back_to_breakeven_is_conservative():
    t = mk()
    assert step(t, 107, 99.5) == ["tp1", "closed"]
    assert (t.exit_reason, t.r_gross) == ("breakeven", pytest.approx(0.75))


def test_timeout_marks_to_market():
    t = mk()
    assert step(t, 100.6, 99.8, close=100.5, max_hold=3) == []
    assert step(t, 100.6, 99.8, close=100.5, max_hold=3) == []
    assert step(t, 100.6, 99.8, close=100.5, max_hold=3) == ["closed"]
    assert (t.exit_reason, t.r_gross) == ("tempo", pytest.approx(0.25))


def test_timeout_after_tp1_only_marks_remaining_half():
    t = mk()
    step(t, 103.5, 100.5, max_hold=2)
    assert step(t, 102, 100.5, close=101, max_hold=2) == ["closed"]
    assert t.r_gross == pytest.approx(0.75 + 0.5 * 0.5)  # (101-100)/2 = 0.5R nos 50% restantes


# ------------------------------------------------------------------ Trade (short, espelho)
def test_short_stop_tp1_tp2():
    t = mk("short")
    assert (t.stop, t.tp1, t.tp2) == (102, 97, 94)
    assert step(t, 102.1, 99) == ["closed"] and t.r_gross == -1.0

    t = mk("short")
    assert step(t, 99.5, 96.9) == ["tp1"]
    assert step(t, 99, 93.9) == ["closed"]
    assert (t.exit_reason, t.r_gross) == ("tp2", pytest.approx(2.25))

    t = mk("short")
    step(t, 99.5, 96.9)
    assert step(t, 100.1, 98) == ["closed"]
    assert (t.exit_reason, t.r_gross) == ("breakeven", pytest.approx(0.75))


# ------------------------------------------------------------------ Engine: regras de risco
def make_feat(n=200, sigs=None, stop_next=True, start="2026-01-01"):
    """Barras planas em 100; `sigs` = {índice: +1/-1}. Com stop_next, a barra seguinte apanha o stop (risco = 1)."""
    idx = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    df = pd.DataFrame({"open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0, "volume": 1.0}, index=idx)
    df["sig"], df["kind"], df["risk"] = 0, "", np.nan
    df["atr"], df["rsi"], df["adx"], df["htf"] = 1.0, 50.0, 25.0, 1
    for i, s in (sigs or {}).items():
        df.iloc[i, df.columns.get_loc("sig")] = s
        df.iloc[i, df.columns.get_loc("kind")] = "pullback"
        df.iloc[i, df.columns.get_loc("risk")] = 1.0
        if stop_next:
            col, val = ("low", 98.5) if s > 0 else ("high", 101.5)
            df.iloc[i + 1, df.columns.get_loc(col)] = val
    return df


def cfg(**kw) -> Config:
    c = Config(max_signals_per_day=10, cooldown_bars=0, daily_stop_r=99.0, max_hold_bars=1000, fee_pct=0, slippage_pct=0)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def drive(engine, feat, **kw):
    out = []
    for i in range(len(feat)):
        out += engine.on_bar(feat, i, **kw)
    return out


def kinds(events):
    return [e.kind for e in events]


def test_one_open_trade_at_a_time():
    feat = make_feat(sigs={10: 1, 20: 1}, stop_next=False)
    ev = drive(Engine(cfg()), feat)
    assert kinds(ev).count("signal") == 1


def test_max_signals_per_day():
    feat = make_feat(sigs={10: 1, 20: 1, 30: 1})
    ev = drive(Engine(cfg(max_signals_per_day=2)), feat)
    assert kinds(ev).count("signal") == 2


def test_cooldown_blocks_then_allows():
    feat = make_feat(sigs={10: 1, 12: 1, 16: 1})
    ev = drive(Engine(cfg(cooldown_bars=4)), feat)
    sig_times = [e.trade.open_time for e in ev if e.kind == "signal"]
    assert sig_times == [feat.index[10].isoformat(), feat.index[16].isoformat()]


def test_daily_stop_blocks_further_signals():
    feat = make_feat(n=90, sigs={10: 1, 20: 1, 30: 1})  # 90 barras < 1 dia UTC
    e = Engine(cfg(daily_stop_r=2.0))
    ev = drive(e, feat)
    assert kinds(ev).count("signal") == 2 and kinds(ev).count("daily_stop") == 1
    assert e.day_stopped and e.day_r == pytest.approx(-2.0)


def test_daily_counters_reset_on_new_day_and_summary_emitted():
    feat = make_feat(n=190, sigs={10: 1, 20: 1, 96 + 10: 1})  # 96 barras = 1 dia -> só 1 mudança de dia
    e = Engine(cfg(max_signals_per_day=2, daily_stop_r=2.0))
    ev = drive(e, feat)
    assert kinds(ev).count("signal") == 3  # o 3º é no dia seguinte
    summaries = [x for x in ev if x.kind == "day_summary"]
    assert len(summaries) == 1
    assert summaries[0].data["date"] == "2026-01-01" and summaries[0].data["day_r"] == pytest.approx(-2.0)


def test_side_filter():
    feat = make_feat(sigs={10: -1})
    assert kinds(drive(Engine(cfg(side="long")), feat)).count("signal") == 0
    assert kinds(drive(Engine(cfg(side="short")), feat)).count("signal") == 1


def test_paused_blocks_signals_but_open_trade_is_still_tracked():
    feat = make_feat(sigs={10: 1})
    e = Engine(cfg())
    for i in range(11):
        e.on_bar(feat, i)
    assert e.trade is not None
    e.paused = True
    ev = e.on_bar(feat, 11)  # a barra 11 apanha o stop
    assert kinds(ev) == ["closed"] and e.trade is None
    e2 = Engine(cfg()); e2.paused = True
    assert kinds(drive(e2, make_feat(sigs={10: 1}))).count("signal") == 0


def test_active_hours_window():
    feat = make_feat(sigs={12: 1, 40: 1})  # 12*15min = 03:00 ; 40*15min = 10:00
    ev = drive(Engine(cfg(active_hours_utc="8-20")), feat)
    assert kinds(ev).count("signal") == 1
    assert [e.trade.open_time for e in ev if e.kind == "signal"] == [feat.index[40].isoformat()]


def test_evaluate_false_only_updates_trade():
    feat = make_feat(n=90, sigs={10: 1})
    ev = drive(Engine(cfg()), feat, evaluate=False)
    assert kinds(ev) == []


def test_costs_are_charged_in_r():
    feat = make_feat(sigs={10: 1})
    e = Engine(cfg(fee_pct=0.05, slippage_pct=0.03))
    ev = drive(e, feat)
    closed = [x for x in ev if x.kind == "closed"][0].trade
    # 0.16% de 100 = 0.16 por unidade de risco 1.0 -> 0.16R
    assert closed.cost_r == pytest.approx(0.16)
    assert closed.r_net == pytest.approx(-1.16)


def test_state_roundtrip():
    feat = make_feat(sigs={10: 1, 30: -1})
    e = Engine(cfg())
    for i in range(31):
        e.on_bar(feat, i)  # até à barra 30: deixa o trade short aberto (o stop só apanha na 31)
    e2 = Engine(cfg())
    e2.load(e.to_dict())
    assert e2.to_dict() == e.to_dict() and e2.trade is not None and e2.trade.side == "short"
