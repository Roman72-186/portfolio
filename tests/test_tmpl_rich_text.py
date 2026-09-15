"""
format_rich_text — минимальная разметка описаний для преподавателя/куратора
(владелец 15.09.2026: жирный/курсив/списки/ссылки в описаниях видео, заданий,
домашних работ и т.п., см. app/tmpl.py). Раньше здесь же жил ticket_desc —
только для билетов пробников; функция теперь общая, ticket_desc — алиас.
"""
from app.tmpl import format_rich_text


def test_bold_and_italic():
    assert format_rich_text("**жирный** и *курсив*") == "<strong>жирный</strong> и <em>курсив</em>"


def test_list_from_dash_lines():
    result = format_rich_text("- первый\n- второй")
    assert result == "<ul><li>первый</li><li>второй</li></ul>"


def test_list_from_bullet_lines():
    result = format_rich_text("• один\n• два")
    assert result == "<ul><li>один</li><li>два</li></ul>"


def test_paragraphs_and_single_line_break():
    result = format_rich_text("первый абзац\nвторая строка\n\nвторой абзац")
    assert result == "первый абзац<br>вторая строка<br><br>второй абзац"


def test_link_with_https_scheme():
    result = format_rich_text("Смотри [здесь](https://example.com)")
    assert result == 'Смотри <a href="https://example.com" target="_blank" rel="noopener noreferrer">здесь</a>'


def test_link_with_http_scheme():
    result = format_rich_text("[сайт](http://example.com)")
    assert '<a href="http://example.com"' in result


def test_link_disabled_via_links_false():
    result = format_rich_text("[текст](https://example.com)", links=False)
    assert "<a " not in result
    assert "[текст](https://example.com)" in result


def test_link_with_unsafe_scheme_is_left_as_text():
    result = format_rich_text("[кликни](javascript:alert(1))")
    assert "<a " not in result
    assert "javascript:alert(1)" in result


def test_html_in_source_text_is_escaped_not_executed():
    result = format_rich_text("<script>alert(1)</script>")
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_empty_and_none_input():
    assert format_rich_text(None) == ""
    assert format_rich_text("") == ""


def test_ticket_desc_alias_is_the_same_function():
    from app.tmpl import format_ticket_description
    assert format_ticket_description is format_rich_text
