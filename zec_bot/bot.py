"""Loop principal do robô: vai buscar candles, corre a estratégia e envia sinais para o Telegram."""
from __future__ import annotations

import json
import logging
import os
import signal as os_signal
import time
from pathlib import Path
from typing import Callable

import pandas as pd

from . import messages
from .config import Config
from .data import get_candles
from .engine import Engine
from .strategy import Params, prepare
from .telegram import Telegram

log = logging.getLogger("zec_bot")

MAX_SIGNAL_AGE = pd.Timedelta(minutes=5)  # não enviar sinais "velhos" (ex.: Mac saiu de suspensão)
ALERT_AFTER_FAILURES = 10


class Bot:
    def __init__(
        self,
        cfg: Config,
        params: Params | None = None,
        fetch: Callable[[], tuple[pd.DataFrame, str]] | None = None,
        tg: Telegram | None = None,
        now: Callable[[], pd.Timestamp] | None = None,
    ):
        self.cfg = cfg
        self.p = params or Params()
        self.engine = Engine(cfg, self.p)
        self.tg = tg or Telegram(cfg.telegram_token, cfg.telegram_chat_id)
        self.fetch = fetch or (
            lambda: get_candles(cfg.exchange, cfg.base, cfg.quote, cfg.interval_min, self.p.warmup_bars + 50)
        )
        self.now = now or (lambda: pd.Timestamp.now(tz="UTC"))
        self.exchange = cfg.exchange
        self.last_price = 0.0
        self.failures = 0
        self.down_alerted = False
        self._load_state()

    # ------------------------------------------------------------------ estado
    def _load_state(self) -> None:
        path = Path(self.cfg.state_file)
        if path.exists():
            try:
                self.engine.load(json.loads(path.read_text(encoding="utf-8")))
                log.info("Estado carregado de %s (%d trades no histórico)", path, len(self.engine.closed))
            except (ValueError, KeyError, TypeError) as e:
                log.error("Ficheiro de estado inválido (%s) — a começar do zero. Cópia em .bak", e)
                path.rename(path.with_suffix(".bak"))

    def _save_state(self) -> None:
        path = Path(self.cfg.state_file)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.engine.to_dict()), encoding="utf-8")
        os.replace(tmp, path)  # escrita atómica: nunca deixa o ficheiro a meio

    # ------------------------------------------------------------------ ciclo
    def _dispatch(self, events) -> None:
        for ev in events:
            text = (
                messages.format_signal(ev.trade, self.cfg, **ev.data)
                if ev.kind == "signal"
                else messages.format_event(ev, self.cfg)
            )
            log.info("evento %s", ev.kind)
            if text:
                self.tg.send(text)

    def cycle(self) -> int:
        """Uma passagem: processa as barras novas. Devolve quantas processou."""
        df, self.exchange = self.fetch()
        if df.empty:
            return 0
        self.last_price = float(df["close"].iloc[-1])
        feat = prepare(df, self.p)

        if self.engine.last_bar:
            todo = [i for i, ts in enumerate(feat.index) if ts > pd.Timestamp(self.engine.last_bar)]
        else:
            todo = [len(feat) - 1]  # primeira vez: só a barra mais recente, sem reprocessar histórico
        last_i = len(feat) - 1

        for i in todo:
            closed_at = feat.index[i] + pd.Timedelta(minutes=self.cfg.interval_min)
            fresh = self.now() - closed_at <= MAX_SIGNAL_AGE
            self._dispatch(self.engine.on_bar(feat, i, evaluate=(i == last_i and fresh)))
        if todo:
            self._save_state()
        return len(todo)

    # ------------------------------------------------------------------ comandos
    def handle_commands(self) -> None:
        for cmd in self.tg.poll_commands():
            e = self.engine
            if cmd == "/status":
                self.tg.send(messages.format_status(e, self.cfg, self.last_price, self.exchange))
            elif cmd == "/stats":
                self.tg.send(messages.format_stats(e.stats(7), "Últimos 7 dias")
                             + "\n\n" + messages.format_stats(e.stats(), "Desde o início"))
            elif cmd == "/pause":
                e.paused = True
                self._save_state()
                self.tg.send("⏸ Sinais em pausa (os trades abertos continuam a ser acompanhados). /resume para retomar.")
            elif cmd == "/resume":
                e.paused = False
                self._save_state()
                self.tg.send("▶️ Sinais retomados.")
            else:
                self.tg.send("Comandos: /status /stats /pause /resume")

    # ------------------------------------------------------------------ loop
    def step(self) -> None:
        """Um passo do loop com tratamento de erros e alertas de falha."""
        try:
            self.cycle()
            if self.down_alerted:
                self.tg.send("✅ Dados de mercado recuperados.")
            self.failures, self.down_alerted = 0, False
        except Exception as e:  # noqa: BLE001 — o loop nunca deve morrer por um erro de rede
            self.failures += 1
            log.exception("Erro no ciclo (%d seguidos): %s", self.failures, e)
            if self.failures >= ALERT_AFTER_FAILURES and not self.down_alerted:
                self.down_alerted = True
                self.tg.send(f"⚠️ O robô não consegue obter dados há {self.failures} tentativas: {e}")
        if self.cfg.enable_commands:
            self.handle_commands()

    def run(self) -> None:
        def _stop(*_):
            raise KeyboardInterrupt

        os_signal.signal(os_signal.SIGTERM, _stop)  # launchd/kill param de forma limpa e guardam o estado
        self.tg.send(messages.format_startup(self.cfg, self.exchange))
        log.info("Robô a correr. Ctrl+C para parar.")
        try:
            while True:
                self.step()
                time.sleep(self.cfg.poll_seconds)
        except KeyboardInterrupt:
            log.info("A parar…")
        finally:
            self._save_state()
