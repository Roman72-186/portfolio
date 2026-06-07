"""Shared Jinja2Templates instance — import this instead of creating per-router."""
import html
import re

from fastapi.templating import Jinja2Templates

from app.config import settings
from app.csrf import generate_csrf_token
from app.services.navigation import curator_nav_items, staff_nav_items, student_nav_items

templates = Jinja2Templates(directory="app/templates")


def _csrf_token_for_request(request) -> str:
    session_id = request.cookies.get("session_id", "")
    return generate_csrf_token(session_id)


# Make csrf_token(request) available in every template automatically
templates.env.globals["csrf_token"] = _csrf_token_for_request
templates.env.globals["settings"] = settings
templates.env.globals["curator_nav_items"] = curator_nav_items
templates.env.globals["staff_nav_items"] = staff_nav_items
templates.env.globals["student_nav_items"] = student_nav_items


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", re.DOTALL)


def format_ticket_description(text: str | None) -> str:
    """
    Минимальная разметка для описаний билетов пробников.

    Синтаксис:
      **жирный**      → <strong>
      *курсив*        → <em>
      строки "- …" или "• …"  → <ul><li>…</li></ul>
      пустая строка   → разделитель абзацев
      \\n             → <br>

    Принимает plain text (экранируется), возвращает безопасный HTML.
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

    # Жирный и курсив
    result = _BOLD_RE.sub(r"<strong>\1</strong>", result)
    result = _ITALIC_RE.sub(r"<em>\1</em>", result)

    # Абзацы и переносы
    parts = re.split(r"\n{2,}", result)
    parts = [p.replace("\n", "<br>") for p in parts]
    return "<br><br>".join(parts)


templates.env.filters["ticket_desc"] = format_ticket_description
