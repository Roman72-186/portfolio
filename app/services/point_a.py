"""Точка А — входная оценка ученика по шести элементам на одном экране.

Лиза 14.09.2026 (голосовое + эскиз): «я захожу в ребёнка, и у ребёнка есть
пробный экзамен… потом портфолио до… портфолио после предобучения… и
контрольная работа… я оцениваю по стобалльной шкале каждый из этих блоков…
когда я всё проставила, вылезает средний балл».

Почему отдельный сервис и отдельный экран, а не адаптер в
`review_aggregate.py` (правило 12 в `AGENTS.md`): то правило про недельные
очереди по одному типу сдачи — их сносили дважды. Здесь другое. Точка А
собирается один раз на входе, из шести разнородных источников сразу, и
Главному преподавателю нужен именно список «кого я ещё не разобрала», а не
строка, растворённая в недельной ленте всего сданного (владелец 15.09.2026;
созвон 26.08.2026: «точка А будет отдельный дашборд, отдельная вкладка»).

Среднее считается на лету и нигде не хранится. Таблица-агрегатор из
`plans/2026-08-27-apparchi-call-26-08-implementation-plan.md` не заводится:
элементов пять-шесть, экран открывают редко, а хранимое среднее молча
разъезжается с источником при любой правке балла в обход сервиса — тот же
довод, по которому в `review_aggregate.py` нет таблицы «проверено».
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session as DBSession

from app.models.exam_assignment import ExamAssignment, ExamTicket
from app.models.exam_cycle import ExamCycle
from app.models.notification import Notification
from app.models.user import User
from app.models.work import WORK_TYPE_BEFORE, WORK_TYPE_MOCK_EXAM, Work
from app.services.point_a_level_audio import get_level_audio
from app.services.portfolio import after_gallery_groups

# Порог уровня по среднему баллу (владелец 17.09.2026, вечерняя правка того
# же дня после уточнения): ≤69 — уровень 1, ≥70 — уровень 2 — как и было на
# созвоне 26.08.2026. Более ранняя версия этого дня разворачивала цифры
# (≥70 — уровень 1); тот разворот был ошибкой прочтения записи, а не новым
# решением — не восстанавливать её.
POINT_A_LEVEL_2_MIN_AVERAGE = 70

# Ранг, с которого видна точка А. Тот же порог, что у снятой карточки
# `_portfolio_before_items` (владелец 09.09.2026: «только Главный
# преподаватель»), поэтому здесь константа, а не «ранг ≥ 2 как на проверке».
POINT_A_RANK = 4

PLATE_WORK = "work"
PLATE_PORTFOLIO_BEFORE = "portfolio_before"
PLATE_PORTFOLIO_AFTER = "portfolio_after"

# Сколько фото тянем в одну плашку. Ученик грузит в «До» до 20 штук
# (`upload_validation.MAX_UPLOAD_FILES`), в «После» за курс копится больше —
# лимит тот же, что был у снятой карточки точки А.
_IMAGE_LIMIT = 50

_SUBJECT_DRAWING = "Рисунок"
_SUBJECT_COMPOSITION = "Композиция"

# Порядок плашек — ровно с эскиза Лизы: пробник Р, пробник К, портфолио «До»,
# портфолио «После», контрольная Р, контрольная К.
_MOCK_PLATES = (
    ("mock_drawing", "mock", _SUBJECT_DRAWING, "Пробник — рисунок"),
    ("mock_composition", "mock", _SUBJECT_COMPOSITION, "Пробник — композиция"),
)
_CONTROL_PLATES = (
    ("control_drawing", "control", _SUBJECT_DRAWING, "Контрольная — рисунок"),
    ("control_composition", "control", _SUBJECT_COMPOSITION, "Контрольная — композиция"),
)


@dataclass(frozen=True)
class PointAPlate:
    """Один оцениваемый элемент точки А.

    `work_id` заполнен только у плашек-работ: балл им ставит существующий
    `POST /cabinet/admin/works/{work_id}/score`, своего эндпоинта у них нет.
    У плашек портфолио оценка лежит колонкой на ученике, и `work_id` пуст.
    """

    key: str
    title: str
    kind: str
    score: int | None = None
    work_id: int | None = None
    images: list[str] = field(default_factory=list)
    comment: str | None = None

    @property
    def is_scored(self) -> bool:
        return self.score is not None


@dataclass(frozen=True)
class PointA:
    student: User
    plates: list[PointAPlate]
    average: int | None
    is_done: bool
    scored_count: int


def _latest_final_work(
    db: DBSession, student_id: int, *, kind: str, subject: str
) -> Work | None:
    """Последняя финальная работа ученика по предмету нужного вида задания.

    Вид («Пробник» или «Контрольная») различается по `ExamAssignment.kind` —
    отдельной сущности у контрольной нет и заводить её не нужно: механика
    цикла ключуется по `subject`, а `kind` это метка задания (см. комментарий
    у `ASSIGNMENT_KINDS` в `app/constants.py`). Этим закрыта развилка, которая
    держала точку А с 27.08.2026.

    Циклов по одному предмету у ученика бывает несколько (попытки, разные
    задания за период), поэтому правило выбора явное — самая свежая сдача.
    `is_final` обязателен: промежуточные попытки несут свой балл в
    `ExamCycle.intermediate_score`, он намеренно не входит в статистику
    пробников, и утечка такой работы испортила бы среднее незаметно.
    """
    return (
        db.query(Work)
        .join(ExamCycle, Work.cycle_id == ExamCycle.id)
        .join(ExamTicket, ExamCycle.ticket_id == ExamTicket.id)
        .join(ExamAssignment, ExamTicket.assignment_id == ExamAssignment.id)
        .filter(
            Work.user_id == student_id,
            Work.work_type == WORK_TYPE_MOCK_EXAM,
            Work.status == "success",
            Work.is_final == True,  # noqa: E712
            Work.subject == subject,
            ExamAssignment.kind == kind,
        )
        .order_by(Work.created_at.desc())
        .first()
    )


def _cycle_images(db: DBSession, work: Work) -> list[str]:
    """Все фото финала того же цикла — одна сдача бывает из нескольких листов.

    Балл при этом остаётся на одной работе (`work`), как и на карточке
    ученика: `uq_works_cycle_final_attempt` разрешает несколько финальных
    работ в цикле, и оценка ставится последней.
    """
    if work.cycle_id is None:
        return [work.s3_url] if work.s3_url else []
    return [
        url for (url,) in (
            db.query(Work.s3_url)
            .filter(
                Work.cycle_id == work.cycle_id,
                Work.is_final == True,  # noqa: E712
                Work.status == "success",
                Work.s3_url.isnot(None),
            )
            .order_by(Work.created_at.desc())
            .limit(_IMAGE_LIMIT)
            .all()
        )
    ]


def _before_images(db: DBSession, student_id: int, *, limit: int = _IMAGE_LIMIT) -> list[str]:
    return [
        url for (url,) in (
            db.query(Work.s3_url)
            .filter(
                Work.user_id == student_id,
                Work.work_type == WORK_TYPE_BEFORE,
                Work.status == "success",
                Work.s3_url.isnot(None),
            )
            .order_by(Work.created_at.desc())
            .limit(limit)
            .all()
        )
    ]


def _after_images(db: DBSession, student_id: int, *, limit: int = _IMAGE_LIMIT) -> list[str]:
    """Работы «После» — через готовую сборку галереи, не своим запросом.

    В «После» попадают и `Work(after)`, и сдачи внутри заданий
    (`task_block_submissions`), причём копий в `works` у последних нет —
    слияние живёт только на показ (владелец 09.09.2026). Свой запрос по `Work`
    показал бы половину набора.
    """
    groups = after_gallery_groups(
        db, student_id, work_limit=limit, submission_limit=limit
    )
    images: list[str] = []
    for group in groups:
        for item in group["works"]:
            url = getattr(item, "s3_url", None)
            if url:
                images.append(url)
            if len(images) >= limit:
                return images
    return images


def _work_plate(
    db: DBSession, student_id: int, *, key: str, kind: str, subject: str, title: str,
    with_images: bool = True,
) -> PointAPlate | None:
    work = _latest_final_work(db, student_id, kind=kind, subject=subject)
    if work is None:
        return None
    return PointAPlate(
        key=key,
        title=title,
        kind=PLATE_WORK,
        score=int(work.score) if work.score is not None else None,
        work_id=work.id,
        images=_cycle_images(db, work) if with_images else [],
        comment=work.comment,
    )


def _average(plates: list[PointAPlate]) -> int | None:
    """Средний балл — одно число на ученика (решение владельца 15.09.2026).

    Разреза по рисунку и композиции нет: созвон 26.08.2026 — «50 баллов за
    портфолио, значит и 50 по композиции, и 50 по рисунку».

    `float()` обязателен: `Work.score` — `Numeric(5,2)` и приходит `Decimal`,
    а баллы портфолио — `Integer`. Смешанная сумма дала бы `Decimal`, и
    округление поехало бы по другим правилам, чем в
    `stats.py::avg_score_by_subject_all_time`.
    """
    scored = [float(plate.score) for plate in plates if plate.score is not None]
    if not scored:
        return None
    return round(sum(scored) / len(scored))


def student_point_a(db: DBSession, student: User, *, with_images: bool = True) -> PointA:
    """Все плашки одного ученика, средний балл и признак «разобран».

    Плашка появляется, только когда у ученика есть соответствующие работы:
    иначе новичок висел бы вечным «не оценено» без способа это снять — тот же
    принцип, по которому карточка точки А на экране проверки не показывалась
    ученику без работ «До».

    `with_images=False` — режим списка: там фотографий никто не показывает, а
    их сбор самый дорогой в функции (`after_gallery_groups` тянет до сотни
    записей из двух таблиц и группирует по месяцам). Тот же приём был у снятой
    карточки: она грузила фото только при запросе одного ученика. Наличие
    плашки при этом считается по-прежнему честно — по факту работ.
    """
    plates: list[PointAPlate] = []

    for key, kind, subject, title in _MOCK_PLATES:
        plate = _work_plate(
            db, student.id, key=key, kind=kind, subject=subject, title=title,
            with_images=with_images,
        )
        if plate is not None:
            plates.append(plate)

    before_images = _before_images(db, student.id, limit=_IMAGE_LIMIT if with_images else 1)
    if before_images:
        plates.append(PointAPlate(
            key="portfolio_before",
            title="Портфолио «До»",
            kind=PLATE_PORTFOLIO_BEFORE,
            score=student.portfolio_before_score,
            images=before_images if with_images else [],
        ))

    after_images = _after_images(db, student.id, limit=_IMAGE_LIMIT if with_images else 1)
    if after_images:
        plates.append(PointAPlate(
            key="portfolio_after",
            title="Портфолио «После»",
            kind=PLATE_PORTFOLIO_AFTER,
            score=student.portfolio_after_score,
            images=after_images if with_images else [],
        ))

    for key, kind, subject, title in _CONTROL_PLATES:
        plate = _work_plate(
            db, student.id, key=key, kind=kind, subject=subject, title=title,
            with_images=with_images,
        )
        if plate is not None:
            plates.append(plate)

    return PointA(
        student=student,
        plates=plates,
        average=_average(plates),
        is_done=bool(plates) and all(plate.is_scored for plate in plates),
        scored_count=sum(1 for plate in plates if plate.is_scored),
    )


def point_a_level(average: int) -> int:
    return 2 if average >= POINT_A_LEVEL_2_MIN_AVERAGE else 1


def maybe_notify_point_a_level(db: DBSession, student: User) -> Notification | None:
    """Уведомление об уровне точки А — один раз, в момент, когда разобрана
    последняя плашка.

    Идемпотентность держит `student.point_a_notified_at`: непустое поле
    значит «уже уведомляли», повторная правка отдельного балла ничего не
    шлёт заново. Вызывается из всех пяти мест простановки балла, влияющих
    на плашки (см. `AGENTS.md`), до `db.commit()` — коммитить обязан
    вызывающий код, в одной транзакции с самим баллом: раздельные commit
    дали бы окно, где балл сохранён, а уведомление не будет отправлено
    никогда (флаг не выставлен, повторного шанса не будет).

    Если голосовое для уровня ещё не загружено — уведомление всё равно
    уходит текстом, не блокируется (владелец 17.09.2026): иначе ученику
    пришлось бы ждать голосовое «задним числом» в момент, когда его
    наконец загрузят, а флаг уже стоял бы.

    Не делает commit. Возвращает None, если уведомлять рано (не все плашки
    оценены) или уже уведомляли.
    """
    if student.point_a_notified_at is not None:
        return None

    point_a = student_point_a(db, student, with_images=False)
    if not point_a.is_done or point_a.average is None:
        return None

    level = point_a_level(point_a.average)
    audio = get_level_audio(db, level)

    notification = Notification(
        user_id=student.id,
        title=f"Точка А разобрана — уровень {level}",
        text=f"Средний балл: {point_a.average} / 100.",
        audio_url=audio.audio_s3_url if audio else None,
    )
    db.add(notification)
    student.point_a_notified_at = datetime.now(timezone.utc)
    db.flush()
    return notification


def point_a_rows(db: DBSession, students: list[User]) -> list[dict]:
    """Строки входного списка: по ученику на строку, разобранные — вниз.

    Разобранные не прячутся, а уезжают вниз и гаснут: Лиза 14.09.2026 —
    «чтобы они уползали вниз… либо чтобы они просто помечались серым, и я
    могла листать и видеть, кого я уже проверила, а кого ещё не проверила».

    Кого показывать, решает вызывающий: роутер передаёт список из
    `review_aggregate._accessible_students`, который уже отсекает архив
    (`is_active`) и держит куратора в рамках своих учеников. Своего фильтра
    здесь нет намеренно — иначе правил доступа стало бы два и они разошлись бы.

    Плашки считаются по каждому ученику отдельно, 2–4 запроса на человека. При
    нынешних пяти активных учениках это дешевле общего запроса с группировкой;
    станет десятки — список стоит переписать одним проходом. Фотографии здесь
    не грузятся вовсе (`with_images=False`): на списке их никто не показывает,
    а собирать их дороже всего остального.
    """
    rows = [
        {"point_a": student_point_a(db, student, with_images=False)}
        for student in students
    ]
    rows.sort(key=lambda row: (
        row["point_a"].is_done,
        (row["point_a"].student.last_name or ""),
        (row["point_a"].student.first_name or row["point_a"].student.name or ""),
    ))
    return rows
