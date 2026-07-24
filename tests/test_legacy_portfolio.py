"""Tests for the read-only legacy (Telegram-archive) portfolio photo viewer."""
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.legacy_portfolio_photo import LegacyPortfolioPhoto
from app.services.s3 import s3_path_legacy_archive
from scripts.import_legacy_portfolio import (
    SourcePhoto,
    _stable_object_name,
    import_photos,
    load_payload,
)


@pytest.fixture()
def curator_client(client, db, user_factory, session_factory):
    curator = user_factory(vk_id=810001, name="Curator", role_name="куратор")
    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)
    return client, curator


@pytest.fixture()
def student(db, user_factory, curator_client):
    _, curator = curator_client
    student = user_factory(vk_id=810002, name="Student One", role_name="ученик")
    student.curator_id = curator.id
    student.first_name = "Анна"
    student.last_name = "Иванова"
    db.add(student)
    db.commit()
    db.refresh(student)
    return student


def _add_photo(db, user_id, dialog_id="12345", month="январь", year=2026):
    p = LegacyPortfolioPhoto(
        user_id=user_id,
        dialog_id=dialog_id,
        month=month,
        year=year,
        original_filename="file_1.jpg",
        s3_path=f"Архив/X/X_{user_id}/{year}-01/{dialog_id}-{month}.jpg",
        s3_url=f"https://s3.example.com/{dialog_id}.jpg",
        sent_at=datetime(year, 1, 15, tzinfo=timezone.utc),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_curator_sees_own_student_archive(curator_client, db, student):
    client, _ = curator_client
    _add_photo(db, student.id, month="январь", year=2026)
    _add_photo(db, student.id, month="февраль", year=2026)

    resp = client.get(f"/cabinet/students/{student.id}/legacy-portfolio")
    assert resp.status_code == 200
    assert "Январь" in resp.text
    assert "Февраль" in resp.text
    assert "Всего 2 фото" in resp.text
    assert 'class="back-link"' in resp.text
    assert 'aria-expanded="true"' in resp.text
    assert 'data-gallery' in resp.text
    assert 'role="dialog"' in resp.text


def test_empty_state_when_no_photos(curator_client, student):
    client, _ = curator_client
    resp = client.get(f"/cabinet/students/{student.id}/legacy-portfolio")
    assert resp.status_code == 200
    assert "Архивных фото нет" in resp.text


def test_curator_denied_for_other_curators_student(client, db, user_factory, session_factory):
    other_curator = user_factory(vk_id=810099, name="Other Curator", role_name="куратор")
    stranger_student = user_factory(vk_id=810003, name="Stranger", role_name="ученик")
    stranger_student.curator_id = other_curator.id
    db.add(stranger_student)
    db.commit()
    db.refresh(stranger_student)

    curator = user_factory(vk_id=810004, name="Curator Two", role_name="куратор")
    sess = session_factory(curator)
    client.cookies.set("session_id", sess.id)

    resp = client.get(f"/cabinet/students/{stranger_student.id}/legacy-portfolio")
    assert resp.status_code in (403, 404)


def test_student_role_denied(client, db, user_factory, session_factory):
    user = user_factory(vk_id=810010, name="Just Student", role_name="ученик")
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)

    resp = client.get(f"/cabinet/students/{user.id}/legacy-portfolio")
    assert resp.status_code == 403


def test_mock_exams_endpoint_includes_legacy_by_month(curator_client, db, student):
    client, _ = curator_client
    _add_photo(db, student.id, dialog_id="1", month="январь", year=2026)
    _add_photo(db, student.id, dialog_id="2", month="январь", year=2026)
    _add_photo(db, student.id, dialog_id="3", month="март", year=2026)

    resp = client.get(f"/cabinet/students/{student.id}/mock-exams")
    assert resp.status_code == 200
    data = resp.json()
    groups = data["legacy_by_month"]
    assert len(groups) == 2
    # Most recent month first.
    assert groups[0]["month"] == "март"
    assert groups[0]["total"] == 1
    assert groups[1]["month"] == "январь"
    assert groups[1]["total"] == 2
    assert groups[1]["photos"][0]["s3_url"]


def test_mock_exams_endpoint_empty_legacy_when_no_archive(curator_client, student):
    client, _ = curator_client
    resp = client.get(f"/cabinet/students/{student.id}/mock-exams")
    assert resp.status_code == 200
    assert resp.json()["legacy_by_month"] == []


def test_student_profile_includes_archive_count(curator_client, db, student):
    client, _ = curator_client
    _add_photo(db, student.id)

    resp = client.get(f"/cabinet/students/{student.id}/profile")
    assert resp.status_code == 200
    assert resp.json()["student"]["legacy_photo_count"] == 1


def test_unique_constraint_on_s3_path(db, user_factory):
    student = user_factory(vk_id=810020, name="Dup Student", role_name="ученик")
    _add_photo(db, student.id, dialog_id="99999")
    with pytest.raises(IntegrityError):
        p2 = LegacyPortfolioPhoto(
            user_id=student.id,
            dialog_id="99999",
            month="январь",
            year=2026,
            original_filename="dup.jpg",
            s3_path=f"Архив/X/X_{student.id}/2026-01/99999-январь.jpg",
            s3_url="https://s3.example.com/dup.jpg",
            sent_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        )
        db.add(p2)
        db.commit()
    db.rollback()


def test_legacy_s3_path_is_stable_and_strips_directories():
    first = s3_path_legacy_archive(123, "УВЕРЕННЫЙ", 2026, 2, "../safe.jpg")
    second = s3_path_legacy_archive(123, "УВЕРЕННЫЙ", 2026, 2, "../safe.jpg")
    assert first == second
    assert first.endswith("/Уверенный_123_safe.jpg")
    assert ".." not in first


def test_load_payload_validates_structure_and_hosts(tmp_path: Path):
    payload = tmp_path / "archive.json"
    payload.write_text(
        """[
          {
            "user_id": 42,
            "dialog_id": "dialog",
            "photos": [
              {
                "filename": "../photo.jpg",
                "url": "https://storage.example/photo/1",
                "date": "2026-02-03T12:30:00Z"
              }
            ]
          }
        ]""",
        encoding="utf-8",
    )

    photos = load_payload(payload, {"storage.example"})
    assert len(photos) == 1
    assert photos[0].filename == "photo.jpg"
    assert photos[0].sent_at == datetime(2026, 2, 3, 12, 30, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="explicitly allowed host"):
        load_payload(payload, {"other.example"})


def test_stable_object_name_hides_source_url():
    photo = SourcePhoto(
        user_id=42,
        dialog_id="dialog",
        filename="photo.JPG",
        url="https://storage.example/private/signed-link",
        sent_at=datetime(2026, 2, 3, tzinfo=timezone.utc),
    )
    name = _stable_object_name(photo)
    assert name.endswith(".jpg")
    assert "private" not in name
    assert name == _stable_object_name(photo)


def test_import_dry_run_counts_without_network_or_writes(db, user_factory):
    student = user_factory(vk_id=810030, name="Dry Run Student", role_name="ученик")
    photos = [
        SourcePhoto(
            user_id=student.id,
            dialog_id="dry-run",
            filename="new.jpg",
            url="https://storage.example/new",
            sent_at=datetime(2026, 3, 4, tzinfo=timezone.utc),
        ),
        SourcePhoto(
            user_id=student.id + 10000,
            dialog_id="missing",
            filename="missing.jpg",
            url="https://storage.example/missing",
            sent_at=datetime(2026, 3, 4, tzinfo=timezone.utc),
        ),
    ]

    result = import_photos(db, photos, dry_run=True)
    assert result == {
        "created": 1,
        "skipped_existing": 0,
        "skipped_no_user": 1,
        "failed": 0,
    }
    assert db.query(LegacyPortfolioPhoto).filter_by(user_id=student.id).count() == 0
