"""Sweep de nível: o port tem de dar EXATAMENTE os números do hourly_scan.py da mesa.

`tests/data/sweep_golden.json` contém ~430 casos (velas, níveis, ATR e OI aleatórios + casos-limite exatos)
com as saídas geradas pelo evaluate()/atr14()/parse_levels() do código original da mesa.
"""
import json
import math
from pathlib import Path

import pandas as pd
import pytest

from zec_bot import sweep
from zec_bot.sweep import SweepParams, evaluate

GOLDEN = json.loads((Path(__file__).parent / "data" / "sweep_golden.json").read_text())


def assert_same(a, b, path=""):
    if isinstance(b, dict):
        assert isinstance(a, dict) and set(a) == set(b), f"{path}: chaves {sorted(set(a) ^ set(b))}"
        for k in b:
            assert_same(a[k], b[k], f"{path}.{k}")
    elif isinstance(b, float) and a is not None:
        assert a == pytest.approx(b, rel=1e-12, abs=1e-12), f"{path}: {a} != {b}"
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


def test_evaluate_matches_the_original_on_every_golden_case():
    for n, case in enumerate(GOLDEN["evaluate"]):
        got = evaluate(case["cluster"], case["candle"], case["atr"], case["oi"])
        assert_same(got, case["out"], f"caso {n}")


def test_golden_set_is_meaningful():
    """Se o ficheiro de referência degenerasse (tudo L0, um só lado...), o teste acima já não provava nada."""
    events = [e for c in GOLDEN["evaluate"] for e in c["out"]]
    assert len(GOLDEN["evaluate"]) >= 400
    assert {e.get("tier") for e in events} >= {"", "L0", "L1", "L2"}
    valid = [e for e in events if e.get("valido")]
    assert {e["lado"] for e in valid} == {"LONG", "SHORT"} and len(valid) >= 20
    assert any(e.get("skip_r") for e in events) and any(e.get("tier") == "L2" and e.get("skip_r") for e in events)
    assert any(not e["reclaim"] for e in valid), "tem de haver L2 válido sem reclaim (regra da mesa)"


def test_atr14_matches_the_original():
    for n, case in enumerate(GOLDEN["atr14"]):
        assert_same(sweep.atr14(case["candles"]), case["atr"], f"atr {n}")
    assert any(c["atr"] is None for c in GOLDEN["atr14"]) and any(c["atr"] is not None for c in GOLDEN["atr14"])


def test_parse_levels_matches_the_original():
    for case in GOLDEN["parse_levels"]:
        assert sweep.parse_levels(case["cell"]) == case["out"]


# ------------------------------------------------------------------ regras, com números à mão (os do selftest da mesa)
ATR = 10.0
CL = {"below": [100.0], "above": [120.0]}
LONG_OK = {"t": 0, "open": 104.0, "high": 105.0, "low": 95.0, "close": 104.5}


def test_long_l2_uses_the_face_of_the_level_and_extreme_stop():
    e = evaluate(CL, LONG_OK, ATR, -4.0)[0]
    assert e["valido"] and e["lado"] == "LONG" and e["tier"] == "L2"
    assert e["entrada"] == pytest.approx(100.0)  # a FACE do nível, não o fecho
    assert e["stop"] == pytest.approx(95.0 - 0.1 * ATR)  # extremo da vela - 0.1 ATR
    assert e["alvo"] == pytest.approx(100.0 + 1.5 * e["R"])
    assert e["R"] == pytest.approx((100.0 - e["stop"]) + 100.0 * 0.00035 * 2)  # o R inclui os custos
    assert e["plan_a"]["entrada"] == pytest.approx(104.5) and e["plan_a"]["alvo_r"] == 2.0


def test_oi_threshold_is_inclusive_and_missing_oi_never_gives_l2():
    assert evaluate(CL, LONG_OK, ATR, -3.0)[0]["tier"] == "L2"  # <= -3.0
    assert evaluate(CL, LONG_OK, ATR, -2.9)[0]["tier"] == "L1"
    assert evaluate(CL, LONG_OK, ATR, None)[0]["tier"] == "L1" and not evaluate(CL, LONG_OK, ATR, None)[0]["valido"]


def test_r_veto():
    deep = {"t": 0, "open": 104.0, "high": 105.0, "low": 90.0, "close": 104.0}
    e = evaluate(CL, deep, 2.0, -5.0)[0]
    assert e["tier"] == "L2" and e["skip_r"] and not e["valido"] and "L2_SKIP_R" in e["motivo"]


def test_weak_wick_is_only_l0_and_doji_is_ignored():
    weak = {"t": 0, "open": 101.0, "high": 110.0, "low": 99.0, "close": 109.0}
    assert evaluate(CL, weak, ATR, -5.0)[0]["tier"] == "L0"
    assert evaluate(CL, {"t": 0, "open": 1, "high": 1, "low": 1, "close": 1}, 1, -5)[0]["motivo"] == "doji"


def test_short_mirror():
    short_ok = {"t": 0, "open": 116.0, "high": 125.0, "low": 115.0, "close": 115.5}
    e = evaluate(CL, short_ok, ATR, -5.0)[0]
    assert e["valido"] and e["lado"] == "SHORT" and e["entrada"] == pytest.approx(120.0)
    assert e["stop"] == pytest.approx(125.0 + 0.1 * ATR) and e["alvo"] < e["entrada"]


# ------------------------------------------------------------------ onde eu me afasto do original (de propósito)
NO_RECLAIM = {"t": 0, "open": 104.0, "high": 105.0, "low": 95.0, "close": 104.5}  # fecha ABAIXO do nível 106
CL106 = {"below": [106.0], "above": []}


def test_original_rule_allows_a_sweep_without_reclaim():
    e = evaluate(CL106, NO_RECLAIM, ATR, -4.0)[0]
    assert e["valido"] and not e["reclaim"]
    assert sweep.is_marketable("LONG", e["entrada"], NO_RECLAIM["close"])  # comprar a 106 com o mercado a 104.5


def test_require_reclaim_actually_blocks_here():
    """No original, ligar SWEEP_EXIGE_RECLAIM só acrescentava 'sem_reclaim' ao texto e o sinal continuava válido."""
    e = evaluate(CL106, NO_RECLAIM, ATR, -4.0, SweepParams(require_reclaim=True))[0]
    assert not e["valido"] and "sem_reclaim" in e["motivo"] and e["tier"] == "L2"
    ok = evaluate(CL, LONG_OK, ATR, -4.0, SweepParams(require_reclaim=True))[0]
    assert ok["valido"]  # com reclaim, passa


def test_marketable_helper():
    assert sweep.is_marketable("LONG", 106.0, 104.5) and not sweep.is_marketable("LONG", 100.0, 104.5)
    assert sweep.is_marketable("SHORT", 100.0, 104.5) and not sweep.is_marketable("SHORT", 120.0, 104.5)


def test_face_depends_on_list_order_exactly_like_the_original():
    candle = {"t": 0, "open": 104.0, "high": 105.0, "low": 95.0, "close": 104.5}
    assert evaluate({"below": [100.0, 98.0], "above": []}, candle, ATR, -4.0)[0]["level"] == 100.0
    assert evaluate({"below": [98.0, 100.0], "above": []}, candle, ATR, -4.0)[0]["level"] == 98.0


def test_pick_event_prefers_the_best_tier_then_long():
    both = {"below": [95.0], "above": [105.0]}
    outside = {"t": 0, "open": 100.0, "high": 106.0, "low": 94.0, "close": 100.0}
    evs = evaluate(both, outside, 5.0, -4.0)
    assert {e["lado"] for e in evs} == {"LONG", "SHORT"}
    assert sweep.pick_event(evs)["lado"] == "LONG" and sweep.best_tier(evs) == "L2"
    assert sweep.pick_event([{"lado": "", "tier": ""}]) is None


# ------------------------------------------------------------------ velas 1H a partir de barras de 15m e níveis
def bars15(start, rows):
    idx = pd.date_range(start, periods=len(rows), freq="15min", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1.0)


def test_hourly_candles_aggregate_complete_hours_only():
    rows = [(100 + k, 101 + k, 99 + k, 100.5 + k) for k in range(10)]  # 10 barras: 2 horas completas + 2 barras soltas
    f = bars15("2026-03-01 00:00", rows)
    cs = sweep.hourly_candles(f, 15)
    assert len(cs) == 2
    h0 = cs[0]
    assert h0["open"] == 100 and h0["high"] == 104 and h0["low"] == 99 and h0["close"] == 103.5
    assert h0["t"] == int(pd.Timestamp("2026-03-01 00:00", tz="UTC").timestamp() * 1000)


def test_hourly_candles_skip_hours_with_a_missing_bar():
    f = bars15("2026-03-01 00:00", [(1, 2, 0, 1)] * 8).drop(pd.Timestamp("2026-03-01 00:30", tz="UTC"))
    assert len(sweep.hourly_candles(f, 15)) == 1  # a hora 00:00 tem uma barra em falta: não conta


def test_load_levels(tmp_path):
    p = tmp_path / "niveis.csv"
    p.write_text("ticker,cluster_below,cluster_above,spot_approx,atualizado_utc\n"
                 "BTC,60000|59000,62000,60500,2026-03-01T10:00:00Z\n"
                 "zec,49.5;48,55.2|56,52.1,2026-03-01T10:00:00Z\n"
                 "SOL,abc,1,2,3\n")
    cl, err = sweep.load_levels(p, "ZEC")
    assert err is None and cl["below"] == [49.5, 48.0] and cl["above"] == [55.2, 56.0] and cl["spot_approx"] == 52.1
    age = sweep.level_age_h(cl, pd.Timestamp("2026-03-01 12:30", tz="UTC").to_pydatetime())
    assert age == pytest.approx(2.5)
    bad, err = sweep.load_levels(p, "SOL")  # uma linha má só estraga esse ativo
    assert err is None and bad["erro"].startswith("parse") and bad["below"] == []
    assert sweep.load_levels(p, "ETH")[0] is None and "sem linha" in sweep.load_levels(p, "ETH")[1]
    assert sweep.load_levels(tmp_path / "nada.csv", "ZEC")[0] is None
    assert sweep.level_age_h({"atualizado": ""}, pd.Timestamp.now(tz="UTC").to_pydatetime()) is None


def test_sweep_params_defaults_agree_with_the_config_and_with_the_desks_constants():
    """Os defaults vivem em dois sítios (SweepParams e Config): não podem divergir. E são os da mesa."""
    from dataclasses import asdict

    from zec_bot.config import Config
    assert asdict(SweepParams.from_cfg(Config())) == pytest.approx(asdict(SweepParams()), rel=1e-12)
    sp = SweepParams()
    assert (sp.oi_max_pct, sp.wick_min_pct, sp.stop_atr, sp.target_r_b, sp.target_r_a) == (-3.0, 50.0, 0.1, 1.5, 2.0)
    assert (sp.r_max_atr, sp.valid_h, sp.cancel_h, sp.require_reclaim, sp.count_from_alert) == (1.2, 4.0, 2.0, False, False)
    assert sp.cost_side == pytest.approx(0.00015 + 0.0002)  # MAKER + SLIP
