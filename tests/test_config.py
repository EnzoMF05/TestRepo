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


def test_derivs_options_parse(tmp_path, monkeypatch, clean_env):
    (tmp_path / ".env").write_text("FUNDING_FILTER=true\nFUNDING_LIMIT_8H_PCT=0.03\nOI_FILTER=sim\n"
                                   "OI_MIN_CHANGE_PCT=0.5\nOI_LOOKBACK_HOURS=2\nCOINALYZE_API_KEY=abc\nALLOW_FALLBACK=true\n")
    monkeypatch.chdir(tmp_path)
    c = Config.from_env()
    assert (c.funding_filter, c.funding_limit_8h_pct, c.oi_filter, c.oi_min_change_pct, c.oi_lookback_hours) == (
        True, 0.03, True, 0.5, 2.0)
    assert (c.coinalyze_api_key, c.allow_fallback) == ("abc", True)
