"""Каждая зависимость закреплена точной версией.

Код-ревью 28.09.2026, P3: `cryptography>=42.0` был единственным незакреплённым
пакетом — пересборка образа при деплое молча тянула бы новую мажорную версию
библиотеки, на которой держится шифрование персональных данных. Закреплено на
версии, что стояла в боевом контейнере 29.09.2026 (50.0.0).
"""
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"


def test_every_requirement_is_pinned():
    loose = []
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        spec = line.split("#", 1)[0].strip()
        if spec and "==" not in spec:
            loose.append(spec)
    assert loose == []
