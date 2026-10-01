"""Превью работ для квадратиков карточки ученика (план
`plans/2026-09-29-apparchi-students-phone.md`, шаг 5).

В квадрат 84–88px карточки грузилось фото 1600px. Владелец 29.09.2026 выбрал
превью только для новых работ: загрузка кладёт рядом с фото копию 320px под
ключом `thumbs/<путь фото>`, а работы до этого дня остаются без превью, и
экран показывает у них само фото.
"""
import asyncio
import io
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.models.work import WORK_TYPE_AFTER, WORK_TYPE_BEFORE, WORK_TYPE_MOCK_EXAM, Work
from app.services import s3 as s3_service
from app.services.works import THUMB_MAX_PX, delete_works_with_dependents, upload_work_thumb

TEMPLATE = Path(__file__).resolve().parent.parent / "app" / "templates" / "cabinet_students.html"
LIGHTBOX = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "lightbox.js"


def _jpeg(width=1600, height=1200) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(buf, format="JPEG")
    return buf.getvalue()


class _FakeS3:
    """Хранилище в памяти: ключ → байты. Ссылка строится как у настоящего."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def upload(self, path, data, content_type="image/jpeg"):
        self.objects[path] = data
        return f"https://s3.example.com/{path}"

    def delete(self, path):
        self.deleted.append(path)
        self.objects.pop(path, None)
        return True

    def download(self, path):
        return self.objects.get(path)

    def patches(self):
        return [
            patch.object(s3_service, "is_configured", return_value=True),
            patch.object(s3_service, "upload_to_s3", side_effect=self.upload),
            patch.object(s3_service, "delete_from_s3", side_effect=self.delete),
            patch.object(s3_service, "download_from_s3", side_effect=self.download),
        ]


def _with(fake):
    class _Ctx:
        def __enter__(self):
            self.ps = fake.patches()
            for p in self.ps:
                p.start()
            return fake

        def __exit__(self, *exc):
            for p in reversed(self.ps):
                p.stop()
    return _Ctx()


def _login(client, session_factory, user):
    client.cookies.set("session_id", session_factory(user).id)


def _chief(user_factory):
    return user_factory(vk_id=960_001, name="Главный", is_admin=True, role_name="админ")


# ── Сервис ───────────────────────────────────────────────────────────────────

def test_thumb_lands_next_to_photo_and_is_small():
    fake = _FakeS3()
    with _with(fake):
        url = upload_work_thumb("portfolio/after/a.jpg", _jpeg())

    assert url == "https://s3.example.com/thumbs/portfolio/after/a.jpg"
    thumb = Image.open(io.BytesIO(fake.objects["thumbs/portfolio/after/a.jpg"]))
    assert max(thumb.size) == THUMB_MAX_PX


def test_deleting_work_removes_its_thumb(db, user_factory):
    student = user_factory(vk_id=960_101)
    work = Work(
        user_id=student.id, work_type=WORK_TYPE_AFTER, month="сентябрь", year=2026,
        filename="a.jpg", s3_url="https://s3.example.com/p/a.jpg", s3_path="p/a.jpg",
        thumb_s3_url="https://s3.example.com/thumbs/p/a.jpg", status="success",
    )
    db.add(work)
    db.commit()

    fake = _FakeS3()
    with _with(fake):
        delete_works_with_dependents(db, [work])
    db.commit()

    assert fake.deleted == ["p/a.jpg", "thumbs/p/a.jpg"]


# ── Загрузка ────────────────────────────────────────────────────────────────

def test_staff_upload_saves_thumb(client, db, user_factory, session_factory):
    chief = _chief(user_factory)
    student = user_factory(vk_id=960_201, name="Ученик")
    _login(client, session_factory, chief)

    fake = _FakeS3()
    with _with(fake):
        resp = client.post(
            f"/cabinet/students/{student.id}/upload",
            data={"work_type": "after", "month": "сентябрь", "year": "2026"},
            files={"photos": ("a.jpg", _jpeg(), "image/jpeg")},
        )

    assert resp.status_code == 200, resp.text
    work = db.query(Work).filter(Work.user_id == student.id).one()
    assert work.thumb_s3_url == f"https://s3.example.com/thumbs/{work.s3_path}"
    assert f"thumbs/{work.s3_path}" in fake.objects


def test_student_upload_saves_thumb(auth_client, db):
    from app.models.user import User

    client, user = auth_client
    db.query(User).filter(User.id == user.id).update({"portfolio_do_completed": True})
    db.commit()

    fake = _FakeS3()
    with _with(fake):
        resp = client.post(
            "/upload", data={"section": "before"},
            files=[("photos", ("before.jpg", _jpeg(), "image/jpeg"))],
        )

    assert resp.status_code == 200, resp.text
    work = db.query(Work).filter(Work.user_id == user.id, Work.work_type == WORK_TYPE_BEFORE).one()
    assert work.thumb_s3_url == f"https://s3.example.com/thumbs/{work.s3_path}"


def test_cycle_upload_returns_thumb():
    from app.api.cycle_upload import _upload_cycle_file_to_s3

    fake = _FakeS3()
    with _with(fake):
        path, url, thumb_url = asyncio.run(
            _upload_cycle_file_to_s3("a.jpg", _jpeg(), lambda fn: f"cycles/1/{fn}")
        )

    assert (path, url) == ("cycles/1/a.jpg", "https://s3.example.com/cycles/1/a.jpg")
    assert thumb_url == "https://s3.example.com/thumbs/cycles/1/a.jpg"


def test_failed_photo_upload_makes_no_thumb():
    from app.api.cycle_upload import _upload_cycle_file_to_s3

    with patch.object(s3_service, "upload_to_s3", return_value=None) as upload:
        _path, url, thumb_url = asyncio.run(
            _upload_cycle_file_to_s3("a.jpg", _jpeg(), lambda fn: f"cycles/1/{fn}")
        )

    assert url is None and thumb_url is None
    assert upload.call_count == 1


# ── Карточка ученика ─────────────────────────────────────────────────────────

def test_portfolio_and_mock_tabs_give_thumb_url(client, db, user_factory, session_factory):
    chief = _chief(user_factory)
    student = user_factory(vk_id=960_301, name="Ученик")
    common = dict(user_id=student.id, month="сентябрь", year=2026, status="success")
    db.add_all([
        Work(work_type=WORK_TYPE_BEFORE, filename="b.jpg", s3_url="https://s3/b.jpg",
             thumb_s3_url="https://s3/thumbs/b.jpg", **common),
        Work(work_type=WORK_TYPE_AFTER, filename="old.jpg", s3_url="https://s3/old.jpg", **common),
        Work(work_type=WORK_TYPE_MOCK_EXAM, filename="m.jpg", s3_url="https://s3/m.jpg",
             thumb_s3_url="https://s3/thumbs/m.jpg", subject="Рисунок", **common),
    ])
    db.commit()
    _login(client, session_factory, chief)

    portfolio = client.get(f"/cabinet/students/{student.id}/portfolio").json()
    assert portfolio["before_flat"][0]["thumb_url"] == "https://s3/thumbs/b.jpg"
    [after] = [w for g in portfolio["after_by_month"] for w in g["works"]]
    assert after["thumb_url"] is None  # работа до превью — экран возьмёт s3_url

    mock = client.get(f"/cabinet/students/{student.id}/mock-exams").json()
    assert "https://s3/thumbs/m.jpg" in str(mock)


def test_tiles_load_thumb_and_lightbox_opens_full_photo():
    # JS экрана с 29.09.2026 — в cabinet_students.js (шаг 10.3).
    source = (TEMPLATE.parent.parent / "static" / "js" / "cabinet_students.js").read_text(encoding="utf-8")
    assert "esc(w.thumb_url || w.s3_url)" in source
    assert 'data-full="\' + esc(w.s3_url)' in source
    # Все три сетки — «До», «После», пробники дня — идут через одну функцию.
    assert source.count("zoomPhoto(") == 4
    assert '<img src="\' + esc(w.s3_url)' not in source


# ── Поворот ──────────────────────────────────────────────────────────────────

def test_rotating_photo_rebuilds_thumb(client, db, user_factory, session_factory):
    admin = user_factory(vk_id=960_401, name="Суперадмин", is_admin=True, role_name="суперадмин")
    student = user_factory(vk_id=960_402)
    work = Work(
        user_id=student.id, work_type=WORK_TYPE_AFTER, month="сентябрь", year=2026,
        filename="a.jpg", s3_path="p/a.jpg", s3_url="https://s3.example.com/p/a.jpg",
        thumb_s3_url="https://s3.example.com/thumbs/p/a.jpg", status="success",
    )
    db.add(work)
    db.commit()
    _login(client, session_factory, admin)

    fake = _FakeS3()
    fake.objects["p/a.jpg"] = _jpeg(1600, 1200)
    with _with(fake), patch.object(
        s3_service, "s3_path_from_public_url",
        side_effect=lambda u: u.removeprefix("https://s3.example.com/"),
    ):
        resp = client.post(
            "/cabinet/rotate-photo",
            data={"src": "https://s3.example.com/p/a.jpg", "direction": "right"},
        )

    assert resp.status_code == 200, resp.text
    thumb_src = resp.json()["thumb_src"]
    assert thumb_src.startswith("https://s3.example.com/thumbs/p/a.jpg?v=")
    db.refresh(work)
    assert work.thumb_s3_url == thumb_src
    assert Image.open(io.BytesIO(fake.objects["thumbs/p/a.jpg"])).size == (240, 320)


def test_lightbox_rotation_updates_tile_thumb():
    source = LIGHTBOX.read_text(encoding="utf-8")
    assert "var newThumb = d.thumb_src || newUrl;" in source
    assert "im.src = newThumb;" in source
