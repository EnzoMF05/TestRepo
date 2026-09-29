"""Uso:
    python -m zec_bot            # corre o robô
    python -m zec_bot --check    # testa dados + Telegram sem ficar a correr
"""
from __future__ import annotations

import argparse
import logging
import sys

from .bot import Bot
from .coinalyze import Coinalyze
from .config import Config
from .data import get_candles
from .derivs import DerivsMonitor
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
        df, ex = get_candles(cfg.exchange, cfg.base, cfg.quote, cfg.interval_min, p.warmup_bars + 50,
                             allow_fallback=cfg.allow_fallback)
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

    print("\n3) Derivados (funding / open interest)…")
    mon = DerivsMonitor(cfg)
    snap = mon.snapshot()
    if snap is None:
        print(f"   ✖ Hyperliquid (metaAndAssetCtxs) FALHOU: {mon.last_error}")
    else:
        print(f"   ✔ Hyperliquid: funding {snap['funding_8h_pct']:+.4f}%/8h ({snap['funding_hr_pct']:+.5f}%/h) · "
              f"prémio {snap['premium_pct']:+.3f}% · OI {snap['oi_coins']:,.0f} {cfg.base} ≈ {snap['oi_usd'] / 1e6:.2f}M$ · "
              f"alavancagem máx. {snap['max_leverage']:.0f}x")
        print("     (a variação de OI só aparece depois de o robô recolher amostras ao longo de 1h; "
              "com Coinalyze aparece já)")
    if not cfg.coinalyze_api_key:
        print("   – Coinalyze: não configurado (opcional: COINALYZE_API_KEY dá histórico de OI logo no arranque)")
    else:
        cz = Coinalyze(cfg.coinalyze_api_key, cfg.coinalyze_symbol, cfg.base)
        try:
            hist = cz.oi_history("15min", hours=6)
            print(f"   ✔ Coinalyze: símbolo {cz.symbol} · {len(hist)} barras de 15m · último OI {hist['c'].iloc[-1]:,.0f} "
                  f"(compara com o site; se o símbolo estiver errado define COINALYZE_SYMBOL)")
            oi1, _ = DerivsMonitor(cfg, coinalyze=cz).oi_change_pct(1.0)
            print(f"     variação de OI na última hora: {'n/d' if oi1 is None else f'{oi1:+.2f}%'}")
        except Exception as e:  # noqa: BLE001
            print(f"   ✖ Coinalyze FALHOU: {e}")

    print("\n4) A enviar mensagem de teste para o Telegram…")
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
