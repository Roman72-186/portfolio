"""Старый домен apparchi.ru нигде не указывается и не переиспользуется.

Владелец 11–12.10.2026: apparchi.ru не наш, сайт переехал на kodarhitektora.ru,
от старого адреса отключились целиком — ни переадресации, ни вебхуков. Хозяин
домена может направить его куда угодно, поэтому любая ссылка, адрес по
умолчанию, роутер Traefik или почта на apparchi.ru — это ученик, уведённый на
чужой сайт, или письмо и вебхук, ушедшие чужому серверу.

Сторож обходит отслеживаемые git файлы кода, шаблонов, скриптов, миграций и
конфигов. Запрещены рабочие формы адреса: ссылка (`https://…apparchi.ru`,
`//…apparchi.ru`), правило Traefik `Host(`apparchi.ru`)`, `DOMAIN=apparchi.ru`,
почта `@apparchi.ru`, строка-значение `"apparchi.ru"`. История в комментариях
(`# до 11.10.2026 — apparchi.ru`) разрешена: строка, которая начинается с `#`
или `//`, не проверяется. Тесты не проверяются — там `apparchi.ru` просто
пример строки. Адрес сайта берут из `settings.domain`, а не пишут руками.
"""
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]

_FORBIDDEN = re.compile(
    r"(?:https?:)?//(?:[a-z0-9-]+\.)*apparchi\.ru"
    r"|Host\(`(?:[a-z0-9-]+\.)*apparchi\.ru`\)"
    r"|DOMAIN\s*=\s*['\"]?apparchi\.ru"
    r"|@apparchi\.ru"
    r"|['\"](?:www\.|view\.)?apparchi\.ru['\"]",
    re.IGNORECASE,
)
_CHECKED = re.compile(
    r"^(app|scripts|alembic)/.+\.(py|html|js|css|json|sh|txt|ya?ml)$"
    r"|^docker-compose[^/]*\.ya?ml$|^Dockerfile$|^\.env\.example$|^alembic\.ini$"
)


def _tracked_files() -> list[pathlib.Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True,
    ).stdout.decode("utf-8")
    return [ROOT / name for name in out.split("\0") if name and _CHECKED.match(name)]


def test_old_domain_is_not_used_anywhere():
    hits = []
    for path in _tracked_files():
        if not path.exists():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if line.lstrip().startswith(("#", "//")):
                continue
            if _FORBIDDEN.search(line):
                hits.append(f"{path.relative_to(ROOT).as_posix()}:{number}: {line.strip()[:120]}")
    assert not hits, (
        "apparchi.ru не наш с 11.10.2026 — адрес сайта берётся из settings.domain "
        "(kodarhitektora.ru):\n" + "\n".join(hits)
    )


def test_guard_catches_working_forms():
    for bad in (
        'url = "https://apparchi.ru/cabinet"',
        "- traefik.http.routers.x.rule=Host(`apparchi.ru`)",
        "DOMAIN=apparchi.ru",
        "VAPID_CLAIM_EMAIL=admin@apparchi.ru",
        'domain: str = "apparchi.ru"',
        '<a href="//www.apparchi.ru/proba">',
    ):
        assert _FORBIDDEN.search(bad), bad
    for ok in ("набора, которого пометили руками (вошёл с apparchi.ru, минуя ссылку",
               "Host(`kodarhitektora.ru`)"):
        assert not _FORBIDDEN.search(ok), ok
