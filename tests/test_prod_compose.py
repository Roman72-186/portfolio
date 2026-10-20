"""Боевой compose: то, что не видно из кода приложения."""

from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parent.parent / "docker-compose.prod-ru.yml"


def test_uvicorn_trusts_forwarded_for_from_traefik_network():
    """Код-ревью 28.09.2026, P1: без `--forwarded-allow-ips` uvicorn верит
    X-Forwarded-For только от 127.0.0.1, приложение видит у всех адрес
    Traefik, и лимит попыток входа один на всю школу (проверено на сервере
    29.09.2026: 505 запросов за два часа — все с 172.18.0.2)."""
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    command = compose["services"]["app"]["command"]

    assert "--forwarded-allow-ips=172.18.0.0/16" in command
    assert "--forwarded-allow-ips=*" not in command
