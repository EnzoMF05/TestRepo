"""Motor de ordens do modo `sweep`: à hora fechada avalia o nível, cria a ordem LIMIT e acompanha-a em paper.

Ciclo de vida (barras base de 15m):
    pendente --toca a face--> aberta --alvo 1.5R / stop / fim da validade--> fechada
        \\--sem fill até cancel_at (ou validade)--> cancelada
Regras de simulação (pior caso, para o paper não ser mais otimista que a realidade):
  * fill na face: LONG se low <= limite, SHORT se high >= limite. Se o limite já estava do lado errado do
    mercado quando o alerta saiu (sem reclaim), assume-se fill imediato AO PREÇO DO LIMITE (sem melhoria);
  * na barra do fill, se também tocou no stop, conta-se o stop; o alvo só conta em barras seguintes;
  * numa barra com stop e alvo, ganha o stop.
Prazos: por defeito contam desde a ABERTURA da vela varrida (como o original da mesa); `count_from_alert`
conta desde o alerta.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from .config import Config
from .engine import Engine, Event, Trade
from .strategy import Params
from .sweep import (SweepParams, atr14, best_tier, evaluate, fmt_oi, fmt_px, hourly_candles, is_marketable,
                    level_age_h, load_levels, pick_event)

log = logging.getLogger("zec_bot.sweep")

JOURNAL_HEADER = [
    "data_utc", "ticker", "lado", "oi_delta_pct", "rejeicao_pct",
    "entrada", "stop", "alvo", "tomei_passei", "resultado_R",
    "oi_1h_pct", "oi_4h_pct", "oi_src", "acordo",
    "L0", "L1", "L2", "dist_atr", "SKIP_R", "nivel", "tier",
    "entrada_A", "stop_A", "alvo_A", "entrada_B", "stop_B", "alvo_B",
    "validade_h", "cancel_nofill_h", "motor",
]  # o mesmo cabeçalho do journal da mesa: dá para concatenar/comparar linha a linha
RESULTS_HEADER = ["candle_utc", "ticker", "lado", "nivel", "entrada", "stop", "alvo", "estado", "fill_utc", "saida_utc",
                  "motivo_saida", "R_bruto", "R_liquido", "oi_delta_pct", "rejeicao_pct", "limite_marketable"]


def _utc(ts: pd.Timestamp) -> str:
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


class SweepJournal:
    """Escreve o journal (uma linha por avaliação horária) e o ficheiro de resultados (uma linha por ordem)."""

    def __init__(self, journal_path: Optional[str], results_path: Optional[str]):
        self.journal_path = Path(journal_path) if journal_path else None
        self.results_path = Path(results_path) if results_path else None

    @staticmethod
    def _append(path: Optional[Path], header: list, rows: list[list]) -> None:
        if path is None:
            return
        new = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(header)
            w.writerows(rows)

    def row(self, **kw) -> list:
        return [kw.get(col, "") for col in JOURNAL_HEADER]

    def log(self, **kw) -> None:
        self._append(self.journal_path, JOURNAL_HEADER, [self.row(**kw)])

    def result(self, **kw) -> None:
        self._append(self.results_path, RESULTS_HEADER, [[kw.get(col, "") for col in RESULTS_HEADER]])


class SweepEngine(Engine):
    def __init__(self, cfg: Config, params: Params | None = None, sp: SweepParams | None = None,
                 levels: Callable[[], tuple[Optional[dict], Optional[str]]] | None = None,
                 oi: Callable[[int], dict] | None = None, journal: SweepJournal | None = None):
        super().__init__(cfg, params)
        self.sp = sp or SweepParams.from_cfg(cfg)
        self.levels = levels or (lambda: load_levels(cfg.niveis_path, cfg.base))
        self.oi = oi or (lambda candle_open_ms: {"delta": None, "src": "none", "errors": ["sem_oi"]})
        self.journal = journal or SweepJournal(None, None)
        self.last_eval = ""  # abertura (UTC) da última vela 1H avaliada: nunca avalia a mesma duas vezes
        self.n_cancelled = 0
        self._levels_down = False

    # ------------------------------------------------------------------ persistência
    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update(last_eval=self.last_eval, n_cancelled=self.n_cancelled)
        return d

    def load(self, d: dict) -> None:
        super().load(d)
        self.last_eval = d.get("last_eval", "")
        self.n_cancelled = d.get("n_cancelled", 0)

    # ------------------------------------------------------------------ barra a barra
    def _is_hour_close(self, close_ts: pd.Timestamp) -> bool:
        return close_ts.minute == 0 and close_ts.second == 0

    def on_bar(self, feat: pd.DataFrame, i: int, evaluate: bool = True, deriv: Optional[dict] = None) -> list[Event]:
        ts = feat.index[i]
        iso = ts.isoformat()
        close_ts = ts + pd.Timedelta(minutes=self.cfg.interval_min)
        events: list[Event] = []
        self._roll_day(ts, events)

        row = feat.iloc[i]
        hi, lo, cl = float(row["high"]), float(row["low"]), float(row["close"])
        t = self.trade
        if t is not None and t.state == "pending":
            events += self._step_pending(t, ts, hi, lo, iso)
        elif t is not None:
            for kind in t.step(hi, lo, cl, iso, self.p, 10**9):
                events.append(Event(kind, t))
            if t.state != "closed" and close_ts >= pd.Timestamp(t.meta["valid_until"]):
                t.force_close(cl, iso, "tempo")  # a validade acabou: sai a mercado
                events.append(Event("closed", t))
        self._archive_if_closed(iso)
        self.last_bar = iso

        if self._is_hour_close(close_ts):
            candle_open = close_ts - pd.Timedelta(hours=1)
            if candle_open.isoformat() != self.last_eval:
                if evaluate:
                    self.last_eval = candle_open.isoformat()
                    events += self._evaluate_hour(feat, i, close_ts, candle_open)
                else:
                    self.journal.log(data_utc=_utc(candle_open), ticker=self.cfg.base, motor="CT-NIVEL",
                                     resultado_R="HORA_NAO_AVALIADA (robô parado ou atrasado; sem sinais antigos)")
        return events

    # ------------------------------------------------------------------ ordem pendente
    def _step_pending(self, t: Trade, ts: pd.Timestamp, hi: float, lo: float, iso: str) -> list[Event]:
        m = t.meta
        if ts >= min(pd.Timestamp(m["cancel_at"]), pd.Timestamp(m["valid_until"])):
            self.n_cancelled += 1
            self.trade = None
            self.journal.result(candle_utc=m["candle_open"], ticker=self.cfg.base, lado=t.side.upper(), nivel=fmt_px(m["level"]),
                                entrada=fmt_px(t.entry), stop=fmt_px(t.stop0), alvo=fmt_px(t.tp1), estado="CANCEL_NOFILL",
                                saida_utc=iso, motivo_saida="sem fill até " + m["cancel_at"],
                                oi_delta_pct=fmt_oi(m["oi_delta"]), rejeicao_pct=f"{m['rejeicao']:.1f}",
                                limite_marketable=int(m["marketable"]))
            return [Event("cancelled", t, data={"reason": "no-fill", "cancel_at": m["cancel_at"]})]
        filled = lo <= t.entry if t.side == "long" else hi >= t.entry
        if not filled:
            return []
        t.state, m["fill_time"] = "open", iso
        events = [Event("filled", t)]
        if t._stop_hit(hi, lo):  # pior caso: na mesma barra do fill também foi ao stop
            t._close(-1.0, "stop", iso)
            events.append(Event("closed", t))
        return events

    def _archive_if_closed(self, iso: str) -> None:
        t = self.trade
        if t is None or t.state != "closed":
            return
        self.day_r += t.r_net
        self.closed.append(asdict(t))
        self.closed = self.closed[-2000:]
        m = t.meta
        self.journal.result(candle_utc=m["candle_open"], ticker=self.cfg.base, lado=t.side.upper(), nivel=fmt_px(m["level"]),
                            entrada=fmt_px(t.entry), stop=fmt_px(t.stop0), alvo=fmt_px(t.tp1), estado="FILLED",
                            fill_utc=m.get("fill_time", ""), saida_utc=t.close_time, motivo_saida=t.exit_reason,
                            R_bruto=f"{t.r_gross:.3f}", R_liquido=f"{t.r_net:.3f}", oi_delta_pct=fmt_oi(m["oi_delta"]),
                            rejeicao_pct=f"{m['rejeicao']:.1f}", limite_marketable=int(m["marketable"]))
        self.trade = None

    # ------------------------------------------------------------------ avaliação da vela 1H fechada
    def _fail(self, candle_open: pd.Timestamp, status: str, motivo: str) -> None:
        self.journal.log(data_utc=_utc(candle_open), ticker=self.cfg.base, resultado_R=motivo[:200], motor="CT-NIVEL")
        log.info("%s: %s", status, motivo)

    def _evaluate_hour(self, feat: pd.DataFrame, i: int, close_ts: pd.Timestamp, candle_open: pd.Timestamp) -> list[Event]:
        cfg, sp, tk = self.cfg, self.sp, self.cfg.base
        events: list[Event] = []
        cluster, err = self.levels()
        if cluster is None or (not cluster["below"] and not cluster["above"]):
            detail = err or (f"parse: {cluster['erro']}" if cluster and cluster.get("erro") else "sem níveis")
            self._fail(candle_open, "SKIP_NO_CLUSTER", f"SKIP_NO_CLUSTER {detail}")
            if not self._levels_down:
                self._levels_down = True
                events.append(Event("note", data={"text": f"Sem níveis para {tk} ({detail}): o modo sweep não pode avaliar."}))
            return events
        self._levels_down = False
        age = level_age_h(cluster, close_ts.to_pydatetime())
        if cfg.niveis_max_age_h and age is not None and age > cfg.niveis_max_age_h:
            self._fail(candle_open, "SKIP_STALE_CLUSTER", f"SKIP_STALE_CLUSTER idade={age:.1f}h > {cfg.niveis_max_age_h:g}h")
            return events

        candles = hourly_candles(feat.iloc[: i + 1], cfg.interval_min, last_hours=45)
        candle = candles[-1] if candles and candles[-1]["t"] == int(candle_open.timestamp() * 1000) else None
        if candle is None:
            self._fail(candle_open, "FAIL_CANDLE", "FAIL_CLOSED 1H: vela incompleta ou ausente")
            return events
        atr = atr14(candles)
        if atr is None or atr <= 0:
            self._fail(candle_open, "FAIL_ATR", "FAIL ATR14")
            return events

        oi = self.oi(candle["t"])
        oi_delta, oi_src = oi["delta"], oi["src"]
        evals = evaluate(cluster, candle, atr, oi_delta, sp)
        tier, event = best_tier(evals), pick_event(evals)
        flagged = [e for e in evals if e.get("lado")]
        l0 = any(e.get("tier") in ("L0", "L1", "L2") for e in flagged)
        l1 = any(e.get("tier") in ("L1", "L2") for e in flagged)
        l2 = any(e.get("tier") == "L2" for e in flagged)
        flags = dict(L0="1" if l0 else "0", L1="1" if l1 else "0", L2="1" if l2 else "0")
        common = dict(data_utc=_utc(candle_open), ticker=tk, oi_delta_pct=fmt_oi(oi_delta), oi_1h_pct=fmt_oi(oi_delta),
                      oi_src=oi_src if oi_src != "none" else "", **flags)

        if not event:
            note = (f"NO_TRIGGER {evals[0].get('motivo')} oi={oi_delta if oi_delta is not None else 'n/a'} "
                    f"H={candle['high']} L={candle['low']} C={candle['close']}")
            self.journal.log(**common, resultado_R=note[:200], motor="CT-NIVEL")
            return events

        pa_, pb_ = event.get("plan_a") or {}, event.get("plan_b") or {}
        detail = dict(
            lado=event["lado"], rejeicao_pct=f"{event['rejeicao']:.1f}",
            entrada=fmt_px(pb_["entrada"]), stop=fmt_px(pb_["stop"]), alvo=fmt_px(pb_["alvo"]),
            dist_atr="" if event.get("dist_atr") is None else f"{event['dist_atr']:.4f}",
            SKIP_R="1" if event.get("skip_r") else "0", nivel=fmt_px(event["level"]), tier=event.get("tier") or "",
            entrada_A=fmt_px(pa_["entrada"]), stop_A=fmt_px(pa_["stop"]), alvo_A=fmt_px(pa_["alvo"]),
            entrada_B=fmt_px(pb_["entrada"]), stop_B=fmt_px(pb_["stop"]), alvo_B=fmt_px(pb_["alvo"]),
            validade_h=str(sp.valid_h) if event.get("tier") == "L2" else "",
            cancel_nofill_h=str(sp.cancel_h) if event.get("tier") == "L2" else "")

        if event.get("tier") == "L2" and event.get("valido"):
            busy = self.trade is not None or self.paused
            if busy:
                self.journal.log(**common, **detail, resultado_R="L2_SKIP_BUSY (ordem ativa ou robô em pausa)", motor="CT-NIVEL")
                return events
            events += self._open_order(event, candle, oi, atr, close_ts, candle_open)
            self.journal.log(**common, **detail, tomei_passei="LIMIT_PAPER", resultado_R="", motor="CT-NIVEL-B")
            return events
        if event.get("tier") == "L2":  # L2 mas recusado: veto de R ou falta de reclaim (se exigido)
            why = "L2_SKIP_R |limit-stop|>1.2×ATR" if event.get("skip_r") else "L2_SEM_RECLAIM (SWEEP_REQUIRE_RECLAIM)"
            self.journal.log(**common, **detail, resultado_R=why, motor="CT-NIVEL")
            return events
        if event.get("tier") == "L1":
            self.journal.log(**common, **detail, resultado_R=f"L1_NO_L2 {event.get('motivo', '')}"[:200], motor="CT-NIVEL")
            if oi_delta is None:  # pavio + varredura mas sem OI: convém saberes que não deu para confirmar
                events.append(Event("note", data={"text": (
                    f"Varredura com pavio {event['rejeicao']:.0f}% em {fmt_px(event['level'])} ({event['lado']}), mas sem OI "
                    f"({'; '.join(oi['errors'])[:120]}): não consigo confirmar o L2.")}))
            return events
        self.journal.log(**common, **detail, resultado_R=f"L0 {event.get('motivo', '')}"[:200], motor="CT-NIVEL")
        return events

    def _open_order(self, event: dict, candle: dict, oi: dict, atr: float, close_ts: pd.Timestamp,
                    candle_open: pd.Timestamp) -> list[Event]:
        sp = self.sp
        pb, pa_ = event["plan_b"], event["plan_a"]
        limit, risk = pb["entrada"], pb["r_raw"]
        base = close_ts if sp.count_from_alert else candle_open
        marketable = is_marketable(event["lado"], limit, candle["close"])
        self.seq += 1
        t = Trade(
            id=self.seq, side=event["lado"].lower(), kind="sweep", entry=limit, stop=pb["stop"], stop0=pb["stop"],
            tp1=pb["alvo"], tp2=pb["alvo"], risk=risk, open_time=close_ts.isoformat(),
            cost_r=2 * sp.cost_side * limit / risk, state="pending",
            tp1_r=abs(pb["alvo"] - limit) / risk, tp2_r=abs(pb["alvo"] - limit) / risk, single_target=True,
            meta={"tier": "L2", "level": event["level"], "oi_delta": event["oi_delta"], "oi_src": oi["src"],
                  "rejeicao": event["rejeicao"], "dist_atr": event.get("dist_atr"), "atr": atr, "R": pb["R"],
                  "candle_open": _utc(candle_open), "candle_close_px": candle["close"],
                  "cancel_at": (base + pd.Timedelta(hours=sp.cancel_h)).isoformat(),
                  "valid_until": (base + pd.Timedelta(hours=sp.valid_h)).isoformat(),
                  "plan_a": {"entrada": pa_["entrada"], "stop": pa_["stop"], "alvo": pa_["alvo"]},
                  "marketable": marketable, "reclaim": event.get("reclaim")})
        self.trade = t
        self.day_signals += 1
        self.last_signal_time = close_ts.isoformat()
        events = [Event("sweep_signal", t)]
        if marketable:  # o limite já estava do lado errado do mercado: executa logo (ao preço do limite: pior caso)
            t.state, t.meta["fill_time"] = "open", close_ts.isoformat()
            events.append(Event("filled", t))
        return events
