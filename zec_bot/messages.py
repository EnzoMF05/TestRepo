"""Formatação das mensagens do Telegram (HTML)."""
from __future__ import annotations

import html
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


def _usd(x: float) -> str:
    return f"{x / 1e6:.2f}M$" if x >= 1e6 else f"{x / 1e3:.0f}k$"


def _signed(x: float | None, unit: str = "%") -> str:
    return "n/d" if x is None or x != x else f"{x:+.2f}{unit}"


def derivs_lines(deriv: dict | None, side: str, cfg: Config) -> str:
    """Linhas de contexto de funding/OI da Binance (vazio se não houver dados)."""
    if not deriv:
        return ""
    f8 = deriv["funding_8h_pct"]
    pays = "longs pagam" if f8 > 0 else "shorts pagam" if f8 < 0 else "neutro"
    pctl = deriv.get("funding_pctl")
    pctl_txt = "" if pctl is None else f" · P{pctl:.0f} dos últimos {cfg.crowd_window_days:g}d"
    hl = deriv.get("hl_funding_8h_pct")
    hl_txt = "" if hl is None else f" · Hyperliquid {hl:+.4f}%/8h"
    lines = [
        f"Funding Binance: <b>{f8:+.4f}%/8h</b> ({pays}){pctl_txt}{hl_txt}",
        f"OI Binance: <b>{_usd(deriv['oi_usd'])}</b> · 1h {_signed(deriv.get('oi_change_1h_pct'))} · "
        f"4h {_signed(deriv.get('oi_change_4h_pct'))} · {cfg.rev_oi_hours:g}h {_signed(deriv.get('oi_rev_pct'))} · "
        f"prémio {deriv['premium_pct']:+.3f}%",
    ]
    if deriv.get("reading"):
        lines.append(f"Leitura: {deriv['reading']}")
    lim = cfg.funding_limit_8h_pct
    if side == "long" and f8 > lim:
        lines.append(f"⚠️ Funding elevado ({f8:+.3f}%/8h): longs sobrelotados, cuidado com squeeze contra ti.")
    if side == "short" and f8 < -lim:
        lines.append(f"⚠️ Funding muito negativo ({f8:+.3f}%/8h): shorts sobrelotados, cuidado com squeeze contra ti.")
    return "\n".join(lines) + "\n"


def _why_reversal(t: Trade, info: dict, cfg: Config) -> str:
    """O porquê de um sinal de reversão, com os números que o dispararam."""
    up = t.side == "short"  # o excesso foi para cima
    txt = (f"Porquê: funding {info['funding_8h_pct']:+.3f}%/8h (P{info['funding_pctl']:.0f} dos últimos "
           f"{cfg.crowd_window_days:g}d) · preço {info['stretch_atr']:.1f} ATR {'acima' if up else 'abaixo'} da EMA55 · "
           f"RSI {'pico' if up else 'mínimo'} {info['rsi_extreme']:.0f} · candle de rejeição")
    if cfg.rev_use_oi and info.get("oi_rev_pct") is not None:  # só quando o OI é uma condição do sinal
        txt += f" · OI {cfg.rev_oi_hours:g}h {info['oi_rev_pct']:+.1f}%"
    return txt + "\n"


def format_signal(t: Trade, cfg: Config, htf: int, rsi: float, adx: float, deriv: dict | None = None,
                  info: dict | None = None) -> str:
    long_ = t.side == "long"
    icon, word = ("🟢", "LONG") if long_ else ("🔴", "SHORT")
    if t.kind == "reversal":
        kind = f"Reversão ({'shorts sobrelotados' if long_ else 'longs sobrelotados'})"
    else:
        kind = "Pullback" if t.kind == "pullback" else "Rutura (breakout)"
    pct = t.risk / t.entry * 100
    qty, notional, lev = position_size(cfg, t.entry, t.risk)
    rr1 = abs(t.tp1 - t.entry) / t.risk
    rr2 = abs(t.tp2 - t.entry) / t.risk
    chase = t.entry + t.d * 0.3 * t.risk  # limite para não perseguir o preço
    trend = "alta" if htf > 0 else "baixa" if htf < 0 else "neutra"
    counter = " (contra-tendência)" if htf * t.d < 0 else ""
    why = _why_reversal(t, info, cfg) if t.kind == "reversal" and info else ""
    return (
        f"{icon} <b>{cfg.base} {word}</b> — {kind} ({cfg.interval_str})\n"
        f"<i>{_local(t.open_time, cfg, cfg.interval_min)}</i>\n\n"
        f"Entrada: <b>{_px(t.entry)}</b>\n"
        f"Stop: <b>{_px(t.stop0)}</b> ({pct:.2f}%)\n"
        f"TP1: <b>{_px(t.tp1)}</b> ({rr1:.1f}R) → fechar 50% e stop a breakeven\n"
        f"TP2: <b>{_px(t.tp2)}</b> ({rr2:.1f}R)\n\n"
        f"Tamanho (risco {cfg.risk_pct:g}% de {cfg.account_size:,.0f}$): "
        f"{qty:.2f} {cfg.base} ≈ {notional:,.0f}$ (~{lev:.1f}x)\n"
        f"{_leverage_warning(lev, deriv)}"
        f"{why}"
        f"Contexto: 1h em {trend}{counter} · ADX {adx:.0f} · RSI {rsi:.0f}\n"
        f"{derivs_lines(deriv, t.side, cfg)}"
        f"Não entrar se o preço já passou {_px(chase)}. Saída a mercado após {cfg.max_hold_bars * cfg.interval_min // 60}h."
    )


def _leverage_warning(lev: float, deriv: dict | None) -> str:
    mx = (deriv or {}).get("max_leverage") or 0.0
    if mx and lev > mx:
        return f"⚠️ Esse tamanho exige {lev:.1f}x, acima do máximo de {mx:.0f}x do ZEC na Hyperliquid: reduz o tamanho.\n"
    return ""


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
    if ev.kind == "blocked":
        d = ev.data
        return (f"🚫 Setup {d['side'].upper()} ({d['kind']}) ignorado pelos filtros: "
                f"{html.escape(d['reason'], quote=False)}")
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


def format_status(engine, cfg: Config, last_price: float, exchange: str, deriv: dict | None = None) -> str:
    t = engine.trade
    lines = [f"🤖 <b>{cfg.base} bot</b> — {'⏸ em pausa' if engine.paused else '▶️ ativo'}",
             f"Preço: {_px(last_price)} ({exchange})",
             f"Hoje: {engine.day_signals} sinais · {engine.day_r:+.2f}R"]
    if t:
        lines.append(f"Trade aberto: {t.side.upper()} {t.kind} @ {_px(t.entry)} · stop {_px(t.stop)} · "
                     f"TP1 {_px(t.tp1)} · TP2 {_px(t.tp2)} ({t.state})")
    else:
        lines.append("Sem trade aberto.")
    if deriv:
        lines.append(f"Funding {deriv['funding_8h_pct']:+.4f}%/8h · OI {_usd(deriv['oi_usd'])} "
                     f"(1h {_signed(deriv.get('oi_change_1h_pct'))})")
    return "\n".join(lines)


def format_startup(cfg: Config, exchange: str) -> str:
    return (f"🚀 <b>{cfg.base} day-trading bot</b> iniciado ({exchange}, {cfg.interval_str}, lado: {cfg.side})\n"
            f"Sinais apenas — o robô NÃO executa ordens. Nada disto é aconselhamento financeiro.")
