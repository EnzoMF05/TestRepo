"""Configuração do robô ZEC. Lida de variáveis de ambiente (ou de um ficheiro .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Carrega KEY=VALUE de um ficheiro .env para os.environ (sem sobrepor o que já existe)."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #")[0]  # comentário na mesma linha: "EXCHANGE=binance  # nota"
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _get(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _bool(name: str, default: bool) -> bool:
    return _get(name, "true" if default else "false").lower() in ("1", "true", "yes", "sim", "on")


@dataclass
class Config:
    # --- Telegram ---
    telegram_token: str = ""
    telegram_chat_id: str = ""
    enable_commands: bool = False  # /status /pause /resume /stats (ver README: token partilhado!)

    # --- Mercado ---
    exchange: str = "hyperliquid"  # hyperliquid | binance | bybit | okx | kraken
    allow_fallback: bool = False  # se a exchange falhar, usar outra? (os preços deixam de bater com a tua)
    base: str = "ZEC"
    quote: str = "USDT"  # só usado fora da Hyperliquid
    interval_min: int = 15

    # --- Estratégia ---
    strategy: str = "reversal"  # reversal (contra o excesso) | trend (a favor da tendência) | both
    avoid_expensive_longs: bool = True  # sem longs de tendência quando o preço já está "caro"

    # --- Reversão: quão "carregado" tem de estar o mercado (funding da Binance, em %/8h) ---
    # Percentis calculados sobre os últimos `crowd_window_days` do PRÓPRIO ativo (adapta-se ao ZEC);
    # os mínimos absolutos evitam que um funding perto de zero conte como "extremo".
    crowd_window_days: float = 30.0
    rev_short_min_pctl: float = 90.0  # short quando os longs estão sobrelotados: funding >= P90
    rev_short_min_funding_8h: float = 0.03  # ... e >= +0.03%/8h (a base da Binance é 0.01)
    rev_long_max_pctl: float = 5.0  # long quando os shorts estão EXTREMAMENTE sobrelotados: funding <= P5
    rev_long_max_funding_8h: float = -0.02  # ... e <= -0.02%/8h
    rev_use_oi: bool = False  # exigir também OI a subir (posições a acumular); só testável ~30 dias
    rev_oi_hours: float = 24.0
    rev_oi_min_pct: float = 3.0

    # --- Filtros opcionais da estratégia de tendência (desligados) ---
    funding_filter: bool = False  # bloqueia longs com funding muito alto / shorts com funding muito negativo
    funding_limit_8h_pct: float = 0.05
    oi_filter: bool = False  # breakouts só com open interest a subir
    oi_min_change_pct: float = 0.0
    oi_lookback_hours: float = 1.0

    # --- Sinais ---
    side: str = "both"  # long | short | both
    active_hours_utc: str = "0-24"  # ex.: "7-22" para ignorar a madrugada
    max_signals_per_day: int = 4
    daily_stop_r: float = 3.0  # depois de -3R no dia, deixa de emitir sinais até ao dia seguinte
    cooldown_bars: int = 4  # barras a esperar após um sinal
    max_hold_bars: int = 48  # 48 x 15m = 12h; depois disso o trade é fechado a mercado

    # --- Risco / custos ---
    account_size: float = 1000.0
    risk_pct: float = 1.0  # % da conta arriscada por trade (só afeta o tamanho sugerido)
    fee_pct: float = 0.045  # comissão por lado (taker base da Hyperliquid) em %
    slippage_pct: float = 0.03  # derrapagem por lado em %

    # --- Execução ---
    poll_seconds: int = 20
    state_file: str = "zec_state.json"
    timezone: str = "Europe/Lisbon"

    @property
    def round_trip_cost_pct(self) -> float:
        return 2 * (self.fee_pct + self.slippage_pct)

    @property
    def derivs_symbol(self) -> str:
        """Perp USDT da Binance usado como referência de funding/OI (o que vês no Coinalyze)."""
        return f"{self.base}USDT"

    @property
    def interval_str(self) -> str:
        return f"{self.interval_min}m"

    @property
    def needs_derivs(self) -> bool:
        """Precisa de funding/OI para gerar sinais ou filtrar?"""
        return self.strategy in ("reversal", "both") or self.funding_filter or self.oi_filter

    @property
    def telegram_ready(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)

    def hours_window(self) -> tuple[int, int]:
        try:
            a, b = self.active_hours_utc.split("-")
            return int(a), int(b)
        except ValueError:
            return 0, 24

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        c = cls()
        c.telegram_token = _get("TELEGRAM_TOKEN", c.telegram_token)
        c.telegram_chat_id = _get("TELEGRAM_CHAT_ID", c.telegram_chat_id)
        c.enable_commands = _bool("ENABLE_COMMANDS", c.enable_commands)
        c.exchange = _get("EXCHANGE", c.exchange).lower()
        c.allow_fallback = _bool("ALLOW_FALLBACK", c.allow_fallback)
        c.strategy = _get("STRATEGY", c.strategy).lower()
        c.avoid_expensive_longs = _bool("AVOID_EXPENSIVE_LONGS", c.avoid_expensive_longs)
        c.crowd_window_days = float(_get("CROWD_WINDOW_DAYS", str(c.crowd_window_days)))
        c.rev_short_min_pctl = float(_get("REV_SHORT_MIN_PCTL", str(c.rev_short_min_pctl)))
        c.rev_short_min_funding_8h = float(_get("REV_SHORT_MIN_FUNDING_8H", str(c.rev_short_min_funding_8h)))
        c.rev_long_max_pctl = float(_get("REV_LONG_MAX_PCTL", str(c.rev_long_max_pctl)))
        c.rev_long_max_funding_8h = float(_get("REV_LONG_MAX_FUNDING_8H", str(c.rev_long_max_funding_8h)))
        c.rev_use_oi = _bool("REV_USE_OI", c.rev_use_oi)
        c.rev_oi_hours = float(_get("REV_OI_HOURS", str(c.rev_oi_hours)))
        c.rev_oi_min_pct = float(_get("REV_OI_MIN_PCT", str(c.rev_oi_min_pct)))
        c.funding_filter = _bool("FUNDING_FILTER", c.funding_filter)
        c.funding_limit_8h_pct = float(_get("FUNDING_LIMIT_8H_PCT", str(c.funding_limit_8h_pct)))
        c.oi_filter = _bool("OI_FILTER", c.oi_filter)
        c.oi_min_change_pct = float(_get("OI_MIN_CHANGE_PCT", str(c.oi_min_change_pct)))
        c.oi_lookback_hours = float(_get("OI_LOOKBACK_HOURS", str(c.oi_lookback_hours)))
        c.base = _get("BASE", c.base).upper()
        c.quote = _get("QUOTE", c.quote).upper()
        c.interval_min = int(_get("INTERVAL_MIN", str(c.interval_min)))
        c.side = _get("SIDE", c.side).lower()
        c.active_hours_utc = _get("ACTIVE_HOURS_UTC", c.active_hours_utc)
        c.max_signals_per_day = int(_get("MAX_SIGNALS_PER_DAY", str(c.max_signals_per_day)))
        c.daily_stop_r = float(_get("DAILY_STOP_R", str(c.daily_stop_r)))
        c.cooldown_bars = int(_get("COOLDOWN_BARS", str(c.cooldown_bars)))
        c.max_hold_bars = int(_get("MAX_HOLD_BARS", str(c.max_hold_bars)))
        c.account_size = float(_get("ACCOUNT_SIZE", str(c.account_size)))
        c.risk_pct = float(_get("RISK_PCT", str(c.risk_pct)))
        c.fee_pct = float(_get("FEE_PCT", str(c.fee_pct)))
        c.slippage_pct = float(_get("SLIPPAGE_PCT", str(c.slippage_pct)))
        c.poll_seconds = int(_get("POLL_SECONDS", str(c.poll_seconds)))
        c.state_file = _get("STATE_FILE", c.state_file)
        c.timezone = _get("TIMEZONE", c.timezone)
        if c.side not in ("long", "short", "both"):
            raise ValueError("SIDE tem de ser long, short ou both")
        if c.strategy not in ("reversal", "trend", "both"):
            raise ValueError("STRATEGY tem de ser reversal, trend ou both")
        return c
