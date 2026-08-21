"""Храповик переиспользования: не даёт интерфейсу расползаться дальше.

Зачем это есть. В `DESIGN.md` описана дизайн-система Spark и «Правило
расширения»: сначала проверь `base.css`, паттерн трижды — вынеси общий класс.
Правило было только текстом, и к 21.08.2026 его обошли 55 шаблонов из 69:
7160 строк CSS живут внутри HTML вместо общего файла. Разгребать это разом
никто не будет, а запрет, который падает на 55 файлах сразу, удалят через
неделю.

Поэтому проверка работает как храповик: текущее состояние зафиксировано в
`tests/reuse_baseline.json` и считается допустимым, а вот **рост** запрещён.
Новый шаблон со своим `<style>`, лишняя строка стилей в существующем, новый
цвет числом вместо токена, новое переопределение общего класса — отказ.
Убавлять можно всегда: как только шаблон похудел, базовый срез пересчитывается
и новая, меньшая цифра становится новым потолком. Назад дороги нет.

Команды:

    python scripts/reuse_check.py            # проверить, ничего не меняя
    python scripts/reuse_check.py --update   # пересчитать базовый срез

`--update` запускается только когда цифры уменьшились или файл осознанно
переименован. Если он вызван, чтобы «погасить красный тест» после новой пачки
инлайновых стилей, — это ровно тот случай, ради которого проверка написана.

Тот же код читает тест `tests/test_reuse_ratchet.py`, так что правило
срабатывает на обычном `pytest`, без отдельной команды и без дисциплины.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "app" / "templates"
BASE_CSS = ROOT / "app" / "static" / "css" / "base.css"
BASELINE_PATH = ROOT / "tests" / "reuse_baseline.json"

# Блок <style> целиком, вместе с обёрткой: считаем и её, иначе дробление
# одного блока на пять мелких выглядело бы как улучшение.
STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)

# Селектор — всё до открывающей фигурной скобки.
SELECTOR_RE = re.compile(r"([^{}]+)\{")

# Имя класса внутри селектора.
CLASS_RE = re.compile(r"\.(-?[A-Za-z_][\w-]*)")

# Цвет, вбитый числом. Токены (var(--blue)) под правило не попадают —
# в этом и смысл.
HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def css_class_names(css_text: str) -> set[str]:
    """Имена классов, для которых в этом CSS есть правило.

    Смотрим только на позиции селекторов, а не на весь текст: упоминание
    `.card` в комментарии — не переопределение.
    """
    names: set[str] = set()
    for selector in SELECTOR_RE.findall(css_text):
        # `@media`, `@supports`, `@keyframes` — не селекторы. Вложенные в них
        # правила регулярка всё равно поймает отдельным проходом.
        if "@" in selector:
            continue
        names.update(CLASS_RE.findall(selector))
    return names


def style_blocks(html: str) -> list[str]:
    """Содержимое всех <style> шаблона."""
    return STYLE_RE.findall(html)


def template_stats(html: str, shared_classes: set[str]) -> dict:
    """Три числа, по которым видно, растёт ли шаблон в сторону своего дизайна.

    `style_lines` — сколько строк CSS живёт прямо в шаблоне;
    `hex` — сколько цветов вбито числом мимо токенов;
    `shadow` — какие общие классы шаблон переопределяет под себя.

    Последнее опаснее первых двух: локальное правило для `.card` меняет вид
    карточки только на одном экране, и дизайн-система тихо перестаёт быть
    источником истины.
    """
    blocks = style_blocks(html)
    joined = "\n".join(blocks)
    return {
        "style_lines": sum(len(block.splitlines()) for block in blocks),
        "hex": len(HEX_RE.findall(joined)),
        "shadow": sorted(css_class_names(joined) & shared_classes),
    }


def shared_class_names() -> set[str]:
    """Классы, которые дизайн-система уже определила в `base.css`."""
    return css_class_names(BASE_CSS.read_text(encoding="utf-8"))


def collect() -> dict[str, dict]:
    """Текущий срез по всем шаблонам, у которых есть собственный CSS.

    Чистые шаблоны в срез не попадают — тогда любой новый файл со стилями
    сразу выглядит как отсутствующий в базовом срезе, и его видно.
    """
    shared = shared_class_names()
    current: dict[str, dict] = {}
    for path in sorted(TEMPLATES_DIR.rglob("*.html")):
        stats = template_stats(path.read_text(encoding="utf-8"), shared)
        if stats["style_lines"] or stats["shadow"]:
            current[path.relative_to(TEMPLATES_DIR).as_posix()] = stats
    return current


def load_baseline() -> dict[str, dict]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def save_baseline(current: dict[str, dict]) -> None:
    BASELINE_PATH.write_text(
        json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def violations(current: dict[str, dict], baseline: dict[str, dict]) -> list[str]:
    """Претензии человеческим языком: что выросло и что с этим делать."""
    problems: list[str] = []

    for name, stats in sorted(current.items()):
        was = baseline.get(name)

        if was is None:
            problems.append(
                f"{name}: новый шаблон со своим блоком <style> "
                f"({stats['style_lines']} строк). Общие стили живут в "
                f"app/static/css/base.css, компоненты — в templates/components/. "
                f"Если экрану правда нужен уникальный CSS — заведите ему "
                f"отдельный файл в static/css/ и подключите с ?v=."
            )
            continue

        if stats["style_lines"] > was["style_lines"]:
            problems.append(
                f"{name}: инлайновых стилей стало больше "
                f"(было {was['style_lines']}, стало {stats['style_lines']} строк). "
                f"Новое правило переносится в base.css или в макрос компонента."
            )

        if stats["hex"] > was["hex"]:
            problems.append(
                f"{name}: цветов числом стало больше (было {was['hex']}, стало "
                f"{stats['hex']}). Цвет берётся токеном: var(--blue), "
                f"var(--text), var(--line). Новый токен заводится в :root "
                f"только под новый устойчивый смысл."
            )

        new_shadow = sorted(set(stats["shadow"]) - set(was["shadow"]))
        if new_shadow:
            problems.append(
                f"{name}: переопределяет общие классы {', '.join(new_shadow)}. "
                f"Правка общего компонента делается в base.css и действует "
                f"на все экраны; если нужен другой вид — это модификатор "
                f"(.btn-blue--compact), а не локальная копия."
            )

    return problems


def main(argv: list[str]) -> int:
    current = collect()

    if "--update" in argv:
        save_baseline(current)
        total = sum(stats["style_lines"] for stats in current.values())
        print(
            f"Базовый срез пересчитан: {len(current)} шаблонов, "
            f"{total} строк инлайнового CSS."
        )
        return 0

    problems = violations(current, load_baseline())
    if problems:
        print("Храповик переиспользования: интерфейс расползается.\n")
        for problem in problems:
            print(f"  • {problem}\n")
        print(
            "Что делать — docs/reuse-contract.md. Что уже есть готового — "
            "docs/component-map.md."
        )
        return 1

    print("Храповик переиспользования: роста нет.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
