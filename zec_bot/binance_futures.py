"""Dados públicos dos futuros USDⓈ-M da Binance (funding, open interest). Sem chaves.

Formatos (documentação oficial da Binance):
  * GET /fapi/v1/premiumIndex?symbol=      -> {"markPrice","indexPrice","lastFundingRate","nextFundingTime","time",...}
  * GET /fapi/v1/openInterest?symbol=      -> {"openInterest","symbol","time"}                 (OI atual, em moedas)
  * GET /fapi/v1/fundingRate?symbol=&startTime=&limit=1000
                                           -> [{"symbol","fundingRate","fundingTime","markPrice"}]
  * GET /futures/data/openInterestHist?symbol=&period=15m&limit=500&startTime=&endTime=
                                           -> [{"sumOpenInterest","sumOpenInterestValue","timestamp"}]
                                              (só os últimos ~30 dias)

O intervalo de funding NÃO é sempre 8h (a Binance muda-o por símbolo: 8h/4h/1h), por isso deduz-se sempre do
espaçamento entre registos em vez de se assumir.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import requests

BASE = "https://fapi.binance.com"
TIMEOUT = 15
OI_MAX_DAYS = 29.5  # a API só guarda ~30 dias de histórico de OI


class BinanceError(RuntimeError):
    pass


def _get(path: str, params: dict) -> Any:
    try:
        r = requests.get(BASE + path, params=params, timeout=TIMEOUT, headers={"User-Agent": "zec-bot/1.0"})
    except requests.RequestException as e:
        raise BinanceError(f"binance futuros: {type(e).__name__}") from e
    if r.status_code == 451:
        raise BinanceError("binance futuros: HTTP 451 (região bloqueada)")
    if r.status_code != 200:
        try:
            msg = r.json().get("msg", "")
        except (ValueError, AttributeError):
            msg = ""
        raise BinanceError(f"binance futuros: HTTP {r.status_code} {msg}".strip())
    try:
        return r.json()
    except ValueError as e:
        raise BinanceError("binance futuros: resposta não é JSON") from e


def _check_list(payload: Any) -> list:
    if isinstance(payload, dict) and "code" in payload:
        raise BinanceError(f"binance futuros: {payload.get('msg', payload)}")
    if not isinstance(payload, list):
        raise BinanceError(f"binance futuros: resposta inesperada ({str(payload)[:80]})")
    return payload


# ---------------------------------------------------------------------------- parsers
def parse_premium_index(payload: Any) -> dict:
    try:
        return {
            "mark": float(payload["markPrice"]),
            "index": float(payload["indexPrice"]),
            "funding_last": float(payload["lastFundingRate"]),  # fração por intervalo de funding (já liquidada)
            "next_funding_ms": int(payload["nextFundingTime"]),
            "time": int(payload["time"]),
        }
    except (KeyError, TypeError, ValueError) as e:
        raise BinanceError(f"binance futuros: premiumIndex com formato inesperado ({e!r})") from e


def parse_open_interest(payload: Any) -> dict:
    try:
        return {"oi_coins": float(payload["openInterest"]), "time": int(payload["time"])}
    except (KeyError, TypeError, ValueError) as e:
        raise BinanceError(f"binance futuros: openInterest com formato inesperado ({e!r})") from e


def parse_funding_history(payload: Any) -> list[list]:
    """[[fundingTime_ms, taxa_por_intervalo], ...] ascendente."""
    try:
        return sorted(([int(r["fundingTime"]), float(r["fundingRate"])] for r in _check_list(payload)),
                      key=lambda r: r[0])
    except (KeyError, TypeError, ValueError) as e:
        raise BinanceError(f"binance futuros: fundingRate com formato inesperado ({e!r})") from e


def parse_oi_hist(payload: Any) -> list[list]:
    """[[timestamp_ms, oi_em_moedas, oi_em_usd], ...] ascendente."""
    try:
        return sorted(([int(r["timestamp"]), float(r["sumOpenInterest"]), float(r["sumOpenInterestValue"])]
                       for r in _check_list(payload)), key=lambda r: r[0])
    except (KeyError, TypeError, ValueError) as e:
        raise BinanceError(f"binance futuros: openInterestHist com formato inesperado ({e!r})") from e


def interval_hours(times_ms: list[int], default: float = 8.0) -> list[float]:
    """Intervalo de funding (horas) de cada registo = distância ao registo anterior (o 1º usa o 2º)."""
    if len(times_ms) < 2:
        return [default] * len(times_ms)
    gaps = [(b - a) / 3_600_000 for a, b in zip(times_ms, times_ms[1:])]
    return [gaps[0]] + gaps


# ---------------------------------------------------------------------------- pedidos
def fetch_funding_history(symbol: str, start_ms: int, end_ms: Optional[int] = None, max_pages: int = 60) -> list[list]:
    out: list[list] = []
    cursor = start_ms
    for _ in range(max_pages):
        params = {"symbol": symbol, "startTime": cursor, "limit": 1000}
        if end_ms:
            params["endTime"] = end_ms
        page = parse_funding_history(_get("/fapi/v1/fundingRate", params))
        if not page:
            break
        out += page
        if len(page) < 1000:
            break
        cursor = page[-1][0] + 1
        time.sleep(0.2)
    dedup = {r[0]: r for r in out}
    return [dedup[k] for k in sorted(dedup)]


def fetch_oi_hist(symbol: str, start_ms: int, end_ms: Optional[int] = None, period: str = "15m",
                  max_pages: int = 40, now_ms: Optional[int] = None) -> list[list]:
    """OI histórico em barras de `period`. Corta o início para dentro dos ~30 dias que a Binance guarda."""
    now_ms = now_ms or int(time.time() * 1000)
    cursor = max(start_ms, now_ms - int(OI_MAX_DAYS * 86_400_000))
    out: list[list] = []
    for _ in range(max_pages):
        params = {"symbol": symbol, "period": period, "limit": 500, "startTime": cursor}
        if end_ms:
            params["endTime"] = end_ms
        page = parse_oi_hist(_get("/futures/data/openInterestHist", params))
        if not page:
            break
        out += page
        if len(page) < 500:
            break
        cursor = page[-1][0] + 1
        time.sleep(0.2)
    dedup = {r[0]: r for r in out}
    return [dedup[k] for k in sorted(dedup)]


class BinanceFutures:
    """Cliente ligado a um símbolo (ex.: ZECUSDT). Os testes substituem-no por um falso."""

    def __init__(self, symbol: str):
        self.symbol = symbol

    def premium_index(self) -> dict:
        return parse_premium_index(_get("/fapi/v1/premiumIndex", {"symbol": self.symbol}))

    def open_interest(self) -> dict:
        return parse_open_interest(_get("/fapi/v1/openInterest", {"symbol": self.symbol}))

    def funding_history(self, start_ms: int) -> list[list]:
        return fetch_funding_history(self.symbol, start_ms)

    def oi_history(self, start_ms: int) -> list[list]:
        return fetch_oi_hist(self.symbol, start_ms)
