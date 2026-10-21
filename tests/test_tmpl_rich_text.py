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
    assert result == (
        '<span class="rt-list" role="list">'
        '<span class="rt-li" role="listitem">первый</span>'
        '<span class="rt-li" role="listitem">второй</span></span>'
    )


def test_list_from_bullet_lines():
    result = format_rich_text("• один\n• два")
    assert result == (
        '<span class="rt-list" role="list">'
        '<span class="rt-li" role="listitem">один</span>'
        '<span class="rt-li" role="listitem">два</span></span>'
    )


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


# Жалоба ГП 21.10.2026: длинные ОС «разъезжались» — у списков пустые строки
# вокруг, маркеры за краем пузыря, текст после списка выпадал из абзаца.

def test_windows_line_breaks_are_paragraphs():
    # Форма шлёт textarea с \r\n: без нормализации абзацы не находились,
    # а три перевода подряд не схлопывались в один разделитель.
    assert format_rich_text("а\r\n\r\n\r\nб\r\nв") == "а<br><br>б<br>в"
    assert "\r" not in format_rich_text("а\rб")


def test_list_is_not_ul_so_it_can_live_inside_p():
    # Вывод вставляют в <p>; <ul> там парсер разрывает, span — нет.
    result = format_rich_text("текст\n- пункт")
    assert "<ul" not in result and "<li" not in result
    assert 'class="rt-list"' in result


def test_no_blank_line_right_after_list():
    result = format_rich_text("- а\n- б\nдальше")
    assert result.endswith("</span></span>дальше")


def test_blank_line_after_list_is_kept_once():
    result = format_rich_text("- а\r\n\r\nдальше")
    assert result.endswith("</span></span><br>дальше")
