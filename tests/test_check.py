"""`python -m zec_bot --check`: a primeira coisa que o utilizador corre. Exercitada com dados simulados."""
import pandas as pd
import pytest
from fakes import FakeBinance, ms
from synthetic import make_candles, reversal_scenario

from zec_bot import __main__ as entry
from zec_bot.config import Config
from zec_bot.derivs import DerivsMonitor


def run_check(monkeypatch, capsys, df, fake, cfg=None, venue=None, tg_ok=True):
    cfg = cfg or Config()
    end = df.index[-1] + pd.Timedelta(minutes=15, seconds=20)
    monkeypatch.setattr(entry, "get_candles", lambda *a, **k: (df, "hyperliquid"))
    monkeypatch.setattr(entry, "DerivsMonitor", lambda c: DerivsMonitor(
        c, binance=fake, venue=venue or (lambda: {"funding_hr": 0.0000125, "max_leverage": 5.0}),
        now=lambda: end.timestamp()))
    sent = []

    class TG:
        def __init__(self, *a):
            pass

        def send(self, text):
            sent.append(text)
            return tg_ok

    monkeypatch.setattr(entry, "Telegram", TG)
    code = entry.check(cfg)
    return code, capsys.readouterr().out, sent


def span(df):
    return ms(str(df.index[0] - pd.Timedelta(days=40))), ms(str(df.index[-1] + pd.Timedelta(days=1)))


def test_check_happy_path_reports_everything(monkeypatch, capsys):
    df, k = reversal_scenario(-1)
    boundary = ms(str(df.index[-1].floor("8h")))
    fake = FakeBinance(0, span_ms=span(df), funding_rate=lambda t: 0.0008 if t >= boundary else 0.0001,
                       funding_last=0.0008, oi_value=980_000.0, oi_now=1_000_000.0)
    code, out, sent = run_check(monkeypatch, capsys, df, fake)
    assert code == 0 and len(sent) == 1
    assert "candles=hyperliquid" in out and "derivados=Binance ZECUSDT" in out and "estratégia=reversal" in out
    assert "funding +0.0800%/8h (P100 dos últimos 30d" in out
    assert "OI 1,000,000 ZEC ≈ 50.00M$" in out and "variação 1h +2.04%" in out
    assert "Hyperliquid (onde pagas): funding +0.0100%/8h · alavancagem máx. 5x" in out
    assert "LONGS sobrelotados → a estratégia só procura SHORTS de reversão" in out
    assert "Sinais brutos nas últimas 400 barras:" in out and "reversal" in out


@pytest.mark.parametrize("funding,expected", [
    (0.0001, "neutro → sem reversões possíveis agora"),
    (-0.0005, "SHORTS sobrelotados (extremo) → a estratégia só procura LONGS de reversão"),
])
def test_check_describes_the_current_crowding_state(monkeypatch, capsys, funding, expected):
    df = make_candles(1500, seed=2)
    boundary = ms(str(df.index[-1].floor("8h")))
    fake = FakeBinance(0, span_ms=span(df), funding_rate=lambda t: funding if t >= boundary else 0.0001,
                       funding_last=funding)
    _, out, _ = run_check(monkeypatch, capsys, df, fake)
    assert expected in out


def test_check_tells_you_when_binance_is_down(monkeypatch, capsys):
    df = make_candles(1500, seed=2)
    fake = FakeBinance(0, span_ms=span(df))
    fake.fail.add("funding")
    code, out, _ = run_check(monkeypatch, capsys, df, fake)
    assert "FALHOU" in out and "funding em baixo" in out and "não gera sinais" in out
    assert code == 0  # o resto (Telegram) continua a ser testado; o utilizador vê o problema em destaque


def test_check_survives_a_dead_hyperliquid_ctx_and_missing_oi(monkeypatch, capsys):
    df = make_candles(1500, seed=2)
    fake = FakeBinance(0, span_ms=span(df))
    fake.fail.add("oi_hist")

    def boom():
        raise RuntimeError("HL em baixo")

    _, out, _ = run_check(monkeypatch, capsys, df, fake, venue=boom)
    assert "funding +0.0100%/8h" in out and "variação 1h n/d" in out and "Hyperliquid (onde pagas)" not in out


def test_check_fails_fast_when_candles_are_unavailable(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("hyperliquid: ProxyError")

    monkeypatch.setattr(entry, "get_candles", boom)
    assert entry.check(Config()) == 1
    assert "FALHOU: hyperliquid: ProxyError" in capsys.readouterr().out


def test_check_reports_a_failing_telegram(monkeypatch, capsys):
    df = make_candles(1500, seed=2)
    code, out, _ = run_check(monkeypatch, capsys, df, FakeBinance(0, span_ms=span(df)), tg_ok=False)
    assert code == 1 and "TELEGRAM_TOKEN" in out
