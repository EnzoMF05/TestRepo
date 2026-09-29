"""Cliente mínimo da API pública da Hyperliquid (POST https://api.hyperliquid.xyz/info). Sem chaves.

Formatos (documentação oficial da Hyperliquid, confirmada por fontes secundárias):
  * candleSnapshot   -> [{"t","T","s","i","o","c","h","l","v","n"}, ...]  (só as ÚLTIMAS 5000 barras)
  * metaAndAssetCtxs -> [ {"universe":[{"name","maxLeverage",...}]}, [ {"funding","openInterest","markPx",
                          "oraclePx","premium","dayNtlVlm",...}, ... ] ]   (mesma ordem nas duas listas)

`funding` é a taxa HORÁRIA (a Hyperliquid cobra funding de hora a hora); `openInterest` está em unidades da moeda.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import requests

URL = "https://api.hyperliquid.xyz/info"
TIMEOUT = 15
MAX_CANDLES = 5000
_INTERVALS = {1: "1m", 3: "3m", 5: "5m", 15: "15m", 30: "30m", 60: "1h"}


class HLError(RuntimeError):
    pass


def _short(e: Exception) -> str:
    resp = getattr(e, "response", None)
    return f"HTTP {resp.status_code}" if resp is not None else type(e).__name__


def post_info(payload: dict) -> Any:
    try:
        r = requests.post(URL, json=payload, timeout=TIMEOUT, headers={"User-Agent": "zec-bot/1.0"})
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as e:
        raise HLError(f"hyperliquid: {_short(e)}") from e


def parse_candles(payload: Any) -> list[list]:
    if not isinstance(payload, list):
        raise HLError(f"hyperliquid: resposta inesperada ({str(payload)[:80]})")
    try:
        rows = [[int(r["t"]), float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]), float(r["v"])] for r in payload]
    except (KeyError, TypeError, ValueError) as e:
        raise HLError(f"hyperliquid: candle com formato inesperado ({e!r})") from e
    return sorted(rows, key=lambda r: r[0])


def fetch_candles(coin: str, interval_min: int, end_ms: Optional[int] = None, limit: int = MAX_CANDLES) -> list[list]:
    """Até `limit` barras (ascendentes) terminadas em `end_ms` (ou agora). Inclui a barra em formação."""
    if interval_min not in _INTERVALS:
        raise HLError(f"intervalo não suportado: {interval_min}m")
    end_ms = end_ms or int(time.time() * 1000)
    start_ms = end_ms - min(limit, MAX_CANDLES) * interval_min * 60_000
    payload = {"type": "candleSnapshot",
               "req": {"coin": coin, "interval": _INTERVALS[interval_min], "startTime": start_ms, "endTime": end_ms}}
    return parse_candles(post_info(payload))


def parse_asset_ctx(payload: Any, coin: str) -> dict:
    """Extrai do metaAndAssetCtxs a linha da `coin`, com números já convertidos."""
    try:
        meta, ctxs = payload[0], payload[1]
        for info, ctx in zip(meta["universe"], ctxs):
            if info.get("name") == coin and not info.get("isDelisted"):
                return {
                    "coin": coin,
                    "funding_hr": float(ctx["funding"]),  # fração por HORA (0.0000125 = 0.00125%/h)
                    "oi_coins": float(ctx["openInterest"]),
                    "mark": float(ctx["markPx"]),
                    "oracle": float(ctx["oraclePx"]),
                    "premium": float(ctx.get("premium") or 0.0),
                    "day_vol_usd": float(ctx.get("dayNtlVlm") or 0.0),
                    "max_leverage": float(info.get("maxLeverage") or 0.0),
                }
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise HLError(f"hyperliquid: metaAndAssetCtxs com formato inesperado ({e!r})") from e
    raise HLError(f"hyperliquid: moeda {coin!r} não encontrada (ou delisted)")


def fetch_asset_ctx(coin: str) -> dict:
    return parse_asset_ctx(post_info({"type": "metaAndAssetCtxs"}), coin)
