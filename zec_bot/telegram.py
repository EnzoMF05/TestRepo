"""Envio (e, opcionalmente, receção de comandos) via Telegram Bot API."""
from __future__ import annotations

import html
import logging
import re
import time

import requests

log = logging.getLogger("zec_bot.telegram")
API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = str(chat_id)
        self._offset = 0

    @property
    def ready(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str, _html: bool = True) -> bool:
        """Envia uma mensagem (HTML). Sem token configurado, imprime na consola (modo de teste).

        Se o Telegram rejeitar o HTML (um "<" solto chega para isso), reenvia em texto simples: é melhor
        receber o aviso sem formatação do que não o receber.
        """
        if not self.ready:
            print("\n[TELEGRAM não configurado — a imprimir na consola]\n" + text + "\n")
            return True
        for attempt in range(3):
            try:
                payload = {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True}
                if _html:
                    payload["parse_mode"] = "HTML"
                r = requests.post(API.format(token=self.token, method="sendMessage"), json=payload, timeout=15)
                if r.status_code == 200:
                    return True
                log.warning("Telegram devolveu %s: %s", r.status_code, r.text[:200])
                if r.status_code == 400 and _html and "parse entities" in r.text.lower():
                    return self.send(html.unescape(re.sub(r"</?[a-z]+>", "", text)), _html=False)
                if r.status_code in (400, 401, 403, 404):  # erro nosso (token, chat id): não repetir
                    return False
            except requests.RequestException as e:
                log.warning("Telegram falhou (%s), tentativa %d/3", e, attempt + 1)
            time.sleep(2 * (attempt + 1))
        return False

    def poll_commands(self) -> list[str]:
        """Devolve os comandos (ex.: '/status') recebidos desde a última chamada, só do teu chat."""
        if not self.ready:
            return []
        try:
            r = requests.get(
                API.format(token=self.token, method="getUpdates"),
                params={"offset": self._offset, "timeout": 0},
                timeout=15,
            )
            if r.status_code != 200:
                log.warning("getUpdates devolveu %s: %s", r.status_code, r.text[:200])
                return []
            out = []
            for upd in r.json().get("result", []):
                self._offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                if str(msg.get("chat", {}).get("id")) != self.chat_id:
                    continue  # ignora quem não és tu
                text = (msg.get("text") or "").strip()
                if text.startswith("/"):
                    out.append(text.split()[0].split("@")[0].lower())
            return out
        except requests.RequestException as e:
            log.warning("getUpdates falhou: %s", e)
            return []
