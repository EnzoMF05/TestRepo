"""Cliente dos futuros da Binance com respostas SIMULADAS no formato documentado.

Nota: não foi possível testar contra a API real a partir do ambiente de desenvolvimento.
Corre `python -m zec_bot --check` no teu Mac para confirmar.
"""
import pytest

from zec_bot import binance_futures as bf
from zec_bot.binance_futures import BinanceError

H = 3_600_000
T0 = 1_700_000_000_000


def fr(t, rate):
    return {"symbol": "ZECUSDT", "fundingRate": f"{rate:.8f}", "fundingTime": t, "markPrice": "50.1"}


def oi(t, coins=1000.0, usd=50000.0):
    return {"symbol": "ZECUSDT", "sumOpenInterest": f"{coins}", "sumOpenInterestValue": f"{usd}", "timestamp": t}


# ------------------------------------------------------------------ parsers
def test_parse_premium_index():
    payload = {"symbol": "ZECUSDT", "markPrice": "50.10", "indexPrice": "50.00", "estimatedSettlePrice": "50.05",
               "lastFundingRate": "0.00010000", "interestRate": "0.00010000", "nextFundingTime": T0 + 8 * H, "time": T0}
    assert bf.parse_premium_index(payload) == {"mark": 50.1, "index": 50.0, "funding_last": 0.0001,
                                                "next_funding_ms": T0 + 8 * H, "time": T0}
    with pytest.raises(BinanceError, match="formato"):
        bf.parse_premium_index({"markPrice": "1"})


def test_parse_open_interest():
    assert bf.parse_open_interest({"openInterest": "10659.509", "symbol": "ZECUSDT", "time": T0}) == {
        "oi_coins": 10659.509, "time": T0}
    with pytest.raises(BinanceError):
        bf.parse_open_interest([])


def test_parse_funding_history_sorts_and_converts():
    rows = bf.parse_funding_history([fr(T0 + 8 * H, -0.0002), fr(T0, 0.0001)])
    assert rows == [[T0, 0.0001], [T0 + 8 * H, -0.0002]]


def test_parse_oi_hist_sorts_and_converts():
    rows = bf.parse_oi_hist([oi(T0 + 900_000, 2.0, 20.0), oi(T0, 1.0, 10.0)])
    assert rows == [[T0, 1.0, 10.0], [T0 + 900_000, 2.0, 20.0]]


@pytest.mark.parametrize("parser", [bf.parse_funding_history, bf.parse_oi_hist])
def test_list_parsers_reject_errors_and_garbage(parser):
    with pytest.raises(BinanceError, match="Invalid symbol"):
        parser({"code": -1121, "msg": "Invalid symbol."})
    with pytest.raises(BinanceError):
        parser("oops")
    with pytest.raises(BinanceError, match="formato"):
        parser([{"nada": 1}])


def test_interval_hours_is_derived_from_spacing_not_assumed():
    assert bf.interval_hours([T0, T0 + 8 * H, T0 + 16 * H]) == [8, 8, 8]
    # a Binance pode mudar o intervalo de um símbolo de 8h para 4h a meio do histórico
    assert bf.interval_hours([T0, T0 + 8 * H, T0 + 12 * H, T0 + 16 * H]) == [8, 8, 4, 4]
    assert bf.interval_hours([T0]) == [8.0] and bf.interval_hours([]) == []


# ------------------------------------------------------------------ paginação
def test_funding_history_paginates_forward(monkeypatch):
    all_rows = [fr(T0 + k * 8 * H, 0.0001) for k in range(2200)]
    calls = []

    def fake_get(path, params):
        calls.append((path, dict(params)))
        return [r for r in all_rows if r["fundingTime"] >= params["startTime"]][: params["limit"]]

    monkeypatch.setattr(bf, "_get", fake_get)
    monkeypatch.setattr(bf.time, "sleep", lambda s: None)
    rows = bf.fetch_funding_history("ZECUSDT", T0)
    assert len(rows) == 2200 and [r[0] for r in rows] == sorted({r[0] for r in rows})
    assert len(calls) == 3 and calls[0][0] == "/fapi/v1/fundingRate"
    assert calls[0][1] == {"symbol": "ZECUSDT", "startTime": T0, "limit": 1000}
    assert calls[1][1]["startTime"] == T0 + 999 * 8 * H + 1  # logo depois do último registo recebido


def test_oi_hist_clamps_to_30_days_and_paginates(monkeypatch):
    now = T0 + 100 * 86_400_000
    step = 900_000
    all_rows = [oi(now - 29 * 86_400_000 + k * step) for k in range(2600)]  # ~27 dias de barras de 15m
    calls = []

    def fake_get(path, params):
        calls.append((path, dict(params)))
        return [r for r in all_rows if r["timestamp"] >= params["startTime"]][: params["limit"]]

    monkeypatch.setattr(bf, "_get", fake_get)
    monkeypatch.setattr(bf.time, "sleep", lambda s: None)
    rows = bf.fetch_oi_hist("ZECUSDT", start_ms=T0, now_ms=now)  # pede desde muito antes do que a Binance guarda
    assert len(rows) == 2600 and len(calls) == 6
    path, first = calls[0]
    assert path == "/futures/data/openInterestHist"
    assert first["period"] == "15m" and first["limit"] == 500
    assert first["startTime"] == now - int(bf.OI_MAX_DAYS * 86_400_000)  # cortado para dentro dos 30 dias


def test_pagination_stops_on_empty_page(monkeypatch):
    monkeypatch.setattr(bf, "_get", lambda path, params: [])
    assert bf.fetch_funding_history("ZECUSDT", T0) == [] and bf.fetch_oi_hist("ZECUSDT", T0, now_ms=T0) == []


# ------------------------------------------------------------------ camada HTTP
class Resp:
    def __init__(self, status=200, body=None, bad_json=False):
        self.status_code, self._body, self._bad = status, body, bad_json

    def json(self):
        if self._bad:
            raise ValueError("no json")
        return self._body


def test_http_layer(monkeypatch):
    seen = {}

    def fake_get(url, params=None, timeout=None, headers=None):
        seen.update(url=url, params=params)
        return Resp(200, {"ok": 1})

    monkeypatch.setattr(bf.requests, "get", fake_get)
    assert bf._get("/fapi/v1/premiumIndex", {"symbol": "ZECUSDT"}) == {"ok": 1}
    assert seen == {"url": "https://fapi.binance.com/fapi/v1/premiumIndex", "params": {"symbol": "ZECUSDT"}}


@pytest.mark.parametrize("resp,match", [
    (Resp(451), "451"),
    (Resp(400, {"code": -1121, "msg": "Invalid symbol."}), "Invalid symbol"),
    (Resp(429, {"msg": "Too many requests"}), "429"),
    (Resp(502, bad_json=True), "502"),
    (Resp(200, bad_json=True), "JSON"),
])
def test_http_errors_are_readable(monkeypatch, resp, match):
    monkeypatch.setattr(bf.requests, "get", lambda *a, **k: resp)
    with pytest.raises(BinanceError, match=match):
        bf._get("/x", {})


def test_network_exception_becomes_binance_error(monkeypatch):
    def boom(*a, **k):
        raise bf.requests.ConnectionError("sem rede")

    monkeypatch.setattr(bf.requests, "get", boom)
    with pytest.raises(BinanceError, match="ConnectionError"):
        bf._get("/x", {})


def test_client_hits_the_right_endpoints(monkeypatch):
    seen = []

    def fake_get(path, params):
        seen.append((path, dict(params)))
        return {"/fapi/v1/premiumIndex": {"markPrice": "1", "indexPrice": "1", "lastFundingRate": "0",
                                          "nextFundingTime": 1, "time": 1},
                "/fapi/v1/openInterest": {"openInterest": "5", "time": 1}}.get(path, [])

    monkeypatch.setattr(bf, "_get", fake_get)
    c = bf.BinanceFutures("ZECUSDT")
    c.premium_index(); c.open_interest(); c.funding_history(T0); c.oi_history(T0)
    assert [p for p, _ in seen[:2]] == ["/fapi/v1/premiumIndex", "/fapi/v1/openInterest"]
    assert all(params["symbol"] == "ZECUSDT" for _, params in seen)
