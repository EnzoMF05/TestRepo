"""Uso:
    python -m zec_bot            # corre o robô
    python -m zec_bot --check    # testa dados + Telegram sem ficar a correr
"""
from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

from .bot import Bot
from .config import Config
from .data import get_candles
from .derivs import DerivsMonitor
from .messages import _px
from .oi_hourly import OiCascade
from .strategy import Params, prepare
from .sweep import SweepParams, atr14, best_tier, evaluate, fmt_px, hourly_candles, level_age_h, load_levels, pick_event
from .telegram import Telegram


def _crowd_state(row, p: Params) -> str:
    f8, pc = row["funding_8h_pct"], row["funding_pctl"]
    if pd.isna(f8) or pd.isna(pc):
        return "sem dados suficientes"
    if f8 >= p.rev_short_min_funding_8h and pc >= p.rev_short_min_pctl:
        return "LONGS sobrelotados → a estratégia só procura SHORTS de reversão"
    if f8 <= p.rev_long_max_funding_8h and pc <= p.rev_long_max_pctl:
        return "SHORTS sobrelotados (extremo) → a estratégia só procura LONGS de reversão"
    return "neutro → sem reversões possíveis agora"


def check_sweep(cfg: Config, df: pd.DataFrame) -> None:
    """Valida o pipeline do sweep com dados reais: níveis, OI e a avaliação da ÚLTIMA vela 1H fechada."""
    print("\n   Sweep de nível (método da mesa):")
    cluster, err = load_levels(cfg.niveis_path, cfg.base)
    if cluster is None:
        print(f"   ✖ níveis: {err}\n     Sem níveis o modo sweep não avalia nada. Copia o niveis.csv da mesa para {cfg.niveis_path} "
              f"(colunas: ticker,cluster_below,cluster_above,spot_approx,atualizado_utc) ou define NIVEIS_PATH.")
        return
    age = level_age_h(cluster, pd.Timestamp.now(tz="UTC").to_pydatetime())
    print(f"   ✔ níveis {cfg.base}: abaixo {cluster['below']} · acima {cluster['above']} · spot≈{cluster['spot_approx']} · "
          f"idade {'n/d' if age is None else f'{age:.1f}h'}" + (f" · ⚠ {cluster['erro']}" if cluster.get("erro") else ""))
    if age is not None and age > 6:
        print("     ⚠ níveis com mais de 6h: confirma que o teu feeder os atualiza (NIVEIS_MAX_AGE_H bloqueia se quiseres).")
    cs = hourly_candles(df, cfg.interval_min, last_hours=45)
    if len(cs) < 15:
        print("   ✖ velas 1H insuficientes para o ATR14")
        return
    candle, atr = cs[-1], atr14(cs)
    oi = OiCascade.from_cfg(cfg).delta(candle["t"])
    ts = pd.Timestamp(candle["t"], unit="ms", tz="UTC")
    print(f"   Última vela 1H fechada ({ts:%Y-%m-%d %H:%M}Z): O {candle['open']} H {candle['high']} L {candle['low']} "
          f"C {candle['close']} · ATR14 {atr:.4f}")
    if oi["delta"] is None:
        print(f"   ✖ OI 1H: sem valor ({'; '.join(oi['errors'])}). Sem OI o sinal nunca passa de L1.")
    else:
        print(f"   ✔ OI 1H desta vela: {oi['delta']:+.2f}% ({oi['src']}) — compara com o Coinalyze/CoinGlass (ZECUSDT, Binance, 1H)")
    evs = evaluate(cluster, candle, atr, oi["delta"], SweepParams.from_cfg(cfg))
    ev = pick_event(evs)
    if ev is None:
        print(f"   Avaliação: {evs[0]['motivo']} (sem varredura de nenhum nível)")
    else:
        print(f"   Avaliação: tier {best_tier(evs)} · {ev['lado']} · {ev['motivo']} · pavio {ev['rejeicao']:.0f}% "
              f"· face {fmt_px(ev['level'])} · válido={ev['valido']}")


def check(cfg: Config) -> int:
    p = Params.from_cfg(cfg)
    print(f"Config: {cfg.base} · {cfg.interval_str} · candles={cfg.exchange} · derivados=Binance {cfg.derivs_symbol} · "
          f"estratégia={cfg.strategy} · lado={cfg.side} · conta={cfg.account_size:g} · risco={cfg.risk_pct:g}%")
    print(f"Telegram configurado: {'sim' if cfg.telegram_ready else 'NÃO (as mensagens vão para a consola)'}")

    print("\n1) A obter candles…")
    try:
        df, ex = get_candles(cfg.exchange, cfg.base, cfg.quote, cfg.interval_min, p.warmup_bars + 50,
                             allow_fallback=cfg.allow_fallback)
    except Exception as e:  # noqa: BLE001
        print(f"   ✖ FALHOU: {e}")
        return 1
    print(f"   ✔ {len(df)} barras de {ex}; última barra fechada: {df.index[-1]} · close {_px(df['close'].iloc[-1])}")

    print("\n2) Derivados da Binance (funding / open interest)…")
    mon = DerivsMonitor(cfg)
    frame = mon.frame(df.index)
    snap = mon.snapshot(frame_row=None if frame is None else frame.iloc[-1])
    if frame is None or snap is None:
        print(f"   ✖ FALHOU: {mon.last_error}")
        if cfg.needs_derivs:
            print("     Sem isto a estratégia de reversão não gera sinais. Resolve antes de avançar.")
    else:
        rows = mon.funding_rows()
        pc = snap["funding_pctl"]
        print(f"   ✔ funding {snap['funding_8h_pct']:+.4f}%/8h ({'percentil n/d' if pc is None else f'P{pc:.0f}'} dos últimos "
              f"{cfg.crowd_window_days:g}d, {len(rows)} registos) · prémio {snap['premium_pct']:+.3f}%")
        print(f"     OI {snap['oi_coins']:,.0f} {cfg.base} ≈ {snap['oi_usd'] / 1e6:.2f}M$ · variação 1h "
              f"{'n/d' if snap['oi_change_1h_pct'] is None else format(snap['oi_change_1h_pct'], '+.2f') + '%'} · "
              f"4h {'n/d' if snap['oi_change_4h_pct'] is None else format(snap['oi_change_4h_pct'], '+.2f') + '%'} · "
              f"{cfg.rev_oi_hours:g}h {'n/d' if snap['oi_rev_pct'] is None else format(snap['oi_rev_pct'], '+.2f') + '%'}")
        if snap["hl_funding_8h_pct"] is not None:
            print(f"     Hyperliquid (onde pagas): funding {snap['hl_funding_8h_pct']:+.4f}%/8h · "
                  f"alavancagem máx. {snap['max_leverage']:.0f}x")
        print("     (compara estes números com o que vês no Coinalyze para ZECUSDT da Binance)")
        print(f"   Estado de crowding agora: {_crowd_state(frame.iloc[-1], p)}")

    if cfg.strategy == "sweep":
        check_sweep(cfg, df)
    feat = prepare(df, p, deriv=frame)
    last = feat.iloc[-1]
    recent = feat.iloc[-400:]
    by_kind = recent[recent["sig"] != 0]["kind"].value_counts().to_dict()
    print(f"\n3) Estado atual: tendência 1h={'alta' if last['htf'] > 0 else 'baixa' if last['htf'] < 0 else 'neutra'} · "
          f"ADX {last['adx']:.1f} · RSI {last['rsi']:.1f} · ATR {last['atr'] / last['close'] * 100:.2f}%")
    if cfg.strategy != "sweep":
        print(f"   Sinais brutos nas últimas 400 barras: {by_kind or 'nenhum'}")

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
