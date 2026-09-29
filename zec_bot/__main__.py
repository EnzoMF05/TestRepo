"""Uso:
    python -m zec_bot            # corre o robô
    python -m zec_bot --check    # testa dados + Telegram sem ficar a correr
"""
from __future__ import annotations

import argparse
import logging
import sys

from .bot import Bot
from .config import Config
from .data import get_candles
from .messages import _px
from .strategy import Params, prepare
from .telegram import Telegram


def check(cfg: Config) -> int:
    p = Params()
    print(f"Config: {cfg.base}/{cfg.quote} · {cfg.interval_str} · exchange={cfg.exchange} · lado={cfg.side} · "
          f"conta={cfg.account_size:g} · risco={cfg.risk_pct:g}%")
    print(f"Telegram configurado: {'sim' if cfg.telegram_ready else 'NÃO (as mensagens vão para a consola)'}")

    print("\n1) A obter candles…")
    try:
        df, ex = get_candles(cfg.exchange, cfg.base, cfg.quote, cfg.interval_min, p.warmup_bars + 50)
    except Exception as e:  # noqa: BLE001
        print(f"   ✖ FALHOU: {e}")
        return 1
    print(f"   ✔ {len(df)} barras de {ex}; última barra fechada: {df.index[-1]} · close {_px(df['close'].iloc[-1])}")

    feat = prepare(df, p)
    last = feat.iloc[-1]
    n_sig = int((feat["sig"] != 0).iloc[-400:].sum())
    print(f"\n2) Estado atual: tendência 1h={'alta' if last['htf'] > 0 else 'baixa' if last['htf'] < 0 else 'neutra'} · "
          f"ADX {last['adx']:.1f} · RSI {last['rsi']:.1f} · ATR {last['atr'] / last['close'] * 100:.2f}%")
    print(f"   Sinais brutos nas últimas 400 barras: {n_sig}")

    print("\n3) A enviar mensagem de teste para o Telegram…")
    ok = Telegram(cfg.telegram_token, cfg.telegram_chat_id).send(
        f"✅ Teste do robô {cfg.base}: dados OK ({ex}), preço {_px(df['close'].iloc[-1])}."
    )
    print("   ✔ enviada" if ok else "   ✖ FALHOU (verifica TELEGRAM_TOKEN e TELEGRAM_CHAT_ID; falaste com o bot primeiro?)")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="zec_bot")
    ap.add_argument("--check", action="store_true", help="testa dados e Telegram e sai")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config.from_env()
    if args.check:
        return check(cfg)
    Bot(cfg).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
