import numpy as np
import pandas as pd
import pytest

from fakes import Clock, FakeBinance

from zec_bot.binance_futures import BinanceError
from zec_bot.config import Config
from zec_bot.derivs import (FUNDING_TTL_S, OI_TTL_S, STALE_FUNDING_S, DerivsMonitor, build_frame, funding_8h_pct,
                            funding_features, interpret, oi_change_series)

from fakes import H, M15

T0 = int(pd.Timestamp("2026-03-01 00:00", tz="UTC").timestamp() * 1000)


def bars(start="2026-03-10 00:00", n=96):
    return pd.date_range(start, periods=n, freq="15min", tz="UTC")


def funding_rows(n, step_h=8, rate=lambda k: 0.0001, start=T0):
    return [[start + k * step_h * H, rate(k)] for k in range(n)]


# ------------------------------------------------------------------ conversões e leitura
def test_funding_units():
    assert funding_8h_pct(0.0000125, 1.0) == pytest.approx(0.01)  # Hyperliquid: 0.01%/8h = taxa de juro base
    assert funding_8h_pct(0.0001, 8.0) == pytest.approx(0.01)  # Binance normal: 0.01%/8h
    assert funding_8h_pct(0.00005, 4.0) == pytest.approx(0.01)  # intervalo de 4h
    assert funding_8h_pct(-0.0005, 8.0) == pytest.approx(-0.05)


@pytest.mark.parametrize("px,oi,expected", [
    (1.0, 2.0, "entram longs"), (1.0, -2.0, "fecho de shorts"), (-1.0, 2.0, "entram shorts"),
    (-1.0, -2.0, "fecho de longs"), (1.0, 0.1, "OI estável"), (0.0, 3.0, "acumular"), (0.0, -3.0, "fechar"),
])
def test_interpret(px, oi, expected):
    assert expected in interpret(px, oi)


def test_interpret_without_data():
    assert interpret(None, 1.0) is None and interpret(1.0, None) is None


# ------------------------------------------------------------------ funding_features
def test_funding_percentile_and_units():
    rows = funding_rows(60, rate=lambda k: 0.0001 + k * 1e-6)  # funding sempre a subir
    idx = bars("2026-03-10 00:00", 96)  # 9 dias depois do início: já há >20 registos
    f = funding_features(rows, idx, 15, 30.0)
    close = idx[-1] + pd.Timedelta(minutes=15)
    known = [k for k, r in enumerate(rows) if pd.Timestamp(r[0], unit="ms", tz="UTC") <= close][-1]
    assert f["funding_8h_pct"].iloc[-1] == pytest.approx((0.0001 + known * 1e-6) * 100)
    assert f["funding_pctl"].iloc[-1] == 100.0  # é o mais alto de todos os registos da janela


def test_percentile_is_nan_until_enough_history():
    rows = funding_rows(60)  # taxa constante
    f = funding_features(rows, bars("2026-03-01 00:00", 96 * 8), 15, 30.0, min_records=20)
    # o 20º registo (k=19) é das 08:00 do dia 7; a barra das 07:45 fecha exatamente aí
    assert f["funding_pctl"].first_valid_index() == pd.Timestamp("2026-03-07 07:45", tz="UTC")
    assert f["funding_pctl"].dropna().eq(100.0).all()  # sem variação, todos empatam (<=): percentil 100


def test_percentile_of_an_intermediate_value():
    cycle = [0.0002, 0.0003, 0.0001]
    rows = funding_rows(41, rate=lambda k: cycle[k % 3] if k < 40 else 0.00025)
    f = funding_features(rows, bars("2026-03-15 08:00", 4), 15, 30.0)
    # dos 41 registos da janela, 28 são <= 0.00025 (14 x 0.0002, 13 x 0.0001 e ele próprio)
    assert f["funding_pctl"].iloc[-1] == pytest.approx(28 / 41 * 100)


def test_interval_change_is_normalised_to_8h():
    rows = funding_rows(30, 8) + funding_rows(30, 4, start=T0 + 30 * 8 * H)  # passa de 8h para 4h a meio
    idx = bars("2026-03-01 00:00", 96 * 22)
    f = funding_features(rows, idx, 15, 30.0)
    at = lambda ts: f.loc[pd.Timestamp(ts, tz="UTC"), "funding_8h_pct"]
    assert at("2026-03-05 00:00") == pytest.approx(0.01)  # intervalo de 8h: 0.0001 -> 0.01%/8h
    assert at("2026-03-20 00:00") == pytest.approx(0.02)  # intervalo de 4h: o dobro por 8h


def test_funding_features_only_use_records_known_at_bar_close():
    hour = 3_600_000
    base = int(pd.Timestamp("2026-03-01 00:00", tz="UTC").timestamp() * 1000)
    rows = [[base + h * 8 * hour + 76, 0.0001 * (h + 1)] for h in range(30)]  # registo a 00:00:00.076, 08:00:00.076...
    idx = bars("2026-03-08 07:00", 8)
    f = funding_features(rows, idx, 15, 30.0, min_records=1)["funding_8h_pct"]
    t = lambda hhmm: pd.Timestamp(f"2026-03-08 {hhmm}", tz="UTC")
    # a barra 07:45 fecha às 08:00:00.000, ANTES do registo das 08:00:00.076 -> ainda vê o das 00:00
    assert f[t("07:45")] == pytest.approx(0.0001 * 22 * 100)
    assert f[t("08:00")] == pytest.approx(0.0001 * 23 * 100)  # fecha às 08:15 -> já vê o das 08:00


def test_funding_features_do_not_change_when_future_records_change():
    rows = funding_rows(80, rate=lambda k: 0.0001 + (k % 7) * 1e-5)
    idx = bars("2026-03-12 00:00", 96)
    cut = int(pd.Timestamp("2026-03-12 12:00", tz="UTC").timestamp() * 1000)
    tampered = [r if r[0] <= cut else [r[0], 0.5] for r in rows]  # futuro absurdo
    a, b = funding_features(rows, idx, 15, 30.0), funding_features(tampered, idx, 15, 30.0)
    upto = pd.Timestamp("2026-03-12 11:45", tz="UTC")  # barras que fecham antes de 12:00
    pd.testing.assert_frame_equal(a[:upto], b[:upto])


def test_percentile_window_forgets_old_extremes():
    rows = funding_rows(150, rate=lambda k: 0.05 if k == 0 else 0.0001)  # um extremo enorme no dia 0
    rows.append([rows[-1][0] + 8 * H, 0.00015])  # depois de 50 dias: ligeiramente acima do "normal"
    idx = pd.DatetimeIndex([pd.Timestamp(rows[-1][0], unit="ms", tz="UTC") + pd.Timedelta(minutes=15)])
    f = funding_features(rows, idx, 15, 30.0)
    assert f["funding_pctl"].iloc[0] == 100.0  # o extremo do dia 0 já saiu da janela de 30 dias


def test_no_funding_rows_gives_nan():
    f = funding_features([], bars(n=4), 15)
    assert list(f.columns) == ["funding_8h_pct", "funding_pctl"] and f.isna().all().all()


# ------------------------------------------------------------------ oi_change_series
def oi_rows_15m(start, n, first=1000.0, inc=10.0):
    return [[start + k * M15, first + k * inc, (first + k * inc) * 50] for k in range(n)]


def test_oi_change_alignment_and_coverage():
    s0 = int(pd.Timestamp("2026-03-01 00:00", tz="UTC").timestamp() * 1000)
    rows = oi_rows_15m(s0, 16)  # 00:00 .. 03:45
    idx = bars("2026-03-01 00:00", 24)
    s = oi_change_series(rows, idx, 15, hours=1.0)
    t = lambda hhmm: pd.Timestamp(f"2026-03-01 {hhmm}", tz="UTC")
    assert s[:t("00:45")].isna().all()  # ainda sem 1h de histórico
    assert s[t("01:00")] == pytest.approx((1040 / 1000 - 1) * 100)  # valor das 01:00 vs o das 00:00
    assert s[t("03:45")] == pytest.approx((1150 / 1110 - 1) * 100)
    # o último valor (03:45) só fica disponível às 04:00; tolera-se 2 períodos de atraso (até ao fecho das 04:30)
    assert s[t("04:15")] == pytest.approx((1150 / 1130 - 1) * 100)
    assert s[t("04:30"):].isna().all()  # mais atrasado que isso: não inventa nada


def test_oi_change_does_not_look_ahead():
    s0 = int(pd.Timestamp("2026-03-01 00:00", tz="UTC").timestamp() * 1000)
    rows = oi_rows_15m(s0, 16)
    idx = bars("2026-03-01 00:00", 16)
    cutoff = s0 + 2 * H
    tampered = [r if r[0] <= cutoff else [r[0], r[1] * 5, r[2]] for r in rows]
    a, b = oi_change_series(rows, idx, 15, 1.0), oi_change_series(tampered, idx, 15, 1.0)
    upto = pd.Timestamp("2026-03-01 01:45", tz="UTC")  # barras que fecham antes de o valor alterado existir
    pd.testing.assert_series_equal(a[:upto], b[:upto])


def test_oi_change_without_rows_is_nan():
    assert oi_change_series([], bars(n=4), 15, 1.0).isna().all()


def test_build_frame_columns_and_values():
    cfg = Config(oi_lookback_hours=1.0, rev_oi_hours=2.0)
    s0 = int(pd.Timestamp("2026-03-10 00:00", tz="UTC").timestamp() * 1000)
    fr = build_frame(bars("2026-03-10 06:00", 12), funding_rows(60), oi_rows_15m(s0, 96), cfg, 30.0)
    assert list(fr.columns) == ["funding_8h_pct", "funding_pctl", "oi_change_pct", "oi_rev_pct"]
    last = fr.iloc[-1]  # barra das 08:45, fecha às 09:00
    assert last["oi_change_pct"] == pytest.approx((1350 / 1310 - 1) * 100)  # valor das 08:45 vs o das 07:45
    assert last["oi_rev_pct"] == pytest.approx((1350 / 1270 - 1) * 100)  # janela de 2h (REV_OI_HOURS)
    assert last["funding_8h_pct"] == pytest.approx(0.01) and last["funding_pctl"] == 100.0


# ------------------------------------------------------------------ DerivsMonitor (Binance simulada)
NOW = 1_780_000_000.0


def monitor(fake=None, venue=None, **cfg):
    clock = Clock(NOW)
    fake = fake or FakeBinance(NOW)
    v = venue if venue is not None else (lambda: {"funding_hr": 0.0000125, "max_leverage": 5.0})
    return DerivsMonitor(Config(**cfg), binance=fake, venue=v, now=clock), fake, clock


def index_at(now=NOW, n=4):
    end = pd.Timestamp(now, unit="s", tz="UTC").floor("15min")
    return pd.date_range(end=end - pd.Timedelta(minutes=15), periods=n, freq="15min")


def test_frame_builds_features_and_caches():
    m, fake, clock = monitor()
    fr = m.frame(index_at())
    assert fr is not None and fr["funding_pctl"].notna().all() and fr["oi_rev_pct"].notna().all()
    m.frame(index_at())
    assert fake.calls["funding"] == 1 and fake.calls["oi_hist"] == 1  # a 2ª chamada usou a cache
    clock.t += OI_TTL_S + 1
    m.frame(index_at(clock.t))
    assert fake.calls["oi_hist"] == 2 and fake.calls["funding"] == 1  # o OI renova depressa; o funding não
    clock.t += FUNDING_TTL_S
    m.frame(index_at(clock.t))
    assert fake.calls["funding"] == 2


def test_frame_is_none_without_funding_and_recovers():
    fake = FakeBinance(NOW)
    fake.fail.add("funding")
    m, _, _ = monitor(fake)
    assert m.frame(index_at()) is None and "funding em baixo" in m.last_error
    fake.fail.clear()
    assert m.frame(index_at()) is not None


def test_stale_funding_cache_is_used_for_a_while_then_dropped():
    fake = FakeBinance(NOW)
    m, _, clock = monitor(fake)
    assert m.frame(index_at()) is not None
    fake.fail.add("funding")
    clock.t += FUNDING_TTL_S + 60  # cache expirada mas com <6h: o funding muda devagar, ainda serve
    assert m.frame(index_at(clock.t)) is not None
    clock.t += STALE_FUNDING_S  # agora com >6h sem atualizar: já não
    assert m.frame(index_at(clock.t)) is None


def test_oi_failure_keeps_funding_features():
    fake = FakeBinance(NOW)
    fake.fail.add("oi_hist")
    m, _, _ = monitor(fake)
    fr = m.frame(index_at())
    assert fr is not None and fr["funding_pctl"].notna().all() and fr["oi_rev_pct"].isna().all()


def test_snapshot_numbers():
    m, fake, _ = monitor(FakeBinance(NOW, funding_last=0.0003, mark=50.0, index=49.9, oi_now=2000.0))
    s = m.snapshot(price_change_1h_pct=1.2)
    assert s["funding_8h_pct"] == pytest.approx(0.03)  # 0.0003 por 8h
    assert s["premium_pct"] == pytest.approx((50.0 / 49.9 - 1) * 100)
    assert s["oi_coins"] == 2000.0 and s["oi_usd"] == pytest.approx(100_000.0)
    # 1h: último valor de OI já disponível há 1h = linha k=379 (1379); 2000/1379 - 1
    assert s["oi_change_1h_pct"] == pytest.approx((2000 / 1379 - 1) * 100)
    assert s["oi_change_4h_pct"] == pytest.approx((2000 / (1000 + 384 - 17) - 1) * 100)
    assert s["oi_rev_pct"] == pytest.approx((2000 / (1000 + 384 - 97) - 1) * 100)  # 24h
    assert s["hl_funding_8h_pct"] == pytest.approx(0.01) and s["max_leverage"] == 5.0
    assert "entram longs" in s["reading"] and s["source"] == "binance"


def test_snapshot_uses_the_real_funding_interval():
    m, _, _ = monitor(FakeBinance(NOW, step_h=4, funding_last=0.0001))
    assert m.snapshot()["funding_8h_pct"] == pytest.approx(0.02)  # 0.0001 por 4h = 0.02%/8h


def test_snapshot_passes_the_percentile_through():
    m, _, _ = monitor()
    row = pd.Series({"funding_pctl": 96.4, "funding_8h_pct": 0.05})
    assert m.snapshot(frame_row=row)["funding_pctl"] == 96.4
    assert m.snapshot(frame_row=pd.Series({"funding_pctl": np.nan}))["funding_pctl"] is None
    assert m.snapshot()["funding_pctl"] is None


def test_snapshot_survives_a_dead_hyperliquid_but_not_a_dead_binance():
    def boom():
        raise RuntimeError("HL em baixo")

    m, fake, _ = monitor(venue=boom)
    s = m.snapshot()
    assert s is not None and s["hl_funding_8h_pct"] is None and s["max_leverage"] == 0.0
    fake.fail.add("premium")
    assert m.snapshot() is None and "premium" in m.last_error
