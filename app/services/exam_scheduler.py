"""
Планировщик уведомлений о предстоящих пробниках.

Каждый час проверяет ExamTicket-ы, у которых:
  - start_date наступит в течение 3 дней (или уже сегодня)
  - В ExamTicketAssignee.notified_at IS NULL

Для каждого такого ученика создаёт in-app Notification и проставляет notified_at.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta, date

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.cache import invalidate_unread
from app.config import settings
from app.db.database import SessionLocal
from app.models.exam_assignment import ExamAssignment, ExamTicket, ExamTicketAssignee
from app.models.login_token import LoginToken
from app.models.mock_exam_attempt import MockExamAttempt
from app.models.notification import Notification
from app.models.role import Role
from app.models.session import Session
from app.models.user import User
from app.services.contacts import normalize_tg_username
from app.services.mock_exam_access import (
    get_student_ids_for_target_tag,
    is_target_tag_allowed_for_student,
    ticket_closes_at,
    ticket_opens_at,
)
from app.services.notify import notify_many_sync
from app.services import telegram as telegram_service
from app.services.tz import MSK_TZ, today_msk

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

NOTIFY_DAYS_BEFORE = 3  # за сколько дней до начала отправляем уведомление


def _run_notification_check() -> None:
    """Запускается по расписанию. Создаёт уведомления для ближайших пробников."""
    db = SessionLocal()
    try:
        today = today_msk()
        now = datetime.now(timezone.utc)
        notify_threshold = now + timedelta(days=NOTIFY_DAYS_BEFORE)

        # Билеты, у которых начало сдачи через 3 дня или раньше, задание опубликовано
        tickets = (
            db.query(ExamTicket)
            .join(ExamAssignment, ExamTicket.assignment_id == ExamAssignment.id)
            .filter(
                ExamAssignment.status == "published",
                ExamAssignment.kind != "guest",
                ExamTicket.end_date >= today,   # legacy prefilter: ещё не закончился
            )
            .all()
        )
        tickets = [
            t for t in tickets
            if ticket_opens_at(t).astimezone(timezone.utc) <= notify_threshold
            and ticket_closes_at(t).astimezone(timezone.utc) > now
        ]

        if not tickets:
            return

        ticket_ids = [t.id for t in tickets]
        ticket_map = {t.id: t for t in tickets}

        # Для каждого билета найдём тех, кому не отправлено уведомление
        pending = (
            db.query(ExamTicketAssignee)
            .filter(
                ExamTicketAssignee.ticket_id.in_(ticket_ids),
                ExamTicketAssignee.notified_at.is_(None),
            )
            .all()
        )

        # Для теговых билетов — найти всех активных учеников с подходящим тегом.
        tag_tickets = [t for t in tickets if t.target_tag_id is not None]
        if tag_tickets:
            student_role = db.query(Role).filter(Role.rank == 1).first()
            for ticket in tag_tickets:
                if not student_role:
                    continue
                tagged_ids = get_student_ids_for_target_tag(
                    db,
                    ticket.target_tag_id,
                    role_id=student_role.id,
                )
                existing_all = (
                    db.query(ExamTicketAssignee.user_id)
                    .filter(ExamTicketAssignee.ticket_id == ticket.id)
                    .all()
                )
                existing_assigned = {row.user_id for row in existing_all}
                missing = tagged_ids - existing_assigned
                if missing:
                    stmt = pg_insert(ExamTicketAssignee).values(
                        [{"ticket_id": ticket.id, "user_id": uid} for uid in missing]
                    ).on_conflict_do_nothing(index_elements=["ticket_id", "user_id"])
                    db.execute(stmt)
                    db.flush()
                    pending.extend(
                        db.query(ExamTicketAssignee)
                        .filter(
                            ExamTicketAssignee.ticket_id == ticket.id,
                            ExamTicketAssignee.user_id.in_(missing),
                            ExamTicketAssignee.notified_at.is_(None),
                        )
                        .all()
                    )

        # Для legacy-билетов с assign_to_all=True — найти всех активных учеников
        all_tickets_ids = [t.id for t in tickets if t.assign_to_all]
        if all_tickets_ids:
            # Получаем ID всех активных учеников (rank=1)
            student_role = db.query(Role).filter(Role.rank == 1).first()
            if student_role:
                all_students = (
                    db.query(User.id)
                    .filter(User.role_id == student_role.id, User.is_active == True)
                    .all()
                )
                all_student_ids = {row.id for row in all_students}

                # Для каждого "всем" билета убеждаемся что все ученики в assignees
                for ticket_id in all_tickets_ids:
                    existing_ids = {
                        a.user_id for a in pending if a.ticket_id == ticket_id
                    }
                    # Находим уже существующих assignees
                    existing_all = (
                        db.query(ExamTicketAssignee.user_id)
                        .filter(ExamTicketAssignee.ticket_id == ticket_id)
                        .all()
                    )
                    existing_assigned = {row.user_id for row in existing_all}
                    missing = all_student_ids - existing_assigned
                    if missing:
                        stmt = pg_insert(ExamTicketAssignee).values(
                            [{"ticket_id": ticket_id, "user_id": uid} for uid in missing]
                        ).on_conflict_do_nothing(index_elements=["ticket_id", "user_id"])
                        db.execute(stmt)
                        db.flush()
                        # reload pending для только что добавленных
                        new_assignees = (
                            db.query(ExamTicketAssignee)
                            .filter(
                                ExamTicketAssignee.ticket_id == ticket_id,
                                ExamTicketAssignee.user_id.in_(missing),
                                ExamTicketAssignee.notified_at.is_(None),
                            )
                            .all()
                        )
                        pending.extend(new_assignees)

        # Отправляем уведомления
        sent = 0
        created_notifications = []
        for assignee in pending:
            if assignee.notified_at is not None:
                continue
            ticket = ticket_map.get(assignee.ticket_id)
            if not ticket:
                continue
            if not is_target_tag_allowed_for_student(
                db, assignee.user_id, ticket.target_tag_id
            ):
                continue
            assignment = db.query(ExamAssignment).filter(
                ExamAssignment.id == ticket.assignment_id
            ).first()
            if not assignment:
                continue

            opens_msk = ticket_opens_at(ticket).astimezone(MSK_TZ)
            closes_msk = ticket_closes_at(ticket).astimezone(MSK_TZ)
            days_left = (opens_msk.date() - today).days
            if days_left > NOTIFY_DAYS_BEFORE:
                continue

            if days_left <= 0:
                when_text = "сегодня"
            elif days_left == 1:
                when_text = "завтра"
            else:
                when_text = f"через {days_left} дн."

            notif = Notification(
                user_id=assignee.user_id,
                title=f"Пробник по {assignment.subject} — {when_text}",
                text=(
                    f"Билет {ticket.ticket_number}: {ticket.title}\n"
                    f"Период сдачи: {opens_msk.strftime('%d.%m.%Y %H:%M')} — "
                    f"{closes_msk.strftime('%d.%m.%Y %H:%M')} МСК"
                ),
            )
            db.add(notif)
            created_notifications.append(notif)
            invalidate_unread(assignee.user_id)
            assignee.notified_at = now
            sent += 1

        db.commit()
        if sent:
            logger.info("Exam scheduler: отправлено %d уведомлений о пробниках", sent)
            notify_many_sync([n.id for n in created_notifications])

    except Exception:
        logger.exception("Ошибка в планировщике уведомлений о пробниках")
        db.rollback()
    finally:
        db.close()


def _run_mock_exam_expiry_check() -> None:
    """Каждые несколько минут помечает expired_at у открытых MockExamAttempt,
    чьё задание стало неопубликованным/архивным (status != "published"), а также
    у осиротевших попыток (ticket_id IS NULL).

    closes_at билета больше НЕ протухает попытку: сдача теперь разрешена в
    любой момент после получения билета (is_mock_exam_ticket_submission_open
    всегда True — см. mock_exam_access), иначе эта проверка молча вернула бы
    старое поведение «после периода доступа сдать нельзя» через expired_at у
    попытки. Протухание оставлено только для архивного/неопубликованного
    задания — это и есть единственный source of truth ротации билетов
    (get_active_ticket фильтрует по ExamAssignment.status == "published").

    Без этого «зависшая» попытка архивного задания осталась бы
    completed_at IS NULL навечно: mock_exam_start резюмировал бы её со
    снимком неактивного билета.

    Осиротевшие попытки (ticket_id IS NULL): билет удалён, FK
    `MockExamAttempt.ticket_id` (ondelete=SET NULL) обнулил ссылку у уже
    начатых попыток. INNER JOIN ниже их НЕ видит, а UI их не резюмирует (нет
    билета — `_active_ticket_for_attempt` возвращает None), поэтому без явного
    протухания они висели бы completed_at/expired_at IS NULL вечно. mock_exam_start
    всегда пишет реальный ticket_id, так что NULL ⇒ только удалённый билет ⇒
    сдать против такой попытки нельзя ⇒ её безопасно протухать.
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        candidates = (
            db.query(MockExamAttempt, ExamTicket, ExamAssignment)
            .join(ExamTicket, MockExamAttempt.ticket_id == ExamTicket.id)
            .join(ExamAssignment, ExamTicket.assignment_id == ExamAssignment.id)
            .filter(
                MockExamAttempt.completed_at.is_(None),
                MockExamAttempt.expired_at.is_(None),
            )
            .all()
        )
        expired_count = 0
        for attempt, ticket, assignment in candidates:
            if assignment.status != "published":
                attempt.expired_at = now
                expired_count += 1

        orphan_count = (
            db.query(MockExamAttempt)
            .filter(
                MockExamAttempt.completed_at.is_(None),
                MockExamAttempt.expired_at.is_(None),
                MockExamAttempt.ticket_id.is_(None),
            )
            .update({"expired_at": now}, synchronize_session=False)
        )

        if expired_count or orphan_count:
            db.commit()
            logger.info(
                "Mock-exam expiry: помечено %d истёкших + %d осиротевших попыток",
                expired_count, orphan_count,
            )

    except Exception:
        logger.exception("Ошибка в mock-exam expiry check")
        db.rollback()
    finally:
        db.close()


def _run_mock_exam_progress_check() -> None:
    """ОТКЛЮЧЕНО. Раньше каждую минуту слала уведомления «прошло 2ч / остался 1ч /
    осталось 10 минут до окончания времени» по таймеру попытки.

    Таймер 1:30 и дневное окно сняты (см. mock_exam_access и
    _run_mock_exam_expiry_check): внутри периода билета у сдачи больше нет
    дедлайна, поэтому эти уведомления были бы ложной срочностью. Job в
    start_scheduler не регистрируется; функция оставлена как точка для будущих,
    корректных по смыслу уведомлений (флаги notif_*_sent в модели не трогаем).
    """
    return


def _run_video_status_sync() -> None:
    """Каждые пару минут подтягивает статус необработанных видео с Bunny и
    публикует те, что куратор уже поставил в день (`auto_publish_on_ready`).

    Раньше статус обновлялся только пока была открыта страница «Загрузка
    видео» — JS-опрос на ней сам дёргал Bunny (cabinet_videos_admin.html).
    Если куратор закрывал вкладку до конца обработки длинного ролика, статус
    навсегда зависал на `processing`, публикация вручную была недоступна
    (требует status == 'ready'), а ученик видел «Видео скоро появится» сколько
    угодно долго (живой баг, найден 29.08.2026: куратор поставил в день
    полуторачасовое видео и ушёл, обработка на Bunny успела завершиться, а
    локальная запись — нет). Решение владельца 29.08.2026: «после загрузки
    видео сразу отправлять на обработку и на публикацию без всяких вторых,
    третьих, четвёртых действий» — эта job и есть отказ от того, чтобы
    свежесть статуса зависела от того, держит ли кто-то нужную вкладку открытой.
    """
    if not settings.bunny_stream_enabled:
        return
    from app.models.learning_video import LearningVideo
    from app.services.bunny_stream import BunnyStreamAPIError, BunnyStreamConfigError
    from app.services.video_catalog import sync_status_from_bunny

    db = SessionLocal()
    try:
        pending = (
            db.query(LearningVideo)
            .filter(
                LearningVideo.status.in_(("uploading", "processing")),
                LearningVideo.deleted_at.is_(None),
            )
            .all()
        )
        published = 0
        for video in pending:
            try:
                if sync_status_from_bunny(db, video):
                    published += 1
            except (BunnyStreamAPIError, BunnyStreamConfigError):
                logger.exception("Video status sync failed for video_id=%s", video.id)
                continue
        if pending:
            db.commit()
        if published:
            logger.info("Video status sync: опубликовано %d видео", published)
    except Exception:
        logger.exception("Ошибка в video status sync job")
        db.rollback()
    finally:
        db.close()


def _run_cleanup() -> None:
    """Каждые 6 часов удаляет протухшие сессии и login-токены."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        sessions_deleted = (
            db.query(Session)
            .filter(Session.expires_at < now)
            .delete(synchronize_session=False)
        )
        tokens_deleted = (
            db.query(LoginToken)
            .filter(LoginToken.expires_at < now)
            .delete(synchronize_session=False)
        )
        db.commit()
        if sessions_deleted or tokens_deleted:
            logger.info(
                "Cleanup: удалено %d сессий, %d токенов",
                sessions_deleted,
                tokens_deleted,
            )
    except Exception:
        logger.exception("Ошибка в cleanup job")
        db.rollback()
    finally:
        db.close()


_TG_CHECK_CONCURRENCY = 10  # ограничитель, чтобы не упереться в rate limit Bot API


async def _fetch_tg_usernames(pairs: list[tuple[int, int]]) -> dict[int, tuple[bool, str | None]]:
    """pairs: [(user_id, chat_id), ...] → {user_id: (ok, live_username)}."""
    sem = asyncio.Semaphore(_TG_CHECK_CONCURRENCY)
    results: dict[int, tuple[bool, str | None]] = {}

    async def _one(user_id: int, chat_id: int) -> None:
        async with sem:
            results[user_id] = await telegram_service.get_chat_username(chat_id)

    await asyncio.gather(*(_one(uid, cid) for uid, cid in pairs))
    return results


def _run_tg_username_check() -> None:
    """Раз в сутки сверяет User.tg_username с тем, что реально отдаёт Telegram
    Bot API (getChat) по telegram_chat_id — у ученика есть возможность вписать
    в анкету любой ник, а сменить его в самом Telegram и не сказать об этом.

    Само tg_username здесь не переписывается — по нему n8n находит папку в
    Google Drive и суперадмин ищет ученика при bulk-импорте (contacts.py),
    менять его в фоне значило бы рвать эту связь без ведома персонала. Вместо
    этого поднимается/снимается флаг tg_username_mismatch, который закрывает
    кабинет ученику до тех пор, пока он сам не подтвердит актуальный ник на
    /cabinet/personal/contacts (см. dependencies.py и cabinet_personal.py).

    Ученики без telegram_chat_id (вход только через VK) пропускаются — для
    них живого источника правды нет, и мы, как и check_channel_membership,
    не меняем состояние, когда Telegram не смог ответить (ok=False): это
    значит «не узнали», а не «ника нет».
    """
    db = SessionLocal()
    try:
        student_role = db.query(Role).filter(Role.rank == 1).first()
        if not student_role:
            return

        students = (
            db.query(User)
            .filter(
                User.role_id == student_role.id,
                User.is_active == True,  # noqa: E712
                User.deleted_at.is_(None),
                User.archived_at.is_(None),
                User.telegram_chat_id.isnot(None),
            )
            .all()
        )
        if not students:
            return

        pairs = [(s.id, s.telegram_chat_id) for s in students]
        results = asyncio.run(_fetch_tg_usernames(pairs))

        # Сетевой обход мог занять заметное время (пусть и ограниченное
        # семафором) — за это время ученик мог сам починить свой ник через
        # /cabinet/personal/contacts и снять блокировку живой сверкой там.
        # Без expire_all() ниже читались бы значения, вычитанные ДО обхода:
        # commit в самом конце этой функции переписал бы уже верный флаг
        # обратно на устаревший результат.
        db.expire_all()

        checked = flagged = cleared = 0
        for student in students:
            ok, live_username = results.get(student.id, (False, None))
            if not ok:
                continue  # временная ошибка API — состояние не трогаем
            checked += 1
            stored = normalize_tg_username(student.tg_username or "").lower()
            live = normalize_tg_username(live_username or "").lower()
            mismatch = stored != live
            if mismatch and not student.tg_username_mismatch:
                flagged += 1
            elif not mismatch and student.tg_username_mismatch:
                cleared += 1
            student.tg_username_mismatch = mismatch

        db.commit()
        if flagged or cleared:
            logger.info(
                "TG username check: проверено %d, новых расхождений %d, снято %d",
                checked, flagged, cleared,
            )
    except Exception:
        logger.exception("Ошибка в проверке ника Telegram")
        db.rollback()
    finally:
        db.close()


BIRTHDAY_NOTIFY_DAYS_BEFORE = 7  # за сколько дней до дня рождения уведомляем куратора


def _upcoming_birthday(birth_date: date, today: date) -> date:
    """Ближайшая дата дня рождения (в этом году или в следующем, если уже прошла).

    29 февраля в невисокосный год — переносим на 28-е: настоящей даты в этом
    году просто нет, а не уведомить совсем — хуже, чем сдвинуть на день.
    """
    try:
        this_year = birth_date.replace(year=today.year)
    except ValueError:
        this_year = date(today.year, 2, 28)
    if this_year >= today:
        return this_year
    try:
        return birth_date.replace(year=today.year + 1)
    except ValueError:
        return date(today.year + 1, 2, 28)


def _run_birthday_check() -> None:
    """Раз в сутки: у кого из активных учеников день рождения через неделю
    или ближе — куратору падает уведомление, чтобы успеть подготовить подарок
    (владелец 12.09.2026, анкета первого входа собирает дату рождения именно
    для этого).

    Идемпотентность — как у _run_notification_check выше (не «ровно на 7-й
    день», а «уже в пределах недели и в этом году ещё не слали»):
    birthday_reminder_sent_year != текущий год. Точное совпадение с днём
    было бы хрупким — пропущенный по любой причине прогон job (деплой,
    перезапуск) уводил бы уведомление совсем, а не сдвигал на день.

    Ученик без curator_id пропускается — слать некому, гадать адресата
    не тот случай, где можно ошибиться молча.
    """
    db = SessionLocal()
    try:
        today = today_msk()
        student_role = db.query(Role).filter(Role.rank == 1).first()
        if not student_role:
            return

        base_filter = (
            User.role_id == student_role.id,
            User.is_active == True,  # noqa: E712
            User.deleted_at.is_(None),
            User.archived_at.is_(None),
            User.birth_date.isnot(None),
        )
        students = (
            db.query(User)
            .filter(*base_filter, User.curator_id.isnot(None))
            .all()
        )
        # Без даты рождения не с кем сравнивать «повезло/не повезло на этой
        # неделе» — отдельный счётчик, чтобы «никого не уведомили» в логе не
        # значило одинаково и «дней рождения рядом нет», и «некому слать,
        # потому что у половины учеников нет куратора».
        without_curator = (
            db.query(User).filter(*base_filter, User.curator_id.is_(None)).count()
        )
        if without_curator:
            logger.info(
                "Birthday check: пропущено %d учеников с датой рождения без куратора",
                without_curator,
            )
        if not students:
            return

        sent = 0
        created_notifications = []
        for student in students:
            upcoming = _upcoming_birthday(student.birth_date, today)
            days_left = (upcoming - today).days
            if days_left > BIRTHDAY_NOTIFY_DAYS_BEFORE:
                continue
            # Год самого дня рождения (upcoming.year), а не today.year: для
            # дат 1-7 января окно "7 дней" зацепляет конец декабря, когда
            # today.year ещё старый, и начало января, когда он уже новый —
            # сравнение с today.year послало бы одно и то же напоминание
            # дважды на стыке года.
            if student.birthday_reminder_sent_year == upcoming.year:
                continue

            if days_left <= 0:
                when_text = "сегодня"
            elif days_left == 1:
                when_text = "завтра"
            else:
                when_text = f"через {days_left} дн."

            notif = Notification(
                user_id=student.curator_id,
                title=f"День рождения — {student.name}",
                text=f"{when_text}, {upcoming.strftime('%d.%m')}. Успейте подготовить подарок.",
            )
            db.add(notif)
            created_notifications.append(notif)
            invalidate_unread(student.curator_id)
            student.birthday_reminder_sent_year = upcoming.year
            sent += 1

        db.commit()
        if sent:
            logger.info("Birthday check: отправлено %d напоминаний кураторам", sent)
            notify_many_sync([n.id for n in created_notifications])
    except Exception:
        logger.exception("Ошибка в проверке дней рождения")
        db.rollback()
    finally:
        db.close()


def start_scheduler() -> None:
    global _scheduler
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _run_notification_check,
        trigger="interval",
        hours=1,
        next_run_time=datetime.now(timezone.utc),  # запустить сразу при старте
        id="exam_notifications",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    # mock_exam_progress job снят: таймер 1:30 убран, уведомления о «остатке
    # времени» стали бы ложной срочностью (см. _run_mock_exam_progress_check).
    _scheduler.add_job(
        _run_mock_exam_expiry_check,
        trigger="interval",
        minutes=5,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=15),
        id="mock_exam_expiry",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    _scheduler.add_job(
        _run_cleanup,
        trigger="interval",
        hours=6,
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=5),
        id="session_cleanup",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )
    _scheduler.add_job(
        _run_video_status_sync,
        trigger="interval",
        minutes=2,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30),
        id="video_status_sync",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=120,
    )
    _scheduler.add_job(
        _run_tg_username_check,
        trigger="cron",
        hour=4,
        minute=0,
        id="tg_username_check",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    _scheduler.add_job(
        _run_birthday_check,
        trigger="cron",
        hour=5,
        minute=0,
        id="birthday_check",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    logger.info(
        "Exam scheduler started (exam_notifications=1h, mock_exam_progress=1min, "
        "mock_exam_expiry=5min, cleanup=6h, video_status_sync=2min, "
        "tg_username_check=daily@04:00 UTC, birthday_check=daily@05:00 UTC)"
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Exam scheduler stopped")
