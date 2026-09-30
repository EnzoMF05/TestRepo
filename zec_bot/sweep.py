"""Sweep de nível ("CT-NÍVEL"): o método da mesa, portado para um só ativo (ZEC).

Ideia: a vela 1H varre um nível de cluster (apanha o stop/liquidação de um lado) e devolve-se:
  L0  contacto/varredura do nível
  L1  L0 + pavio de rejeição >= 50% da amplitude
  L2  L1 + OI da hora a cair <= -3% (posições a serem fechadas/liquidadas: "flush")
Só o L2 é sinal. Entrada = LIMIT na FACE do nível (nunca a mercado), stop = extremo da vela +/- 0.1 ATR14,
alvo 1.5R, veto se |limite-stop| > 1.2 ATR.

Este módulo é um port do `evaluate()` / `build_trade_plan()` / `atr14()` do hourly_scan.py da mesa e foi
verificado contra esse código (tests/data/sweep_golden.json tem saídas geradas por ele).

Diferença consciente (o original tem um bug): com `require_reclaim=True` o original só acrescenta a nota
"sem_reclaim" e o sinal continua válido. Aqui, exigir reclaim invalida mesmo o sinal.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from .config import Config

H1_MS = 3_600_000


@dataclass
class SweepParams:
    oi_max_pct: float = -3.0  # OI_MIN_QUEDA: delta de OI da hora <= -3% => L2
    wick_min_pct: float = 50.0  # REJEICAO_MIN
    stop_atr: float = 0.1  # STOP_ATR_BUFFER: stop = extremo +/- 0.1 ATR14
    target_r_a: float = 2.0  # plano A (Gemini): entrada no fecho, 2R
    target_r_b: float = 1.5  # plano B (CT-NÍVEL): entrada na face, 1.5R
    r_max_atr: float = 1.2  # veto: |limite - stop| > 1.2 ATR14
    cost_side: float = 0.00035  # MAKER 0.015% + SLIP 0.02% por lado (fração)
    require_reclaim: bool = False  # False = regra da mesa
    valid_h: float = 4.0  # validade do trade (4 x 1H)
    cancel_h: float = 2.0  # cancela o limite sem fill (2 x 1H)
    count_from_alert: bool = False  # False = como no original: conta desde a ABERTURA da vela varrida

    @classmethod
    def from_cfg(cls, cfg: Config) -> "SweepParams":
        return cls(
            oi_max_pct=cfg.sweep_oi_max_pct, wick_min_pct=cfg.sweep_wick_min_pct, stop_atr=cfg.sweep_stop_atr,
            target_r_b=cfg.sweep_target_r, r_max_atr=cfg.sweep_r_max_atr, cost_side=cfg.sweep_cost_side_pct / 100.0,
            require_reclaim=cfg.sweep_require_reclaim, valid_h=cfg.sweep_valid_h, cancel_h=cfg.sweep_cancel_h,
            count_from_alert=cfg.sweep_count_from_alert,
        )


# ---------------------------------------------------------------------------- níveis (niveis.csv)
def parse_levels(cell: Any) -> list[float]:
    if not cell or not str(cell).strip():
        return []
    out = []
    for part in str(cell).replace(";", "|").split("|"):
        part = part.strip()
        if part:
            out.append(float(part))
    return out


def parse_utc(s: Any) -> Optional[datetime]:
    if not s or not str(s).strip():
        return None
    try:
        d = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def load_levels(path: str | Path, ticker: str) -> tuple[Optional[dict], Optional[str]]:
    """(cluster, erro) para `ticker` no niveis.csv (colunas: ticker, cluster_below, cluster_above, spot_approx,
    atualizado_utc). Níveis separados por '|' ou ';'. Uma linha má só estraga este ativo."""
    p = Path(path)
    try:
        with p.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if (row.get("ticker") or "").upper().strip() != ticker.upper():
                    continue
                cl: dict = {"below": [], "above": [], "spot_approx": None,
                            "atualizado": row.get("atualizado_utc"), "erro": None}
                try:
                    cl["below"] = parse_levels(row.get("cluster_below", ""))
                    cl["above"] = parse_levels(row.get("cluster_above", ""))
                    cl["spot_approx"] = float(row["spot_approx"]) if row.get("spot_approx") else None
                except (ValueError, TypeError) as e:
                    cl["erro"] = f"parse: {e}"[:80]
                return cl, None
    except OSError as e:
        return None, f"niveis.csv ilegível: {type(e).__name__}: {p}"
    return None, f"sem linha para {ticker} em {p}"


def level_age_h(cluster: dict, now: datetime) -> Optional[float]:
    d = parse_utc(cluster.get("atualizado"))
    return None if d is None else (now - d).total_seconds() / 3600.0


# ---------------------------------------------------------------------------- velas 1H a partir das barras base
def _hour_groups(feat: pd.DataFrame, interval_min: int) -> pd.core.groupby.DataFrameGroupBy:
    return feat.groupby(feat.index.floor("1h"))


def hourly_candles(feat: pd.DataFrame, interval_min: int, last_hours: int = 60) -> list[dict]:
    """Velas 1H COMPLETAS (todas as barras base presentes) das últimas `last_hours` horas, ascendentes."""
    n = 60 // interval_min
    sub = feat.iloc[-(last_hours + 1) * n:]
    out = []
    for hour, g in _hour_groups(sub, interval_min):
        if len(g) != n:
            continue
        out.append({"t": int(hour.timestamp() * 1000), "open": float(g["open"].iloc[0]), "high": float(g["high"].max()),
                    "low": float(g["low"].min()), "close": float(g["close"].iloc[-1])})
    return out


def atr14(candles: list[dict]) -> Optional[float]:
    """Média simples dos últimos 14 True Range (como no original; não é Wilder)."""
    if len(candles) < 15:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-14:]) / 14.0


# ---------------------------------------------------------------------------- o método
def build_trade_plan(lado: str, entry: float, candle: dict, atr: float, alvo_r: float, sp: SweepParams) -> dict:
    """Stop no extremo da vela +/- stop_atr*ATR. O R inclui os custos (entrada+saída)."""
    if lado == "LONG":
        stop = candle["low"] - sp.stop_atr * atr
        R = (entry - stop) + entry * sp.cost_side * 2
        alvo = entry + alvo_r * R
        r_raw = entry - stop
    else:
        stop = candle["high"] + sp.stop_atr * atr
        R = (stop - entry) + entry * sp.cost_side * 2
        alvo = entry - alvo_r * R
        r_raw = stop - entry
    return {"entrada": entry, "stop": stop, "alvo": alvo, "R": R, "r_raw": r_raw, "alvo_r": alvo_r}


def evaluate(cluster: dict, candle: dict, atr: Optional[float], oi_delta: Optional[float],
             sp: Optional[SweepParams] = None) -> list[dict]:
    """Classifica o contacto da vela com os níveis: um evento por lado (o 1º nível varrido, NA ORDEM da lista)."""
    sp = sp or SweepParams()
    amp = candle["high"] - candle["low"]
    if amp <= 0:
        return [{"lado": "", "valido": False, "tier": "", "motivo": "doji",
                 "oi_delta": oi_delta, "rejeicao": None, "dist_atr": None}]
    ok_oi = oi_delta is not None and oi_delta <= sp.oi_max_pct
    lados = (
        ("LONG", cluster["below"], lambda l: candle["low"] < l, lambda l: candle["close"] > l,
         (min(candle["open"], candle["close"]) - candle["low"]) / amp * 100.0),
        ("SHORT", cluster["above"], lambda l: candle["high"] > l, lambda l: candle["close"] < l,
         (candle["high"] - max(candle["open"], candle["close"])) / amp * 100.0),
    )
    results = []
    for lado, niveis, varreu, recuperou, rejeicao in lados:
        for lvl in niveis:
            if not varreu(lvl):
                continue
            reclaim = recuperou(lvl)
            dist_atr = abs(candle["close"] - lvl) / atr if atr and atr > 0 else None
            tier = "L0"
            motivo_parts = []
            if sp.require_reclaim and not reclaim:
                motivo_parts.append("sem_reclaim")
            if rejeicao >= sp.wick_min_pct:
                tier = "L1"
            else:
                motivo_parts.append(f"rej={rejeicao:.1f}")
            if tier == "L1" and ok_oi:
                tier = "L2"
            elif tier == "L1" and not ok_oi:
                motivo_parts.append(f"oi={oi_delta}")

            plan_a = build_trade_plan(lado, candle["close"], candle, atr, sp.target_r_a, sp)
            plan_b = build_trade_plan(lado, lvl, candle, atr, sp.target_r_b, sp)  # LIMIT na FACE
            skip_r = bool(plan_b["r_raw"] > sp.r_max_atr * atr) if atr and atr > 0 else False
            no_reclaim_block = sp.require_reclaim and not reclaim  # o original ignorava isto (bug)
            results.append({
                "lado": lado, "level": lvl, "oi_delta": oi_delta, "rejeicao": rejeicao,
                "reclaim": reclaim, "tier": tier, "dist_atr": dist_atr,
                "atr": atr, "skip_r": skip_r,
                "plan_a": plan_a, "plan_b": plan_b,
                "entrada": plan_b["entrada"], "stop": plan_b["stop"], "alvo": plan_b["alvo"],
                "R": plan_b["R"], "r_raw": plan_b["r_raw"],
                "valido": tier == "L2" and not skip_r and not no_reclaim_block,
                "motivo": (f"sweep@{lvl} tier={tier}" +
                           ((" " + " ".join(motivo_parts)) if motivo_parts else "") +
                           (" L2_SKIP_R" if skip_r and tier == "L2" else "")),
            })
            break  # um evento por lado
    if not results:
        results.append({"lado": "", "valido": False, "tier": "", "motivo": "no_sweep",
                        "oi_delta": oi_delta, "rejeicao": None, "dist_atr": None})
    return results


_ORDER = {"L2": 3, "L1": 2, "L0": 1, "": 0}


def best_tier(evals: list[dict]) -> str:
    best = ""
    for e in evals:
        t = e.get("tier") or ""
        if _ORDER.get(t, 0) > _ORDER.get(best, 0):
            best = t
    return best


def pick_event(evals: list[dict]) -> Optional[dict]:
    """O melhor evento: 1º com o tier mais alto (LONG antes de SHORT em empate); senão o 1º com lado."""
    flagged = [e for e in evals if e.get("lado")]
    tier = best_tier(evals)
    for e in flagged:
        if e.get("tier") == tier:
            return e
    return flagged[0] if flagged else None


def is_marketable(lado: str, limit: float, close: float) -> bool:
    """Um limite de compra >= mercado (ou de venda <= mercado) executa logo, a mercado."""
    return limit >= close if lado == "LONG" else limit <= close


# ---------------------------------------------------------------------------- formatação (como no journal da mesa)
def fmt_px(x: float) -> str:
    ax = abs(x)
    if ax >= 1000:
        return f"{x:.1f}" if ax < 10000 else f"{x:.0f}"
    if ax >= 100:
        return f"{x:.2f}"
    if ax >= 1:
        return f"{x:.3f}"
    if ax >= 0.1:
        return f"{x:.4f}"
    return f"{x:.5f}"


def fmt_oi(x: Optional[float]) -> str:
    return "" if x is None else f"{x:.2f}"
