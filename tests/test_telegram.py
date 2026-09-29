"""Camada do Telegram com `requests` simulado (nunca toca na rede)."""
import pytest

from zec_bot import telegram
from zec_bot.telegram import Telegram


class Resp:
    def __init__(self, status=200, text="", body=None):
        self.status_code, self.text, self._body = status, text, body

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(telegram.time, "sleep", lambda s: None)


def tg():
    return Telegram("123:ABC", "42")


def test_unconfigured_prints_instead_of_sending(capsys):
    assert Telegram("", "").send("olá <b>mundo</b>") is True
    assert "olá <b>mundo</b>" in capsys.readouterr().out


def test_successful_send_uses_html(monkeypatch):
    seen = []
    monkeypatch.setattr(telegram.requests, "post", lambda url, json=None, timeout=None: seen.append((url, json)) or Resp(200))
    assert tg().send("<b>x</b>") is True
    url, payload = seen[0]
    assert url == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert payload["chat_id"] == "42" and payload["parse_mode"] == "HTML" and payload["text"] == "<b>x</b>"


def test_html_rejected_by_telegram_is_resent_as_plain_text(monkeypatch):
    """Um '<' solto num texto faz o Telegram devolver 400 'can't parse entities': o aviso não pode perder-se."""
    seen = []

    def fake_post(url, json=None, timeout=None):
        seen.append(dict(json))
        if "parse_mode" in json:
            return Resp(400, "Bad Request: can't parse entities: Unsupported start tag")
        return Resp(200)

    monkeypatch.setattr(telegram.requests, "post", fake_post)
    assert tg().send("🚫 <b>Setup</b> ignorado: funding 0.09 &lt; 0.05 e a &amp; b") is True
    assert len(seen) == 2 and "parse_mode" not in seen[1]
    assert seen[1]["text"] == "🚫 Setup ignorado: funding 0.09 < 0.05 e a & b"  # sem tags, entidades repostas


@pytest.mark.parametrize("status", [401, 403, 404])
def test_client_errors_are_not_retried(monkeypatch, status):
    calls = []
    monkeypatch.setattr(telegram.requests, "post", lambda *a, **k: calls.append(1) or Resp(status, "nope"))
    assert tg().send("x") is False and len(calls) == 1


def test_other_400_is_not_retried_either(monkeypatch):
    calls = []
    monkeypatch.setattr(telegram.requests, "post", lambda *a, **k: calls.append(1) or Resp(400, "chat not found"))
    assert tg().send("x") is False and len(calls) == 1


def test_server_errors_are_retried_then_succeed(monkeypatch):
    replies = iter([Resp(500, "boom"), Resp(502, "boom"), Resp(200)])
    monkeypatch.setattr(telegram.requests, "post", lambda *a, **k: next(replies))
    assert tg().send("x") is True


def test_gives_up_after_three_network_failures(monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise telegram.requests.ConnectionError("sem rede")

    monkeypatch.setattr(telegram.requests, "post", boom)
    assert tg().send("x") is False and len(calls) == 3


def updates(*items):
    return Resp(200, body={"result": [{"update_id": uid, "message": {"chat": {"id": chat}, "text": text}}
                                      for uid, chat, text in items]})


def test_commands_only_from_your_chat_and_offset_advances(monkeypatch):
    seen = []

    def fake_get(url, params=None, timeout=None):
        seen.append(dict(params))
        return updates((10, 42, "/Status"), (11, 999, "/pause"), (12, 42, "olá"), (13, 42, "/stats@meu_bot arg"))

    monkeypatch.setattr(telegram.requests, "get", fake_get)
    t = tg()
    assert t.poll_commands() == ["/status", "/stats"]  # ignora outro chat e texto sem '/', tira o @bot e os argumentos
    t.poll_commands()
    assert seen[0]["offset"] == 0 and seen[1]["offset"] == 14  # não relê mensagens antigas


def test_poll_survives_errors(monkeypatch):
    monkeypatch.setattr(telegram.requests, "get", lambda *a, **k: Resp(409, "Conflict: another getUpdates"))
    assert tg().poll_commands() == []

    def boom(*a, **k):
        raise telegram.requests.Timeout()

    monkeypatch.setattr(telegram.requests, "get", boom)
    assert tg().poll_commands() == []
    assert Telegram("", "").poll_commands() == []
