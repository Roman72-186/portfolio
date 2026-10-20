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


def test_period_create_rejects_retake_feature(admin_client):
    client, _user = admin_client

    resp = client.post("/cabinet/periods/create", data={
        "feature": "retake",
        "start_date": "2026-09-01",
        "end_date": "2026-09-30",
    })

    assert resp.status_code == 400
