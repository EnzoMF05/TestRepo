"""Sensores de OI 1H da Binance (CoinGlass e Coinalyze) e a cascata da mesa. Port de oi_coinglass_1h.py e
oi_coinalyze.py: mesmos URLs, cabeçalhos, parâmetros e regra de escolha da vela.

Cada sensor devolve o delta % do OI DENTRO da vela 1H pedida: (close - open) / open * 100, da barra de OI cujo
timestamp coincide com a abertura da vela (nunca "a última", que já deu a hora errada). Se a fonte ainda não tem
essa vela, devolve None: não adivinha.

Ambas as fontes espelham o perp USDT da BINANCE (é o "macro mirror" da mesa), não o OI da Hyperliquid.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

import requests

log = logging.getLogger("zec_bot.oi")

H1 = 3600
H1_MS = H1 * 1000
CG_BASE = "https://open-api-v4.coinglass.com"
CG_PATH = "/api/futures/open-interest/history"
CA_URL = "https://api.coinalyze.net/v1/open-interest-history"

HttpGet = Callable[[str, dict, dict], Any]


def http_get(url: str, params: dict, headers: dict) -> Any:
    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()


def _to_ms(ts: Any) -> int:
    ts = int(ts)
    return ts * 1000 if ts < 10**12 else ts


class CoinalyzeOI:
    label = "Coinalyze"

    def __init__(self, api_key: str, base: str, get: HttpGet = http_get, now: Callable[[], float] = time.time):
        self.key, self.symbol, self._get, self._now = api_key, f"{base.upper()}USDT_PERP.A", get, now

    def fetch(self, candle_open_ms: int) -> tuple[Optional[float], Optional[str]]:
        """(delta %, erro). erro None + delta None = a fonte ainda não tem a vela."""
        if not self.key:
            return None, "sem_key"
        now = int(self._now())
        try:
            data = self._get(CA_URL, {"symbols": self.symbol, "interval": "1hour", "from": now - 6 * H1, "to": now},
                             {"User-Agent": "zec-bot/oi_coinalyze", "api_key": self.key})
        except Exception as e:  # noqa: BLE001 — qualquer falha de rede/JSON: None, como o sensor original
            return None, type(e).__name__
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            return None, "resposta_invalida"
        rows = []
        for v in data[0].get("history") or []:
            try:
                rows.append({"t": int(v["t"]), "o": float(v["o"]), "c": float(v["c"])})  # t em SEGUNDOS
            except (KeyError, TypeError, ValueError):
                continue
        alvo = candle_open_ms // 1000
        for r in sorted(rows, key=lambda x: x["t"]):
            if abs(r["t"] - alvo) < 60:
                return (None, "open_zero") if r["o"] == 0 else (round((r["c"] - r["o"]) / r["o"] * 100, 2), None)
        return None, None


class CoinGlassOI:
    label = "CG1H"

    def __init__(self, api_key: str, base: str, get: HttpGet = http_get):
        self.key, self.symbol, self._get = api_key, f"{base.upper()}USDT", get

    def fetch(self, candle_open_ms: int) -> tuple[Optional[float], Optional[str]]:
        if not self.key:
            return None, "sem_key"
        try:
            body = self._get(f"{CG_BASE}{CG_PATH}",
                             {"exchange": "Binance", "symbol": self.symbol, "interval": "1h", "limit": 8},
                             {"CG-API-KEY": self.key, "accept": "application/json", "User-Agent": "zec-bot/oi_coinglass_1h"})
        except Exception as e:  # noqa: BLE001
            return None, type(e).__name__
        if not isinstance(body, dict) or str(body.get("code")) != "0" or not isinstance(body.get("data"), list):
            return None, "resposta_invalida"
        rows = []
        for item in body["data"]:
            try:
                if isinstance(item, dict):
                    ts = item.get("t") or item.get("time") or item.get("timestamp")
                    o, c = item.get("o") or item.get("open"), item.get("c") or item.get("close")
                    if ts is None or c is None or o is None:
                        continue
                    rows.append({"t": _to_ms(ts), "o": float(o), "c": float(c)})
                elif isinstance(item, list) and len(item) >= 5:
                    rows.append({"t": _to_ms(item[0]), "o": float(item[1]), "c": float(item[4])})
            except (TypeError, ValueError):
                continue
        for r in sorted(rows, key=lambda x: x["t"]):
            if abs(r["t"] - candle_open_ms) < 60_000:
                return (None, "open_zero") if r["o"] == 0 else (round((r["c"] - r["o"]) / r["o"] * 100, 2), None)
        return None, None


class OiCascade:
    """CoinGlass 1H -> Coinalyze 1H (a cascata da mesa, sem o fallback 4H). Repete se nenhuma tiver a vela ainda."""

    def __init__(self, sources: list, attempts: int = 3, retry_s: float = 20.0,
                 sleep: Callable[[float], None] = time.sleep):
        self.sources, self.attempts, self.retry_s, self._sleep = sources, max(1, attempts), retry_s, sleep

    @classmethod
    def from_cfg(cls, cfg, get: HttpGet = http_get) -> "OiCascade":
        srcs = []
        if cfg.coinglass_api_key:
            srcs.append(CoinGlassOI(cfg.coinglass_api_key, cfg.base, get))
        if cfg.coinalyze_api_key:
            srcs.append(CoinalyzeOI(cfg.coinalyze_api_key, cfg.base, get))
        return cls(srcs, cfg.sweep_oi_attempts, cfg.sweep_oi_retry_s)

    def delta(self, candle_open_ms: int) -> dict:
        """{"delta": float|None, "src": rótulo|"none", "errors": [...]}. Nunca lança."""
        if not self.sources:
            return {"delta": None, "src": "none", "errors": ["sem_keys (COINGLASS_API_KEY / COINALYZE_API_KEY)"]}
        errors: list[str] = []
        for attempt in range(self.attempts):
            errors = []
            for s in self.sources:
                value, err = s.fetch(candle_open_ms)
                if value is not None:
                    return {"delta": value, "src": s.label, "errors": errors}
                errors.append(f"{s.label}:{err or 'sem_vela'}")
            if attempt < self.attempts - 1:
                log.info("OI 1H ainda sem a vela (%s); nova tentativa em %ss", "; ".join(errors), self.retry_s)
                self._sleep(self.retry_s)
        return {"delta": None, "src": "none", "errors": errors}
