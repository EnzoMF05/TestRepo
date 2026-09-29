"""Obtenção de candles OHLCV de exchanges (APIs públicas, sem chaves).

Cada `_fetch_*` devolve uma lista de linhas [open_time_ms, open, high, low, close, volume] em
ordem CRESCENTE, com no máximo `limit` barras terminadas em `end_ms` (ou as mais recentes).
`get_candles` junta as páginas necessárias, normaliza para um DataFrame indexado em UTC e
descarta a barra ainda em formação, para que o resto do robô só veja barras FECHADAS.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import pandas as pd
import requests

log = logging.getLogger("zec_bot.data")

TIMEOUT = 15
EXCHANGES = ["binance", "bybit", "okx", "kraken"]

_BINANCE_INTERVAL = {1: "1m", 5: "5m", 15: "15m", 30: "30m", 60: "1h"}
_OKX_INTERVAL = {1: "1m", 5: "5m", 15: "15m", 30: "30m", 60: "1H"}


class DataError(RuntimeError):
    pass


def _get(url: str, params: dict) -> dict | list:
    r = requests.get(url, params=params, timeout=TIMEOUT, headers={"User-Agent": "zec-bot/1.0"})
    r.raise_for_status()
    return r.json()


def _short(e: Exception) -> str:
    """Erro de rede em poucas palavras (o detalhe completo fica no nível DEBUG)."""
    log.debug("detalhe do erro: %r", e)
    resp = getattr(e, "response", None)
    return f"HTTP {resp.status_code}" if resp is not None else type(e).__name__


def _f(row: list) -> list:
    return [int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])]


# ---------------------------------------------------------------------------- parsers
def _parse_binance(payload: list) -> list[list]:
    # [openTime, o, h, l, c, v, closeTime, ...]
    return [_f(r) for r in payload]


def _parse_bybit(payload: dict) -> list[list]:
    if str(payload.get("retCode")) != "0":
        raise DataError(f"bybit: {payload.get('retMsg')}")
    # [startTime, o, h, l, c, volume, turnover], mais recente primeiro
    return sorted((_f(r) for r in payload["result"]["list"]), key=lambda r: r[0])


def _parse_okx(payload: dict) -> list[list]:
    if str(payload.get("code")) != "0":
        raise DataError(f"okx: {payload.get('msg')}")
    # [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm], mais recente primeiro
    return sorted((_f(r) for r in payload["data"]), key=lambda r: r[0])


def _parse_kraken(payload: dict) -> list[list]:
    if payload.get("error"):
        raise DataError(f"kraken: {payload['error']}")
    result = payload["result"]
    key = next(k for k in result if k != "last")
    # [time_s, o, h, l, c, vwap, volume, count]
    return [[int(r[0]) * 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[6])] for r in result[key]]


# ---------------------------------------------------------------------------- fetchers
def _fetch_binance(base: str, quote: str, interval: int, end_ms: Optional[int], limit: int = 1000) -> list[list]:
    params = {"symbol": f"{base}{quote}", "interval": _BINANCE_INTERVAL[interval], "limit": min(limit, 1000)}
    if end_ms:
        params["endTime"] = end_ms
    last_err: Exception | None = None
    for host in ("https://api.binance.com", "https://data-api.binance.vision"):
        try:
            return _parse_binance(_get(f"{host}/api/v3/klines", params))
        except (requests.RequestException, ValueError) as e:
            last_err = e
    raise DataError(f"binance: {_short(last_err)}")


def _fetch_bybit(base: str, quote: str, interval: int, end_ms: Optional[int], limit: int = 1000) -> list[list]:
    params = {"category": "linear", "symbol": f"{base}{quote}", "interval": str(interval), "limit": min(limit, 1000)}
    if end_ms:
        params["end"] = end_ms
    return _parse_bybit(_get("https://api.bybit.com/v5/market/kline", params))


def _fetch_okx(base: str, quote: str, interval: int, end_ms: Optional[int], limit: int = 100) -> list[list]:
    params = {"instId": f"{base}-{quote}", "bar": _OKX_INTERVAL[interval], "limit": min(limit, 100)}
    if end_ms:
        params["after"] = end_ms + 1  # devolve barras com ts < after
    return _parse_okx(_get("https://www.okx.com/api/v5/market/history-candles", params))


def _fetch_kraken(base: str, quote: str, interval: int, end_ms: Optional[int], limit: int = 720) -> list[list]:
    # Kraken só guarda ~720 barras e usa USD em vez de USDT; serve como reserva para o modo live.
    pair = f"{'XBT' if base == 'BTC' else base}{'USD' if quote.startswith('USD') else quote}"
    return _parse_kraken(_get("https://api.kraken.com/0/public/OHLC", {"pair": pair, "interval": interval}))


_FETCHERS: dict[str, Callable[..., list[list]]] = {
    "binance": _fetch_binance,
    "bybit": _fetch_bybit,
    "okx": _fetch_okx,
    "kraken": _fetch_kraken,
}
_PAGEABLE = {"binance", "bybit", "okx"}


# ---------------------------------------------------------------------------- API pública
def to_frame(rows: list[list]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("ts").sort_values("ts")
    df.index = pd.to_datetime(df.pop("ts"), unit="ms", utc=True)
    df.index.name = "time"
    return df.astype("float64")


def drop_open_bar(df: pd.DataFrame, interval_min: int, now: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    """Remove a última barra se ainda não fechou."""
    now = now or pd.Timestamp.now(tz="UTC")
    closed = df.index + pd.Timedelta(minutes=interval_min) <= now
    return df[closed]


def fetch_exchange(exchange: str, base: str, quote: str, interval: int, n_bars: int) -> pd.DataFrame:
    """Vai buscar as últimas `n_bars` barras (fechadas) a UMA exchange, paginando se preciso."""
    if exchange not in _FETCHERS:
        raise DataError(f"exchange desconhecida: {exchange}")
    fetch = _FETCHERS[exchange]
    rows: list[list] = []
    end_ms: Optional[int] = None
    seen_first = None
    for _ in range(200):
        try:
            page = fetch(base, quote, interval, end_ms)
        except (requests.RequestException, ValueError, KeyError) as e:
            raise DataError(f"{exchange}: {_short(e)}") from e
        if not page:
            break
        rows = page + rows
        first = page[0][0]
        if len(rows) >= n_bars or exchange not in _PAGEABLE or first == seen_first:
            break
        seen_first = first
        end_ms = first - 1
        time.sleep(0.15)  # boa educação com os limites de pedidos
    if not rows:
        raise DataError(f"{exchange}: sem dados para {base}/{quote}")
    df = drop_open_bar(to_frame(rows), interval)
    return df.iloc[-n_bars:]


def get_candles(preferred: str, base: str, quote: str, interval: int, n_bars: int) -> tuple[pd.DataFrame, str]:
    """Tenta a exchange preferida e depois as restantes. Devolve (df, exchange_usada)."""
    order = [preferred] + [e for e in EXCHANGES if e != preferred]
    errors = []
    for ex in order:
        try:
            return fetch_exchange(ex, base, quote, interval, n_bars), ex
        except DataError as e:
            log.warning("Falhou %s: %s", ex, e)
            errors.append(str(e))
    raise DataError("Nenhuma exchange respondeu: " + " | ".join(errors))
