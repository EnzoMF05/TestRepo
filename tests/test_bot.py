import json
import re

import pandas as pd
import pytest
from synthetic import make_candles

from zec_bot import messages
from zec_bot.backtest import run_backtest
from zec_bot.bot import Bot
from zec_bot.config import Config
from zec_bot.engine import Engine, Event, Trade
from zec_bot.strategy import Params, prepare

P = Params()
INTERVAL = pd.Timedelta(minutes=15)


class FakeTG:
    ready = True

    def __init__(self):
        self.sent, self.cmds = [], []

    def send(self, text):
        self.sent.append(text)
        return True

    def poll_commands(self):
        out, self.cmds = self.cmds, []
        return out


@pytest.fixture(scope="module")
def candles():
    return make_candles(2400, seed=1)  # esta série tem sinais logo a seguir ao aquecimento


def make_bot(tmp_path, df, upto, tg, side="both", now_offset=pd.Timedelta(seconds=20)):
    """Bot cujo 'mercado' termina na barra `upto` e cujo relógio é logo a seguir ao seu fecho."""
    cfg = Config(state_file=str(tmp_path / "state.json"), side=side)
    holder = {"upto": upto}
    bot = Bot(
        cfg, P, tg=tg,
        fetch=lambda: (df.iloc[: holder["upto"] + 1], "fake"),
        now=lambda: df.index[holder["upto"]] + INTERVAL + now_offset,
    )
    return bot, holder


def first_signal_bar(df):
    feat = prepare(df, P)
    return int(next(i for i in range(P.warmup_bars, len(feat)) if feat["sig"].iloc[i] != 0))


def test_signal_is_sent_saved_and_not_repeated(tmp_path, candles):
    k = first_signal_bar(candles)
    tg = FakeTG()
    bot, _ = make_bot(tmp_path, candles, k, tg)
    assert bot.cycle() == 1
    assert len(tg.sent) == 1 and re.search(r"LONG|SHORT", tg.sent[0])
    assert json.loads((tmp_path / "state.json").read_text())["trade"] is not None
    assert bot.cycle() == 0 and len(tg.sent) == 1  # a mesma barra não gera nada de novo


def test_stale_bar_does_not_emit_signal(tmp_path, candles):
    """Se o Mac acordou muito depois do fecho da barra, o sinal já não é acionável."""
    k = first_signal_bar(candles)
    tg = FakeTG()
    bot, _ = make_bot(tmp_path, candles, k, tg, now_offset=pd.Timedelta(hours=1))
    bot.cycle()
    assert tg.sent == [] and bot.engine.trade is None


def test_state_survives_restart(tmp_path, candles):
    k = first_signal_bar(candles)
    tg = FakeTG()
    bot, _ = make_bot(tmp_path, candles, k, tg)
    bot.cycle()
    tg2 = FakeTG()
    bot2, _ = make_bot(tmp_path, candles, k, tg2)
    assert bot2.engine.trade is not None and bot2.engine.trade.open_time == bot.engine.trade.open_time
    assert bot2.cycle() == 0 and tg2.sent == []


def test_corrupt_state_file_is_moved_aside(tmp_path, candles):
    (tmp_path / "state.json").write_text("{isto não é json")
    bot, _ = make_bot(tmp_path, candles, 1500, FakeTG())
    assert bot.engine.trade is None and (tmp_path / "state.bak").exists()


def test_data_failures_alert_once_then_recover(tmp_path, candles):
    tg = FakeTG()
    bot, _ = make_bot(tmp_path, candles, 1500, tg)
    good = bot.fetch
    bot.fetch = lambda: (_ for _ in ()).throw(RuntimeError("sem rede"))
    for _ in range(15):
        bot.step()
    assert sum("não consegue obter dados" in m for m in tg.sent) == 1
    bot.fetch = good
    bot.step()
    assert any("recuperados" in m for m in tg.sent)


def test_commands(tmp_path, candles):
    tg = FakeTG()
    bot, _ = make_bot(tmp_path, candles, 1500, tg)
    bot.cfg.enable_commands = True
    bot.cycle()
    tg.cmds = ["/status"]; bot.handle_commands()
    assert "ativo" in tg.sent[-1]
    tg.cmds = ["/pause"]; bot.handle_commands()
    assert bot.engine.paused and json.loads((tmp_path / "state.json").read_text())["paused"] is True
    tg.cmds = ["/resume"]; bot.handle_commands()
    assert not bot.engine.paused
    tg.cmds = ["/stats"]; bot.handle_commands()
    assert "Últimos 7 dias" in tg.sent[-1]
    tg.cmds = ["/quem"]; bot.handle_commands()
    assert "Comandos" in tg.sent[-1]


def test_live_replay_matches_backtest(tmp_path, candles):
    """O robô live, alimentado barra a barra, tem de abrir exatamente os mesmos trades que o backtest."""
    start, end = P.warmup_bars, P.warmup_bars + 450
    expected, _ = run_backtest(candles.iloc[: end + 1], Config(), P)
    expected_open = [t["open_time"] for t in expected]

    tg = FakeTG()
    bot, holder = make_bot(tmp_path, candles, start, tg)
    opened = []
    orig = bot._dispatch

    def spy(events):
        opened.extend(e.trade.open_time for e in events if e.kind == "signal")
        orig(events)

    bot._dispatch = spy
    for j in range(start, end + 1):
        holder["upto"] = j
        bot.cycle()

    live_closed_open_times = [t["open_time"] for t in bot.engine.closed]
    assert live_closed_open_times == expected_open
    assert set(expected_open) <= set(opened)
    assert len(opened) - len(expected_open) in (0, 1)  # no máximo 1 trade ainda aberto no fim


# ------------------------------------------------------------------ mensagens
def _trade(side="long"):
    d = 1 if side == "long" else -1
    return Trade(id=1, side=side, kind="breakout", entry=52.4, stop=52.4 - d * 0.9, stop0=52.4 - d * 0.9,
                 tp1=52.4 + d * 1.35, tp2=52.4 + d * 2.7, risk=0.9, open_time="2026-03-01T10:00:00+00:00",
                 cost_r=0.1)


def test_position_size_risks_the_configured_amount():
    cfg = Config(account_size=2000, risk_pct=1.0)
    qty, notional, lev = messages.position_size(cfg, entry=50.0, risk=1.0)
    assert qty == pytest.approx(20.0) and notional == pytest.approx(1000.0) and lev == pytest.approx(0.5)
    assert qty * 1.0 == pytest.approx(20.0)  # 1% de 2000 = 20$ perdidos se o stop for atingido


@pytest.mark.parametrize("side", ["long", "short"])
def test_all_messages_are_valid_telegram_html(side):
    cfg = Config()
    t = _trade(side)
    texts = [messages.format_signal(t, cfg, htf=1 if side == "long" else -1, rsi=55.0, adx=27.0)]
    t2 = _trade(side); t2.state, t2.exit_reason, t2.r_net = "closed", "tp2", 2.1
    for ev in (Event("tp1", t), Event("closed", t2), Event("daily_stop", data={"day_r": -3.2}),
               Event("day_summary", data={"date": "2026-03-01", "signals": 2, "n": 2, "wins": 1, "losses": 1,
                                          "day_r": 0.4})):
        texts.append(messages.format_event(ev, cfg))
    e = Engine(cfg)
    texts += [messages.format_stats(e.stats(), "x"), messages.format_status(e, cfg, 50.0, "binance"),
              messages.format_startup(cfg, "binance")]
    for text in texts:
        plain = re.sub(r"</?(b|i)>", "", text)
        assert not re.search(r"[<>&]", plain), text  # caracteres que partiriam o parse_mode HTML
    assert side.upper() in texts[0] and "52.4" in texts[0]
