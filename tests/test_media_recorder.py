"""Голосовое и «кружок» преподавателя (владелец 25.09.2026: «чтобы можно было
записать голосовое и даже кружок как в тг в любом из имеющихся заданиях»).

Две стороны одной функции:

1. Переписка по работе — три экрана (блок задания, домашка, пробник). Запись
   из браузера приходит обычным файлом, кружок — видео с флагом `video_note=1`.
   Флаг принимается только от преподавателя.
2. Блок конструктора «Голосовое / кружок» (`BLOCK_MEDIA`): запись грузится на
   `/upload-media`, сохраняется вместе с заданием, у ученика играет плеером и
   закрывается отметкой «Выполнено».
"""

from datetime import date, datetime, timedelta, timezone
import re
from unittest.mock import patch

import pytest

from app.models.exam_cycle import ExamCycle
from app.models.feedback import Feedback, FeedbackMessage
from app.models.homework_feedback import HomeworkFeedback, HomeworkFeedbackMessage
from app.models.homework_submission import HomeworkSubmission
from app.models.task_block import (
    BLOCK_MEDIA,
    BLOCK_TYPE_LABELS,
    BLOCK_UPLOAD,
    MEDIA_NOTE,
    MEDIA_VOICE,
    TaskBlock,
    TaskBlockState,
    TaskBlockSubmission,
)
from app.models.task_block_feedback import TaskBlockFeedback, TaskBlockFeedbackMessage
from app.models.tracker import ITEM_HOMEWORK, SOURCE_HOMEWORK, TrackerTask
from app.models.work import WORK_TYPE_MOCK_EXAM, Work
from app.services import s3 as s3_service
from app.services.feedback import ROLE_CURATOR, ROLE_STUDENT
from app.services.task_block_feedback import get_or_create_feedback, send_message
from app.services.task_blocks import sync_blocks
from app.services.tracker import copy_task_blocks, create_homework, create_task

PROGRAM = "/cabinet/staff/program"
EVERYONE = {"assign_to_all": True, "tag_ids": [], "assignee_usernames": ""}
NOTE_URL = "https://s3.example.com/zadaniya-media/note/circle.webm"
VOICE_URL = "https://s3.example.com/zadaniya-media/voice/voice.webm"


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)


def _block_submission(db, student):
    task = TrackerTask(
        title="Домашняя работа", kind="material", is_published=True, assign_to_all=True,
    )
    db.add(task)
    db.flush()
    block = TaskBlock(task_id=task.id, block_type=BLOCK_UPLOAD, title="Сдать листы")
    db.add(block)
    db.flush()
    submission = TaskBlockSubmission(
        block_id=block.id, user_id=student.id, submitted_at=datetime.now(timezone.utc),
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    return submission


def _curator_and_student(db, user_factory, *, base):
    curator = user_factory(vk_id=base, name="Куратор", role_name="куратор")
    student = user_factory(vk_id=base + 1, name="Ученик", role_name="ученик")
    student.curator_id = curator.id
    db.commit()
    return curator, student


# ── Переписка: блок задания ────────────────────────────────────────────────

def test_teacher_sends_video_note_recorded_in_browser(db, user_factory, session_factory, client):
    """Запись из браузера: MIME с параметром кодека и имя с расширением.
    Кружок сохраняется флагом и рисуется кругом у ученика."""
    curator, student = _curator_and_student(db, user_factory, base=981_001)
    submission = _block_submission(db, student)
    _login(client, session_factory, curator)

    with (
        patch("app.services.task_block_feedback.s3_service.upload_to_s3", return_value=NOTE_URL),
        patch("app.api.task_block_feedback.notify"),
    ):
        response = client.post(
            f"/cabinet/staff/task-block-submissions/{submission.id}/messages",
            data={"video_note": "1"},
            files={"video": ("circle-1.webm", b"note-bytes", "video/webm;codecs=vp8,opus")},
        )

    assert response.status_code == 200, response.text
    feedback = db.query(TaskBlockFeedback).filter_by(submission_id=submission.id).one()
    message = db.query(TaskBlockFeedbackMessage).filter_by(feedback_id=feedback.id).one()
    assert message.video_s3_url == NOTE_URL
    assert message.video_is_note is True

    _login(client, session_factory, student)
    page = client.get(f"/cabinet/task-block-submissions/{submission.id}/feedback")
    assert page.status_code == 200
    assert 'class="msg-video-note"' in page.text
    # Кнопок записи у ученика нет: записывает только преподаватель.
    assert "data-media-recorder" not in page.text


def test_teacher_page_offers_recorder(db, user_factory, session_factory, client):
    curator, student = _curator_and_student(db, user_factory, base=981_011)
    submission = _block_submission(db, student)
    _login(client, session_factory, curator)

    page = client.get(f"/cabinet/staff/task-block-submissions/{submission.id}/feedback")

    assert page.status_code == 200
    assert "data-media-recorder" in page.text
    assert "/static/js/media-recorder-field.js?v=" in page.text


def test_voice_with_codec_mime_is_accepted(db, user_factory, session_factory, client):
    curator, student = _curator_and_student(db, user_factory, base=981_021)
    submission = _block_submission(db, student)
    _login(client, session_factory, curator)

    with (
        patch("app.services.task_block_feedback.s3_service.upload_to_s3", return_value=VOICE_URL),
        patch("app.api.task_block_feedback.notify"),
    ):
        response = client.post(
            f"/cabinet/staff/task-block-submissions/{submission.id}/messages",
            files={"audio": ("voice-1.webm", b"voice-bytes", "audio/webm;codecs=opus")},
        )

    assert response.status_code == 200, response.text
    message = db.query(TaskBlockFeedbackMessage).one()
    assert message.audio_s3_url == VOICE_URL
    assert message.video_is_note is False


def test_note_flag_without_video_is_ignored(db, user_factory, session_factory, client):
    curator, student = _curator_and_student(db, user_factory, base=981_031)
    submission = _block_submission(db, student)
    _login(client, session_factory, curator)

    with patch("app.api.task_block_feedback.notify"):
        response = client.post(
            f"/cabinet/staff/task-block-submissions/{submission.id}/messages",
            data={"text": "Просто текст", "video_note": "1"},
        )

    assert response.status_code == 200, response.text
    assert db.query(TaskBlockFeedbackMessage).one().video_is_note is False


@pytest.mark.asyncio
async def test_student_cannot_send_video_note(db, user_factory):
    """Защита в общем слое, а не только в роуте: ученик кружок не шлёт,
    даже если флаг дошёл до сервиса."""
    curator, student = _curator_and_student(db, user_factory, base=981_041)
    submission = _block_submission(db, student)
    feedback, _ = get_or_create_feedback(db, submission_id=submission.id, initiator_id=curator.id)

    with patch("app.services.task_block_feedback.s3_service.upload_to_s3", return_value=NOTE_URL):
        student_msg = await send_message(
            db, feedback=feedback, sender_id=student.id, sender_role=ROLE_STUDENT,
            text=None, photo=None, video=("circle.webm", b"x", "video/webm"),
            video_is_note=True,
        )
        curator_msg = await send_message(
            db, feedback=feedback, sender_id=curator.id, sender_role=ROLE_CURATOR,
            text=None, photo=None, video=("circle.webm", b"x", "video/webm"),
            video_is_note=True,
        )

    assert student_msg.video_is_note is False
    assert curator_msg.video_is_note is True


# ── Переписка: домашка ─────────────────────────────────────────────────────

def test_homework_teacher_video_note(auth_client, db, user_factory, session_factory):
    client, student = auth_client
    homework = create_homework(
        db, title="Нарисуй куб", user_id=student.id, description="Карандашом.",
        submission_required=True, max_files=1,
    )
    task = create_task(
        db, title="Нарисуй куб", user_id=student.id, kind=ITEM_HOMEWORK,
        source_kind=SOURCE_HOMEWORK, source_id=homework.id, assign_to_all=True,
    )
    task.is_published = True
    db.commit()
    client.get(f"/cabinet/homework/{task.id}")
    submission = db.query(HomeworkSubmission).one()

    curator = user_factory(vk_id=981_051, name="Куратор", role_name="куратор")
    student.curator_id = curator.id
    db.commit()
    _login(client, session_factory, curator)

    staff_page = client.get(f"/cabinet/staff/homework/submissions/{submission.id}/feedback")
    assert "data-media-recorder" in staff_page.text

    with patch.object(s3_service, "upload_to_s3", return_value=NOTE_URL):
        response = client.post(
            f"/cabinet/staff/homework/submissions/{submission.id}/message",
            data={"video_note": "1"},
            files={"video": ("circle-1.mp4", b"note-bytes", "video/mp4")},
        )
    assert response.status_code == 200, response.text

    feedback = db.query(HomeworkFeedback).filter_by(submission_id=submission.id).one()
    message = db.query(HomeworkFeedbackMessage).filter_by(feedback_id=feedback.id).one()
    assert message.video_is_note is True

    _login(client, session_factory, student)
    page = client.get(f"/cabinet/homework/{task.id}/feedback")
    assert 'class="msg-video-note"' in page.text
    assert "data-media-recorder" not in page.text


# ── Переписка: пробник ─────────────────────────────────────────────────────

def test_mock_exam_dialog_video_note(client, db, user_factory, session_factory):
    curator, student = _curator_and_student(db, user_factory, base=981_061)
    cycle = ExamCycle(user_id=student.id, subject="Drawing", started_at=date(2026, 5, 10))
    db.add(cycle)
    db.commit()
    work = Work(
        user_id=student.id, work_type=WORK_TYPE_MOCK_EXAM, month="05", year=2026,
        filename="final.jpg", subject="Drawing", status="success",
        s3_url="https://example.test/final.jpg", is_final=True, cycle_id=cycle.id,
        attempt_number=1,
    )
    db.add(work)
    db.commit()
    _login(client, session_factory, curator)

    with patch("app.services.feedback.s3_service.upload_to_s3", return_value=NOTE_URL):
        response = client.post(
            f"/cabinet/feedback/{work.id}/message",
            data={"video_note": "1"},
            files={"video": ("circle-1.webm", b"note-bytes", "video/webm")},
            headers={"Accept": "application/json"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["message"]["video_is_note"] is True
    feedback = db.query(Feedback).filter_by(work_id=work.id).one()
    message = db.query(FeedbackMessage).filter_by(feedback_id=feedback.id).one()
    assert message.video_is_note is True


# ── Конструктор: загрузка записи ───────────────────────────────────────────

def _staff(client, user_factory, session_factory, *, vk_id=981_101):
    user = user_factory(
        vk_id=vk_id, name="Главный преподаватель", is_admin=True,
        is_group_member=False, role_name="админ",
    )
    _login(client, session_factory, user)
    return user


def test_upload_media_stores_voice_in_s3(client, user_factory, session_factory):
    _staff(client, user_factory, session_factory)

    with patch.object(s3_service, "upload_to_s3", return_value=VOICE_URL) as upload:
        response = client.post(
            f"{PROGRAM}/upload-media",
            data={"kind": MEDIA_VOICE},
            files={"file": ("voice-1.webm", b"voice-bytes", "audio/webm;codecs=opus")},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "ok": True, "url": VOICE_URL, "path": body["path"], "kind": MEDIA_VOICE,
    }
    assert body["path"].startswith("zadaniya-media/voice/")
    assert body["path"].endswith(".webm")
    # Файл уходит как есть, без перекодирования, с MIME без параметров.
    assert upload.call_args.args[1:] == (b"voice-bytes", "audio/webm")


def test_upload_media_accepts_attached_files_in_other_formats(client, user_factory, session_factory):
    """«Прикрепить файл» (владелец 25.09.2026: «чтобы можно было загрузить
    голосовое в любом формате и видео») — в отличие от живой записи, которая
    в браузере всегда получается webm или mp4, файл с диска может быть любым
    форматом, который умеет разобрать телефон или диктофон. Сервер это уже
    умел (`read_audio_upload`/`read_video_upload`, `app/services/feedback.py`)
    — здесь фиксируем это тестом на самом эндпоинте загрузки блока."""
    _staff(client, user_factory, session_factory, vk_id=981_112)

    with patch.object(s3_service, "upload_to_s3", return_value=VOICE_URL) as upload:
        mp3 = client.post(
            f"{PROGRAM}/upload-media",
            data={"kind": MEDIA_VOICE},
            files={"file": ("голосовое.mp3", b"mp3-bytes", "audio/mpeg")},
        )
    assert mp3.status_code == 200, mp3.text
    assert upload.call_args.args[1:] == (b"mp3-bytes", "audio/mpeg")

    with patch.object(s3_service, "upload_to_s3", return_value=NOTE_URL) as upload:
        mov = client.post(
            f"{PROGRAM}/upload-media",
            data={"kind": MEDIA_NOTE},
            files={"file": ("клип.mov", b"mov-bytes", "video/quicktime")},
        )
    assert mov.status_code == 200, mov.text
    assert upload.call_args.args[1:] == (b"mov-bytes", "video/quicktime")


def test_upload_media_rejects_wrong_format_and_kind(client, user_factory, session_factory):
    _staff(client, user_factory, session_factory, vk_id=981_111)

    with patch.object(s3_service, "upload_to_s3", return_value=NOTE_URL) as upload:
        bad_type = client.post(
            f"{PROGRAM}/upload-media",
            data={"kind": MEDIA_NOTE},
            files={"file": ("notes.txt", b"text", "text/plain")},
        )
        bad_kind = client.post(
            f"{PROGRAM}/upload-media",
            data={"kind": "podcast"},
            files={"file": ("voice.webm", b"x", "audio/webm")},
        )

    assert bad_type.status_code == 422
    assert bad_kind.status_code == 422
    upload.assert_not_called()


def test_upload_media_fails_loudly_without_storage(client, user_factory, session_factory):
    """Без S3 блок остался бы без файла и молча пропал бы при сохранении."""
    _staff(client, user_factory, session_factory, vk_id=981_121)

    with patch.object(s3_service, "upload_to_s3", return_value=None):
        response = client.post(
            f"{PROGRAM}/upload-media",
            data={"kind": MEDIA_VOICE},
            files={"file": ("voice.webm", b"x", "audio/webm")},
        )

    assert response.status_code == 502
    assert response.json()["ok"] is False


def test_upload_media_rejects_missing_csrf_with_json_body(client, user_factory, session_factory):
    """`tests/conftest.py` отключает CSRF глобально, и без этого теста баг
    с 25.09.2026 не поймать: в конструкторе не было `input[name=csrf_token]`,
    компонент слал пустой токен, `require_csrf` отвечал 403, а без заголовка
    `Accept: application/json` сервер (`app/main.py`, обработчик 403) отдавал
    HTML вместо JSON — компонент показывал общий текст вместо настоящей
    причины. Здесь включаем настоящую проверку токена и просим JSON, как
    теперь делает сам компонент (`media-recorder-field.js`, `upload()`)."""
    from app.dependencies import require_csrf
    from app.main import app

    _staff(client, user_factory, session_factory, vk_id=981_102)
    csrf_override = app.dependency_overrides.pop(require_csrf)
    try:
        response = client.post(
            f"{PROGRAM}/upload-media",
            data={"kind": MEDIA_VOICE, "csrf_token": ""},
            files={"file": ("voice.webm", b"x", "audio/webm")},
            headers={"Accept": "application/json"},
        )
    finally:
        app.dependency_overrides[require_csrf] = csrf_override

    assert response.status_code == 403
    body = response.json()
    assert body["detail"]


def test_attach_file_button_is_wired_in_static_script(client):
    """«Прикрепить аудио/видео» рисуется скриптом на клиенте, а не сервером —
    в HTML страницы этой разметки нет, проверить можно только сам файл.
    Не полная замена браузерной проверки (клики и file-picker здесь не
    воспроизвести), но ловит снесённые определения при следующей правке —
    как это уже случилось с `return` в program_blocks_editor_js.html."""
    response = client.get("/static/js/media-recorder-field.js")
    assert response.status_code == 200
    source = response.text

    assert "data-mrf-attach" in source
    assert "data-mrf-file" in source
    assert "Recorder.prototype.attachFile" in source
    assert "Recorder.prototype.useFile" in source
    # attachFile должен реально вызываться из обработчика change, иначе выбор
    # файла молча ничего не делает.
    assert "self.attachFile(kind, file)" in source


def test_upload_media_is_closed_for_students(client, user_factory, session_factory):
    student = user_factory(vk_id=981_131, name="Ученик", role_name="ученик")
    _login(client, session_factory, student)

    with patch.object(s3_service, "upload_to_s3", return_value=VOICE_URL) as upload:
        response = client.post(
            f"{PROGRAM}/upload-media",
            data={"kind": MEDIA_VOICE},
            files={"file": ("voice.webm", b"x", "audio/webm")},
        )

    assert response.status_code in (302, 303, 401, 403)
    upload.assert_not_called()


# ── Конструктор: блок «Голосовое / кружок» ─────────────────────────────────

def _future_day_iso(offset: int = 3) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def _media_item(**extra):
    item = {
        "block_type": BLOCK_MEDIA, "media_kind": MEDIA_NOTE,
        "media_url": NOTE_URL, "media_path": "zadaniya-media/note/circle.webm",
        "title": "Пара слов перед заданием",
    }
    item.update(extra)
    return item


def test_constructor_offers_media_block(client, db, user_factory, session_factory, monkeypatch):
    monkeypatch.setattr("app.api.cabinet_program.today_msk", date.today)
    monkeypatch.setattr("app.services.program.today_msk", date.today)
    _staff(client, user_factory, session_factory, vk_id=981_141)

    page = client.get(f"{PROGRAM}/{_future_day_iso()}")

    assert page.status_code == 200
    assert 'data-add-block="media"' in page.text
    assert BLOCK_TYPE_LABELS[BLOCK_MEDIA] in page.text
    assert "/static/js/media-recorder-field.js?v=" in page.text
    # Регрессия на баг с 25.09.2026: на этой странице нет
    # `input[name=csrf_token]` (конструктор шлёт JSON), и блок «media» без
    # явного `data-mrf-csrf` в своей разметке грузил запись с пустым токеном.
    assert "data-mrf-csrf=\"' + escapeHTML(csrfToken) + '\"" in page.text


def _block_body_branches(page_text: str) -> dict[str, str]:
    """Нарезать `blockBodyHTML` на ветки: {тип блока: исходник его ветки}.

    Типы не перечислены списком намеренно — новый тип блока попадёт под
    сторожей ниже сам, без правки тестов.
    """
    body_start = page_text.index("function blockBodyHTML(type)")
    body_end = page_text.index("function blockSettingsHTML(type)", body_start)
    source = page_text[body_start:body_end]

    marks = [(m.start(), m.group(1)) for m in re.finditer(r"if \(type === '(\w+)'\)", source)]
    assert len(marks) >= 12, f"ветвей блоков нашлось {len(marks)} — разметка конструктора изменилась"

    branches = {}
    for index, (start, kind) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else len(source)
        branches[kind] = source[start:end]
    # Дефолтная ветка без `if` — вопрос; она начинается после последней ветки.
    branches["question"] = source[marks[-1][0]:][source[marks[-1][0]:].index("// question"):]
    return branches


def test_every_block_branch_returns_before_its_markup(
    client, db, user_factory, session_factory, monkeypatch
):
    """Регрессия 25.09.2026: правка, добавившая `data-mrf-csrf` в разметку,
    случайно снесла `return ''` перед ней. Без `return` JS проваливался мимо
    всех `if (type === …)` до самой последней ветки функции — дефолтного
    вопроса («Текст вопроса», «Тип ответа», «Добавить вариант») — и вместо
    записи владелец в конструкторе видел редактор вопроса. Подстрочный поиск
    разметки этого не ловит: строка остаётся в исходнике JS, даже если ветка
    недостижима. Поэтому проверяем каждую ветку: `return` есть и стоит до
    своей разметки."""
    monkeypatch.setattr("app.api.cabinet_program.today_msk", date.today)
    monkeypatch.setattr("app.services.program.today_msk", date.today)
    _staff(client, user_factory, session_factory, vk_id=981_143)

    page = client.get(f"{PROGRAM}/{_future_day_iso()}")
    assert page.status_code == 200

    for kind, branch in _block_body_branches(page.text).items():
        assert "return" in branch, f"ветка '{kind}' не возвращает разметку — провалится в дефолт-вопрос"
        # Первый же оператор ветки обязан быть `return`: если разметка
        # начинается раньше, она склеивается в никуда, а функция идёт дальше.
        body = branch.split("{", 1)[-1] if branch.startswith("if (") else branch
        statements = [
            line.strip() for line in body.splitlines()
            if line.strip() and not line.strip().startswith("//")
        ]
        assert statements[0].startswith("return"), (
            f"в ветке '{kind}' первым идёт не `return`, а «{statements[0]}» — ветка недостижима"
        )


def test_every_block_branch_starts_with_title_then_description(
    client, db, user_factory, session_factory, monkeypatch
):
    """Правило владельца 25.09.2026: у каждого типа блока есть поле названия и
    поле описания, и они идут первыми — сначала название, потом описание.
    Полная формулировка — инвариант в AGENTS.md. Здесь сторож на разметку:
    новый тип блока без этих полей (или с ними в конце, как было у фото,
    видео, голосового и ссылки) тест не пропустит."""
    monkeypatch.setattr("app.api.cabinet_program.today_msk", date.today)
    monkeypatch.setattr("app.services.program.today_msk", date.today)
    _staff(client, user_factory, session_factory, vk_id=981_144)

    page = client.get(f"{PROGRAM}/{_future_day_iso()}")
    assert page.status_code == 200

    for kind, branch in _block_body_branches(page.text).items():
        # Поля приходят и литералом, и общей константой — сторожу важен
        # порядок, а не способ вставки.
        title_at = min(
            (branch.index(mark) for mark in ("data-b-title", "TITLE_FIELD_OPTIONAL") if mark in branch),
            default=-1,
        )
        body_at = min(
            (branch.index(mark) for mark in ("data-b-body", "BODY_FIELD_OPTIONAL") if mark in branch),
            default=-1,
        )
        assert title_at != -1, f"у блока '{kind}' нет поля названия"
        assert body_at != -1, f"у блока '{kind}' нет поля описания"
        assert title_at < body_at, f"у блока '{kind}' описание идёт раньше названия"


def test_cycle_items_constructor_media_block_carries_csrf(
    client, db, user_factory, session_factory
):
    """Тот же блок «media» подключается и на экране «Задания внутри цикла»
    (`cabinet_program_cycle_items.html`) — токен в разметке нужен там тоже."""
    from datetime import timedelta

    from app.services.tz import today_msk

    _staff(client, user_factory, session_factory, vk_id=981_142)
    today = today_msk()
    created = client.post(
        f"{PROGRAM}/cycles",
        json={
            "title": "Цикл", "description": None,
            "starts_on": today.isoformat(),
            "ends_on": (today + timedelta(days=5)).isoformat(),
            "is_published": True,
        },
    )
    assert created.status_code == 200, created.text
    cycle_id = created.json()["cycle_id"]

    page = client.get(f"{PROGRAM}/cycles/{cycle_id}")

    assert page.status_code == 200
    assert "data-mrf-csrf=\"' + escapeHTML(csrfToken) + '\"" in page.text


def test_constructor_saves_media_block_and_keeps_it(client, db, user_factory, session_factory, monkeypatch):
    """Сохранённый блок уходит обратно в форму правки с записью — иначе
    повторное сохранение сочло бы его пустым и стёрло."""
    monkeypatch.setattr("app.api.cabinet_program.today_msk", date.today)
    monkeypatch.setattr("app.services.program.today_msk", date.today)
    _staff(client, user_factory, session_factory, vk_id=981_151)

    response = client.post(
        f"{PROGRAM}/{_future_day_iso()}/material",
        json={"title": "Материал", "audience": EVERYONE, "blocks": [_media_item()]},
    )
    assert response.status_code == 200, response.text

    task = db.query(TrackerTask).filter(TrackerTask.kind == "material").one()
    [block] = db.query(TaskBlock).filter(TaskBlock.task_id == task.id).all()
    assert block.block_type == BLOCK_MEDIA
    assert block.media_kind == MEDIA_NOTE
    assert block.media_s3_url == NOTE_URL
    assert block.media_s3_path == "zadaniya-media/note/circle.webm"

    source = client.get(f"{PROGRAM}/blocks-source/{task.id}").json()["blocks"]
    assert source[0]["media_url"] == NOTE_URL
    assert source[0]["media_kind"] == MEDIA_NOTE

    page = client.get(f"{PROGRAM}/{_future_day_iso()}")
    assert NOTE_URL in page.text


def test_constructor_rejects_media_url_with_other_scheme(client, user_factory, session_factory, monkeypatch):
    monkeypatch.setattr("app.api.cabinet_program.today_msk", date.today)
    monkeypatch.setattr("app.services.program.today_msk", date.today)
    _staff(client, user_factory, session_factory, vk_id=981_161)

    response = client.post(
        f"{PROGRAM}/{_future_day_iso()}/material",
        json={
            "title": "Материал", "audience": EVERYONE,
            "blocks": [_media_item(media_url="javascript:alert(1)")],
        },
    )

    assert response.status_code == 422


def test_sync_blocks_drops_empty_media_and_clears_foreign_types(db, user_factory):
    staff = user_factory(vk_id=981_171, name="Стафф", is_admin=True, role_name="админ")
    task = create_task(db, title="Материал", user_id=staff.id, kind="material", assign_to_all=True)

    rows = sync_blocks(db, task_id=task.id, items=[
        {"block_type": BLOCK_MEDIA, "media_kind": MEDIA_VOICE},  # записи нет
        _media_item(),
        # Чужой тип с медиаполями: мусор не должен лечь в базу.
        {"block_type": "text", "body": "Текст", "media_url": NOTE_URL, "media_kind": MEDIA_NOTE},
    ])

    assert [r.block_type for r in rows] == [BLOCK_MEDIA, "text"]
    assert rows[1].media_s3_url is None
    assert rows[1].media_kind is None


def test_copy_task_blocks_carries_media(db, user_factory):
    staff = user_factory(vk_id=981_181, name="Стафф", is_admin=True, role_name="админ")
    source = create_task(db, title="Оригинал", user_id=staff.id, kind="material", assign_to_all=True)
    target = create_task(db, title="Копия", user_id=staff.id, kind="material", assign_to_all=True)
    sync_blocks(db, task_id=source.id, items=[_media_item()])

    copy_task_blocks(db, from_task_id=source.id, to_task_id=target.id)

    [clone] = db.query(TaskBlock).filter(TaskBlock.task_id == target.id).all()
    assert clone.media_kind == MEDIA_NOTE
    assert clone.media_s3_url == NOTE_URL
    assert clone.media_s3_path == "zadaniya-media/note/circle.webm"


# ── Ученик: плеер и отметка «Выполнено» ────────────────────────────────────

def test_student_gets_media_and_closes_block(client, db, user_factory, session_factory):
    staff = user_factory(vk_id=981_191, name="Стафф", is_admin=True, role_name="админ")
    task = create_task(
        db, title="Материал", user_id=staff.id, kind="material",
        assign_to_all=True, is_required=True,
    )
    task.is_published = True
    db.add(TaskBlock(
        task_id=task.id, sort_order=0, block_type=BLOCK_MEDIA, is_required=True,
        media_kind=MEDIA_VOICE, media_s3_url=VOICE_URL,
    ))
    db.commit()
    [block] = db.query(TaskBlock).filter(TaskBlock.task_id == task.id).all()
    student = user_factory(vk_id=981_192, name="Ученик", role_name="ученик")
    _login(client, session_factory, student)

    payload = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"][0]
    assert payload["media_kind"] == MEDIA_VOICE
    assert payload["media_url"] == VOICE_URL
    assert payload["done"] is False
    assert payload["confirm_endpoint"] == f"/cabinet/tracker/blocks/{block.id}/done"

    response = client.post(f"/cabinet/tracker/blocks/{block.id}/done")
    assert response.status_code == 200, response.text

    state = db.query(TaskBlockState).filter_by(block_id=block.id, user_id=student.id).one()
    assert state.completion_source == "media_confirmed"
    payload = client.get(f"/cabinet/tracker/tasks/{task.id}/blocks").json()["blocks"][0]
    assert payload["done"] is True
