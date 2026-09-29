"""Cliente (opcional) da API do Coinalyze para o histórico de open interest.

Só é usado se definires COINALYZE_API_KEY (a chave gratuita do Coinalyze). Serve para teres o histórico
de OI logo desde o arranque, em vez de esperares que o robô o vá recolhendo.

Confirmado: base https://api.coinalyze.net/v1, chave em `api_key` (header), limite de 40 chamadas/min,
/open-interest-history -> [{"symbol", "history": [{"t","o","h","l","c"}]}], e o Coinalyze só guarda
1500-2000 pontos por intervalo intradiário (15min ≈ 2-3 semanas).
Assumido a partir da documentação pública, ainda por confirmar contra o serviço real: nomes dos parâmetros
(symbols, interval, from, to em segundos), /exchanges e /future-markets. `python -m zec_bot --check` testa tudo.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

import pandas as pd
import requests

BASE = "https://api.coinalyze.net/v1"
TIMEOUT = 15


class CoinalyzeError(RuntimeError):
    pass


def _to_utc(t: float) -> pd.Timestamp:
    """O Coinalyze usa segundos; aceita também milissegundos por segurança."""
    return pd.Timestamp(t, unit="ms" if t > 1e11 else "s", tz="UTC")


class Coinalyze:
    def __init__(self, api_key: str, symbol: str = "", coin: str = "ZEC",
                 getter: Optional[Callable[[str, dict], Any]] = None):
        self.api_key = api_key
        self.symbol = symbol
        self.coin = coin.upper()
        self._get_json = getter or self._http_get

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _http_get(self, path: str, params: dict) -> Any:
        try:
            r = requests.get(BASE + path, params=params, headers={"api_key": self.api_key},
                             timeout=TIMEOUT)
        except requests.RequestException as e:
            raise CoinalyzeError(f"coinalyze: {type(e).__name__}") from e
        if r.status_code == 429:
            raise CoinalyzeError(f"coinalyze: limite de chamadas atingido (Retry-After {r.headers.get('Retry-After', '?')}s)")
        if r.status_code in (401, 403):
            raise CoinalyzeError("coinalyze: chave inválida (COINALYZE_API_KEY)")
        if r.status_code != 200:
            raise CoinalyzeError(f"coinalyze: HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise CoinalyzeError("coinalyze: resposta não é JSON") from e

    def resolve_symbol(self) -> str:
        """Símbolo do perp da Hyperliquid para `coin` (ex. definido em COINALYZE_SYMBOL, ou descoberto)."""
        if self.symbol:
            return self.symbol
        exchanges = self._get_json("/exchanges", {})
        codes = [e.get("code") for e in exchanges if "hyperliquid" in str(e.get("name", "")).lower()]
        if not codes:
            raise CoinalyzeError("coinalyze: 'Hyperliquid' não aparece na lista de exchanges")
        markets = self._get_json("/future-markets", {})
        found = [m for m in markets
                 if m.get("exchange") in codes and str(m.get("base_asset", "")).upper() == self.coin
                 and m.get("is_perpetual", True)]
        if not found:
            raise CoinalyzeError(f"coinalyze: sem perp de {self.coin} na Hyperliquid (define COINALYZE_SYMBOL)")
        self.symbol = found[0]["symbol"]
        return self.symbol

    def oi_history(self, interval: str = "15min", hours: float = 6.0, now: Optional[float] = None) -> pd.DataFrame:
        """Open interest (OHLC do OI) das últimas `hours`, índice UTC = início de cada barra."""
        symbol = self.resolve_symbol()
        to = int(now if now is not None else time.time())
        params = {"symbols": symbol, "interval": interval, "from": to - int(hours * 3600), "to": to}
        data = self._get_json("/open-interest-history", params)
        try:
            history = data[0]["history"]
            rows = [(_to_utc(h["t"]), float(h["o"]), float(h["h"]), float(h["l"]), float(h["c"])) for h in history]
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise CoinalyzeError(f"coinalyze: open-interest-history com formato inesperado ({e!r})") from e
        if not rows:
            raise CoinalyzeError("coinalyze: sem dados de OI para o período (símbolo certo?)")
        df = pd.DataFrame(rows, columns=["time", "o", "h", "l", "c"]).drop_duplicates("time").set_index("time")
        return df.sort_index()
