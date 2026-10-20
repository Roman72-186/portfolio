"""Линейка тарифов к предобучению (владелец 07.09.2026).

«На предобучении будет 3 тарифа: Я САМ, Я С ВАМИ, УВЕРЕННЫЙ МАКСИМУМ +
новенькие (кто зайдёт на пробный период с 18 по 27 сентября)», и тут же —
**«старые записи не трогаем, всё работаем с новыми»**.

Поэтому переноса данных нет: МАКСИМУМ и УВЕРЕННЫЙ остаются валидными
значениями, на них висят 122 человека и их работы. Новая линейка встаёт
рядом, а назначать можно только её.

Главное, что здесь проверяется, — карточка ученика со старым тарифом. Если в
выпадающем списке не окажется его нынешнего значения, браузер выберет первый
пункт, и обычное сохранение карточки молча переведёт человека с «МАКСИМУМА»
на «Я САМ».
"""
from datetime import datetime, timezone

from app.constants import (
    TARIFFS,
    tariff_choices,
    tariffs_for_data,
    TARIFFS_CURRENT,
    TARIFFS_LEGACY,
    TARIFF_CODES,
    TARIFF_DISPLAY,
)
from app.services.s3 import tariff_display
from app.services.user_management import tariffs_in_use

import pytest


@pytest.fixture()
def superadmin_client(client, db, user_factory, session_factory):
    user = user_factory(vk_id=910001, name="Super Admin", role_name="суперадмин")
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    return client, user


# ── состав линейки ──────────────────────────────────────────────────────────


def test_old_values_stay_valid():
    """«Старые записи не трогаем»: на них живые люди, работы и фильтр архива."""
    for legacy in ("МАКСИМУМ", "УВЕРЕННЫЙ"):
        assert legacy in TARIFFS
        assert legacy not in TARIFFS_CURRENT


def test_every_value_has_a_label_and_a_code():
    """Без подписи тариф уедет в путь файла как есть, без кода — уйдёт в n8n
    подстановкой `02`, то есть чужим тарифом."""
    for tariff in TARIFFS:
        assert TARIFF_DISPLAY.get(tariff)
        assert TARIFF_CODES.get(tariff)
    assert len(set(TARIFF_CODES.values())) == len(TARIFF_CODES)


def test_new_tariffs_make_safe_file_names():
    """Подпись тарифа попадает в имя файла в хранилище."""
    for tariff in TARIFFS:
        name = tariff_display(tariff)
        assert name
        assert not set(name) & set('/\\:*?"<>|')


# ── карточка ученика: что предлагает выпадающий список ──────────────────────

def _manage(client, target) -> dict:
    """Выбор тарифа ученика с 05.10.2026 — в блоке «Управление» карточки
    «Учеников» (`/profile`, поле `manage`), карточка «Людей» туда уводит."""
    resp = client.get(f"/cabinet/students/{target.id}/profile")
    assert resp.status_code == 200, resp.text
    return resp.json()["student"]["manage"]


def test_card_offers_only_the_current_lineup(superadmin_client, db, user_factory):
    student = user_factory(vk_id=910002, name="Новый", role_name="ученик")
    student.tariff = "Я С ВАМИ"
    db.commit()
    client, _ = superadmin_client

    choices = _manage(client, student)["tariff_choices"]

    for tariff in TARIFFS_CURRENT:
        assert tariff in choices
    for legacy in TARIFFS_LEGACY:
        assert legacy not in choices


def test_card_of_a_legacy_student_keeps_his_own_tariff(superadmin_client, db, user_factory):
    """Иначе сохранение карточки молча переведёт его на первый пункт списка."""
    student = user_factory(vk_id=910003, name="Старый", role_name="ученик")
    student.tariff = "МАКСИМУМ"
    db.commit()
    client, _ = superadmin_client

    manage = _manage(client, student)

    assert "МАКСИМУМ" in manage["tariff_choices"]
    assert manage["tariff"] == "МАКСИМУМ"
    # Второй отработавший тариф не подмешивается — только собственный.
    assert "УВЕРЕННЫЙ" not in manage["tariff_choices"]


def test_legacy_value_still_saves(superadmin_client, db, user_factory):
    """Валидация осталась по всему списку: карточка старого ученика сохраняется."""
    student = user_factory(vk_id=910004, name="Старый", role_name="ученик")
    student.tariff = "УВЕРЕННЫЙ"
    db.commit()
    client, _ = superadmin_client

    resp = client.post(
        f"/cabinet/superadmin/users/{student.id}/tags",
        data={"tariff": "УВЕРЕННЫЙ", "csrf_token": "bypass"},
        follow_redirects=False,
    )

    assert resp.status_code in (200, 302, 303)
    db.refresh(student)
    assert student.tariff == "УВЕРЕННЫЙ"


# ── где предлагается выбор, а где остаётся весь список ──────────────────────
#
# Владелец 08.09.2026: «новый учебный год, старых учеников поместили в архив и
# забыли». Отсюда граница: **выбор** сузили до действующей линейки, **поиск и
# проверку** оставили по всему списку — иначе архив станет ненаходимым, а
# карточки живых staff-аккаунтов со старым тарифом перестанут сохраняться.

def test_student_profile_offers_only_the_current_lineup(client, db, user_factory, session_factory, rendered):
    """Шаг «Тариф обучения» при регистрации — то, что увидит новичок 18 сентября."""
    student = user_factory(vk_id=910005, name="Новичок", role_name="ученик")
    # Анкета первого входа: заполненный профиль роут уводит на «Контакты».
    student.profile_completed = False
    db.commit()
    sess = session_factory(student)
    client.cookies.set("session_id", sess.id)

    assert client.get("/cabinet/profile").status_code == 200

    offered = rendered("profile.html")["tariffs"]
    assert offered == [TARIFF_DISPLAY[tariff] for tariff in TARIFFS_CURRENT]
    assert not {TARIFF_DISPLAY[legacy] for legacy in TARIFFS_LEGACY} & set(offered)


def test_profile_labels_survive_the_round_trip():
    """Форма шлёт подпись, сервер приводит её к каноническому значению
    `.upper()`. Разъедься эти два списка — тариф перестал бы сохраняться."""
    from app.api.cabinet_student import TARIFF_LABELS

    assert [label.upper() for label in TARIFF_LABELS] == TARIFFS_CURRENT


def test_unknown_tariff_never_leaves_the_pill_colourless():
    """Пустой и незнакомый тариф красятся нейтральным, а не наследуют белый.

    Плашка белая, надпись берёт цвет из модификатора. Без модификатора текст
    унаследовал бы белый цвет шапки — то есть исчез бы.
    """
    from app.tmpl import tariff_label, tariff_slug

    assert tariff_slug(None) == "legacy"
    assert tariff_slug("") == "legacy"
    assert tariff_slug("ЧТО-ТО НОВОЕ") == "legacy"
    assert tariff_slug("я сам") == "self"          # регистр не важен

    assert tariff_label(None) == ""
    assert tariff_label("УВЕРЕННЫЙ МАКСИМУМ") == "Уверенный максимум"
    assert tariff_label("ЧТО-ТО НОВОЕ") == "ЧТО-ТО НОВОЕ"


# ── Отработавшие тарифы спрятаны, пока никому не назначены ───────────────────
# Владелец 28.09.2026: «те, которые сейчас не задействованы тарифами, те нужно
# скрыть, чтобы их нигде не было видно. Если переиспользовали какой-то тариф —
# оставляем». Отсюда два правила: выбор даём из действующей линейки, а фильтры,
# таблицы и выгрузки перечисляют тарифы по фактическим данным.

def test_tariff_choices_offers_current_lineup():
    assert tariff_choices() == TARIFFS_CURRENT
    assert tariff_choices("") == TARIFFS_CURRENT
    assert tariff_choices(None) == TARIFFS_CURRENT


def test_tariff_choices_keeps_assigned_legacy_value():
    """Свой отработавший тариф остаётся в списке — иначе сохранение карточки
    молча переведёт человека на первый пункт."""
    assert tariff_choices("МАКСИМУМ") == TARIFFS_CURRENT + ["МАКСИМУМ"]
    assert tariff_choices("  УВЕРЕННЫЙ  ") == TARIFFS_CURRENT + ["УВЕРЕННЫЙ"]
    assert tariff_choices("Я С ВАМИ") == TARIFFS_CURRENT
    assert tariff_choices("МАКСИМУМ", "МАКСИМУМ") == TARIFFS_CURRENT + ["МАКСИМУМ"]


def test_tariffs_for_data_adds_legacy_only_when_present():
    assert tariffs_for_data([]) == TARIFFS_CURRENT
    assert tariffs_for_data(["Я С ВАМИ", None, ""]) == TARIFFS_CURRENT
    assert tariffs_for_data(["МАКСИМУМ"]) == TARIFFS_CURRENT + ["МАКСИМУМ"]
    assert tariffs_for_data(["УВЕРЕННЫЙ", "МАКСИМУМ", "МАКСИМУМ"]) == (
        TARIFFS_CURRENT + ["МАКСИМУМ", "УВЕРЕННЫЙ"]
    )


def test_tariffs_in_use_reads_living_rows(db, user_factory):
    """Удалённых не считаем, архив считаем: по архиву как раз и фильтруют."""
    user_factory(vk_id=910101, tariff="МАКСИМУМ").archived_at = datetime.now(timezone.utc)
    deleted = user_factory(vk_id=910102, tariff="УВЕРЕННЫЙ")
    deleted.deleted_at = datetime.now(timezone.utc)
    db.commit()

    in_use = tariffs_in_use(db)

    assert "МАКСИМУМ" in in_use
    assert "УВЕРЕННЫЙ" not in in_use
    assert in_use[:3] == TARIFFS_CURRENT


def test_stats_export_has_no_sheets_for_unused_legacy_tariffs(superadmin_client):
    """Выгрузка статистики рисовала лист на каждый тариф из справочника — в
    каждом отчёте нового потока висели пустые «МАКСИМУМ» и «УВЕРЕННЫЙ»."""
    import io

    import openpyxl

    client, _ = superadmin_client

    resp = client.get("/cabinet/superadmin/stats/export")

    assert resp.status_code == 200, resp.text
    sheets = openpyxl.load_workbook(io.BytesIO(resp.content)).sheetnames
    for tariff in TARIFFS_CURRENT:
        assert tariff[:31] in sheets
    for legacy in TARIFFS_LEGACY:
        assert legacy not in sheets
        assert f"Не сдали — {legacy}"[:31] not in sheets
