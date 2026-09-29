"""Ссылка в новой вкладке — только с `rel="noopener"`.

Код-ревью 28.09.2026, P3: три шаблона выдачи доступа открывали страницу входа
через `target="_blank"` без `rel` — открытая вкладка получала `window.opener`
и могла увести кабинет сотрудника на чужой адрес. Везде в проекте `rel`
стоит; сторож держит это для новых шаблонов.
"""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"

# Экран отказа входа через VK: владелец 29.09.2026 — «через ВК у нас нет хода»,
# под VK правок не делаем (AGENTS.md, сквозные правила).
SKIP = {"denied.html"}

_A_TAG = re.compile(r"<a\b[^>]*>", re.IGNORECASE | re.DOTALL)


def test_every_blank_link_has_noopener():
    offenders = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        if path.name in SKIP:
            continue
        for tag in _A_TAG.findall(path.read_text(encoding="utf-8")):
            if re.search(r"""target\s*=\s*["']_blank["']""", tag) and not re.search(
                r"""rel\s*=\s*["'][^"']*noopener""", tag
            ):
                offenders.append(f"{path.relative_to(TEMPLATES)}: {' '.join(tag.split())[:120]}")
    assert offenders == [], "target=_blank без rel=noopener:\n" + "\n".join(offenders)
