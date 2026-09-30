"""Backtest da estratégia com o MESMO motor do robô live.

    python -m zec_bot.backtest                      # candles da Hyperliquid: só ~52 dias
    python -m zec_bot.backtest --days 365 --exchange binance --save-csv zec_15m.csv   # histórico longo (proxy)
    python -m zec_bot.backtest --csv zec_15m.csv --strategy both     # reversão E tendência, separadas por tipo
    python -m zec_bot.backtest --funding-filter     # compara COM e SEM o filtro de funding (estratégia de tendência)

O funding e o OI vêm da Binance (perp USDT). O funding tem histórico fundo; o OI só ~30 dias, por isso as
regras que dependem de OI (REV_USE_OI, --oi-filter) só podem ser testadas nesse período.

Como ler os resultados (leitura honesta):
  * R = unidade de risco (1R = distância entrada-stop). Os resultados já incluem comissões e derrapagem.
  * Vê SEMPRE as duas metades do período: se só uma metade é lucrativa, é sorte/regime, não "edge".
  * Poucos trades (< ~100) = estatística fraca. Não otimizes parâmetros até a curva ficar bonita.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, replace
from typing import Optional

import numpy as np
import pandas as pd

from . import binance_futures as bf
from .config import Config
from .data import fetch_exchange
from .derivs import build_frame
from .engine import Engine, summarize
from .strategy import Params, prepare


def run_backtest(df: pd.DataFrame, cfg: Config, params: Params | None = None, deriv: Optional[pd.DataFrame] = None,
                 start: Optional[int] = None, blocked: Optional[list] = None) -> tuple[list[dict], pd.DataFrame]:
    """`deriv`: features de funding/OI alinhadas com `df` (`derivs.build_frame`): alimentam a estratégia de
    reversão e os filtros. `blocked`: se for dada uma lista, recebe os setups que os filtros bloquearam."""
    p = params or Params.from_cfg(cfg)
    feat = prepare(df, p, deriv=deriv)
    engine = Engine(cfg, p)
    trades: list[dict] = []
    first = min(p.warmup_bars, max(len(feat) - 1, 0)) if start is None else start
    if deriv is not None:
        deriv = deriv.reindex(feat.index)
        fund, oi = deriv["funding_8h_pct"].to_numpy(), deriv["oi_change_pct"].to_numpy()
    for i in range(first, len(feat)):
        d = {"funding_8h_pct": fund[i], "oi_change_pct": oi[i]} if deriv is not None else None
        for ev in engine.on_bar(feat, i, deriv=d):
            if ev.kind == "closed":
                trades.append(asdict(ev.trade))
            elif ev.kind == "blocked" and blocked is not None:
                blocked.append(ev.data)
    return trades, feat.iloc[first:]  # um trade ainda aberto no fim dos dados não conta


def build_deriv(index: pd.DatetimeIndex, cfg: Config, need_oi: bool) -> pd.DataFrame:
    """Funding (e, se pedido, OI) da Binance alinhados com as barras. Só usa dados conhecidos no fecho de cada barra."""
    start_ms = int(index[0].timestamp() * 1000) - int((cfg.crowd_window_days + 3) * 86_400_000)
    try:
        funding = bf.fetch_funding_history(cfg.derivs_symbol, start_ms)
        oi = bf.fetch_oi_hist(cfg.derivs_symbol, int(index[0].timestamp() * 1000) - 2 * 86_400_000) if need_oi else []
    except bf.BinanceError as e:
        raise SystemExit(f"Não consegui o funding/OI da Binance: {e}")
    if not funding:
        raise SystemExit(f"A Binance não devolveu funding para {cfg.derivs_symbol}.")
    return build_frame(index, funding, oi, cfg, cfg.crowd_window_days)


def first_valid_bar(deriv: pd.DataFrame, cfg: Config, floor: int) -> int:
    """Primeira barra (>= floor) em que existem TODOS os dados exigidos pela configuração."""
    ok = np.ones(len(deriv), dtype=bool)
    if cfg.strategy in ("reversal", "both"):
        ok &= deriv["funding_pctl"].notna().to_numpy()
        if cfg.rev_use_oi:
            ok &= deriv["oi_rev_pct"].notna().to_numpy()
    if cfg.funding_filter:
        ok &= deriv["funding_8h_pct"].notna().to_numpy()
    if cfg.oi_filter:
        ok &= deriv["oi_change_pct"].notna().to_numpy()
    ok[:floor] = False
    if not ok.any():
        raise SystemExit("Sem barras com dados de funding/OI para o que pediste (o OI da Binance só cobre ~30 dias).")
    return int(np.argmax(ok))


def compare(base: list[dict], filt: list[dict], blocked: list, feat: pd.DataFrame) -> str:
    mid = feat.index[len(feat) // 2].isoformat()

    def parts(t):
        return summarize([x for x in t if x["close_time"] < mid]), summarize([x for x in t if x["close_time"] >= mid])

    (b1, b2), (f1, f2) = parts(base), parts(filt)
    out = ["— Efeito dos filtros (mesmo período) —",
           _line("sem filtros", summarize(base)), _line("com filtros", summarize(filt)),
           _line("  sem, 1ª metade", b1), _line("  com, 1ª metade", f1),
           _line("  sem, 2ª metade", b2), _line("  com, 2ª metade", f2),
           f"Setups bloqueados pelos filtros: {len(blocked)}"]
    if len(filt) < 100:
        out.append("⚠️ Menos de 100 trades com filtros: a diferença pode ser puro acaso.")
    better = [summarize(filt)["total_r"] > summarize(base)["total_r"], f1["total_r"] > b1["total_r"],
              f2["total_r"] > b2["total_r"]]
    if not all(better):
        out.append("⚠️ Os filtros não melhoram o resultado total E as duas metades: não há evidência de que ajudem.")
    return "\n".join(out)


def max_drawdown(rs: list[float]) -> float:
    peak = equity = worst = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def longest_losing_streak(rs: list[float]) -> int:
    best = cur = 0
    for r in rs:
        cur = cur + 1 if r <= 0 else 0
        best = max(best, cur)
    return best


def _line(name: str, s: dict) -> str:
    pf = "  ∞ " if s["profit_factor"] == float("inf") else f"{s['profit_factor']:5.2f}"
    return (f"{name:<22} n={s['n']:>4}  acerto={s['win_rate']:5.1f}%  total={s['total_r']:+8.2f}R  "
            f"média={s['avg_r']:+.3f}R  PF={pf}")


def report(trades: list[dict], feat: pd.DataFrame, cfg: Config) -> str:
    out = []
    days = max((feat.index[-1] - feat.index[0]).total_seconds() / 86400, 1e-9)
    bh = (feat["close"].iloc[-1] / feat["close"].iloc[0] - 1) * 100
    out.append(f"Período: {feat.index[0]:%Y-%m-%d} → {feat.index[-1]:%Y-%m-%d} ({days:.0f} dias, {len(feat)} barras)")
    out.append(f"Custos: {cfg.fee_pct:g}% comissão + {cfg.slippage_pct:g}% derrapagem por lado · buy&hold no período: {bh:+.1f}%")
    out.append("")
    if not trades:
        out.append("Sem trades neste período.")
        return "\n".join(out)

    rs = [t["r_net"] for t in trades]
    out.append(_line("TOTAL", summarize(trades)))
    out.append(f"Trades/dia: {len(trades) / days:.2f} · pior drawdown: {max_drawdown(rs):.2f}R · "
               f"maior série de perdas: {longest_losing_streak(rs)} · "
               f"barras em posição (média): {sum(t['bars_held'] for t in trades) / len(trades):.1f}")
    out.append(f"≈ {sum(rs) * cfg.risk_pct:+.1f}% da conta a {cfg.risk_pct:g}% de risco/trade (sem compor)")
    out.append("")
    out.append("— Por tipo / lado —")
    for label, key, vals in (("setup", "kind", ("reversal", "pullback", "breakout")), ("lado", "side", ("long", "short"))):
        for v in vals:
            sub = [t for t in trades if t[key] == v]
            if sub:
                out.append(_line(f"{label}: {v}", summarize(sub)))
    out.append("")
    out.append("— Robustez: 1ª metade vs 2ª metade do período —")
    mid = feat.index[len(feat) // 2].isoformat()
    first = [t for t in trades if t["close_time"] < mid]
    second = [t for t in trades if t["close_time"] >= mid]
    out.append(_line("1ª metade", summarize(first)))
    out.append(_line("2ª metade", summarize(second)))
    out.append("")
    reasons = pd.Series([t["exit_reason"] for t in trades]).value_counts()
    out.append("Saídas: " + " · ".join(f"{k}={v}" for k, v in reasons.items()))
    if len(trades) < 100:
        out.append("\n⚠️ Menos de 100 trades: amostra pequena, não tires conclusões fortes.")
    if summarize(first)["total_r"] <= 0 or summarize(second)["total_r"] <= 0:
        out.append("⚠️ Uma das metades não é lucrativa: o resultado depende do período. Cuidado.")
    return "\n".join(out)


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.index = pd.DatetimeIndex(df.index).tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
    df.index.name = "time"
    return df[["open", "high", "low", "close", "volume"]].astype("float64").sort_index()


def main() -> int:
    ap = argparse.ArgumentParser(prog="zec_bot.backtest")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--exchange", default=None, help="hyperliquid (~52 dias) | binance | bybit | okx (histórico longo)")
    ap.add_argument("--csv", help="usa um CSV guardado em vez de ir à exchange")
    ap.add_argument("--save-csv", help="guarda os candles descarregados")
    ap.add_argument("--strategy", choices=["reversal", "trend", "both", "sweep"])
    ap.add_argument("--side", choices=["long", "short", "both"])
    ap.add_argument("--fee", type=float, help="comissão por lado, em %%")
    ap.add_argument("--slippage", type=float, help="derrapagem por lado, em %%")
    ap.add_argument("--funding-filter", action="store_true", help="compara com/sem o filtro de funding (tendência)")
    ap.add_argument("--oi-filter", action="store_true", help="compara com/sem o filtro de OI (tendência)")
    ap.add_argument("--use-oi", action="store_true", help="a reversão exige também OI a subir (só ~30 dias de dados)")
    ap.add_argument("--trades-csv", default="zec_backtest_trades.csv")
    args = ap.parse_args()

    cfg = Config.from_env()
    if args.strategy:
        cfg.strategy = args.strategy
    if args.side:
        cfg.side = args.side
    if args.fee is not None:
        cfg.fee_pct = args.fee
    if args.slippage is not None:
        cfg.slippage_pct = args.slippage
    cfg.funding_filter = cfg.funding_filter or args.funding_filter
    cfg.oi_filter = cfg.oi_filter or args.oi_filter
    cfg.rev_use_oi = cfg.rev_use_oi or args.use_oi
    if cfg.strategy == "sweep":
        print("O modo sweep (método da mesa) não é backtestável: depende dos níveis de cluster de cada hora "
              "(niveis.csv) e o histórico desses níveis não existe. O que dá para fazer é acumular evidência em paper: "
              "o robô regista cada hora no journal e o resultado de cada ordem em zec_sweep_results.csv.\n"
              "Para testar as outras estratégias: --strategy reversal | trend | both.")
        return 2
    p = Params.from_cfg(cfg)

    exchange = args.exchange or cfg.exchange
    if args.csv:
        df = load_csv(args.csv)
    else:
        n = args.days * 24 * 60 // cfg.interval_min + p.warmup_bars
        print(f"A descarregar ~{n} barras de {exchange} (pode demorar)…")
        df = fetch_exchange(exchange, cfg.base, cfg.quote, cfg.interval_min, n)
        if len(df) < 0.95 * n:
            extra = (" A Hyperliquid só guarda as últimas 5000 barras; para mais histórico usa --exchange binance"
                     " (preços muito próximos, mas não idênticos)." if exchange == "hyperliquid" else "")
            print(f"Nota: só há {len(df)} barras das {n} pedidas.{extra}")
        if args.save_csv:
            df.to_csv(args.save_csv)
            print(f"Guardado em {args.save_csv}")

    if len(df) <= p.warmup_bars + 100:
        print(f"Dados insuficientes ({len(df)} barras).")
        return 1

    deriv, start = None, None
    if cfg.needs_derivs:
        print(f"A obter funding/OI da Binance ({cfg.derivs_symbol})…")
        deriv = build_deriv(df.index, cfg, need_oi=cfg.rev_use_oi or cfg.oi_filter)
        start = first_valid_bar(deriv, cfg, p.warmup_bars)
    print(f"Estratégia: {cfg.strategy} · lado: {cfg.side}")

    if cfg.funding_filter or cfg.oi_filter:
        no_filters = replace(cfg, funding_filter=False, oi_filter=False)
        blocked: list = []
        base, feat = run_backtest(df, no_filters, Params.from_cfg(no_filters), deriv=deriv, start=start)
        trades, _ = run_backtest(df, cfg, p, deriv=deriv, start=start, blocked=blocked)
        print(report(base, feat, no_filters))
        print("\n" + compare(base, trades, blocked, feat))
    else:
        trades, feat = run_backtest(df, cfg, p, deriv=deriv, start=start)
        print(report(trades, feat, cfg))
    if trades:
        pd.DataFrame(trades).to_csv(args.trades_csv, index=False)
        print(f"\nTrades guardados em {args.trades_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
