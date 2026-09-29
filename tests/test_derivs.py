import numpy as np
import pandas as pd
import pytest

from zec_bot import coinalyze as cz_mod
from zec_bot.coinalyze import Coinalyze, CoinalyzeError
from zec_bot.config import Config
from zec_bot.derivs import (KEEP_S, SAMPLE_EVERY_S, DerivsMonitor, funding_8h_pct, funding_series, interpret,
                            oi_change_series)

H = 3600.0


def ctx(oi=1_000_000.0, mark=50.0, funding=0.0000125):
    return {"funding_hr": funding, "oi_coins": oi, "mark": mark, "oracle": mark, "premium": 0.0002,
            "day_vol_usd": 1e7, "max_leverage": 5.0}


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def monitor(clock, fetch=None, coinalyze=None, **cfg_kw):
    return DerivsMonitor(Config(**cfg_kw), fetch_ctx=fetch or (lambda: ctx()), coinalyze=coinalyze or Coinalyze(""),
                         now=clock)


# ------------------------------------------------------------------ conversões e leitura
def test_funding_conversion_matches_hyperliquid_baseline():
    assert funding_8h_pct(0.0000125) == pytest.approx(0.01)  # 0.01% por 8h = a taxa de juro base
    assert funding_8h_pct(-0.00005) == pytest.approx(-0.04)


@pytest.mark.parametrize("px,oi,expected", [
    (1.0, 2.0, "entram longs"), (1.0, -2.0, "fecho de shorts"), (-1.0, 2.0, "entram shorts"),
    (-1.0, -2.0, "fecho de longs"), (1.0, 0.1, "OI estável"), (0.0, 3.0, "acumular"), (0.0, -3.0, "fechar"),
])
def test_interpret(px, oi, expected):
    assert expected in interpret(px, oi)


def test_interpret_without_data():
    assert interpret(None, 1.0) is None and interpret(1.0, None) is None


# ------------------------------------------------------------------ monitor
def test_refresh_is_cached_and_samples_every_five_minutes():
    clock = Clock()
    calls = []
    m = monitor(clock, fetch=lambda: calls.append(1) or ctx())
    m.refresh(); m.refresh()
    assert len(calls) == 1 and len(m.samples) == 1
    clock.t += SAMPLE_EVERY_S + 1
    m.refresh()
    assert len(calls) == 2 and len(m.samples) == 2
    m.refresh(force=True)  # força chamada mas não duplica a amostra
    assert len(calls) == 3 and len(m.samples) == 2


def test_old_samples_are_pruned():
    clock = Clock()
    m = monitor(clock)
    m.refresh()
    clock.t += KEEP_S + 600
    m.refresh()
    assert len(m.samples) == 1 and m.samples[0][0] == clock.t


def test_oi_change_from_own_samples():
    clock = Clock()
    oi = {"v": 100.0}
    m = monitor(clock, fetch=lambda: ctx(oi=oi["v"]))
    m.refresh()
    assert m.oi_change_pct(1.0) == (None, None)  # ainda sem histórico
    for minutes, value in ((30, 101.0), (60, 102.0)):
        clock.t = 1_700_000_000.0 + minutes * 60
        oi["v"] = value
        m.refresh(force=True)
    pct, src = m.oi_change_pct(1.0)
    assert src == "hl" and pct == pytest.approx(2.0)
    assert m.oi_change_pct(4.0) == (None, None)  # não há 4h de amostras


def test_oi_change_needs_a_sample_near_the_target_time():
    clock = Clock()
    m = monitor(clock)
    m.samples = [[clock.t - 2 * H, 100.0, 50.0], [clock.t, 110.0, 50.0]]
    assert m.oi_change_pct(1.0) == (None, None)  # a amostra mais próxima está a 1h de distância do alvo
    assert m.oi_change_pct(2.0)[0] == pytest.approx(10.0)


def test_failures_never_raise_and_are_reported():
    clock = Clock()

    def boom():
        raise RuntimeError("HTTP 500")

    m = monitor(clock, fetch=boom)
    assert m.refresh() is None and m.snapshot() is None and "500" in m.last_error


def test_snapshot_contents():
    clock = Clock()
    m = monitor(clock, fetch=lambda: ctx(oi=2_000_000, mark=50.0, funding=0.00005))
    m.samples = [[clock.t - H, 1_960_000.0, 50.0]]
    s = m.snapshot(price_change_1h_pct=1.2)
    assert s["funding_8h_pct"] == pytest.approx(0.04) and s["oi_usd"] == pytest.approx(100e6)
    assert s["oi_change_1h_pct"] == pytest.approx((2_000_000 / 1_960_000 - 1) * 100) and s["oi_source"] == "hl"
    assert s["oi_change_4h_pct"] is None and s["max_leverage"] == 5.0
    assert s["oi_change_pct"] == s["oi_change_1h_pct"] and "entram longs" in s["reading"]


# ------------------------------------------------------------------ Coinalyze (respostas simuladas)
def oi_payload(start_s, n, step=900, first=1000.0, inc=1.0):
    return [{"symbol": "ZEC.H", "history": [
        {"t": start_s + i * step, "o": first + i * inc, "h": first + i * inc, "l": first + i * inc,
         "c": first + i * inc} for i in range(n)]}]


def make_getter(routes):
    calls = []

    def getter(path, params):
        calls.append((path, params))
        return routes[path]

    getter.calls = calls
    return getter


def test_coinalyze_resolves_hyperliquid_symbol():
    g = make_getter({
        "/exchanges": [{"name": "Binance", "code": "A"}, {"name": "Hyperliquid", "code": "H"}],
        "/future-markets": [
            {"symbol": "ZECUSDT_PERP.A", "exchange": "A", "base_asset": "ZEC", "is_perpetual": True},
            {"symbol": "BTCUSD_PERP.H", "exchange": "H", "base_asset": "BTC", "is_perpetual": True},
            {"symbol": "ZEC.H", "exchange": "H", "base_asset": "ZEC", "is_perpetual": True},
        ]})
    c = Coinalyze("k", coin="zec", getter=g)
    assert c.resolve_symbol() == "ZEC.H" and c.resolve_symbol() == "ZEC.H"
    assert len(g.calls) == 2  # resolvido uma vez só


def test_coinalyze_symbol_override_and_missing_market():
    g = make_getter({})
    assert Coinalyze("k", symbol="MYSYM", getter=g).resolve_symbol() == "MYSYM" and not g.calls
    with pytest.raises(CoinalyzeError, match="Hyperliquid"):
        Coinalyze("k", getter=make_getter({"/exchanges": [{"name": "Binance", "code": "A"}]})).resolve_symbol()
    with pytest.raises(CoinalyzeError, match="COINALYZE_SYMBOL"):
        Coinalyze("k", getter=make_getter({"/exchanges": [{"name": "Hyperliquid", "code": "H"}],
                                           "/future-markets": []})).resolve_symbol()


def test_coinalyze_oi_history_parses_seconds_and_milliseconds():
    t0 = 1_700_000_000
    for scale in (1, 1000):
        payload = oi_payload(t0 * scale, 4, step=900 * scale)
        c = Coinalyze("k", symbol="ZEC.H", getter=make_getter({"/open-interest-history": payload}))
        df = c.oi_history("15min", hours=1, now=t0 + 3600)
        assert list(df["c"]) == [1000.0, 1001.0, 1002.0, 1003.0]
        assert df.index[0] == pd.Timestamp(t0, unit="s", tz="UTC")


def test_coinalyze_request_parameters():
    g = make_getter({"/open-interest-history": oi_payload(1_700_000_000, 2)})
    Coinalyze("k", symbol="ZEC.H", getter=g).oi_history("15min", hours=6, now=1_700_021_600)
    path, params = g.calls[0]
    assert path == "/open-interest-history"
    assert params == {"symbols": "ZEC.H", "interval": "15min", "from": 1_700_021_600 - 6 * 3600, "to": 1_700_021_600}


@pytest.mark.parametrize("payload", [[], [{"symbol": "x", "history": []}], {"error": 1}, [{"symbol": "x"}]])
def test_coinalyze_bad_payloads(payload):
    c = Coinalyze("k", symbol="ZEC.H", getter=make_getter({"/open-interest-history": payload}))
    with pytest.raises(CoinalyzeError):
        c.oi_history()


class FakeResp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


def test_coinalyze_http_layer(monkeypatch):
    sent = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        sent.update(url=url, params=params, headers=headers)
        return FakeResp(200, [{"ok": 1}])

    monkeypatch.setattr(cz_mod.requests, "get", fake_get)
    assert Coinalyze("secret")._http_get("/exchanges", {}) == [{"ok": 1}]
    assert sent["url"] == "https://api.coinalyze.net/v1/exchanges" and sent["headers"] == {"api_key": "secret"}

    monkeypatch.setattr(cz_mod.requests, "get", lambda *a, **k: FakeResp(429, headers={"Retry-After": "12"}))
    with pytest.raises(CoinalyzeError, match="12s"):
        Coinalyze("k")._http_get("/x", {})
    monkeypatch.setattr(cz_mod.requests, "get", lambda *a, **k: FakeResp(401))
    with pytest.raises(CoinalyzeError, match="chave"):
        Coinalyze("k")._http_get("/x", {})


def test_monitor_falls_back_to_coinalyze_and_survives_its_errors():
    clock = Clock()
    t0 = int(clock.t) - 3 * 3600
    g = make_getter({"/open-interest-history": oi_payload(t0, 13, first=1000.0, inc=10.0)})  # 3h de barras de 15m
    m = monitor(clock, coinalyze=Coinalyze("k", symbol="ZEC.H", getter=g))
    pct, src = m.oi_change_pct(1.0)
    assert src == "coinalyze" and pct == pytest.approx((1120 / 1080 - 1) * 100)
    m.oi_change_pct(4.0)
    assert len(g.calls) == 1  # cache de 5 minutos

    def boom(path, params):
        raise CoinalyzeError("coinalyze: HTTP 500")

    m2 = monitor(clock, coinalyze=Coinalyze("k", symbol="ZEC.H", getter=boom))
    assert m2.oi_change_pct(1.0) == (None, None) and "500" in m2.last_error


def test_own_samples_win_over_coinalyze():
    clock = Clock()
    g = make_getter({"/open-interest-history": oi_payload(int(clock.t) - 3 * 3600, 13)})
    m = monitor(clock, coinalyze=Coinalyze("k", symbol="ZEC.H", getter=g))
    m.samples = [[clock.t - H, 100.0, 50.0], [clock.t, 103.0, 50.0]]
    assert m.oi_change_pct(1.0) == (pytest.approx(3.0), "hl") and not g.calls


# ------------------------------------------------------------------ backtest: alinhamento sem lookahead
def bars(start="2026-03-01 00:00", n=24):
    return pd.date_range(start, periods=n, freq="15min", tz="UTC")


def test_funding_series_only_uses_records_known_at_bar_close():
    hour = 3_600_000
    base = int(pd.Timestamp("2026-03-01 00:00", tz="UTC").timestamp() * 1000)
    rows = [[base + h * hour + 76, (h + 1) * 0.0000125, 0.0] for h in range(6)]  # registo de cada hora, +76 ms
    s = funding_series(rows, bars(n=24), 15)
    at = lambda hhmm: s[pd.Timestamp(f"2026-03-01 {hhmm}", tz="UTC")]
    # barra 00:45 fecha às 01:00:00.000, ANTES do registo das 01:00:00.076 -> ainda vê o das 00:00
    assert at("00:45") == pytest.approx(funding_8h_pct(1 * 0.0000125))
    # barra 01:00 fecha às 01:15 -> já vê o das 01:00
    assert at("01:00") == pytest.approx(funding_8h_pct(2 * 0.0000125))
    assert at("00:00") == pytest.approx(funding_8h_pct(1 * 0.0000125))  # fecha às 00:15, vê o das 00:00


def test_funding_series_is_nan_before_the_first_record_and_when_empty():
    base = int(pd.Timestamp("2026-03-01 02:00", tz="UTC").timestamp() * 1000)
    s = funding_series([[base, 0.00001, 0.0]], bars(n=16), 15)
    assert s.iloc[:7].isna().all() and s.iloc[8:].notna().all()
    assert funding_series([], bars(n=4), 15).isna().all()


def make_oi(start="2026-03-01 00:00", n=16):
    idx = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    c = pd.Series(1000.0 + np.arange(n) * 10.0, index=idx)
    return pd.DataFrame({"o": c, "h": c, "l": c, "c": c})


def test_oi_change_series_alignment_and_coverage():
    oi = make_oi(n=16)  # 00:00 .. 03:45
    idx = bars("2026-03-01 00:00", 24)  # as barras de preço vão além do histórico de OI
    s = oi_change_series(oi, idx, 15, hours=1.0)
    t = lambda hhmm: pd.Timestamp(f"2026-03-01 {hhmm}", tz="UTC")
    assert s[:t("00:45")].isna().all()  # sem 1h de histórico
    assert s[t("01:00")] == pytest.approx((1040 / 1000 - 1) * 100)  # c[01:00] / c[00:00]
    assert s[t("03:45")] == pytest.approx((1150 / 1110 - 1) * 100)
    assert s[t("04:00"):].isna().all()  # depois do último dado não inventa nada


def test_oi_change_series_does_not_look_ahead():
    oi = make_oi(n=16)
    idx = bars("2026-03-01 00:00", 16)
    a = oi_change_series(oi, idx, 15, 1.0)
    tampered = oi.copy()
    tampered.loc[tampered.index > pd.Timestamp("2026-03-01 02:00", tz="UTC"), "c"] *= 5  # mexe só no futuro
    b = oi_change_series(tampered, idx, 15, 1.0)
    upto = pd.Timestamp("2026-03-01 02:00", tz="UTC")
    pd.testing.assert_series_equal(a[:upto], b[:upto])


def test_oi_series_only_supports_15m():
    with pytest.raises(ValueError):
        oi_change_series(make_oi(), bars(), 5, 1.0)
