import json
import re

import numpy as np
import pandas as pd
import pytest
from fakes import FakeBinance, ms
from synthetic import informed_funding, make_candles, reversal_scenario

from zec_bot import messages
from zec_bot.backtest import run_backtest
from zec_bot.bot import Bot
from zec_bot.config import Config
from zec_bot.derivs import DerivsMonitor, build_frame
from zec_bot.engine import Engine, Event, Trade
from zec_bot.strategy import Params, prepare

P = Params()  # tendência: os testes "clássicos" do robô usam esta estratégia
INTERVAL = pd.Timedelta(minutes=15)
H8 = 8 * 3_600_000


def assert_valid_html(text):
    """O Telegram (parse_mode=HTML) só aceita <b>/<i> e as entidades &lt; &gt; &amp;: qualquer outro <, > ou & parte a mensagem."""
    plain = re.sub(r"</?(b|i)>", "", text)
    plain = re.sub(r"&(lt|gt|amp);", "", plain)
    assert not re.search(r"[<>&]", plain), text


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


def span_of(df):
    return ms(str(df.index[0] - pd.Timedelta(days=40))), ms(str(df.index[-1] + pd.Timedelta(days=1)))


def venue_ok():
    return {"funding_hr": 0.0000125, "max_leverage": 5.0}


def make_bot(tmp_path, df, upto, tg, side="both", now_offset=pd.Timedelta(seconds=20), params=P, fake=None,
             venue=venue_ok, **cfg_kw):
    """Bot cujo 'mercado' termina na barra `upto` e cujo relógio é logo a seguir ao seu fecho.
    Binance e Hyperliquid são sempre simuladas (os testes nunca tocam na rede)."""
    cfg = Config(state_file=str(tmp_path / "state.json"), side=side, **cfg_kw)
    holder = {"upto": upto}

    def clock():
        return df.index[holder["upto"]] + INTERVAL + now_offset

    fake = fake or FakeBinance(0, span_ms=span_of(df), oi_value=980_000.0, oi_now=1_000_000.0)
    derivs = DerivsMonitor(cfg, binance=fake, venue=venue, now=lambda: clock().timestamp())
    bot = Bot(cfg, params, tg=tg, fetch=lambda: (df.iloc[: holder["upto"] + 1], "fake"), now=clock, derivs=derivs)
    return bot, holder


def first_signal_bar(df):
    feat = prepare(df, P)
    return int(next(i for i in range(P.warmup_bars, len(feat)) if feat["sig"].iloc[i] != 0))


# ------------------------------------------------------------------ comportamento base do robô
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


def test_state_files_from_earlier_versions_still_load(tmp_path, candles):
    old = Engine(Config()).to_dict()
    old["derivs_samples"] = [[1.0, 2.0, 3.0]]  # chave que versões anteriores gravavam e já não existe
    (tmp_path / "state.json").write_text(json.dumps(old))
    bot, _ = make_bot(tmp_path, candles, 1500, FakeTG())
    assert bot.engine.closed == []
    bot.cycle()  # e continua a funcionar e a gravar
    assert "derivs_samples" not in json.loads((tmp_path / "state.json").read_text())


def test_data_failures_alert_once_then_recover(tmp_path, candles):
    tg = FakeTG()
    bot, _ = make_bot(tmp_path, candles, 1500, tg)
    good = bot.fetch
    bot.fetch = lambda: (_ for _ in ()).throw(RuntimeError("ligação <recusada> & sem rede"))
    for _ in range(15):
        bot.step()
    assert sum("não consegue obter dados" in m for m in tg.sent) == 1
    alert = next(m for m in tg.sent if "não consegue obter dados" in m)
    assert_valid_html(alert)
    assert "ligação &lt;recusada&gt; &amp; sem rede" in alert
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


def test_live_replay_matches_backtest_trend(tmp_path, candles):
    """O robô live, alimentado barra a barra, tem de abrir exatamente os mesmos trades que o backtest."""
    start, end = P.warmup_bars, P.warmup_bars + 450
    expected, _ = run_backtest(candles.iloc[: end + 1], Config(), P)
    expected_open = [t["open_time"] for t in expected]

    tg = FakeTG()
    bot, holder = make_bot(tmp_path, candles, start, tg)
    opened = []
    orig = bot._dispatch

    def spy(events, deriv=None):
        opened.extend(e.trade.open_time for e in events if e.kind == "signal")
        orig(events, deriv)

    bot._dispatch = spy
    for j in range(start, end + 1):
        holder["upto"] = j
        bot.cycle()

    assert [t["open_time"] for t in bot.engine.closed] == expected_open
    assert set(expected_open) <= set(opened)
    assert len(opened) - len(expected_open) in (0, 1)  # no máximo 1 trade ainda aberto no fim


# ------------------------------------------------------------------ mensagens
def _trade(side="long", kind="breakout"):
    d = 1 if side == "long" else -1
    return Trade(id=1, side=side, kind=kind, entry=52.4, stop=52.4 - d * 0.9, stop0=52.4 - d * 0.9,
                 tp1=52.4 + d * 1.35, tp2=52.4 + d * 2.7, risk=0.9, open_time="2026-03-01T10:00:00+00:00",
                 cost_r=0.1)


def test_position_size_risks_the_configured_amount():
    cfg = Config(account_size=2000, risk_pct=1.0)
    qty, notional, lev = messages.position_size(cfg, entry=50.0, risk=1.0)
    assert qty == pytest.approx(20.0) and notional == pytest.approx(1000.0) and lev == pytest.approx(0.5)
    assert qty * 1.0 == pytest.approx(20.0)  # 1% de 2000 = 20$ perdidos se o stop for atingido


DERIV = {"funding_8h_pct": 0.062, "funding_pctl": 96.0, "premium_pct": 0.1, "oi_usd": 4.2e7,
         "oi_change_1h_pct": 2.1, "oi_change_4h_pct": None, "oi_rev_pct": float("nan"), "hl_funding_8h_pct": 0.031,
         "max_leverage": 5.0, "reading": "preço↑ + OI↑: entram longs novos"}
INFO = {"stretch_atr": 3.4, "rsi_extreme": 78.0, "funding_8h_pct": 0.062, "funding_pctl": 96.0, "oi_rev_pct": 4.2}


@pytest.mark.parametrize("side,kind", [("long", "breakout"), ("short", "pullback"), ("short", "reversal"),
                                       ("long", "reversal")])
def test_all_messages_are_valid_telegram_html(side, kind):
    cfg = Config()
    t = _trade(side, kind)
    texts = [messages.format_signal(t, cfg, htf=1, rsi=55.0, adx=27.0, deriv=DERIV, info=INFO),
             messages.format_signal(t, cfg, htf=-1, rsi=55.0, adx=27.0)]  # com e sem derivados
    t2 = _trade(side, kind); t2.state, t2.exit_reason, t2.r_net = "closed", "tp2", 2.1
    for ev in (Event("tp1", t), Event("closed", t2), Event("daily_stop", data={"day_r": -3.2}),
               Event("blocked", data={"side": side, "kind": kind, "reason": "funding +0.090%/8h > 0.05%"}),
               Event("day_summary", data={"date": "2026-03-01", "signals": 2, "n": 2, "wins": 1, "losses": 1,
                                          "day_r": 0.4})):
        texts.append(messages.format_event(ev, cfg))
    e = Engine(cfg)
    texts += [messages.format_stats(e.stats(), "x"), messages.format_status(e, cfg, 50.0, "hyperliquid", DERIV),
              messages.format_startup(cfg, "hyperliquid")]
    for text in texts:
        assert_valid_html(text)
    assert side.upper() in texts[0] and "52.4" in texts[0]
    blocked = messages.format_event(Event("blocked", data={"side": side, "kind": kind, "reason": "a < b & c > d"}), cfg)
    assert "a &lt; b &amp; c &gt; d" in blocked


def test_reversal_message_explains_why():
    cfg = Config()
    text = messages.format_signal(_trade("short", "reversal"), cfg, htf=1, rsi=44.0, adx=30.0, deriv=DERIV, info=INFO)
    assert "Reversão (longs sobrelotados)" in text and "SHORT" in text
    assert "Porquê: funding +0.062%/8h (P96 dos últimos 30d)" in text and "3.4 ATR acima da EMA55" in text
    assert "RSI pico 78" in text and "(contra-tendência)" in text
    assert "Porquê" in text and "OI 24h +4.2%" not in text  # sem REV_USE_OI, o OI não é condição: fica só na linha do OI
    with_oi = messages.format_signal(_trade("short", "reversal"), Config(rev_use_oi=True), htf=1, rsi=44.0, adx=30.0,
                                     deriv=DERIV, info=INFO)
    assert "candle de rejeição · OI 24h +4.2%" in with_oi  # com REV_USE_OI, é uma das condições e aparece no porquê
    assert "Funding Binance: <b>+0.0620%/8h</b>" in text and "Hyperliquid +0.0310%/8h" in text
    long_text = messages.format_signal(_trade("long", "reversal"), cfg, htf=-1, rsi=30.0, adx=30.0, info=INFO)
    assert "Reversão (shorts sobrelotados)" in long_text and "abaixo da EMA55" in long_text and "RSI mínimo 78" in long_text


def test_squeeze_warning_only_when_you_are_on_the_crowded_side():
    from zec_bot.messages import derivs_lines
    cfg = Config()
    assert "squeeze" in derivs_lines(DERIV, "long", cfg)  # long com funding muito alto: cuidado
    assert "squeeze" not in derivs_lines(DERIV, "short", cfg)  # short com funding alto é o lado a favor


def test_derivs_lines_handle_missing_numbers():
    from zec_bot.messages import derivs_lines
    text = derivs_lines(DERIV, "short", Config())
    assert "OI Binance: <b>42.00M$</b>" in text and "4h n/d" in text and "24h n/d" in text
    assert derivs_lines(None, "long", Config()) == ""


# ------------------------------------------------------------------ derivados no robô (tendência)
def test_signal_message_carries_binance_funding_and_oi(tmp_path, candles):
    k = first_signal_bar(candles)
    tg = FakeTG()
    bot, _ = make_bot(tmp_path, candles, k, tg)
    bot.cycle()
    msg = tg.sent[0]
    assert "Funding Binance: <b>+0.0100%/8h</b>" in msg and "Hyperliquid +0.0100%/8h" in msg
    assert "OI Binance: <b>50.00M$</b>" in msg and "1h +2.04%" in msg and "4h +2.04%" in msg
    assert "Leitura:" in msg


def test_leverage_above_hyperliquid_max_is_flagged(tmp_path, candles):
    k = first_signal_bar(candles)
    tg = FakeTG()
    bot, _ = make_bot(tmp_path, candles, k, tg, account_size=100.0, risk_pct=10.0)  # exige alavancagem alta
    bot.cycle()
    assert "acima do máximo de 5x" in tg.sent[0]


def test_derivs_failure_does_not_stop_the_signal(tmp_path, candles):
    k = first_signal_bar(candles)
    fake = FakeBinance(0, span_ms=span_of(candles))
    fake.fail.update({"premium", "oi_now"})  # só o snapshot da mensagem falha
    tg = FakeTG()
    bot, _ = make_bot(tmp_path, candles, k, tg, fake=fake)
    bot.cycle()
    assert len(tg.sent) == 1 and re.search(r"LONG|SHORT", tg.sent[0]) and "Funding Binance" not in tg.sent[0]
    assert bot.engine.trade is not None


def test_funding_filter_blocks_and_tells_you(tmp_path, candles):
    k = first_signal_bar(candles)
    side = "long" if prepare(candles.iloc[: k + 1], P)["sig"].iloc[k] > 0 else "short"
    hot = 0.0008 if side == "long" else -0.0008  # 0.08%/8h: além do limite de 0.05
    fake = FakeBinance(0, span_ms=span_of(candles), funding_rate=lambda t: hot)
    tg = FakeTG()
    bot, _ = make_bot(tmp_path, candles, k, tg, fake=fake, funding_filter=True)
    bot.cycle()
    assert bot.engine.trade is None and bot.engine.day_signals == 0
    assert len(tg.sent) == 1 and "ignorado pelos filtros" in tg.sent[0] and "funding" in tg.sent[0]


def test_trend_only_does_not_download_funding_history_each_cycle(tmp_path, candles):
    feat = prepare(candles, P)
    quiet = int(next(i for i in range(P.warmup_bars, len(feat)) if feat["sig"].iloc[i] == 0))
    fake = FakeBinance(0, span_ms=span_of(candles))
    bot, _ = make_bot(tmp_path, candles, quiet, FakeTG(), fake=fake, strategy="trend")
    bot.cycle()
    assert fake.calls["funding"] == 0 and fake.calls["oi_hist"] == 0  # não precisa de funding/OI para sinalizar


def test_status_shows_funding_and_oi(tmp_path, candles):
    tg = FakeTG()
    bot, _ = make_bot(tmp_path, candles, 1500, tg)
    bot.cfg.enable_commands = True
    bot.cycle()
    tg.cmds = ["/status"]
    bot.handle_commands()
    assert "Funding +0.0100%/8h" in tg.sent[-1] and "OI 50.00M$" in tg.sent[-1]


# ------------------------------------------------------------------ reversão no robô
def spike_funding(df, k, spike):
    """Funding base 0.01%/8h e, do último registo antes da barra k em diante, o valor `spike`."""
    boundary = ms(str(df.index[k].floor("8h")))
    return lambda t: spike if t >= boundary else 0.0001


def reversal_bot(tmp_path, direction, spike, **kw):
    df, k = reversal_scenario(direction)
    fake = FakeBinance(0, span_ms=span_of(df), funding_rate=spike_funding(df, k, spike), oi_value=980_000.0,
                       oi_now=1_000_000.0)
    tg = FakeTG()
    bot, _ = make_bot(tmp_path, df, k, tg, params=None, fake=fake, **kw)  # params=None -> Params.from_cfg(reversal)
    return bot, tg, df, k


def test_reversal_short_end_to_end(tmp_path):
    bot, tg, df, k = reversal_bot(tmp_path, -1, spike=0.0008)  # funding +0.08%/8h: longs sobrelotados
    assert bot.p.mode == "reversal"
    assert bot.cycle() == 1
    assert len(tg.sent) == 1
    msg = tg.sent[0]
    assert "SHORT" in msg and "Reversão (longs sobrelotados)" in msg and "Porquê: funding +0.080%/8h (P100" in msg
    assert "RSI pico" in msg
    assert "Funding Binance: <b>+0.0100%/8h</b>" in msg  # o snapshot mostra o último funding LIQUIDADO da Binance
    t = bot.engine.trade
    assert t.kind == "reversal" and t.side == "short" and (t.tp1_r, t.tp2_r) == (1.0, 2.0)
    saved = json.loads((tmp_path / "state.json").read_text())["trade"]
    assert saved["tp1_r"] == 1.0 and saved["tp2_r"] == 2.0
    bot2, _ = make_bot(tmp_path, df, k, FakeTG(), params=None)  # reinício: recupera o trade com os alvos de reversão
    assert bot2.engine.trade.tp1_r == 1.0 and bot2.engine.trade.kind == "reversal"


def test_reversal_long_end_to_end(tmp_path):
    bot, tg, df, k = reversal_bot(tmp_path, +1, spike=-0.0005)  # funding -0.05%/8h: shorts sobrelotados (extremo)
    assert bot.cycle() == 1
    assert "LONG" in tg.sent[0] and "Reversão (shorts sobrelotados)" in tg.sent[0]


@pytest.mark.parametrize("direction,spike", [(-1, 0.0001), (+1, 0.0001), (-1, -0.0005), (+1, 0.0008)])
def test_no_reversal_without_the_right_crowding(tmp_path, direction, spike):
    """Neutro, ou crowding do lado errado (funding negativo não dá short; positivo não dá long)."""
    bot, tg, df, k = reversal_bot(tmp_path, direction, spike)
    bot.cycle()
    assert tg.sent == [] and bot.engine.trade is None


def test_binance_outage_disables_reversal_says_so_and_recovers(tmp_path):
    df, k = reversal_scenario(-1)
    fake = FakeBinance(0, span_ms=span_of(df), funding_rate=spike_funding(df, k, 0.0008))
    fake.fail.add("funding")
    tg = FakeTG()
    bot, holder = make_bot(tmp_path, df, k - 3, tg, params=None, fake=fake)
    bot.cycle(); bot.cycle()
    alerts = [m for m in tg.sent if "Sem funding/OI da Binance" in m]
    assert len(alerts) == 1 and "REVERSÃO desativados" in alerts[0]  # avisa uma vez, não em cada ciclo
    assert_valid_html(alerts[0])
    holder["upto"] = k  # a barra do sinal chega com a Binance ainda em baixo -> não pode inventar crowding
    bot.cycle()
    assert bot.engine.trade is None and not any("SHORT" in m for m in tg.sent)
    fake.fail.clear()
    holder["upto"] = k + 1
    bot.cycle()
    assert any("recuperados" in m for m in tg.sent)


def test_live_replay_matches_backtest_reversal(tmp_path):
    """Reversão: robô live (features de funding calculados a cada ciclo a partir dos registos em bruto)
    vs backtest (features calculados de uma vez). Têm de abrir exatamente os mesmos trades."""
    df = make_candles(2400, seed=4)
    rates = informed_funding(df)
    fake = FakeBinance(0, span_ms=span_of(df), funding_rate=lambda t: rates.get(t - t % H8, 0.0001))
    cfg = Config(strategy="reversal", max_signals_per_day=10, cooldown_bars=0)
    p = Params.from_cfg(cfg)
    full_frame = build_frame(df.index, fake.frows, fake.orows, cfg, cfg.crowd_window_days)

    sides = set()
    total = 0
    for start, end in ((690, 730), (1340, 1440)):  # janelas com trades de reversão (long e short)
        expected, _ = run_backtest(df.iloc[: end + 1], cfg, p, deriv=full_frame.iloc[: end + 1], start=start)
        bot, holder = make_bot(tmp_path / f"w{start}", df, start, FakeTG(), params=p, fake=fake, max_signals_per_day=10,
                               cooldown_bars=0) if (tmp_path / f"w{start}").mkdir() is None else (None, None)
        for j in range(start, end + 1):
            holder["upto"] = j
            bot.cycle()
        assert [t["open_time"] for t in bot.engine.closed] == [t["open_time"] for t in expected]
        assert [round(t["r_net"], 9) for t in bot.engine.closed] == [round(t["r_net"], 9) for t in expected]
        sides |= {t["side"] for t in expected}
        total += len(expected)
    assert total >= 2 and sides == {"long", "short"}, "a janela de teste devia ter trades nos dois lados"
