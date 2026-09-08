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
from app.constants import (
    TARIFFS,
    TARIFFS_CURRENT,
    TARIFFS_LEGACY,
    TARIFFS_WITH_FEEDBACK,
    TARIFFS_WITH_SUBMISSION,
    TARIFF_CODES,
    TARIFF_DISPLAY,
)
from app.services.s3 import tariff_display

import pytest


@pytest.fixture()
def superadmin_client(client, db, user_factory, session_factory):
    user = user_factory(vk_id=910001, name="Super Admin", role_name="суперадмин")
    sess = session_factory(user)
    client.cookies.set("session_id", sess.id)
    return client, user


# ── состав линейки ──────────────────────────────────────────────────────────

def test_current_lineup_is_the_three_the_owner_named():
    assert TARIFFS_CURRENT == ["Я САМ", "Я С ВАМИ", "УВЕРЕННЫЙ МАКСИМУМ"]


def test_old_values_stay_valid():
    """«Старые записи не трогаем»: на них живые люди, работы и фильтр архива."""
    for legacy in ("МАКСИМУМ", "УВЕРЕННЫЙ"):
        assert legacy in TARIFFS
        assert legacy not in TARIFFS_CURRENT
    assert TARIFFS_LEGACY == ["МАКСИМУМ", "УВЕРЕННЫЙ"]


def test_every_value_has_a_label_and_a_code():
    """Без подписи тариф уедет в путь файла как есть, без кода — уйдёт в n8n
    подстановкой `02`, то есть чужим тарифом."""
    for tariff in TARIFFS:
        assert TARIFF_DISPLAY.get(tariff)
        assert TARIFF_CODES.get(tariff)
    assert len(set(TARIFF_CODES.values())) == len(TARIFF_CODES)


def test_confident_max_inherits_the_rules_of_the_pair_it_replaces():
    """«Уверенный максимум» — преемник пары, обязанной сдавать работу."""
    assert "УВЕРЕННЫЙ МАКСИМУМ" in TARIFFS_WITH_SUBMISSION
    assert "УВЕРЕННЫЙ МАКСИМУМ" in TARIFFS_WITH_FEEDBACK


def test_self_study_tariff_has_no_curator_checking():
    """Созвон 03.09 [00:05:05]: доступ «как у тарифа я с вами или я сам»."""
    assert "Я САМ" not in TARIFFS_WITH_SUBMISSION
    assert "Я САМ" not in TARIFFS_WITH_FEEDBACK
    assert "Я С ВАМИ" not in TARIFFS_WITH_SUBMISSION
    assert "Я С ВАМИ" not in TARIFFS_WITH_FEEDBACK


def test_new_tariffs_make_safe_file_names():
    """Подпись тарифа попадает в имя файла в хранилище."""
    for tariff in TARIFFS:
        name = tariff_display(tariff)
        assert name
        assert not set(name) & set('/\\:*?"<>|')


# ── карточка ученика: что предлагает выпадающий список ──────────────────────

def _card(client, target):
    return client.get(f"/cabinet/superadmin/users/{target.id}")


def test_card_offers_only_the_current_lineup(superadmin_client, db, user_factory):
    student = user_factory(vk_id=910002, name="Новый", role_name="ученик")
    student.tariff = "Я С ВАМИ"
    db.commit()
    client, _ = superadmin_client

    page = _card(client, student).text

    for tariff in TARIFFS_CURRENT:
        assert f'<option value="{tariff}"' in page
    for legacy in TARIFFS_LEGACY:
        assert f'<option value="{legacy}"' not in page


def test_card_of_a_legacy_student_keeps_his_own_tariff(superadmin_client, db, user_factory):
    """Иначе сохранение карточки молча переведёт его на первый пункт списка."""
    student = user_factory(vk_id=910003, name="Старый", role_name="ученик")
    student.tariff = "МАКСИМУМ"
    db.commit()
    client, _ = superadmin_client

    page = _card(client, student).text

    assert '<option value="МАКСИМУМ"' in page
    assert 'value="МАКСИМУМ" selected' in page.replace("  ", " ")
    # Второй отработавший тариф не подмешивается — только собственный.
    assert '<option value="УВЕРЕННЫЙ"' not in page


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

def test_student_profile_offers_only_the_current_lineup(client, db, user_factory, session_factory):
    """Шаг «Тариф обучения» при регистрации — то, что увидит новичок 18 сентября."""
    student = user_factory(vk_id=910005, name="Новичок", role_name="ученик")
    # Анкета первого входа: заполненный профиль роут уводит на «Контакты».
    student.profile_completed = False
    db.commit()
    sess = session_factory(student)
    client.cookies.set("session_id", sess.id)

    page = client.get("/cabinet/profile").text
    assert "Тариф обучения" in page

    for tariff in TARIFFS_CURRENT:
        assert f'value="{TARIFF_DISPLAY[tariff]}"' in page
    for legacy in TARIFFS_LEGACY:
        assert f'value="{TARIFF_DISPLAY[legacy]}"' not in page


def test_profile_labels_survive_the_round_trip():
    """Форма шлёт подпись, сервер приводит её к каноническому значению
    `.upper()`. Разъедься эти два списка — тариф перестал бы сохраняться."""
    from app.api.cabinet_student import TARIFF_LABELS

    assert [label.upper() for label in TARIFF_LABELS] == TARIFFS_CURRENT


def test_day_constructor_offers_only_the_current_lineup(admin_client):
    """Кому виден блок — выбор из действующих."""
    from datetime import timedelta
    from app.services.tz import today_msk

    client, _ = admin_client
    day = (today_msk() + timedelta(days=14)).isoformat()

    page = client.get(f"/cabinet/staff/program/{day}").text

    assert "УВЕРЕННЫЙ МАКСИМУМ" in page
    assert '"МАКСИМУМ"' not in page.replace("УВЕРЕННЫЙ МАКСИМУМ", "")


def test_user_filter_still_finds_the_archive(superadmin_client):
    """Фильтр по людям не сужаем: архив должен оставаться находимым."""
    client, _ = superadmin_client

    page = client.get("/cabinet/superadmin/users").text

    for legacy in TARIFFS_LEGACY:
        assert f'<option value="{legacy}"' in page
