"""build_week_tabs — чистая функция, БД не нужна.

Восемь вкладок недели в фиксированном порядке (решение владельца 22.08/23.08,
plans/2026-08-22-apparchi-student-cabinet-open-questions.md, п.7) с блокировкой
«следующая вкладка открыта, только когда закрыта предыдущая».
"""
from types import SimpleNamespace

from app.models.tracker import WEEK_TAB_SEQUENCE
from app.services.tracker import build_week_tabs


def _entry(kind: str, status: str) -> dict:
    return {"task": SimpleNamespace(kind=kind), "status": status}


def test_no_entries_gives_eight_open_tabs_in_order():
    tabs = build_week_tabs([])
    assert [t["kind"] for t in tabs] == list(WEEK_TAB_SEQUENCE)
    assert all(not t["is_locked"] for t in tabs)
    assert all(t["entries"] == [] for t in tabs)


def test_unfinished_task_locks_every_following_tab():
    entries = [_entry("homework", "overdue")]
    tabs = build_week_tabs(entries)
    by_kind = {t["kind"]: t for t in tabs}

    assert not by_kind["homework"]["is_locked"]
    assert by_kind["checklist"]["is_locked"]
    assert by_kind["checklist"]["locked_reason"] == "Задание"
    # Заперты все вкладки после «Задания» по порядку, с той же причиной.
    assert by_kind["survey"]["is_locked"]
    assert by_kind["feedback"]["is_locked"]


def test_empty_tab_does_not_block_the_chain():
    # Нет ни материалов, ни видео, ни теста — блокировать нечем, «Занятие»
    # остаётся открытым (решение владельца 23.08).
    entries = [_entry("lesson", "upcoming")]
    tabs = build_week_tabs(entries)
    by_kind = {t["kind"]: t for t in tabs}

    assert not by_kind["material"]["is_locked"]
    assert not by_kind["video"]["is_locked"]
    assert not by_kind["quiz"]["is_locked"]
    assert not by_kind["lesson"]["is_locked"]
    assert by_kind["lesson"]["entries"] == entries


def test_done_task_does_not_lock_next_tab():
    entries = [_entry("homework", "done")]
    tabs = build_week_tabs(entries)
    by_kind = {t["kind"]: t for t in tabs}

    assert not by_kind["checklist"]["is_locked"]


def test_locked_reason_advances_to_next_real_blocker():
    entries = [
        _entry("video", "done"),
        _entry("lesson", "overdue"),
        _entry("homework", "overdue"),
    ]
    tabs = build_week_tabs(entries)
    by_kind = {t["kind"]: t for t in tabs}

    # «Видео» сдано — не блокирует. «Занятие» не сдано — это и есть причина
    # блокировки для «Задания» и всех дальше, а не «Видео».
    assert not by_kind["video"]["is_locked"]
    assert not by_kind["lesson"]["is_locked"]
    assert by_kind["homework"]["is_locked"]
    assert by_kind["homework"]["locked_reason"] == "Занятие"


def test_feedback_tab_is_reserved():
    tabs = build_week_tabs([])
    feedback = next(t for t in tabs if t["kind"] == "feedback")
    assert feedback["reserved"] is True


def test_mock_exam_entry_shows_up_inside_homework_tab():
    # Решение владельца 24.08: билет Пробника — не отдельная карточка вне
    # вкладок, а часть вкладки «Задание». У mock_exam своей позиции в
    # WEEK_TAB_SEQUENCE нет и не появляется.
    entries = [_entry("homework", "overdue"), _entry("mock_exam", "overdue")]
    tabs = build_week_tabs(entries)
    by_kind = {t["kind"]: t for t in tabs}

    assert "mock_exam" not in by_kind
    assert len(by_kind["homework"]["entries"]) == 2
    assert {e["task"].kind for e in by_kind["homework"]["entries"]} == {"homework", "mock_exam"}


def test_unfinished_mock_exam_does_not_lock_following_tabs():
    # Пробник блокирует только переход на следующий месяц (is_month_complete),
    # к недельной цепочке вкладок отношения не имеет — решение владельца 24.08.
    entries = [_entry("homework", "done"), _entry("mock_exam", "overdue")]
    tabs = build_week_tabs(entries)
    by_kind = {t["kind"]: t for t in tabs}

    assert not by_kind["homework"]["is_locked"]
    assert not by_kind["checklist"]["is_locked"]


def test_unfinished_homework_still_locks_even_with_open_mock_exam():
    # Обычная домашка внутри той же вкладки продолжает запирать цепочку —
    # ослабляем требование только для mock_exam, не для homework.
    entries = [_entry("homework", "overdue"), _entry("mock_exam", "done")]
    tabs = build_week_tabs(entries)
    by_kind = {t["kind"]: t for t in tabs}

    assert by_kind["checklist"]["is_locked"]
    assert by_kind["checklist"]["locked_reason"] == "Задание"
