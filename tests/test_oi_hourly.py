"""Sensores de OI 1H (CoinGlass / Coinalyze) e a cascata da mesa, com respostas SIMULADAS.

Os formatos, URLs, cabeçalhos e a regra de escolha da vela vêm do código de produção da mesa
(oi_coinglass_1h.py e oi_coinalyze.py). Não foi possível testar contra os serviços reais daqui.
"""
import pandas as pd
import pytest

from zec_bot.config import Config
from zec_bot.oi_hourly import CoinalyzeOI, CoinGlassOI, OiCascade

OPEN = pd.Timestamp("2026-03-01 10:00", tz="UTC")
OPEN_MS, OPEN_S = int(OPEN.timestamp() * 1000), int(OPEN.timestamp())
HOUR = 3600


class Http:
    """`get` falso: devolve `payload` (ou levanta) e regista o pedido."""

    def __init__(self, payload=None, exc=None):
        self.payload, self.exc, self.calls = payload, exc, []

    def __call__(self, url, params, headers):
        self.calls.append((url, dict(params), dict(headers)))
        if self.exc:
            raise self.exc
        return self.payload


def ca_rows(*specs):  # (hora relativa à vela, open, close); t em SEGUNDOS
    return [{"symbol": "ZECUSDT_PERP.A", "history": [{"t": OPEN_S + h * HOUR, "o": o, "h": max(o, c), "l": min(o, c), "c": c}
                                                     for h, o, c in specs]}]


# ------------------------------------------------------------------ Coinalyze
def test_coinalyze_request_and_delta():
    http = Http(ca_rows((-2, 1000.0, 1010.0), (-1, 1010.0, 1000.0), (0, 1000.0, 957.0), (1, 957.0, 960.0)))
    s = CoinalyzeOI("KEY", "zec", http, now=lambda: OPEN_S + HOUR + 130)
    assert s.fetch(OPEN_MS) == (-4.3, None)  # (957-1000)/1000*100, a barra da MESMA hora (não a última)
    url, params, headers = http.calls[0]
    assert url == "https://api.coinalyze.net/v1/open-interest-history"
    assert params == {"symbols": "ZECUSDT_PERP.A", "interval": "1hour", "from": OPEN_S + HOUR + 130 - 6 * HOUR, "to": OPEN_S + HOUR + 130}
    assert headers["api_key"] == "KEY"  # a chave vai no cabeçalho, não no URL


def test_coinalyze_does_not_guess_when_the_candle_is_missing_yet():
    http = Http(ca_rows((-2, 1000.0, 1010.0), (-1, 1010.0, 1000.0)))  # ainda não tem a hora das 10:00
    assert CoinalyzeOI("KEY", "ZEC", http).fetch(OPEN_MS) == (None, None)


def test_coinalyze_matches_the_bar_within_a_minute_and_handles_unsorted_rows():
    payload = ca_rows((1, 1.0, 1.0), (0, 1000.0, 990.0), (-1, 1.0, 1.0))
    payload[0]["history"][1]["t"] += 45  # 45 s de folga (a mesa aceita < 60 s)
    assert CoinalyzeOI("KEY", "ZEC", Http(payload)).fetch(OPEN_MS) == (-1.0, None)
    payload[0]["history"][1]["t"] += 30  # 75 s: já não é a mesma barra
    assert CoinalyzeOI("KEY", "ZEC", Http(payload)).fetch(OPEN_MS) == (None, None)


@pytest.mark.parametrize("payload,err", [([], "resposta_invalida"), ({"erro": 1}, "resposta_invalida"), (["x"], "resposta_invalida")])
def test_coinalyze_bad_payloads(payload, err):
    assert CoinalyzeOI("KEY", "ZEC", Http(payload)).fetch(OPEN_MS) == (None, err)


def test_coinalyze_open_zero_network_error_and_no_key():
    assert CoinalyzeOI("KEY", "ZEC", Http(ca_rows((0, 0.0, 5.0)))).fetch(OPEN_MS) == (None, "open_zero")
    assert CoinalyzeOI("KEY", "ZEC", Http(exc=TimeoutError())).fetch(OPEN_MS) == (None, "TimeoutError")
    assert CoinalyzeOI("", "ZEC", Http()).fetch(OPEN_MS) == (None, "sem_key")
    junk = ca_rows((0, 1000.0, 990.0))
    junk[0]["history"].insert(0, {"t": "abc"})  # linha estragada é ignorada, não rebenta
    assert CoinalyzeOI("KEY", "ZEC", Http(junk)).fetch(OPEN_MS) == (-1.0, None)


# ------------------------------------------------------------------ CoinGlass
def cg_body(items, code="0"):
    return {"code": code, "data": items}


def test_coinglass_request_and_delta_ms_and_seconds():
    for t in (OPEN_MS, OPEN_S):  # o CoinGlass pode devolver segundos ou milissegundos
        http = Http(cg_body([{"t": t - 3_600_000 if t == OPEN_MS else t - 3600, "o": 5, "c": 5}, {"t": t, "o": 2000.0, "c": 1940.0}]))
        assert CoinGlassOI("K", "zec", http).fetch(OPEN_MS) == (-3.0, None)
    url, params, headers = http.calls[0]
    assert url == "https://open-api-v4.coinglass.com/api/futures/open-interest/history"
    assert params == {"exchange": "Binance", "symbol": "ZECUSDT", "interval": "1h", "limit": 8}
    assert headers["CG-API-KEY"] == "K"


def test_coinglass_accepts_list_rows_and_alternative_field_names():
    assert CoinGlassOI("K", "ZEC", Http(cg_body([[OPEN_MS, 1000.0, 1001.0, 990.0, 970.0]]))).fetch(OPEN_MS) == (-3.0, None)
    assert CoinGlassOI("K", "ZEC", Http(cg_body([{"time": OPEN_MS, "open": 1000.0, "close": 1030.0}]))).fetch(OPEN_MS) == (3.0, None)


@pytest.mark.parametrize("body", [cg_body([], code="40001"), {"code": "0", "data": None}, "oops", cg_body([{"t": OPEN_MS}])])
def test_coinglass_failures_do_not_raise(body):
    value, err = CoinGlassOI("K", "ZEC", Http(body)).fetch(OPEN_MS)
    assert value is None


def test_coinglass_without_key_or_with_network_error():
    assert CoinGlassOI("", "ZEC", Http()).fetch(OPEN_MS) == (None, "sem_key")
    assert CoinGlassOI("K", "ZEC", Http(exc=ConnectionError())).fetch(OPEN_MS) == (None, "ConnectionError")


# ------------------------------------------------------------------ cascata
class Src:
    def __init__(self, label, results):
        self.label, self.results, self.calls = label, list(results), 0

    def fetch(self, ms):
        self.calls += 1
        return self.results.pop(0) if self.results else (None, None)


def test_cascade_prefers_coinglass_then_falls_back_to_coinalyze():
    cg, ca = Src("CG1H", [(-5.0, None)]), Src("Coinalyze", [(-4.0, None)])
    assert OiCascade([cg, ca], sleep=lambda s: None).delta(OPEN_MS) == {"delta": -5.0, "src": "CG1H", "errors": []}
    assert ca.calls == 0  # a 2ª fonte nem é consultada
    cg, ca = Src("CG1H", [(None, "TimeoutError")]), Src("Coinalyze", [(-4.0, None)])
    r = OiCascade([cg, ca], attempts=1).delta(OPEN_MS)
    assert (r["delta"], r["src"], r["errors"]) == (-4.0, "Coinalyze", ["CG1H:TimeoutError"])


def test_cascade_retries_when_the_candle_is_not_there_yet():
    sleeps = []
    cg, ca = Src("CG1H", [(None, None), (None, None)]), Src("Coinalyze", [(None, None), (-3.5, None)])
    r = OiCascade([cg, ca], attempts=3, retry_s=20, sleep=sleeps.append).delta(OPEN_MS)
    assert r["delta"] == -3.5 and r["src"] == "Coinalyze" and sleeps == [20]  # 1 espera entre a 1ª e a 2ª tentativa


def test_cascade_gives_up_cleanly():
    sleeps = []
    r = OiCascade([Src("CG1H", []), Src("Coinalyze", [(None, "sem_key")] * 3)], attempts=3, retry_s=7, sleep=sleeps.append).delta(OPEN_MS)
    assert r["delta"] is None and r["src"] == "none" and sleeps == [7, 7]
    assert r["errors"] == ["CG1H:sem_vela", "Coinalyze:sem_key"]


def test_cascade_without_keys_does_not_sleep():
    r = OiCascade([], sleep=lambda s: pytest.fail("não devia dormir")).delta(OPEN_MS)
    assert r["delta"] is None and "sem_keys" in r["errors"][0]


def test_cascade_from_config_uses_only_the_keys_you_have():
    both = OiCascade.from_cfg(Config(coinglass_api_key="a", coinalyze_api_key="b"))
    assert [s.label for s in both.sources] == ["CG1H", "Coinalyze"]  # a ordem da mesa
    assert [s.label for s in OiCascade.from_cfg(Config(coinalyze_api_key="b")).sources] == ["Coinalyze"]
    assert OiCascade.from_cfg(Config()).sources == []
    assert OiCascade.from_cfg(Config(coinalyze_api_key="b", sweep_oi_attempts=5, sweep_oi_retry_s=9)).attempts == 5
