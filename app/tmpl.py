"""Shared Jinja2Templates instance — import this instead of creating per-router."""
import html
import re

from fastapi.templating import Jinja2Templates

from app.config import settings
from app.constants import TARIFF_DISPLAY, TARIFF_SLUGS, TIMEZONE_DISPLAY
from app.csrf import generate_csrf_token
from app.services.navigation import curator_nav_items, staff_nav_items, student_nav_items

templates = Jinja2Templates(directory="app/templates")


def _csrf_token_for_request(request) -> str:
    session_id = request.cookies.get("session_id", "")
    return generate_csrf_token(session_id)


def _unread_count_for(user) -> int:
    """Непрочитанные уведомления для бейджа колокольчика — на любой странице.

    Бейдж рендерится глобально из base.html, а контекст-переменную unread_count
    прокидывают только отдельные роуты. Считаем здесь через Redis-кэш
    (get_cached_unread), чтобы число было корректным везде без доп. запросов.
    """
    if not user:
        return 0
    try:
        from app.cache import get_cached_unread, set_cached_unread
        cached = get_cached_unread(user["user_id"])
        if cached is not None:
            return cached
        from sqlalchemy import func
        from app.db.database import SessionLocal
        from app.models.notification import Notification
        db = SessionLocal()
        try:
            count = db.query(func.count(Notification.id)).filter(
                Notification.user_id == user["user_id"],
                Notification.is_read.is_(False),
            ).scalar() or 0
        finally:
            db.close()
        set_cached_unread(user["user_id"], count)
        return count
    except Exception:
        return 0


# Make csrf_token(request) available in every template automatically
templates.env.globals["csrf_token"] = _csrf_token_for_request
templates.env.globals["settings"] = settings
templates.env.globals["unread_count_for"] = _unread_count_for
templates.env.globals["curator_nav_items"] = curator_nav_items
templates.env.globals["staff_nav_items"] = staff_nav_items
templates.env.globals["student_nav_items"] = student_nav_items


def tariff_label(tariff: str | None) -> str:
    """Название тарифа для показа человеку: «Уверенный максимум», не «УВЕРЕННЫЙ МАКСИМУМ».

    В базе тариф лежит капсом, и экраны, которые печатали его как есть, кричали
    на ученика и занимали лишнюю ширину. Форма для интерфейса одна на проект —
    `TARIFF_DISPLAY` (её же берут пути в S3 и вкладка «Личное»).
    Незнакомое значение возвращаем как есть: пустой плашки быть не должно.
    """
    if not tariff:
        return ""
    return TARIFF_DISPLAY.get(tariff.upper(), tariff)


def tariff_slug(tariff: str | None) -> str:
    """Имя цветовой группы тарифа для CSS-модификатора (`profile-tariff--self`).

    Неизвестный или пустой тариф даёт `legacy` — нейтральный тёмный цвет.
    Молчаливого «без модификатора» тут быть не должно: плашка белая, и надпись
    без своего цвета унаследовала бы белый текст поверх белого фона.
    """
    if not tariff:
        return "legacy"
    return TARIFF_SLUGS.get(tariff.upper(), "legacy")


templates.env.filters["tariff_label"] = tariff_label
templates.env.filters["tariff_slug"] = tariff_slug


def timezone_label(tz: str | None) -> str:
    """Название часового пояса для показа: «МСК+3», не сырой код смещения."""
    if not tz:
        return ""
    return TIMEZONE_DISPLAY.get(tz, tz)


templates.env.filters["timezone_label"] = timezone_label


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", re.DOTALL)
# Ссылка вида [текст](url) — экранированный текст уже прошёл html.escape(),
# поэтому здесь ищем &quot;-свободный url в скобках, без вложенных ] или ).
_LINK_RE = re.compile(r"\[([^\[\]]+)\]\(([^()\s]+)\)")
_LINK_SAFE_SCHEMES = ("http://", "https://")


def _link_sub(match: "re.Match[str]") -> str:
    label, url = match.group(1), match.group(2)
    if not url.lower().startswith(_LINK_SAFE_SCHEMES):
        return match.group(0)
    return f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>'


def format_rich_text(text: str | None, links: bool = True) -> str:
    """
    Минимальная разметка для текстов преподавателя/куратора, которые видит ученик.

    JS-зеркало для WYSIWYG-редактора ввода — app/static/js/rich-text-field.js
    (markdownToHtml/htmlToMarkdown). Меняешь синтаксис здесь — поменяй и там,
    иначе то, что преподаватель видит в редакторе, разойдётся с тем, что
    ученик увидит после сохранения.

    Синтаксис:
      **жирный**       → <strong>
      *курсив*         → <em>
      строки "- …" или "• …"  → <ul><li>…</li></ul>
      [текст](url)     → <a> (только http/https, иначе остаётся как есть)
      пустая строка    → разделитель абзацев
      \\n              → <br>

    Принимает plain text (экранируется), возвращает безопасный HTML.

    `links=False` — для мест, где вывод уже сам лежит внутри `<a>` (например
    строка списка видео, целиком обёрнутая в ссылку на карточку): вложенный
    `<a>` внутри `<a>` невалиден и ломает кликабельность строки.
    """
    if not text:
        return ""
    escaped = html.escape(text)

    # Списки: последовательные строки, начинающиеся с "- " или "• "
    lines = escaped.split("\n")
    out_lines: list[str] = []
    buf: list[str] = []

    def flush():
        if buf:
            items = "".join(f"<li>{item}</li>" for item in buf)
            out_lines.append(f"<ul>{items}</ul>")
            buf.clear()

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("- ") or stripped.startswith("• "):
            buf.append(stripped[2:].strip())
        else:
            flush()
            out_lines.append(line)
    flush()
    result = "\n".join(out_lines)

    # Ссылки, жирный и курсив
    if links:
        result = _LINK_RE.sub(_link_sub, result)
    result = _BOLD_RE.sub(r"<strong>\1</strong>", result)
    result = _ITALIC_RE.sub(r"<em>\1</em>", result)

    # Абзацы и переносы
    parts = re.split(r"\n{2,}", result)
    parts = [p.replace("\n", "<br>") for p in parts]
    return "<br><br>".join(parts)


# Историческое имя оставлено алиасом — уже используется в шаблонах билетов
# пробников (superadmin_exam_assignment_detail.html, guest/guest_exam.html)
# и на клиенте после серверного рендера (upload_mock.html, mock_exam.html).
format_ticket_description = format_rich_text

templates.env.filters["rich_text"] = format_rich_text
templates.env.filters["ticket_desc"] = format_rich_text
