"""Храповик переиспользования — правило, которое держится тестом, а не текстом.

`DESIGN.md` требует переиспользовать дизайн-систему Spark с самого начала, и это
требование обходили 54 раза из 69: столько шаблонов носят собственный блок
`<style>` на 7073 строки, а 35 из них переопределяют общие классы под себя.
Текст правила это не остановило. Останавливает вот это.

Проверка сравнивает текущее состояние с зафиксированным срезом
`tests/reuse_baseline.json`: сегодняшние цифры законны, а любой рост — нет.
Убавлять можно свободно, для этого есть `python scripts/reuse_check.py --update`.

Механику и объяснение, почему запрет устроен именно так, см. в
`scripts/reuse_check.py` и `../docs/reuse-contract.md`.
"""
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_reuse_check():
    """Скрипт грузится по пути — так же, как в `test_deploy_script.py`."""
    spec = importlib.util.spec_from_file_location(
        "reuse_check", ROOT / "scripts" / "reuse_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reuse = _load_reuse_check()


def test_interface_does_not_sprawl_further():
    """Ни одного нового инлайнового стиля, цвета числом и захвата общего класса.

    Падение этого теста не означает «плохо написан код». Оно означает: то, что
    вы сейчас делаете, уже есть в готовом виде — сообщение об ошибке называет,
    где именно.
    """
    problems = reuse.violations(reuse.collect(), reuse.load_baseline())
    assert not problems, "\n\n".join(problems)


def test_baseline_matches_reality():
    """Срез не должен быть завышен: убавили — зафиксируйте.

    Иначе освободившийся запас копится и однажды позволит вписать полсотни
    строк, не тронув красный.
    """
    current = reuse.collect()
    baseline = reuse.load_baseline()

    stale: list[str] = []
    for name, stats in sorted(current.items()):
        was = baseline.get(name)
        if was is None:
            continue  # это ловит `test_interface_does_not_sprawl_further`

        # Проверяются все три числа. Иначе шаблон отдаёт три цвета в одном
        # месте, забирает три в другом и годами стоит зелёным у старого потолка.
        if stats["style_lines"] < was["style_lines"]:
            stale.append(
                f"{name}: строк CSS было {was['style_lines']}, "
                f"стало {stats['style_lines']}"
            )
        if stats["hex"] < was["hex"]:
            stale.append(
                f"{name}: цветов числом было {was['hex']}, стало {stats['hex']}"
            )
        dropped = sorted(set(was["shadow"]) - set(stats["shadow"]))
        if dropped:
            stale.append(
                f"{name}: больше не переопределяет общие классы "
                f"{', '.join(dropped)}"
            )

    gone = sorted(set(baseline) - set(current))

    assert not stale and not gone, (
        "Стилей стало меньше — это хорошо, но срез надо пересчитать:\n"
        "  python scripts/reuse_check.py --update\n\n"
        + "\n".join(stale + [f"{name}: шаблона больше нет в срезе" for name in gone])
    )


# Цветов, вбитых числом в `base.css` мимо блока `:root`, на 21.08.2026.
# Это не норма, а замер: в идеале ноль, каждый такой цвет — либо забытый токен,
# либо оттенок, живущий в одном месте и невидимый для дизайн-системы. Цифра
# только уменьшается; выросла — значит токен опять обошли.
BASE_CSS_HEX_OUTSIDE_ROOT = 35


def test_design_tokens_stay_the_single_source_of_colour():
    """Общий CSS тоже не должен обрастать цветами мимо токенов.

    Храповик по шаблонам не поможет, если сам `base.css` начнёт копить оттенки
    внутри правил: тогда «источник истины» перестанет им быть тихо и изнутри.
    """
    css = reuse.BASE_CSS.read_text(encoding="utf-8")
    root_block = css[css.index(":root") : css.index("}", css.index(":root"))]
    outside_root = reuse.HEX_RE.findall(css.replace(root_block, ""))

    assert len(outside_root) <= BASE_CSS_HEX_OUTSIDE_ROOT, (
        f"В base.css стало больше цветов числом вне :root "
        f"(было {BASE_CSS_HEX_OUTSIDE_ROOT}, стало {len(outside_root)}). Цвет "
        f"объявляется токеном в :root и дальше используется как var(--имя). "
        f"Стало меньше — уменьшите BASE_CSS_HEX_OUTSIDE_ROOT в этом файле."
    )
