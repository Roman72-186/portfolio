"""Сторожевые тесты: экран /cabinet/periods не предлагает окно «Отработки».

Владелец 09.09.2026: доступ к отработке определяет назначение куратором
(`_has_retake_assignment` в app/api/upload.py), а не окно FeaturePeriod —
пункт в списке фич стал обманкой, даты ставились бы, а доступ они больше
не открывают. Убран из ALL_FEATURES (app/api/cabinet_superadmin.py) и из
_features (app/api/cabinet_admin.py). Старые записи FeaturePeriod с
feature='retake' в базе не удалялись — история, ни на что не влияют.
Возвращать пункт — только по новой дословной просьбе владельца, записанной
в коде.
"""


def test_periods_screen_has_no_retake_section(admin_client):
    client, _user = admin_client

    resp = client.get("/cabinet/periods")

    assert resp.status_code == 200
    assert "Отработки" not in resp.text
    assert "Загрузка портфолио" in resp.text
    assert "Пробные экзамены" in resp.text


def test_periods_screen_hides_old_retake_period_but_keeps_it_in_db(admin_client, db):
    """Старая запись с feature='retake' не удаляется, просто не показывается."""
    from datetime import date, timedelta
    from app.models.feature_period import FeaturePeriod

    client, user = admin_client
    old_period = FeaturePeriod(
        feature="retake",
        start_date=date.today() - timedelta(days=60),
        end_date=date.today() - timedelta(days=30),
        is_active=False,
        created_by_id=user.id,
    )
    db.add(old_period)
    db.commit()

    resp = client.get("/cabinet/periods")

    assert resp.status_code == 200
    assert "Отработки" not in resp.text
    assert db.query(FeaturePeriod).filter(FeaturePeriod.feature == "retake").count() == 1


def test_period_create_rejects_retake_feature(admin_client):
    client, _user = admin_client

    resp = client.post("/cabinet/periods/create", data={
        "feature": "retake",
        "start_date": "2026-09-01",
        "end_date": "2026-09-30",
    })

    assert resp.status_code == 400
