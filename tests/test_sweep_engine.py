"""Motor de ordens do sweep: da vela 1H fechada ao alerta, ao fill, ao alvo/stop/validade ou ao cancelamento."""
import csv

import pandas as pd
import pytest
from synthetic import append_bars, sweep_scenario

from zec_bot.config import Config
from zec_bot.engine import Engine
from zec_bot.strategy import Params
from zec_bot.sweep import SweepParams, atr14, hourly_candles
from zec_bot.sweep_engine import JOURNAL_HEADER, RESULTS_HEADER, SweepEngine, SweepJournal

CLUSTER = {"below": [98.5], "above": [101.5], "spot_approx": 100.0, "atualizado": "2026-03-01T00:00:00Z", "erro": None}


def make(tmp_path, levels=None, oi=-4.2, sp=None, **cfg_kw):
    cfg = Config(strategy="sweep", state_file=str(tmp_path / "s.json"), **cfg_kw)
    oi_fn = oi if callable(oi) else (lambda ms: {"delta": oi, "src": "CG1H", "errors": [] if oi is not None else ["CG1H:sem_vela"]})
    e = SweepEngine(cfg, Params.from_cfg(cfg), sp=sp, levels=levels or (lambda: (dict(CLUSTER), None)), oi=oi_fn,
                    journal=SweepJournal(tmp_path / "j.csv", tmp_path / "r.csv"))
    return e


def run(e, df, start, stop=None, evaluate=True):
    out = []
    for i in range(start, stop if stop is not None else len(df)):
        out += [(i, ev) for ev in e.on_bar(df, i, evaluate=evaluate)]
    return out


def kinds(evs):
    return [ev.kind for _, ev in evs]


def rows(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture
def long_case():
    return sweep_scenario(+1)


# ------------------------------------------------------------------ do sinal à ordem
def test_l2_sweep_creates_a_pending_limit_on_the_face(tmp_path, long_case):
    df, info = long_case
    e = make(tmp_path)
    evs = run(e, df, len(df) - 4)
    assert kinds(evs) == ["sweep_signal"]  # avalia uma só vez, no fecho da hora (última barra)
    t = e.trade
    atr = atr14(hourly_candles(df, 15, 45))
    assert (t.state, t.side, t.kind, t.entry) == ("pending", "long", "sweep", 98.5)  # a FACE do nível, não o fecho
    assert t.stop == pytest.approx(97.0 - 0.1 * atr) and t.single_target
    R = (98.5 - t.stop) + 98.5 * 0.00035 * 2
    assert t.tp1 == pytest.approx(98.5 + 1.5 * R)  # alvo 1.5R com custos dentro do R, como a mesa
    assert t.meta["tier"] == "L2" and t.meta["oi_delta"] == -4.2 and t.meta["marketable"] is False
    assert e.day_signals == 1


def test_deadlines_count_from_the_candle_open_like_the_original(tmp_path, long_case):
    df, info = long_case
    t = (lambda e: (run(e, df, len(df) - 4), e.trade)[1])(make(tmp_path))
    cancel, valid = pd.Timestamp(t.meta["cancel_at"]), pd.Timestamp(t.meta["valid_until"])
    assert cancel == info["candle_open"] + pd.Timedelta(hours=2)  # 2h desde a ABERTURA da vela = só 1h depois do alerta
    assert valid == info["candle_open"] + pd.Timedelta(hours=4)
    assert cancel - info["close_ts"] == pd.Timedelta(hours=1)


def test_deadlines_can_count_from_the_alert(tmp_path, long_case):
    df, info = long_case
    e = make(tmp_path, sp=SweepParams(count_from_alert=True))
    run(e, df, len(df) - 4)
    assert pd.Timestamp(e.trade.meta["cancel_at"]) == info["close_ts"] + pd.Timedelta(hours=2)
    assert pd.Timestamp(e.trade.meta["valid_until"]) == info["close_ts"] + pd.Timedelta(hours=4)


def test_journal_row_has_the_desks_header_and_the_trigger_fields(tmp_path, long_case):
    df, _ = long_case
    e = make(tmp_path)
    run(e, df, len(df) - 4)
    with open(tmp_path / "j.csv", encoding="utf-8") as f:
        assert next(csv.reader(f)) == JOURNAL_HEADER  # concatenável com o journal do Sniper
    (r,) = rows(tmp_path / "j.csv")
    assert (r["ticker"], r["lado"], r["tier"], r["L0"], r["L1"], r["L2"]) == ("ZEC", "LONG", "L2", "1", "1", "1")
    assert (r["tomei_passei"], r["motor"], r["nivel"], r["oi_delta_pct"], r["oi_src"]) == ("LIMIT_PAPER", "CT-NIVEL-B", "98.500", "-4.20", "CG1H")
    assert r["data_utc"] == "2026-03-04T15:00:00Z" or r["data_utc"].endswith("Z")
    assert r["SKIP_R"] == "0" and r["validade_h"] == "4.0" and r["cancel_nofill_h"] == "2.0"
    assert float(r["entrada_A"]) == pytest.approx(99.9) and float(r["entrada_B"]) == pytest.approx(98.5)  # A: fecho; B: face


# ------------------------------------------------------------------ da ordem ao resultado
def follow(tmp_path, long_case, bars, **kw):
    df, info = long_case
    e = make(tmp_path, **kw)
    run(e, df, len(df) - 4)
    full = append_bars(df, bars)
    evs = run(e, full, len(df))
    return e, evs, full


def test_limit_waits_until_price_touches_the_face(tmp_path, long_case):
    e, evs, _ = follow(tmp_path, long_case, [(99.9, 100.2, 99.3, 99.6), (99.6, 100.0, 98.8, 99.4)])  # low 98.8 > 98.5
    assert kinds(evs) == [] and e.trade.state == "pending"


def test_fill_then_target_closes_everything_at_the_full_target(tmp_path, long_case):
    df, _ = long_case
    e = make(tmp_path)
    run(e, df, len(df) - 4)
    t = e.trade
    bars = [(99.9, 100.1, 98.4, 99.0),  # toca a face (98.5): fill
            (99.0, 99.8, 98.7, 99.5),   # nada
            (99.5, t.tp1 + 0.2, 99.3, t.tp1)]  # alvo
    evs = run(e, append_bars(df, bars), len(df))
    assert kinds(evs) == ["filled", "closed"]
    closed = evs[-1][1].trade
    assert (closed.exit_reason, closed.state) == ("target", "closed")
    assert closed.r_gross == pytest.approx(closed.tp1_r) and closed.tp1_r > 1.5  # 1.5 × (risco + custos)/risco
    assert closed.r_net == pytest.approx(closed.r_gross - closed.cost_r)
    assert e.trade is None and len(e.closed) == 1 and e.day_r == pytest.approx(closed.r_net)
    res = rows(tmp_path / "r.csv")
    assert list(res[0]) == RESULTS_HEADER and res[0]["estado"] == "FILLED" and res[0]["motivo_saida"] == "target"
    assert float(res[0]["R_liquido"]) == pytest.approx(closed.r_net, abs=1e-3)


def test_fill_then_stop(tmp_path, long_case):
    df, _ = long_case
    e = make(tmp_path)
    run(e, df, len(df) - 4)
    t = e.trade
    evs = run(e, append_bars(df, [(99.9, 100.1, 98.4, 99.0), (99.0, 99.2, t.stop - 0.1, t.stop)]), len(df))
    assert kinds(evs) == ["filled", "closed"] and evs[-1][1].trade.exit_reason == "stop"
    assert evs[-1][1].trade.r_gross == -1.0 and evs[-1][1].trade.r_net < -1.0  # custos por cima


def test_a_bar_that_touches_both_stop_and_target_counts_the_stop(tmp_path, long_case):
    df, _ = long_case
    e = make(tmp_path)
    run(e, df, len(df) - 4)
    t = e.trade
    evs = run(e, append_bars(df, [(99.9, 100.1, 98.4, 99.0), (99.0, t.tp1 + 1, t.stop - 1, 99.0)]), len(df))
    assert evs[-1][1].trade.exit_reason == "stop"


def test_fill_bar_that_also_reaches_the_stop_is_a_loss_and_never_a_win(tmp_path, long_case):
    df, _ = long_case
    e = make(tmp_path)
    run(e, df, len(df) - 4)
    t = e.trade
    evs = run(e, append_bars(df, [(99.9, t.tp1 + 5, t.stop - 0.5, 99.0)]), len(df))  # varre tudo na barra do fill
    assert kinds(evs) == ["filled", "closed"] and evs[-1][1].trade.exit_reason == "stop"


def test_unfilled_limit_is_cancelled_at_the_deadline_with_no_result(tmp_path, long_case):
    df, info = long_case
    e = make(tmp_path)
    run(e, df, len(df) - 4)
    cancel_at = pd.Timestamp(e.trade.meta["cancel_at"])
    quiet = [(99.9, 100.2, 99.4, 99.9)] * 8
    evs = run(e, append_bars(df, quiet), len(df))
    assert kinds(evs) == ["cancelled"]
    i_cancel = evs[0][0]
    assert append_bars(df, quiet).index[i_cancel] == cancel_at  # cancela na 1ª barra que abre em cancel_at
    assert e.trade is None and e.n_cancelled == 1 and e.closed == [] and e.day_r == 0
    assert rows(tmp_path / "r.csv")[0]["estado"] == "CANCEL_NOFILL"


def test_filled_trade_is_closed_at_market_when_validity_ends(tmp_path, long_case):
    df, _ = long_case
    e = make(tmp_path)
    run(e, df, len(df) - 4)
    t = e.trade
    valid = pd.Timestamp(t.meta["valid_until"])
    bars = [(99.9, 100.1, 98.4, 99.0)] + [(99.0, 99.6, 98.7, 99.2)] * 12  # fill e depois nada de relevante
    full = append_bars(df, bars)
    evs = run(e, full, len(df))
    assert kinds(evs) == ["filled", "closed"]
    closed = evs[-1][1].trade
    i_close = evs[-1][0]
    assert closed.exit_reason == "tempo" and full.index[i_close] + pd.Timedelta(minutes=15) == valid
    assert closed.r_gross == pytest.approx((99.2 - 98.5) / closed.risk)  # marca a mercado ao fecho da barra


def test_limit_already_on_the_wrong_side_of_the_market_fills_at_once_at_the_limit_price(tmp_path):
    hour = ((100.0, 100.3, 99.4, 99.6), (99.6, 99.7, 97.0, 97.4), (97.4, 99.2, 97.3, 98.8), (98.8, 99.4, 98.7, 99.0))
    df, _ = sweep_scenario(+1, level=99.2, hour=hour)  # nível 99.2, fecho 99.0: SEM reclaim
    e = make(tmp_path, levels=lambda: ({"below": [99.2], "above": [], "atualizado": None, "erro": None}, None))
    evs = run(e, df, len(df) - 4)
    assert kinds(evs) == ["sweep_signal", "filled"]
    t = e.trade
    assert t.state == "open" and t.meta["marketable"] is True and t.entry == 99.2  # pior caso: sem melhoria de preço


# ------------------------------------------------------------------ o que NÃO dá alerta
def test_missing_oi_gives_l1_no_alert_and_a_note(tmp_path, long_case):
    df, _ = long_case
    e = make(tmp_path, oi=None)
    evs = run(e, df, len(df) - 4)
    assert kinds(evs) == ["note"] and "sem OI" in evs[0][1].data["text"] and e.trade is None
    (r,) = rows(tmp_path / "j.csv")
    assert r["tier"] == "L1" and r["L2"] == "0" and r["resultado_R"].startswith("L1_NO_L2")


@pytest.mark.parametrize("oi,expect_alert", [(-3.0, True), (-2.99, False), (0.0, False), (-9.0, True)])
def test_oi_threshold(tmp_path, long_case, oi, expect_alert):
    df, _ = long_case
    e = make(tmp_path, oi=oi)
    assert ("sweep_signal" in kinds(run(e, df, len(df) - 4))) is expect_alert


def test_r_veto_blocks_the_alert_but_is_journaled(tmp_path):
    df, _ = sweep_scenario(+1, level=99.9)  # face longe do extremo (97.0): |limite-stop| > 1.2 ATR
    e = make(tmp_path, levels=lambda: ({"below": [99.9], "above": [], "atualizado": None, "erro": None}, None))
    assert kinds(run(e, df, len(df) - 4)) == [] and e.trade is None
    (r,) = rows(tmp_path / "j.csv")
    assert r["tier"] == "L2" and r["SKIP_R"] == "1" and r["resultado_R"].startswith("L2_SKIP_R")


def test_require_reclaim_really_blocks(tmp_path):
    hour = ((100.0, 100.3, 99.4, 99.6), (99.6, 99.7, 97.0, 97.4), (97.4, 99.2, 97.3, 98.8), (98.8, 99.4, 98.7, 99.0))
    df, _ = sweep_scenario(+1, level=99.2, hour=hour)
    e = make(tmp_path, levels=lambda: ({"below": [99.2], "above": [], "atualizado": None, "erro": None}, None),
             sp=SweepParams(require_reclaim=True))
    assert kinds(run(e, df, len(df) - 4)) == [] and rows(tmp_path / "j.csv")[0]["resultado_R"].startswith("L2_SEM_RECLAIM")


def test_only_one_active_order_at_a_time_and_pause_blocks_new_ones(tmp_path, long_case):
    df, _ = long_case
    e = make(tmp_path)
    run(e, df, len(df) - 4)
    first = e.trade
    e.last_eval = ""  # força a reavaliação da mesma hora, com a ordem ainda ativa
    assert kinds(run(e, df, len(df) - 1)) == [] and e.trade is first
    assert rows(tmp_path / "j.csv")[-1]["resultado_R"].startswith("L2_SKIP_BUSY")
    e2 = make(tmp_path / "p") if (tmp_path / "p").mkdir() is None else None
    e2.paused = True
    assert kinds(run(e2, df, len(df) - 4)) == [] and e2.trade is None


def test_no_levels_alerts_once_and_journals_every_hour(tmp_path, long_case):
    df, _ = long_case
    e = make(tmp_path, levels=lambda: (None, "niveis.csv ilegível: FileNotFoundError: niveis.csv"))
    evs = run(e, df, len(df) - 12)  # 3 horas fechadas
    assert kinds(evs) == ["note"] and "Sem níveis" in evs[0][1].data["text"]
    assert [r["resultado_R"][:16] for r in rows(tmp_path / "j.csv")] == ["SKIP_NO_CLUSTER "] * 3


def test_stale_levels_are_skipped_only_when_a_max_age_is_set(tmp_path, long_case):
    df, _ = long_case  # níveis de 2026-03-01 00:00, avaliados a ~4 dias
    assert kinds(run(make(tmp_path, niveis_max_age_h=0.0), df, len(df) - 4)) == ["sweep_signal"]  # 0 = nunca bloqueia
    p2 = tmp_path / "b"
    p2.mkdir()
    e = make(p2, niveis_max_age_h=24.0)
    assert kinds(run(e, df, len(df) - 4)) == [] and "SKIP_STALE_CLUSTER" in rows(p2 / "j.csv")[0]["resultado_R"]


def test_incomplete_hour_fails_closed(tmp_path, long_case):
    df, _ = long_case
    holed = df.drop(df.index[-3])  # falta uma barra de 15m dentro da hora varrida
    e = make(tmp_path)
    assert kinds(run(e, holed, len(holed) - 3)) == [] and e.trade is None
    assert "FAIL_CLOSED 1H" in rows(tmp_path / "j.csv")[0]["resultado_R"]


def test_evaluates_only_at_the_hour_close_and_only_once(tmp_path, long_case):
    df, _ = long_case
    e = make(tmp_path)
    run(e, df, len(df) - 8, len(df) - 1)  # não inclui a barra que fecha a hora varrida
    assert len(rows(tmp_path / "j.csv")) == 1  # só o fecho da hora anterior (n-4) foi avaliado
    run(e, df, len(df) - 1)
    run(e, df, len(df) - 1)  # repetir a mesma barra não repete a avaliação
    assert len(rows(tmp_path / "j.csv")) == 2 and e.day_signals == 1


def test_hours_missed_while_the_bot_was_down_are_journaled_not_signalled(tmp_path, long_case):
    df, _ = long_case
    e = make(tmp_path)
    assert kinds(run(e, df, len(df) - 4, evaluate=False)) == [] and e.trade is None
    (r,) = rows(tmp_path / "j.csv")
    assert r["resultado_R"].startswith("HORA_NAO_AVALIADA")


def test_short_side_full_cycle(tmp_path):
    df, _ = sweep_scenario(-1)
    e = make(tmp_path)
    assert kinds(run(e, df, len(df) - 4)) == ["sweep_signal"]
    t = e.trade
    assert (t.side, t.entry) == ("short", 101.5) and t.stop > t.entry > t.tp1
    bars = [(100.1, 101.6, 99.8, 101.0), (101.0, 101.1, t.tp1 - 0.3, t.tp1 - 0.2)]  # toca a face por cima, depois o alvo
    evs = run(e, append_bars(df, bars), len(df))
    assert kinds(evs) == ["filled", "closed"] and evs[-1][1].trade.exit_reason == "target"
    assert evs[-1][1].trade.r_net > 1.4


def test_state_roundtrip_keeps_the_pending_order(tmp_path, long_case):
    df, _ = long_case
    e = make(tmp_path)
    run(e, df, len(df) - 4)
    e.n_cancelled = 2
    e2 = make(tmp_path / "x") if (tmp_path / "x").mkdir() is None else None
    e2.load(e.to_dict())
    assert e2.to_dict() == e.to_dict() and e2.trade.state == "pending" and e2.trade.meta["level"] == 98.5
    assert e2.last_eval == e.last_eval and e2.n_cancelled == 2 and e2.trade.single_target


def test_the_other_strategies_are_unaffected_by_the_new_trade_fields():
    assert Engine(Config()).to_dict()["trade"] is None
