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
    TARIFFS_WITH_FEEDBACK,
    TARIFFS_WITH_SUBMISSION,
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


def test_user_filter_shows_legacy_tariff_only_while_someone_has_it(
    superadmin_client, db, user_factory
):
    """Фильтр перечисляет тарифы по факту (владелец 28.09.2026: «те, которые
    сейчас не задействованы, скрыть, чтобы их нигде не было видно»).

    Прежнее правило 07.09 «фильтр не сужаем» отменено: оно держало в списке
    оба отработавших тарифа всегда, даже когда на них никого не осталось. Но
    архив обязан оставаться находимым, поэтому тариф исчезает из фильтра не по
    коду, а вместе с последним человеком на нём.
    """
    client, _ = superadmin_client

    page = client.get("/cabinet/superadmin/users").text
    assert '<option value="МАКСИМУМ"' not in page

    archived = user_factory(vk_id=910055, name="Прошлый Поток", tariff="МАКСИМУМ")
    archived.archived_at = datetime.now(timezone.utc)
    archived.is_active = False
    db.commit()

    page = client.get("/cabinet/superadmin/users").text
    assert '<option value="МАКСИМУМ"' in page


def test_students_sidebar_uses_current_tariffs_and_marks_students_without_tariff_as_newcomers(
    admin_client, db, user_factory
):
    client, _ = admin_client
    newcomer = user_factory(
        vk_id=901001,
        name="Пробный Новичок",
        role_name="ученик",
        tariff="",
        profile_completed=True,
    )
    regular = user_factory(
        vk_id=901002,
        name="Без Тарифа",
        role_name="ученик",
        tariff="",
        profile_completed=True,
    )
    db.commit()

    page = client.get("/cabinet/students").text
    tariff_filters = page.split('<div class="tariff-pills">', 1)[1].split("</div>", 1)[0]

    for tariff in TARIFFS_CURRENT:
        assert f'data-tariff="{tariff}"' in tariff_filters
        assert f'>{TARIFF_DISPLAY[tariff]}</button>' in tariff_filters
    for legacy in TARIFFS_LEGACY:
        assert f'data-tariff="{legacy}"' not in tariff_filters
    assert 'data-tariff="__newcomer__"' in tariff_filters
    newcomer_row = page.split(f'id="srow-{newcomer.id}"', 1)[1].split("</button>", 1)[0]
    regular_row = page.split(f'id="srow-{regular.id}"', 1)[1].split("</button>", 1)[0]
    assert 'data-tariff="__newcomer__"' in newcomer_row
    assert "Новенький" in newcomer_row
    assert 'data-tariff="__newcomer__"' in regular_row
    assert "Новенький" in regular_row


def test_every_tariff_has_a_colour_group():
    """У каждого валидного тарифа есть цветовая группа для плашки.

    Владелец 13.09.2026: «у каждого тарифа есть свой цвет». Заведут шестой
    тариф без группы — плашка молча станет нейтральной, и на проде это увидят
    не сразу: она белая и читается, просто цвет неверный.
    """
    from app.constants import TARIFF_SLUGS

    assert set(TARIFF_SLUGS) == set(TARIFFS)
    for tariff in TARIFFS_CURRENT:
        assert TARIFF_SLUGS[tariff] != "legacy", tariff


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


def test_student_card_offers_current_lineup_only(admin_client):
    """Список тарифов в карточке ученика был вписан в JS руками: до 28.09.2026
    там стояли МАКСИМУМ с УВЕРЕННЫМ, а новых двух тарифов не было вовсе."""
    client, _ = admin_client

    page = client.get("/cabinet/students").text

    assert "const TARIFF_OPTIONS = " in page
    for tariff in TARIFFS_CURRENT:
        assert tariff in page
    assert "'<option value=\"МАКСИМУМ\"'" not in page
    assert "'<option value=\"УВЕРЕННЫЙ\"'" not in page


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
