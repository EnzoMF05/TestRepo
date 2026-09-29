import pytest
import requests


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Os testes nunca podem falar com exchanges/Telegram/Coinalyze de verdade."""
    def blocked(self, method, url, *a, **k):
        raise AssertionError(f"Chamada de rede real num teste: {method} {url}")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
