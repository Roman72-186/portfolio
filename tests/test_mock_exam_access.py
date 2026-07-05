from datetime import datetime, timedelta

from app.models.exam_assignment import ExamAssignment, ExamTicket, ExamTicketAssignee
from app.models.tag import Tag, UserTag
from app.models.user import User
from app.services.exam_cycle import get_active_ticket, get_active_tickets
from app.services.mock_exam_access import (
    get_student_ids_for_target_tag,
    mock_exam_deadline_for_started_at,
    get_allowed_mock_subjects,
    is_mock_exam_attempt_open,
    is_mock_exam_ticket_start_open,
    is_mock_exam_ticket_submission_open,
    ticket_closes_at,
    ticket_latest_start_at,
    ticket_duration_sec,
)
from app.services.tz import MSK_TZ


def _create_ticket(db, user, subject: str):
    from app.services.tz import today_msk

    today = today_msk()
    assignment = ExamAssignment(
        title=f"Тест {subject}",
        subject=subject,
        created_by_id=user.id,
        status="published",
    )
    db.add(assignment)
    db.flush()
    ticket = ExamTicket(
        assignment_id=assignment.id,
        ticket_number=1,
        title=f"Билет {subject}",
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=30),
        assign_to_all=True,
    )
    db.add(ticket)
    db.commit()
    return ticket


def _create_tagged_exact_ticket(
    db,
    user,
    tag,
    *,
    subject: str = "Рисунок",
    opens_at: datetime | None = None,
    closes_at: datetime | None = None,
    duration_minutes: int = 90,
):
    assignment = ExamAssignment(
        title=f"Тест {subject}",
        subject=subject,
        created_by_id=user.id,
        status="published",
    )
    db.add(assignment)
    db.flush()
    from app.services.tz import today_msk

    today = today_msk()
    ticket = ExamTicket(
        assignment_id=assignment.id,
        ticket_number=1,
        title=f"Билет {subject}",
        # Доступ гейтится по периоду opens_at..closes_at (точные дата+время, МСК).
        # По умолчанию opens_at/closes_at не заданы → окно непрерывно тянется по
        # fallback от start_date 11:45 до end_date 18:30 (today-1..today+30), что
        # охватывает замороженное «сегодня 13:00» — так тесты тегов/предметов видят
        # билет активным. Тесты периода передают opens_at/closes_at явно.
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=30),
        opens_at=opens_at,
        closes_at=closes_at,
        duration_minutes=duration_minutes,
        target_tag_id=tag.id,
        assign_to_all=True,
    )
    db.add(ticket)
    db.commit()
    return ticket


def test_active_tickets_prefer_newer_assignment_over_later_start_date(db, regular_user):
    """Свежевыданное задание должно перекрывать старое, даже если у старого start_date позже."""
    from app.services.tz import today_msk

    today = today_msk()
    old_assignment = ExamAssignment(
        title="Старое общее задание",
        subject="Рисунок",
        created_by_id=regular_user.id,
        status="published",
    )
    db.add(old_assignment)
    db.flush()
    old_ticket = ExamTicket(
        assignment_id=old_assignment.id,
        ticket_number=1,
        title="Старый общий билет",
        start_date=today - timedelta(days=2),
        end_date=today + timedelta(days=3),
        assign_to_all=True,
    )
    db.add(old_ticket)
    db.flush()

    new_assignment = ExamAssignment(
        title="Новое персональное задание",
        subject="Рисунок",
        created_by_id=regular_user.id,
        status="published",
    )
    db.add(new_assignment)
    db.flush()
    new_tickets = []
    for n in (1, 2):
        ticket = ExamTicket(
            assignment_id=new_assignment.id,
            ticket_number=n,
            title=f"Новый билет {n}",
            start_date=today - timedelta(days=9),
            end_date=today + timedelta(days=16),
            assign_to_all=False,
        )
        db.add(ticket)
        db.flush()
        db.add(ExamTicketAssignee(ticket_id=ticket.id, user_id=regular_user.id))
        new_tickets.append(ticket)
    db.commit()

    active_ids = [ticket.id for ticket in get_active_tickets(db, regular_user.id, "Рисунок")]

    assert active_ids == [new_tickets[1].id, new_tickets[0].id]
    assert old_ticket.id not in active_ids
    assert get_active_ticket(db, regular_user.id, "Рисунок").id == new_tickets[1].id


def test_attempt_open_ignores_90_minute_timer():
    """Таймер 1:30 снят: попытка открыта для сдачи даже спустя часы после старта
    и за пределами окна по времени (это owner-слой фикса «ученики не могут сдать
    в отведённый период»)."""
    started_at = datetime(2026, 1, 1, 11, 45, tzinfo=MSK_TZ)
    much_later = datetime(2026, 1, 1, 23, 0, tzinfo=MSK_TZ)  # +11 ч, далеко за 90 мин и за 18:30
    assert (
        is_mock_exam_attempt_open(
            started_at,
            value=much_later,
            closes_at=datetime(2026, 1, 1, 18, 0, tzinfo=MSK_TZ),
            duration_sec=90 * 60,
        )
        is True
    )


def test_mock_exam_deadline_is_90_minutes_but_not_after_day_close():
    started_at = datetime(2026, 1, 1, 11, 45, tzinfo=MSK_TZ)
    assert mock_exam_deadline_for_started_at(started_at) == datetime(
        2026, 1, 1, 13, 15, tzinfo=MSK_TZ
    )

    latest_start = datetime(2026, 1, 1, 17, 0, tzinfo=MSK_TZ)
    assert mock_exam_deadline_for_started_at(latest_start) == datetime(
        2026, 1, 1, 18, 30, tzinfo=MSK_TZ
    )


def _inmemory_ticket(*, opens_at, closes_at, duration_minutes=90, restrict_start_by_duration=True):
    """ExamTicket БЕЗ записи в БД — чтобы проверять точные границы окна без
    tz-артефакта SQLite (DateTime(timezone=True) при round-trip сдвигает время)."""
    return ExamTicket(
        assignment_id=1,
        ticket_number=1,
        title="Билет",
        start_date=opens_at.date(),
        end_date=closes_at.date(),
        opens_at=opens_at,
        closes_at=closes_at,
        duration_minutes=duration_minutes,
        restrict_start_by_duration=restrict_start_by_duration,
        assign_to_all=True,
    )


def test_active_ticket_within_access_window(db, regular_user):
    """Билет активен, пока «сейчас» внутри периода доступа opens_at..closes_at.

    Запас в днях (а не в часах) — чтобы tz-round-trip SQLite (сдвиг на ~3 ч) не
    выталкивал «сейчас» за границу окна; точные границы проверяются на in-memory
    билете в test_ticket_window_boundaries.
    """
    from app.services.tz import now_msk

    tag = Tag(name="Р")
    db.add(tag)
    db.flush()
    db.add(UserTag(user_id=regular_user.id, tag_id=tag.id))
    _now = now_msk()
    ticket = _create_tagged_exact_ticket(
        db,
        regular_user,
        tag,
        opens_at=_now - timedelta(days=10),
        closes_at=_now + timedelta(days=10),
    )

    assert get_active_ticket(db, regular_user.id, "Рисунок").id == ticket.id


def test_active_ticket_none_before_window_opens(db, regular_user):
    """До opens_at билет ещё не активен — период доступа не начался."""
    from app.services.tz import now_msk

    tag = Tag(name="Р")
    db.add(tag)
    db.flush()
    db.add(UserTag(user_id=regular_user.id, tag_id=tag.id))
    _now = now_msk()
    _create_tagged_exact_ticket(
        db,
        regular_user,
        tag,
        opens_at=_now + timedelta(days=10),
        closes_at=_now + timedelta(days=20),
    )

    assert get_active_ticket(db, regular_user.id, "Рисунок") is None


def test_active_ticket_still_found_after_window_closes(db, regular_user):
    """После closes_at билет остаётся активным для сдачи — верхняя граница периода
    больше не отключает билет (только нижняя, opens_at, и архивация задания)."""
    from app.services.tz import now_msk

    tag = Tag(name="Р")
    db.add(tag)
    db.flush()
    db.add(UserTag(user_id=regular_user.id, tag_id=tag.id))
    _now = now_msk()
    ticket = _create_tagged_exact_ticket(
        db,
        regular_user,
        tag,
        opens_at=_now - timedelta(days=20),
        closes_at=_now - timedelta(days=10),
    )

    found = get_active_ticket(db, regular_user.id, "Рисунок")
    assert found is not None
    assert found.id == ticket.id


def test_submission_open_from_opens_at_with_no_upper_bound():
    """Сдача открыта от opens_at и без ограничения сверху — closes_at больше не
    закрывает сдачу (только получение билета, см. is_mock_exam_ticket_start_open)."""
    ticket = _inmemory_ticket(
        opens_at=datetime(2026, 6, 1, 10, 0, tzinfo=MSK_TZ),
        closes_at=datetime(2026, 6, 1, 14, 0, tzinfo=MSK_TZ),
        duration_minutes=90,
    )
    f = is_mock_exam_ticket_submission_open
    assert f(ticket, value=datetime(2026, 6, 1, 13, 0, tzinfo=MSK_TZ)) is True
    assert f(ticket, value=datetime(2026, 6, 1, 14, 0, tzinfo=MSK_TZ)) is True   # включительно
    assert f(ticket, value=datetime(2026, 6, 1, 9, 0, tzinfo=MSK_TZ)) is False  # до открытия
    assert f(ticket, value=datetime(2026, 6, 10, 0, 0, tzinfo=MSK_TZ)) is True  # давно после closes_at


def test_start_cutoff_is_close_minus_duration():
    """Получить билет можно не позже closes_at − duration.

    Период до 14:00, «время на выполнение» 90 мин → последний старт 12:30.
    Сдавать при этом можно до 14:00 — отсечка касается ТОЛЬКО получения билета.
    """
    ticket = _inmemory_ticket(
        opens_at=datetime(2026, 6, 1, 10, 0, tzinfo=MSK_TZ),
        closes_at=datetime(2026, 6, 1, 14, 0, tzinfo=MSK_TZ),
        duration_minutes=90,
    )

    assert ticket_latest_start_at(ticket) == datetime(2026, 6, 1, 12, 30, tzinfo=MSK_TZ)

    f = is_mock_exam_ticket_start_open
    assert f(ticket, value=datetime(2026, 6, 1, 10, 0, tzinfo=MSK_TZ)) is True    # на открытии
    assert f(ticket, value=datetime(2026, 6, 1, 12, 30, tzinfo=MSK_TZ)) is True   # ровно отсечка
    assert f(ticket, value=datetime(2026, 6, 1, 12, 31, tzinfo=MSK_TZ)) is False  # после отсечки
    assert f(ticket, value=datetime(2026, 6, 1, 9, 59, tzinfo=MSK_TZ)) is False   # до открытия

    # Сдача после отсечки старта всё ещё открыта (до closes_at).
    assert is_mock_exam_ticket_submission_open(
        ticket, value=datetime(2026, 6, 1, 13, 30, tzinfo=MSK_TZ)
    ) is True


def test_start_cutoff_disabled_allows_start_until_closes_at():
    """restrict_start_by_duration=False — получить билет можно до самого closes_at,
    отсечка closes_at-duration больше не применяется (только визуальный счётчик)."""
    ticket = _inmemory_ticket(
        opens_at=datetime(2026, 6, 1, 10, 0, tzinfo=MSK_TZ),
        closes_at=datetime(2026, 6, 1, 14, 0, tzinfo=MSK_TZ),
        duration_minutes=90,
        restrict_start_by_duration=False,
    )

    f = is_mock_exam_ticket_start_open
    assert f(ticket, value=datetime(2026, 6, 1, 12, 31, tzinfo=MSK_TZ)) is True   # за отсечкой duration, но до closes_at
    assert f(ticket, value=datetime(2026, 6, 1, 14, 0, tzinfo=MSK_TZ)) is True    # ровно closes_at
    assert f(ticket, value=datetime(2026, 6, 1, 14, 1, tzinfo=MSK_TZ)) is False   # после closes_at
    assert f(ticket, value=datetime(2026, 6, 1, 9, 59, tzinfo=MSK_TZ)) is False   # до открытия


def test_ticket_duration_sets_attempt_deadline_but_not_after_close():
    """«Время на выполнение» (визуальный отсчёт) ограничивается closes_at билета."""
    ticket = _inmemory_ticket(
        opens_at=datetime(2026, 1, 1, 9, 0, tzinfo=MSK_TZ),
        closes_at=datetime(2026, 1, 1, 18, 0, tzinfo=MSK_TZ),
        duration_minutes=45,
    )

    started_at = datetime(2026, 1, 1, 17, 30, tzinfo=MSK_TZ)

    assert mock_exam_deadline_for_started_at(
        started_at,
        closes_at=ticket_closes_at(ticket),
        duration_sec=ticket_duration_sec(ticket),
    ) == datetime(2026, 1, 1, 18, 0, tzinfo=MSK_TZ)


def test_target_tag_is_required_even_when_ticket_assign_to_all(db, user_factory, regular_user):
    tag = Tag(name="Только группа Р")
    db.add(tag)
    db.flush()
    db.add(UserTag(user_id=regular_user.id, tag_id=tag.id))
    untagged = user_factory(vk_id=202_602, name="No Tag Student")
    ticket = _create_tagged_exact_ticket(db, regular_user, tag)

    assert get_active_ticket(db, regular_user.id, "Рисунок").id == ticket.id
    assert get_active_ticket(db, untagged.id, "Рисунок") is None


def test_combined_subject_tag_grants_both_single_subject_target_tickets(db, regular_user):
    tag_r = Tag(name="Р")
    tag_k = Tag(name="К")
    tag_rk = Tag(name="Р+К")
    db.add_all([tag_r, tag_k, tag_rk])
    db.flush()
    db.add(UserTag(user_id=regular_user.id, tag_id=tag_rk.id))
    drawing_ticket = _create_tagged_exact_ticket(
        db,
        regular_user,
        tag_r,
        subject="Рисунок",
    )
    composition_ticket = _create_tagged_exact_ticket(
        db,
        regular_user,
        tag_k,
        subject="Композиция",
    )

    assert get_allowed_mock_subjects(db, regular_user.id) == ["Рисунок", "Композиция"]
    assert get_active_ticket(db, regular_user.id, "Рисунок").id == drawing_ticket.id
    assert get_active_ticket(db, regular_user.id, "Композиция").id == composition_ticket.id
    assert regular_user.id in get_student_ids_for_target_tag(db, tag_r.id)
    assert regular_user.id in get_student_ids_for_target_tag(db, tag_k.id)


def test_combined_target_tag_does_not_match_single_subject_student_tag(db, regular_user):
    """Тег ученика больше не сужает список предметов (см.
    test_tag_no_longer_restricts_allowed_subjects) — здесь проверяется только
    ticket-level matching: билет с target_tag_id="Р+К" не достаётся ученику с
    более узким тегом "Р"."""
    tag_r = Tag(name="Р")
    tag_rk = Tag(name="Р+К")
    db.add_all([tag_r, tag_rk])
    db.flush()
    db.add(UserTag(user_id=regular_user.id, tag_id=tag_r.id))
    db.commit()
    _create_tagged_exact_ticket(db, regular_user, tag_rk, subject="Рисунок")

    assert get_active_ticket(db, regular_user.id, "Рисунок") is None
    assert regular_user.id not in get_student_ids_for_target_tag(db, tag_rk.id)


def test_profile_exam_subjects_limit_active_ticket(db, regular_user):
    db.query(User).filter(User.id == regular_user.id).update({"exam_subjects": "Р"})
    db.commit()
    _create_ticket(db, regular_user, "Рисунок")
    _create_ticket(db, regular_user, "Композиция")

    assert get_allowed_mock_subjects(db, regular_user.id) == ["Рисунок"]
    assert get_active_ticket(db, regular_user.id, "Рисунок") is not None
    assert get_active_ticket(db, regular_user.id, "Композиция") is None


def test_free_text_subject_tags_no_longer_restrict_active_ticket(db, regular_user):
    """Произвольный тег ученика (даже совпадающий по названию с предметом)
    больше не сужает доступные предметы — иначе служебные теги вроде "Р"
    (группа/уровень куратора, не предмет) случайно прятали бы билеты другого
    предмета, как это произошло в проде. Фильтрация теперь только через
    target_tag_id самого билета (см. test_profile_exam_subjects_limit_active_ticket
    для единственного оставшегося способа сузить предметы — явное поле профиля)."""
    tag = Tag(name="Композиция")
    db.add(tag)
    db.flush()
    db.add(UserTag(user_id=regular_user.id, tag_id=tag.id))
    db.commit()
    _create_ticket(db, regular_user, "Рисунок")
    _create_ticket(db, regular_user, "Композиция")

    assert get_allowed_mock_subjects(db, regular_user.id) == ["Рисунок", "Композиция"]
    assert get_active_ticket(db, regular_user.id, "Рисунок") is not None
    assert get_active_ticket(db, regular_user.id, "Композиция") is not None


def test_non_subject_tags_do_not_restrict_default_access(db, regular_user):
    tag = Tag(name="Куратор")
    db.add(tag)
    db.flush()
    db.add(UserTag(user_id=regular_user.id, tag_id=tag.id))
    db.commit()

    assert get_allowed_mock_subjects(db, regular_user.id) == ["Рисунок", "Композиция"]
