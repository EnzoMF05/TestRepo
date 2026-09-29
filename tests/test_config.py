import os
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest

from zec_bot.config import Config, load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items() if k in ("PATH", "HOME")})


def test_env_example_loads_and_matches_defaults(tmp_path, monkeypatch, clean_env):
    """O .env.example (com comentários na mesma linha) tem de dar exatamente a configuração por defeito."""
    shutil.copy(ROOT / ".env.example", tmp_path / ".env")
    monkeypatch.chdir(tmp_path)
    assert asdict(Config.from_env()) == asdict(Config())


def test_dotenv_values_and_quotes(tmp_path, monkeypatch, clean_env):
    (tmp_path / ".env").write_text(
        '# comentário\nTELEGRAM_TOKEN="123:ABC-def"\nTELEGRAM_CHAT_ID=987654  # o meu chat\nSIDE=long\nRISK_PCT=0.5\n'
        "EMPTY=\nnão_é_uma_linha_válida\n")
    monkeypatch.chdir(tmp_path)
    c = Config.from_env()
    assert (c.telegram_token, c.telegram_chat_id, c.side, c.risk_pct) == ("123:ABC-def", "987654", "long", 0.5)
    assert c.telegram_ready


def test_real_environment_wins_over_dotenv(tmp_path, monkeypatch, clean_env):
    (tmp_path / ".env").write_text("EXCHANGE=okx\n")
    monkeypatch.chdir(tmp_path)
    os.environ["EXCHANGE"] = "bybit"
    assert Config.from_env().exchange == "bybit"


def test_invalid_side_is_rejected(tmp_path, monkeypatch, clean_env):
    monkeypatch.chdir(tmp_path)
    os.environ["SIDE"] = "sideways"
    with pytest.raises(ValueError):
        Config.from_env()


def test_missing_dotenv_is_fine(tmp_path, monkeypatch, clean_env):
    monkeypatch.chdir(tmp_path)
    load_dotenv()
    c = Config.from_env()
    assert (c.exchange, c.allow_fallback, c.funding_filter, c.oi_filter) == ("hyperliquid", False, False, False)


def test_strategy_options_parse(tmp_path, monkeypatch, clean_env):
    (tmp_path / ".env").write_text(
        "STRATEGY=both\nAVOID_EXPENSIVE_LONGS=false\nCROWD_WINDOW_DAYS=14\nREV_SHORT_MIN_PCTL=95\n"
        "REV_SHORT_MIN_FUNDING_8H=0.05\nREV_LONG_MAX_PCTL=2\nREV_LONG_MAX_FUNDING_8H=-0.04\nREV_USE_OI=sim\n"
        "REV_OI_HOURS=12\nREV_OI_MIN_PCT=5\nFUNDING_FILTER=true\nFUNDING_LIMIT_8H_PCT=0.03\nOI_FILTER=true\n"
        "OI_MIN_CHANGE_PCT=0.5\nOI_LOOKBACK_HOURS=2\nALLOW_FALLBACK=true\n")
    monkeypatch.chdir(tmp_path)
    c = Config.from_env()
    assert (c.strategy, c.avoid_expensive_longs, c.crowd_window_days) == ("both", False, 14.0)
    assert (c.rev_short_min_pctl, c.rev_short_min_funding_8h) == (95.0, 0.05)
    assert (c.rev_long_max_pctl, c.rev_long_max_funding_8h) == (2.0, -0.04)
    assert (c.rev_use_oi, c.rev_oi_hours, c.rev_oi_min_pct) == (True, 12.0, 5.0)
    assert (c.funding_filter, c.funding_limit_8h_pct, c.oi_filter, c.oi_min_change_pct, c.oi_lookback_hours) == (
        True, 0.03, True, 0.5, 2.0)
    assert c.allow_fallback is True


def test_defaults_describe_a_reversal_trader():
    c = Config()
    assert c.strategy == "reversal" and c.avoid_expensive_longs and c.derivs_symbol == "ZECUSDT"
    # "muito carregado de longs" = os 10% mais altos; "EXTREMAMENTE carregado de shorts" = só os 5% mais baixos
    assert 100 - c.rev_short_min_pctl == 10 and c.rev_long_max_pctl == 5
    assert c.rev_long_max_pctl < 100 - c.rev_short_min_pctl  # o lado dos longs é o mais exigente
    assert c.needs_derivs and not Config(strategy="trend").needs_derivs
    assert Config(strategy="trend", funding_filter=True).needs_derivs


def test_invalid_strategy_is_rejected(tmp_path, monkeypatch, clean_env):
    monkeypatch.chdir(tmp_path)
    os.environ["STRATEGY"] = "yolo"
    with pytest.raises(ValueError, match="STRATEGY"):
        Config.from_env()


def test_params_follow_the_config():
    from zec_bot.strategy import Params
    c = Config(strategy="reversal", rev_short_min_pctl=95, rev_long_max_pctl=2, rev_use_oi=True,
               rev_short_min_funding_8h=0.05, rev_long_max_funding_8h=-0.04, rev_oi_min_pct=5, avoid_expensive_longs=False)
    p = Params.from_cfg(c)
    assert (p.mode, p.rev_short_min_pctl, p.rev_long_max_pctl, p.rev_use_oi) == ("reversal", 95, 2, True)
    assert (p.rev_short_min_funding_8h, p.rev_long_max_funding_8h, p.rev_oi_min_pct) == (0.05, -0.04, 5)
    assert p.avoid_expensive_longs is False
    assert Params().mode == "trend"  # o default do Params continua a ser a estratégia clássica; o da Config é a reversão


def test_every_variable_the_code_reads_is_documented_in_env_example():
    """Se acrescento uma opção ao código e me esqueço do .env.example, o utilizador nunca a descobre."""
    import re
    src = (ROOT / "zec_bot" / "config.py").read_text(encoding="utf-8")
    read = set(re.findall(r'_(?:get|bool)\("([A-Z0-9_]+)"', src))
    documented = set(re.findall(r"^([A-Z0-9_]+)=", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M))
    assert read and read <= documented, f"em falta no .env.example: {sorted(read - documented)}"
    assert not (documented - read), f"no .env.example mas ignoradas pelo código: {sorted(documented - read)}"
