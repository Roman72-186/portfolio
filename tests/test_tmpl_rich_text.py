"""
format_rich_text — минимальная разметка описаний для преподавателя/куратора
(владелец 15.09.2026: жирный/курсив/списки/ссылки в описаниях видео, заданий,
домашних работ и т.п., см. app/tmpl.py). Раньше здесь же жил ticket_desc —
только для билетов пробников; функция теперь общая, ticket_desc — алиас.
"""
from app.tmpl import format_rich_text


def test_bold_and_italic():
    assert format_rich_text("**жирный** и *курсив*") == "<strong>жирный</strong> и <em>курсив</em>"


def test_link_with_https_scheme():
    from xml.etree import ElementTree

    result = format_rich_text("Смотри [здесь](https://example.com)")
    anchor, = ElementTree.fromstring(f"<root>{result}</root>").iter("a")
    link = anchor.attrib
    assert link["href"] == "https://example.com"
    assert link["target"] == "_blank"
    assert {"noopener", "noreferrer"} <= set(link["rel"].split())


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


# Жалоба ГП 21.10.2026: длинные ОС «разъезжались» — у списков пустые строки
# вокруг, маркеры за краем пузыря, текст после списка выпадал из абзаца.

def test_windows_line_breaks_are_paragraphs():
    # Форма шлёт textarea с \r\n: без нормализации абзацы не находились,
    # а три перевода подряд не схлопывались в один разделитель.
    assert format_rich_text("а\r\n\r\nб\r\nв") == format_rich_text("а\n\nб\nв")
    assert "\r" not in format_rich_text("а\rб")
