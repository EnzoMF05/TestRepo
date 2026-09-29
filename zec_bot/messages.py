"""Formatação das mensagens do Telegram (HTML)."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from .config import Config
from .engine import Event, Trade


def _px(x: float) -> str:
    return f"{x:,.2f}" if x >= 10 else f"{x:.3f}"


def _local(iso: str, cfg: Config, add: int = 0) -> str:
    """Hora local (fecho da barra = abertura + intervalo) para mostrar ao utilizador."""
    ts = pd.Timestamp(iso) + pd.Timedelta(minutes=add)
    return ts.tz_convert(ZoneInfo(cfg.timezone)).strftime("%d/%m %H:%M")


def position_size(cfg: Config, entry: float, risk: float) -> tuple[float, float, float]:
    """(quantidade, valor nocional, alavancagem) para arriscar risk_pct da conta neste stop."""
    risk_usd = cfg.account_size * cfg.risk_pct / 100.0
    qty = risk_usd / risk
    notional = qty * entry
    return qty, notional, notional / cfg.account_size


def format_signal(t: Trade, cfg: Config, htf: int, rsi: float, adx: float) -> str:
    long_ = t.side == "long"
    icon, word = ("🟢", "LONG") if long_ else ("🔴", "SHORT")
    kind = "Pullback" if t.kind == "pullback" else "Rutura (breakout)"
    pct = t.risk / t.entry * 100
    qty, notional, lev = position_size(cfg, t.entry, t.risk)
    rr1 = abs(t.tp1 - t.entry) / t.risk
    rr2 = abs(t.tp2 - t.entry) / t.risk
    chase = t.entry + t.d * 0.3 * t.risk  # limite para não perseguir o preço
    trend = "alta" if htf > 0 else "baixa"
    return (
        f"{icon} <b>{cfg.base} {word}</b> — {kind} ({cfg.interval_str})\n"
        f"<i>{_local(t.open_time, cfg, cfg.interval_min)}</i>\n\n"
        f"Entrada: <b>{_px(t.entry)}</b>\n"
        f"Stop: <b>{_px(t.stop0)}</b> ({pct:.2f}%)\n"
        f"TP1: <b>{_px(t.tp1)}</b> ({rr1:.1f}R) → fechar 50% e stop a breakeven\n"
        f"TP2: <b>{_px(t.tp2)}</b> ({rr2:.1f}R)\n\n"
        f"Tamanho (risco {cfg.risk_pct:g}% de {cfg.account_size:,.0f}$): "
        f"{qty:.2f} {cfg.base} ≈ {notional:,.0f}$ (~{lev:.1f}x)\n"
        f"Contexto: 1h em {trend} · ADX {adx:.0f} · RSI {rsi:.0f}\n"
        f"Não entrar se o preço já passou {_px(chase)}. Saída a mercado após {cfg.max_hold_bars * cfg.interval_min // 60}h."
    )


def format_event(ev: Event, cfg: Config) -> str | None:
    t = ev.trade
    if ev.kind == "tp1" and t:
        return (f"🎯 <b>{cfg.base} {t.side.upper()}</b> — TP1 atingido ({_px(t.tp1)})\n"
                f"Fecha 50% e move o stop para breakeven ({_px(t.entry)}).")
    if ev.kind == "closed" and t:
        why = {"stop": "Stop atingido", "tp2": "TP2 atingido", "breakeven": "Fechado em breakeven (após TP1)",
               "tempo": "Fechado por tempo"}[t.exit_reason]
        icon = "✅" if t.r_net > 0 else "❌"
        return (f"{icon} <b>{cfg.base} {t.side.upper()}</b> — {why}\n"
                f"Resultado: <b>{t.r_net:+.2f}R</b> (já com comissões/derrapagem)")
    if ev.kind == "daily_stop":
        return (f"⛔ Limite diário atingido ({ev.data['day_r']:+.1f}R). "
                f"Sem mais sinais até às 00:00 UTC.")
    if ev.kind == "day_summary":
        d = ev.data
        if d["signals"] == 0 and d["n"] == 0:
            return f"📊 <b>{d['date']}</b> — sem sinais."
        return (f"📊 <b>Resumo {d['date']}</b>\n"
                f"Sinais: {d['signals']} · fechados: {d['n']} ({d['wins']}✔ / {d['losses']}✖)\n"
                f"Resultado do dia: <b>{d['day_r']:+.2f}R</b>")
    return None


def format_stats(stats: dict, title: str) -> str:
    if stats["n"] == 0:
        return f"📈 <b>{title}</b>\nAinda sem trades fechados."
    pf = "∞" if stats["profit_factor"] == float("inf") else f"{stats['profit_factor']:.2f}"
    return (f"📈 <b>{title}</b>\n"
            f"Trades: {stats['n']} · acerto {stats['win_rate']:.0f}%\n"
            f"Total: <b>{stats['total_r']:+.2f}R</b> · média {stats['avg_r']:+.2f}R · profit factor {pf}")


def format_status(engine, cfg: Config, last_price: float, exchange: str) -> str:
    t = engine.trade
    lines = [f"🤖 <b>{cfg.base} bot</b> — {'⏸ em pausa' if engine.paused else '▶️ ativo'}",
             f"Preço: {_px(last_price)} ({exchange})",
             f"Hoje: {engine.day_signals} sinais · {engine.day_r:+.2f}R"]
    if t:
        lines.append(f"Trade aberto: {t.side.upper()} {t.kind} @ {_px(t.entry)} · stop {_px(t.stop)} · "
                     f"TP1 {_px(t.tp1)} · TP2 {_px(t.tp2)} ({t.state})")
    else:
        lines.append("Sem trade aberto.")
    return "\n".join(lines)


def format_startup(cfg: Config, exchange: str) -> str:
    return (f"🚀 <b>{cfg.base} day-trading bot</b> iniciado ({exchange}, {cfg.interval_str}, lado: {cfg.side})\n"
            f"Sinais apenas — o robô NÃO executa ordens. Nada disto é aconselhamento financeiro.")
