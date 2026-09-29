"""Estratégia de reversão: mercado carregado + preço esticado + candle de rejeição.

As regras vêm da forma como o utilizador opera:
  * short quando os longs estão sobrelotados (funding muito alto) e o preço está caro
  * long quando os shorts estão EXTREMAMENTE sobrelotados (funding muito negativo) e o preço está barato
  * nunca entrar só por "estar caro": exige o candle de rejeição
  * não abrir longs de tendência quando o preço já está caro
"""
import numpy as np
import pandas as pd
import pytest
from synthetic import crowd_frame, make_candles, reversal_scenario

from zec_bot.backtest import run_backtest
from zec_bot.config import Config
from zec_bot.derivs import funding_features
from zec_bot.engine import summarize
from zec_bot.strategy import Params, prepare, signal_at

REV = Params(mode="reversal")
CROWDED_LONG = dict(funding=0.08, pctl=97.0)
CROWDED_SHORT = dict(funding=-0.05, pctl=2.0)


def sig_at(df, k, deriv, p=REV):
    feat = prepare(df, p, deriv=deriv)
    return int(feat["sig"].iloc[k]), str(feat["kind"].iloc[k]), feat


# ------------------------------------------------------------------ o caso feliz
def test_short_reversal_when_longs_are_crowded_and_price_is_expensive():
    df, k = reversal_scenario(-1)
    s, kind, feat = sig_at(df, k, crowd_frame(df.index, **CROWDED_LONG))
    assert (s, kind) == (-1, "reversal")
    sig = signal_at(feat, k, REV)
    assert sig.side == "short" and sig.stop > sig.entry > sig.tp1 > sig.tp2
    assert (sig.tp1_r, sig.tp2_r) == (1.0, 2.0)  # alvos de reversão: mais curtos que os de tendência
    assert (sig.entry - sig.tp1) == pytest.approx(sig.risk) and (sig.entry - sig.tp2) == pytest.approx(2 * sig.risk)
    assert sig.htf > 0  # é contra a tendência de 1h, por desenho
    assert sig.info["funding_8h_pct"] == 0.08 and sig.info["funding_pctl"] == 97.0
    assert sig.info["stretch_atr"] >= REV.rev_ext_atr and sig.info["rsi_extreme"] >= REV.rev_rsi


def test_long_reversal_when_shorts_are_extremely_crowded_and_price_is_cheap():
    df, k = reversal_scenario(+1)
    s, kind, feat = sig_at(df, k, crowd_frame(df.index, **CROWDED_SHORT))
    assert (s, kind) == (1, "reversal")
    sig = signal_at(feat, k, REV)
    assert sig.side == "long" and sig.stop < sig.entry < sig.tp1 < sig.tp2 and sig.htf < 0


# ------------------------------------------------------------------ cada condição em falta anula o sinal
@pytest.mark.parametrize("name,kw", [
    ("funding neutro", dict(funding=0.01, pctl=50.0)),
    ("percentil alto mas funding abaixo do mínimo absoluto", dict(funding=0.02, pctl=99.0)),
    ("funding alto mas percentil baixo (é normal neste ativo)", dict(funding=0.08, pctl=60.0)),
    ("longs sobrelotados = só shorts; funding negativo não dá short", dict(funding=-0.05, pctl=2.0)),
])
def test_short_needs_crowded_longs(name, kw):
    df, k = reversal_scenario(-1)
    assert sig_at(df, k, crowd_frame(df.index, **kw))[0] == 0, name


@pytest.mark.parametrize("name,kw", [
    ("funding neutro", dict(funding=0.01, pctl=50.0)),
    ("shorts só um pouco carregados (P8): 'extremamente' exige P<=5", dict(funding=-0.05, pctl=8.0)),
    ("percentil extremo mas funding pouco negativo", dict(funding=-0.005, pctl=1.0)),
    ("longs sobrelotados não dão long", dict(funding=0.08, pctl=97.0)),
])
def test_long_needs_extremely_crowded_shorts(name, kw):
    df, k = reversal_scenario(+1)
    assert sig_at(df, k, crowd_frame(df.index, **kw))[0] == 0, name


def with_bar(df, k, s, o_off, c_off, up_off, dn_off):
    """Substitui o candle k. Offsets face ao fecho anterior P, no sentido do excesso (s=+1 excesso para cima).

    up_off = extremo do lado do excesso; dn_off = extremo oposto (distância abaixo/acima de P).
    """
    df = df.copy()
    P = df["close"].iloc[k - 1]
    hi, lo = (P + up_off, P - dn_off) if s > 0 else (P + dn_off, P - up_off)
    for col, val in (("open", P + s * o_off), ("close", P + s * c_off), ("high", hi), ("low", lo)):
        df.iloc[k, df.columns.get_loc(col)] = val
    return df


# Cada caso viola UMA só parte do gatilho (o resto do setup está perfeito) e por isso tem de dar zero sinais.
TRIGGER_CASES = {
    "candle na direção do excesso (não é de rejeição), mesmo com pavio e RSI a virar":
        dict(o_off=-0.30, c_off=-0.15, up_off=0.30, dn_off=0.32),
    "candle certo mas sem pavio de rejeição nem rompimento da mínima anterior":
        dict(o_off=0.10, c_off=-0.20, up_off=0.11, dn_off=0.21),
    "candle de rejeição mas o RSI ainda não virou (fecha acima do fecho anterior)":
        dict(o_off=0.30, c_off=0.10, up_off=0.60, dn_off=0.12),
}


@pytest.mark.parametrize("direction", [-1, +1])
@pytest.mark.parametrize("case", list(TRIGGER_CASES))
def test_each_part_of_the_rejection_trigger_is_required(direction, case):
    df, k = reversal_scenario(direction)
    crowd = crowd_frame(df.index, **(CROWDED_LONG if direction < 0 else CROWDED_SHORT))
    assert sig_at(df, k, crowd)[0] == direction  # controlo positivo: o setup completo dispara
    broken = with_bar(df, k, s=-direction, **TRIGGER_CASES[case])  # s = sentido do excesso
    assert sig_at(broken, k, crowd)[0] == 0


@pytest.mark.parametrize("crowd,side", [(CROWDED_LONG, "short"), (CROWDED_SHORT, "long")])
def test_every_reversal_signal_satisfies_all_conditions(crowd, side):
    """Propriedade sobre ~12000 barras aleatórias com crowding sempre ligado: nenhum sinal escapa às condições."""
    seen = 0
    for seed in (1, 2):
        df = make_candles(6000, seed=seed)
        feat = prepare(df, REV, deriv=crowd_frame(df.index, **crowd))
        rev = feat[feat["kind"] == "reversal"]
        seen += len(rev)
        if side == "short":
            assert (rev["sig"] == -1).all() and (rev["close"] < rev["open"]).all()
            assert (rev["stretch_up"] >= REV.rev_ext_atr).all() and (rev["rsi_hi3"] >= REV.rev_rsi).all()
        else:
            assert (rev["sig"] == 1).all() and (rev["close"] > rev["open"]).all()
            assert (rev["stretch_dn"] >= REV.rev_ext_atr).all() and (rev["rsi_lo3"] <= 100 - REV.rev_rsi).all()
        assert (rev["risk"] <= REV.rev_stop_max_atr * rev["atr"] + 1e-9).all()
        assert (rev["risk"] / rev["close"] * 100 >= REV.min_risk_pct).all()
    assert seen > 0, "esperava alguns sinais de reversão nos dados aleatórios"


def test_stretch_without_crowding_or_crowding_without_stretch_gives_nothing():
    df = make_candles(6000, seed=1)
    assert (prepare(df, REV, deriv=crowd_frame(df.index, funding=0.01, pctl=50.0))["kind"] == "reversal").sum() == 0
    # crowding em todas as barras mas o mercado calmo (sem esticão): só passam barras que realmente estão esticadas
    feat = prepare(df, REV, deriv=crowd_frame(df.index, **CROWDED_LONG))
    calm = feat[feat["stretch_up"] < REV.rev_ext_atr]
    assert (calm["kind"] == "reversal").sum() == 0


def test_no_reversal_without_derivatives_data_or_in_trend_mode():
    df, k = reversal_scenario(-1)
    crowded = crowd_frame(df.index, **CROWDED_LONG)
    assert sig_at(df, k, None)[0] == 0  # sem funding não inventa crowding
    assert (prepare(df, REV, deriv=None)["kind"] == "reversal").sum() == 0
    assert (prepare(df, Params(mode="trend"), deriv=crowded)["kind"] == "reversal").sum() == 0  # modo tendência ignora-o


def test_oi_requirement_is_optional_and_never_guessed():
    df, k = reversal_scenario(-1)
    with_oi = Params(mode="reversal", rev_use_oi=True, rev_oi_min_pct=3.0)
    assert sig_at(df, k, crowd_frame(df.index, oi_rev=5.0, **CROWDED_LONG), with_oi)[0] == -1  # OI +5% em 24h
    assert sig_at(df, k, crowd_frame(df.index, oi_rev=1.0, **CROWDED_LONG), with_oi)[0] == 0  # não acumulou
    assert sig_at(df, k, crowd_frame(df.index, oi_rev=np.nan, **CROWDED_LONG), with_oi)[0] == 0  # sem dados: sem sinal
    assert sig_at(df, k, crowd_frame(df.index, oi_rev=1.0, **CROWDED_LONG), REV)[0] == -1  # desligado: ignora o OI


def test_stop_too_far_beyond_the_swing_is_skipped():
    df, k = reversal_scenario(-1)
    df = df.copy()
    df.iloc[k - 3, df.columns.get_loc("high")] += 3.0  # um pico enorme há 3 barras: o stop teria de ir lá para cima
    assert sig_at(df, k, crowd_frame(df.index, **CROWDED_LONG))[0] == 0


# ------------------------------------------------------------------ "evito long quando já está caro"
def test_expensive_longs_are_avoided_in_trend_mode():
    """Na estratégia de tendência, com a regra ligada nenhum long abre com o preço caro (>2 ATR acima da EMA55 ou RSI>=70)."""
    df = make_candles(6000)
    off = prepare(df, Params(mode="trend"))
    on = prepare(df, Params(mode="trend", avoid_expensive_longs=True))
    longs_off, longs_on = off[off["sig"] > 0], on[on["sig"] > 0]
    assert len(longs_on) < len(longs_off)  # bloqueou alguns
    ext = (longs_on["close"] - longs_on["ema_s"]) / longs_on["atr"]
    assert (ext < 2.0).all() and (longs_on["rsi"] < 70).all()
    pd.testing.assert_frame_equal(off[off["sig"] < 0][["sig", "kind"]], on[on["sig"] < 0][["sig", "kind"]])  # shorts intactos


def test_reversal_mode_has_no_trend_signals():
    df = make_candles(6000)
    feat = prepare(df, Params(mode="reversal"), deriv=crowd_frame(df.index))
    assert (feat["sig"] == 0).all()


# ------------------------------------------------------------------ sem espreitar o futuro
def test_reversal_signal_does_not_change_with_future_data():
    df, k = reversal_scenario(-1)
    deriv = crowd_frame(df.index, **CROWDED_LONG)
    full = prepare(df, REV, deriv=deriv).iloc[k]
    cut = prepare(df.iloc[: k + 1], REV, deriv=deriv.iloc[: k + 1]).iloc[-1]
    for col in ("sig", "risk", "stretch_up", "rsi_hi3", "tp1_r"):
        assert cut[col] == pytest.approx(full[col]), col


# ------------------------------------------------------------------ controlo: sem edge em dados aleatórios
def _random_market(seed, n=8000):
    r = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    rets = 0.006 * r.standard_normal(n)
    close = 50 * np.exp(np.cumsum(rets))
    open_ = np.r_[close[0], close[:-1]]
    w = np.abs(r.standard_normal((2, n))) * 0.004 * close
    df = pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + w[0], "low": np.minimum(open_, close) - w[1],
                       "close": close, "volume": 1000 * np.exp(0.4 * r.standard_normal(n))}, index=idx)
    # funding INDEPENDENTE do preço: um registo de 8h com valores aleatórios (a maior parte perto da base 0.01%)
    times = pd.date_range(idx[0] - pd.Timedelta(days=40), idx[-1], freq="8h")
    rates = (0.0001 + 0.0003 * r.standard_t(3, len(times)) * 0.5)
    rows = [[int(t.timestamp() * 1000), float(x)] for t, x in zip(times, rates)]
    return df, funding_features(rows, idx, 15, 30.0)


def test_reversal_has_no_edge_when_funding_is_unrelated_to_price():
    """Se o funding não tem informação sobre o preço, a reversão tem de perder (custos) e nunca ganhar 'de graça'."""
    cfg = Config(strategy="reversal", max_signals_per_day=10, cooldown_bars=0)
    trades = []
    for seed in range(25):
        df, frame = _random_market(seed)
        frame["oi_change_pct"] = np.nan
        frame["oi_rev_pct"] = np.nan
        t, _ = run_backtest(df, cfg, Params.from_cfg(cfg), deriv=frame)
        trades += t
    s = summarize(trades)
    gross = np.mean([t["r_gross"] for t in trades])
    assert s["n"] >= 300, f"amostra pequena demais para o controlo ({s['n']})"
    assert {t["kind"] for t in trades} == {"reversal"} and {t["side"] for t in trades} == {"long", "short"}
    # medido: R bruto médio -0.11 (erro-padrão 0.04), -0.22R por trade após custos, profit factor 0.60
    assert gross < 0.05, f"R bruto médio {gross:.3f} sem informação real é suspeito (lookahead?)"
    assert s["total_r"] < 0 and s["profit_factor"] < 1.0
