"""Modo `sweep` dentro do robô: do fecho da hora ao Telegram, ao journal e ao estado."""
import csv
import json
import re
import sys

import pandas as pd
import pytest
from fakes import FakeBinance, ms
from synthetic import append_bars, sweep_scenario

from zec_bot import __main__ as entry
from zec_bot import backtest, messages
from zec_bot.bot import Bot
from zec_bot.config import Config
from zec_bot.derivs import DerivsMonitor
from zec_bot.engine import Event
from zec_bot.oi_hourly import OiCascade
from zec_bot.sweep_engine import SweepEngine, SweepJournal

CLUSTER = {"below": [98.5], "above": [101.5], "spot_approx": 100.0, "atualizado": "2026-03-01T00:00:00Z", "erro": None}


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


def assert_valid_html(text):
    plain = re.sub(r"&(lt|gt|amp);", "", re.sub(r"</?(b|i)>", "", text))
    assert not re.search(r"[<>&]", plain), text


def rows(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def make(tmp_path, df, upto, tg, offset_s=130, levels=None, oi=-4.2, fake=None, **cfg_kw):
    cfg = Config(strategy="sweep", state_file=str(tmp_path / "state.json"), sweep_journal=str(tmp_path / "j.csv"),
                 sweep_results=str(tmp_path / "r.csv"), **cfg_kw)
    box = {"upto": upto, "offset": offset_s}

    def clock():
        return df.index[box["upto"]] + pd.Timedelta(minutes=15, seconds=box["offset"])

    engine = SweepEngine(cfg, None, levels=levels or (lambda: (dict(CLUSTER), None)),
                         oi=lambda ms_: {"delta": oi, "src": "CG1H", "errors": []},
                         journal=SweepJournal(cfg.sweep_journal, cfg.sweep_results))
    fake = fake or FakeBinance(0, span_ms=(ms(str(df.index[0] - pd.Timedelta(days=40))), ms(str(df.index[-1] + pd.Timedelta(days=2)))),
                               oi_value=980_000.0, oi_now=1_000_000.0)
    derivs = DerivsMonitor(cfg, binance=fake, venue=lambda: {"funding_hr": 0.0000125, "max_leverage": 5.0},
                           now=lambda: clock().timestamp())
    bot = Bot(cfg, None, tg=tg, fetch=lambda: (df.iloc[: box["upto"] + 1], "fake"), now=clock, derivs=derivs, engine=engine)
    return bot, box


@pytest.fixture
def case():
    return sweep_scenario(+1)


def test_bot_builds_a_sweep_engine_from_the_config(tmp_path):
    bot = Bot(Config(strategy="sweep", state_file=str(tmp_path / "s.json"), coinalyze_api_key="k", sweep_journal=str(tmp_path / "j.csv")),
              tg=FakeTG())
    assert isinstance(bot.engine, SweepEngine) and bot.p.mode == "sweep" and bot.max_age == pd.Timedelta(minutes=12)
    assert bot.settle == pd.Timedelta(seconds=125)
    assert Bot(Config(state_file=str(tmp_path / "t.json")), tg=FakeTG()).settle == pd.Timedelta(0)  # outras estratégias: sem espera


def test_it_waits_for_the_hour_to_settle_before_evaluating(tmp_path, case):
    df, _ = case
    tg = FakeTG()
    bot, box = make(tmp_path, df, len(df) - 1, tg, offset_s=30)  # 30 s depois do fecho da hora
    assert bot.cycle() == 0 and tg.sent == [] and bot.engine.last_bar == ""  # a mesa espera até às :02:05
    box["offset"] = 130
    assert bot.cycle() == 1 and len(tg.sent) == 1


def test_the_alert_carries_the_desks_numbers_and_is_valid_html(tmp_path, case):
    df, _ = case
    tg = FakeTG()
    bot, _ = make(tmp_path, df, len(df) - 1, tg)
    bot.cycle()
    msg = tg.sent[0]
    for expected in ("ZEC LONG", "Sweep de nível · L2 (1H)", "LIMIT na face: <b>98.500</b> (nunca a mercado)", "Alvo 1.5R",
                     "Pavio 85% · OI -4.2% na hora (CG1H)", "Plano A (fecho/2R): 99.900", "Válido até", "cancela se não executar até",
                     "extremo da vela ∓ 0.1 ATR", "Funding Binance: <b>+0.0100%/8h</b>", "Só sinal"):
        assert expected in msg, expected
    assert_valid_html(msg)
    (r,) = rows(tmp_path / "j.csv")
    assert (r["lado"], r["tier"], r["motor"]) == ("LONG", "L2", "CT-NIVEL-B")
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["trade"]["state"] == "pending" and saved["trade"]["meta"]["level"] == 98.5 and saved["last_eval"]


def test_the_pending_order_survives_a_restart_and_is_not_signalled_again(tmp_path, case):
    df, _ = case
    bot, _ = make(tmp_path, df, len(df) - 1, FakeTG())
    bot.cycle()
    tg2 = FakeTG()
    bot2, _ = make(tmp_path, df, len(df) - 1, tg2)
    assert bot2.engine.trade.state == "pending" and bot2.engine.trade.entry == 98.5
    assert bot2.cycle() == 0 and tg2.sent == []


def test_full_life_signal_fill_target_reaches_telegram_and_stats(tmp_path, case):
    df, _ = case
    n = len(df)
    probe, _ = make(tmp_path / "probe" if (tmp_path / "probe").mkdir() is None else None, df, n - 1, FakeTG())
    probe.cycle()
    t = probe.engine.trade
    full = append_bars(df, [(99.9, 100.1, 98.4, 99.0), (99.0, 99.7, 98.7, 99.5), (99.5, t.tp1 + 0.2, 99.3, t.tp1)])
    tg = FakeTG()
    bot, box = make(tmp_path, full, n - 1, tg)
    for j in range(n - 1, len(full)):
        box["upto"] = j
        bot.cycle()
    kinds = [("Sweep de nível" in m, "limite executado" in m, "Alvo atingido" in m) for m in tg.sent]
    assert kinds == [(True, False, False), (False, True, False), (False, False, True)]
    assert "✅" in tg.sent[-1] and re.search(r"Resultado: <b>\+1\.\d\dR</b>", tg.sent[-1])
    assert all(m for m in tg.sent) and all(assert_valid_html(m) is None for m in tg.sent)
    st = bot.engine.stats()
    assert st["n"] == 1 and st["wins"] == 1 and st["total_r"] > 1.4
    assert rows(tmp_path / "r.csv")[0]["estado"] == "FILLED"


def test_hour_closed_more_than_12_minutes_ago_is_not_signalled(tmp_path, case):
    df, _ = case
    tg = FakeTG()
    bot, _ = make(tmp_path, df, len(df) - 1, tg, offset_s=11 * 60)  # 11 min depois: ainda dentro da janela da mesa
    bot.cycle()
    assert len(tg.sent) == 1
    tg2 = FakeTG()
    bot2, _ = make(tmp_path / "late" if (tmp_path / "late").mkdir() is None else None, df, len(df) - 1, tg2, offset_s=13 * 60)
    bot2.cycle()
    assert tg2.sent == [] and bot2.engine.trade is None
    assert rows(tmp_path / "late" / "j.csv")[0]["resultado_R"].startswith("HORA_NAO_AVALIADA")


def test_alert_still_goes_out_when_binance_context_is_down(tmp_path, case):
    df, _ = case
    fake = FakeBinance(0, span_ms=(ms("2026-01-01"), ms("2026-04-01")))
    fake.fail.update({"premium", "oi_now", "funding"})
    tg = FakeTG()
    bot, _ = make(tmp_path, df, len(df) - 1, tg, fake=fake)
    bot.cycle()
    assert len(tg.sent) == 1 and "LIMIT na face" in tg.sent[0] and "Funding Binance" not in tg.sent[0]


def test_status_shows_the_pending_order(tmp_path, case):
    df, _ = case
    tg = FakeTG()
    bot, _ = make(tmp_path, df, len(df) - 1, tg)
    bot.cfg.enable_commands = True
    bot.cycle()
    tg.cmds = ["/status"]
    bot.handle_commands()
    assert "Ordem LIMIT pendente: LONG sweep @ 98.500" in tg.sent[-1] and "cancela" in tg.sent[-1]


def test_missing_levels_send_one_note_and_no_signal(tmp_path, case):
    df, _ = case
    tg = FakeTG()
    bot, _ = make(tmp_path, df, len(df) - 1, tg, levels=lambda: (None, "niveis.csv ilegível: FileNotFoundError: niveis.csv"))
    bot.cycle()
    assert len(tg.sent) == 1 and tg.sent[0].startswith("ℹ️ Sem níveis para ZEC") and bot.engine.trade is None
    assert_valid_html(tg.sent[0])


# ------------------------------------------------------------------ mensagens
def pending_trade(marketable=False):
    df, _ = sweep_scenario(+1)
    e = SweepEngine(Config(strategy="sweep"), None, levels=lambda: (dict(CLUSTER), None),
                    oi=lambda ms_: {"delta": -4.2, "src": "Coinalyze", "errors": []})
    for i in range(len(df) - 4, len(df)):
        e.on_bar(df, i)
    t = e.trade
    t.meta["marketable"] = marketable
    return t


@pytest.mark.parametrize("marketable", [False, True])
def test_every_sweep_message_is_valid_html(marketable):
    cfg = Config(strategy="sweep")
    t = pending_trade(marketable)
    texts = [messages.format_sweep_signal(t, cfg), messages.format_sweep_signal(t, cfg, {"funding_8h_pct": 0.09, "premium_pct": 0.1,
             "oi_usd": 4.2e7, "oi_change_1h_pct": None, "reading": None, "max_leverage": 5.0})]
    t.state = "open"
    texts.append(messages.format_event(Event("filled", t), cfg))
    texts.append(messages.format_event(Event("cancelled", t, data={"cancel_at": t.meta["cancel_at"]}), cfg))
    texts.append(messages.format_event(Event("note", data={"text": "OI <n/d> & fontes"}), cfg))
    for reason, r in (("target", 1.6), ("stop", -1.1), ("tempo", 0.2)):
        t.exit_reason, t.r_net = reason, r
        texts.append(messages.format_event(Event("closed", t), cfg))
    texts.append(messages.format_startup(cfg, "hyperliquid"))
    for text in texts:
        assert text and assert_valid_html(text) is None
    assert ("sem reclaim" in texts[0].lower() or "lado errado" in texts[0]) is marketable
    assert "Validade esgotada" in texts[-2] and "níveis" in texts[-1]


# ------------------------------------------------------------------ --check e backtest
def test_check_sweep_reports_levels_oi_and_the_last_closed_candle(tmp_path, monkeypatch, capsys):
    df, _ = sweep_scenario(+1)
    niveis = tmp_path / "niveis.csv"
    niveis.write_text("ticker,cluster_below,cluster_above,spot_approx,atualizado_utc\nZEC,98.5,101.5,100,2026-03-01T00:00:00Z\n")

    class StubCascade:
        @classmethod
        def from_cfg(cls, cfg):
            return cls()

        def delta(self, candle_open_ms):
            return {"delta": -4.2, "src": "Coinalyze", "errors": []}

    monkeypatch.setattr(entry, "OiCascade", StubCascade)
    entry.check_sweep(Config(strategy="sweep", niveis_path=str(niveis)), df)
    out = capsys.readouterr().out
    assert "níveis ZEC: abaixo [98.5] · acima [101.5]" in out and "OI 1H desta vela: -4.20% (Coinalyze)" in out
    assert "tier L2 · LONG" in out and "válido=True" in out and "face 98.500" in out
    assert "Última vela 1H fechada (2026-03-05 03:00Z): O 100.0 H 100.4 L 97.0 C 99.9" in out  # a vela varrida do cenário
    assert "idade" in out and "⚠ níveis com mais de 6h" in out  # níveis de março avaliados em "agora": avisa


def test_check_sweep_explains_how_to_fix_missing_levels_and_oi(tmp_path, monkeypatch, capsys):
    df, _ = sweep_scenario(+1)
    entry.check_sweep(Config(strategy="sweep", niveis_path=str(tmp_path / "nada.csv")), df)
    assert "níveis:" in capsys.readouterr().out
    niveis = tmp_path / "n.csv"
    niveis.write_text("ticker,cluster_below,cluster_above,spot_approx,atualizado_utc\nZEC,98.5,,100,\n")
    entry.check_sweep(Config(strategy="sweep", niveis_path=str(niveis)), df)  # sem chaves de OI
    out = capsys.readouterr().out
    assert "OI 1H: sem valor" in out and "nunca passa de L1" in out and "sem_keys" in out


def test_backtest_refuses_sweep_and_explains_why(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["backtest", "--strategy", "sweep"])
    assert backtest.main() == 2
    out = capsys.readouterr().out
    assert "não é backtestável" in out and "niveis.csv" in out and "zec_sweep_results.csv" in out
