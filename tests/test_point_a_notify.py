"""Уведомление об уровне точки А (Фаза 1, владелец 17.09.2026).

`maybe_notify_point_a_level` не пересчитывает `student_point_a` заново в этих
тестах — она монкипатчится на управляемый `PointA`, чтобы проверить саму
функцию (идемпотентность, выбор уровня, голое текстовое уведомление без
аудио) без сборки полного набора работ пробника/контрольной/портфолио.
"""
import pytest

from app.models.point_a_level_audio import PointALevelAudio
from app.services import point_a as point_a_service
from app.services.point_a import PointA, maybe_notify_point_a_level


@pytest.fixture()
def student(user_factory):
    return user_factory(vk_id=861_001, name="Ученик Голосовой")


@pytest.fixture()
def admin(user_factory):
    return user_factory(vk_id=861_002, name="ГП", role_name="админ")


def _fake_point_a(student, *, is_done, average):
    return PointA(student=student, plates=[], average=average, is_done=is_done, scored_count=0)


def _patch(monkeypatch, student, *, is_done, average):
    monkeypatch.setattr(
        point_a_service, "student_point_a",
        lambda db, s, with_images=True: _fake_point_a(student, is_done=is_done, average=average),
    )


def test_not_done_does_not_notify(db, student, monkeypatch):
    _patch(monkeypatch, student, is_done=False, average=None)

    result = maybe_notify_point_a_level(db, student)

    assert result is None
    assert student.point_a_notified_at is None


def test_done_notifies_once(db, student, monkeypatch):
    _patch(monkeypatch, student, is_done=True, average=85)

    result = maybe_notify_point_a_level(db, student)

    assert result is not None
    assert result.user_id == student.id
    assert "уровень 2" in result.title
    assert student.point_a_notified_at is not None


def test_second_call_does_not_renotify(db, student, monkeypatch):
    _patch(monkeypatch, student, is_done=True, average=85)
    first = maybe_notify_point_a_level(db, student)
    db.commit()

    second = maybe_notify_point_a_level(db, student)

    assert first is not None
    assert second is None


@pytest.mark.parametrize("average,expected_level", [(70, 2), (69, 1), (100, 2), (0, 1)])
def test_level_boundary(db, student, monkeypatch, average, expected_level):
    _patch(monkeypatch, student, is_done=True, average=average)

    result = maybe_notify_point_a_level(db, student)

    assert f"уровень {expected_level}" in result.title


def test_without_uploaded_audio_sends_text_only_not_blocked(db, student, monkeypatch):
    _patch(monkeypatch, student, is_done=True, average=85)

    result = maybe_notify_point_a_level(db, student)

    assert result is not None
    assert result.audio_url is None
    assert result.text


def test_with_uploaded_audio_attaches_it(db, student, admin, monkeypatch):
    db.add(PointALevelAudio(
        level=2, audio_s3_path="point-a-audio/2/x.mp3",
        audio_s3_url="https://s3.example/point-a-audio/2/x.mp3",
        uploaded_by_id=admin.id,
    ))
    db.commit()
    _patch(monkeypatch, student, is_done=True, average=90)

    result = maybe_notify_point_a_level(db, student)

    assert result.audio_url == "https://s3.example/point-a-audio/2/x.mp3"

