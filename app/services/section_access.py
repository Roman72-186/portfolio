"""Доступ сотрудников к разделам кабинета (владелец 30.09, 03.10 и 04.10.2026).

Суперадмин задаёт сотрудникам уровень в каждом разделе — всей роли сразу или
одному человеку: «Нет» (`none`), «Смотреть» (`view`), «Менять» (`edit`).
Любой уровень доступен любой настраиваемой роли, в том числе сверх её ранга
(владелец 03.10.2026: «всем ролям по максимум добавить поля с доступами, а я
уже буду решать, выдавать или нет»). Без правила действует уровень по
умолчанию (`native_level`): то, что роль может сейчас, — поэтому уровни
ничего не меняют, пока их не тронули.

Как устроено поверх прежних слоёв (`judge_request`, зовёт `get_current_user`):

1. Ранг роли (`require_*` в `app/dependencies.py`) — по-прежнему потолок для
   всего, где суперадмин не поднял уровень выше положенного роли.
2. Уровень запроса — по методу: GET и HEAD требуют «Смотреть», остальное —
   «Менять». Запрос к разделу, где уровня не хватает, отвечает 403: раздел
   закрыт (`SECTION_CLOSED_DETAIL`, пропадает из меню) или открыт только на
   просмотр (`SECTION_VIEW_ONLY_DETAIL`). Раздел вне роли без правила 403 от
   этого слоя не получает — его держит ранг, как раньше.
3. Уровень выше положенного роли поднимает ранг на запрос до
   `Section.min_rank`, и прежние `require_*` пропускают сами — 450 проверок
   ранга переписывать не нужно. «Смотреть» поднимает только на GET,
   «Менять» — на всех методах. Подъём действует только на этот запрос и
   только на адреса раздела: соседние разделы, общие адреса и меню живут по
   родному рангу (`user["nav_rank"]`).
4. Модератор — наблюдатель (28.09.2026): положенные ему разделы по умолчанию
   на просмотр, и всё, что суперадмин не поднял выше, держит белый список
   `rbac.py`. Поднятый уровень открывает модератору раздел так же, как
   остальным: «Смотреть» — чтение, «Менять» — работу (прод 04.10.2026: АОП
   ролью — смотреть, лично Александрии — менять). Ранг модератору поднимать
   не нужно: его уровень и так равен ГП (`rbac.effective_role_rank`).

Балл подъём ранга не даёт: адреса с `score` в конце (`rbac.is_score_request`)
остаются за родным рангом — правило 30.09.2026 «балл ставит только Главный
преподаватель» сильнее переключателя.

`Section.elevates = False` у раздела, который код открывает сам через
`has_grant` (архив куратору: весь архив школы, но не чужие действующие
ученики). Подъём ранга там открыл бы карточки всех учеников.

Настраиваются только сотрудники: куратор, модератор, Главный преподаватель.
Ученика держат срок доступа и гейты, суперадмина не закрывает ничто — иначе
он запер бы сам себя. Экран «Доступы» не входит ни в один раздел и требует
ранга суперадмина: открыть его себе нельзя.

**Общие адреса не входят ни в один раздел.** Экран «Пробники» и диалог
обратной связи ставят балл и отправляют на доработку через адреса карточки
ученика (`/cabinet/students/{id}/works/{id}/score`, `.../revision`), видео и
задания встроены в чужие экраны. Закрой их префиксом — закрытие одного
раздела сломает кнопки в другом (тот же урок, что у
`PORTFOLIO_GATE_BLOCKED_EXACT` в `dependencies.py`). Поэтому у адреса бывает
несколько разделов-владельцев, и закрыт он, только когда закрыты все они:
карточку ученика открывают и «Ученики», и «Архив». Диалоги обратной связи,
переписка по домашкам и сдачам блоков, уведомления и главная не закрываются
никогда. Подъём ранга на общих адресах тоже не срабатывает.

Новый раздел — строка в `SECTIONS` и правила в `_RULES`. Тест
`tests/test_section_access.py` проверяет, что каждое правило совпадает хотя бы
с одним живым маршрутом: переименовали адрес — тест покраснеет, а не молча
перестанет закрывать.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import or_
from sqlalchemy.orm import Session as DBSession

from app.models.audit_log import AuditLog
from app.models.role import Role
from app.models.section_access import SectionAccessRule
from app.models.user import User
from app.services.rbac import is_score_request

SECTION_CLOSED_DETAIL = "Раздел закрыт суперадмином"
SECTION_VIEW_ONLY_DETAIL = "Раздел открыт только на просмотр"

ROLE_CURATOR = "куратор"
ROLE_MODERATOR = "модератор"
ROLE_HEAD = "админ"  # в интерфейсе — «Главный преподаватель»

# Порядок — порядок столбцов на странице «Доступы».
CONFIGURABLE_ROLES: tuple[str, ...] = (ROLE_CURATOR, ROLE_MODERATOR, ROLE_HEAD)

LEVEL_NONE = "none"
LEVEL_VIEW = "view"
LEVEL_EDIT = "edit"
LEVELS: tuple[str, ...] = (LEVEL_NONE, LEVEL_VIEW, LEVEL_EDIT)
_LEVEL_ORDER = {level: i for i, level in enumerate(LEVELS)}


def at_least(level: str, needed: str) -> bool:
    return _LEVEL_ORDER.get(level, 0) >= _LEVEL_ORDER[needed]


def request_level(method: str) -> str:
    """Какой уровень нужен запросу: чтение — «Смотреть», остальное — «Менять»."""
    return LEVEL_VIEW if method in ("GET", "HEAD") else LEVEL_EDIT

# Личное правило в карточке сотрудника: «как у роли» или один из уровней.
USER_STATE_ROLE = "role"


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    hint: str
    # Роли, которым раздел положен по рангу: без правила он у них открыт
    # (`native_level`), у остальных закрыт. Открыть сверх роли можно любой
    # раздел любой роли.
    roles: tuple[str, ...]
    # Пункты меню (`navigation.py`), которые пропадают вместе с разделом и
    # появляются, когда его открыли сверх роли.
    nav_keys: tuple[str, ...] = ()
    # Ранг, с которым сотрудник работает в разделе, открытом сверх роли:
    # столько требуют `require_*` адресов раздела.
    min_rank: int = 4
    # False — раздел, который код открывает сам через `has_grant`, без подъёма
    # ранга (архив куратору). См. докстринг модуля.
    elevates: bool = True


SECTIONS: tuple[Section, ...] = (
    Section(
        "students", "Ученики", "Список учеников и карточка ученика",
        (ROLE_CURATOR, ROLE_MODERATOR, ROLE_HEAD), ("students",), min_rank=2,
    ),
    Section(
        "students_review", "Проверка по ученику", "Разбор всего, что сдал ученик, за один заход",
        (ROLE_CURATOR, ROLE_HEAD), ("students_review",), min_rank=2,
    ),
    Section(
        "mock_check", "Проверка пробников", "Пробники и отработки на проверке",
        (ROLE_HEAD,), ("mock_check",),
    ),
    Section(
        "point_a", "Оценка точки А", "Входной замер уровня ученика",
        (ROLE_HEAD,), ("point_a",),
    ),
    Section(
        "program", "Актуальное образовательное пространство",
        "Учебные программы, конструктор недели, трекер, цели, дайджест, загрузка видео",
        (ROLE_HEAD,), ("program",),
    ),
    Section(
        "reports", "Видео-отчёты", "Отчёты кураторов",
        (ROLE_CURATOR, ROLE_HEAD), ("reports",), min_rank=2,
    ),
    Section(
        "archive", "Архив учеников", "Прошлые потоки, только просмотр",
        (ROLE_MODERATOR, ROLE_HEAD), ("archive",),
        # Куратору, которому открыт архив, — весь архив школы, только на
        # чтение (владелец 30.09.2026). Среди действующих — по-прежнему только
        # свои: это держит `has_grant` в коде раздела, а не подъём ранга.
        elevates=False,
    ),
    Section(
        "statistics", "Статистика", "Статистика активности, выгрузки, статистика циклов",
        (ROLE_CURATOR, ROLE_MODERATOR, ROLE_HEAD), ("statistics", "activity"),
    ),
    Section(
        "guest_exam", "Гостевой пробник", "Ссылка, билеты и работы гостей",
        (ROLE_HEAD,), ("guest_exam",),
    ),
    Section(
        "exams", "Билеты и периоды", "Задания пробников и периоды сдачи",
        (ROLE_HEAD,), ("exams",),
    ),
    Section(
        "lab3d", "3D Лаб", "3D-лаборатория",
        (ROLE_CURATOR, ROLE_HEAD), ("3dlab",), min_rank=2,
    ),
    Section(
        "people", "Люди и доступы",
        "Пользователи, выдача входа, кураторы, теги, вход «глазами»",
        (ROLE_HEAD,), ("people",),
    ),
)

SECTIONS_BY_KEY: dict[str, Section] = {s.key: s for s in SECTIONS}


@dataclass(frozen=True)
class _Rule:
    owners: tuple[str, ...]
    pattern: re.Pattern
    methods: frozenset[str] | None = None
    # (параметр, значение): правило срабатывает, только если query совпал.
    query: tuple[str, str] | None = None


def _exact(path: str) -> str:
    return "^" + re.escape(path) + "$"


def _tree(path: str) -> str:
    """Сам адрес и всё под ним. Граница по сегменту, как `rbac.py::_in_section`:
    `/cabinet/students-x` за `/cabinet/students` не проходит."""
    return "^" + re.escape(path) + "(/.*)?$"


def _rule(owners, pattern: str, methods=None, query=None) -> _Rule:
    if isinstance(owners, str):
        owners = (owners,)
    return _Rule(
        owners=tuple(owners),
        pattern=re.compile(pattern),
        methods=frozenset(methods) if methods else None,
        query=query,
    )


_GET = ("GET", "HEAD")
_STUDENT = r"^/cabinet/students/\d+"

# Порядок важен: срабатывает первое совпадение, поэтому частное — выше общего
# (статистика цикла выше программ, вкладка статистики выше списка учеников).
_RULES: tuple[_Rule, ...] = (
    # ── Статистика ──
    _rule("statistics", _exact("/cabinet/students"), _GET, ("tab", "statistics")),
    _rule("statistics", _tree("/cabinet/superadmin/activity")),
    _rule("statistics", _tree("/cabinet/superadmin/stats")),
    _rule("statistics", _exact("/cabinet/admin/registration-stats.csv")),
    _rule("statistics", _exact("/cabinet/superadmin/registration-stats.csv")),
    _rule("statistics", r"^/cabinet/staff/program/cycles/\d+/stats$"),
    # ── Ученики ──
    _rule("students", _exact("/cabinet/students"), _GET),
    _rule("students", _tree("/cabinet/admin/students")),
    _rule("students", _tree("/cabinet/curator/portfolio")),
    _rule("students", _tree("/cabinet/curator/mock-exams")),
    _rule("students", _STUDENT + "$"),
    _rule("students", _STUDENT + "/profile$", ("POST",)),
    _rule("students", _STUDENT + "/upload$"),
    _rule("students", _STUDENT + "/works/bulk$"),
    _rule("students", _STUDENT + r"/portfolio/(month|works/\d+/move)$"),
    # Карточка ученика: её вкладки грузит и список учеников, и архив.
    _rule(
        ("students", "archive"),
        _STUDENT + "/(profile|tasks|portfolio|statistics|legacy-portfolio)$", _GET,
    ),
    # Пробники ученика читает ещё и экран «Проверка пробников».
    _rule(("students", "archive", "mock_check"), _STUDENT + "/mock-exams$", _GET),
    # ── Архив ──
    _rule("archive", _tree("/cabinet/archive")),
    # ── Проверка ──
    _rule("students_review", _tree("/cabinet/staff/students-review")),
    _rule("mock_check", _tree("/cabinet/admin/mock-check")),
    _rule("mock_check", _tree("/cabinet/admin/retake-check")),
    _rule("point_a", _tree("/cabinet/staff/point-a")),
    _rule("point_a", _tree("/cabinet/staff/point-a-audio")),
    # ── Учебные программы ──
    _rule("program", _tree("/cabinet/staff/program")),
    _rule("program", _tree("/cabinet/staff/tracker")),
    _rule("program", _tree("/cabinet/staff/digest")),
    _rule("program", _tree("/cabinet/staff/goals")),
    _rule("program", _tree("/cabinet/admin/videos")),
    # ── Остальное ──
    _rule("reports", _tree("/cabinet/curator/reports")),
    _rule("guest_exam", _tree("/cabinet/staff/guest-exam")),
    _rule("exams", _tree("/cabinet/exam-assignments")),
    _rule("exams", _tree("/cabinet/superadmin/exam-assignments")),
    _rule("exams", _tree("/cabinet/periods")),
    _rule("exams", _tree("/cabinet/intake")),
    # Фото билета грузит и редактор дня программы (блок пробника) — без
    # «program» в владельцах кнопка не работала бы у сотрудника, которому
    # открыт только АОП.
    _rule(("exams", "program"), _exact("/cabinet/upload-ticket-image")),
    _rule("lab3d", _exact("/3dlab")),
    _rule("lab3d", _tree("/cabinet/3dlab")),
    _rule("lab3d", _tree("/lab")),
    _rule("people", _tree("/cabinet/superadmin/users")),
    _rule("people", _tree("/cabinet/superadmin/create-staff")),
    _rule("people", _tree("/cabinet/superadmin/assign-curator")),
    _rule("people", _tree("/cabinet/superadmin/curators")),
    _rule("people", _tree("/cabinet/superadmin/tags")),
    _rule("people", _exact("/cabinet/superadmin/set-credentials")),
    _rule("people", _exact("/cabinet/superadmin/issue-link")),
    # Выход из режима «глазами» (`/impersonate/stop`) сюда не попадает.
    _rule("people", r"^/cabinet/superadmin/impersonate/\d+$"),
)


def section_owners(method: str, path: str, query_params) -> tuple[str, ...]:
    """Разделы, которым принадлежит запрос. Пусто — адрес общий, не закрывается."""
    for rule in _RULES:
        if rule.methods is not None and method not in rule.methods:
            continue
        if rule.query is not None:
            name, value = rule.query
            if query_params.get(name) != value:
                continue
        if rule.pattern.match(path):
            return rule.owners
    return ()


# ── Действия внутри разделов (шаг 4 плана, 05.10.2026) ──────────────────────
#
# Действие — отдельное право поверх уровня раздела: «можно» (`edit`) или
# «нельзя» (`none`), строка той же `section_access_rules` с ключом действия.
# Запрос к адресу действия решает действие, а не раздел (`judge_request`).
#
# Без своего правила действие с `inherits=True` повторяет раздел: «Менять» в
# разделе — можно. Так ГП после выкатки делает в «Людях» ровно то же, что
# раньше. Действие с `inherits=False` — бывшее «только суперадмин»: без
# правила закрыто у всех трёх ролей (владелец 04.10.2026: «добавить все
# правила по максимуму… но отметить те, что есть сейчас»).
#
# Потолок «роль и вход «глазами» — только ниже своей» в настройку не входит:
# его держат `can_assign_role_rank`, `can_manage_user_by_rank`,
# `can_impersonate_by_rank` по рангу запроса.


@dataclass(frozen=True)
class Action:
    key: str
    label: str
    # Раздел, под строкой которого действие стоит на экране; None — общий
    # адрес вне разделов.
    section: str | None
    inherits: bool
    # Ранг на запрос, если действие открыто сверх положенного роли.
    min_rank: int = 4
    # Доступ к чужим аккаунтам — экран просит подтвердить.
    risky: bool = False


ACTIONS: tuple[Action, ...] = (
    Action("people:students", "Куратор, тариф, когорта и теги ученика", "people", True),
    Action("people:block", "Заблокировать и удалить", "people", True),
    Action("people:role", "Менять роль", "people", True, risky=True),
    Action("people:login", "Логин и пароль, ссылки входа", "people", True, risky=True),
    Action("people:impersonate", "Вход «глазами»", "people", True, risky=True),
    Action("people:create", "Создавать учеников и сотрудников", "people", False, risky=True),
    Action("people:archive", "Отправить в архив и вернуть", "people", False),
    Action("people:hard_delete", "Удалить ученика навсегда", "people", False),
    Action(
        "students:portfolio_months", "Месяцы портфолио: переименовать, перенести работу",
        "students", False,
    ),
    Action("program:video_delete", "Удалить видео", "program", False),
    Action("guest_exam:participants", "Участники гостевого пробника", "guest_exam", False),
    Action(
        "feedback:dialogs", "Диалоги обратной связи: удалить, переоткрыть, вернуть куратору",
        None, False,
    ),
)

ACTIONS_BY_KEY: dict[str, Action] = {a.key: a for a in ACTIONS}

ACTION_CLOSED_DETAIL = "Действие закрыто суперадмином"

_USER = r"^/cabinet/superadmin/users/\d+"
_POST = ("POST",)

# Первое совпадение решает; адреса действий не пересекаются.
_ACTION_RULES: tuple[tuple[str, re.Pattern, frozenset[str]], ...] = tuple(
    (key, re.compile(pattern), frozenset(methods))
    for key, pattern, methods in (
        ("people:students", _USER + "/(tags|curator|tariff|cohort-tag)$", _POST),
        ("people:students", _exact("/cabinet/superadmin/users/assign-curator-bulk"), _POST),
        ("people:students", r"^/cabinet/superadmin/tags/\d+$", _POST),
        # Поиск по списку ников — часть массовой проставки тегов, POST на чтение.
        ("people:students", _exact("/cabinet/superadmin/tags/bulk-lookup"), _POST),
        ("people:students", r"^/cabinet/superadmin/tags/\d+/\d+$", ("DELETE",)),
        ("people:block", _USER + "/(toggle-active|delete)$", _POST),
        ("people:role", _USER + "/role$", _POST),
        ("people:login", _USER + "/(set-credentials|issue-link|issue-telegram-link)$", _POST),
        ("people:login", _exact("/cabinet/superadmin/set-credentials"), _POST),
        ("people:login", _exact("/cabinet/superadmin/issue-link"), _POST),
        ("people:impersonate", r"^/cabinet/superadmin/impersonate/\d+$", _POST),
        ("people:create", _exact("/cabinet/superadmin/create-staff"), ("GET", "HEAD")),
        ("people:create", _exact("/cabinet/superadmin/users/create-student"), _POST),
        ("people:create", _exact("/cabinet/superadmin/users/create-staff"), _POST),
        ("people:archive", _USER + "/(archive|unarchive)$", _POST),
        ("people:hard_delete", _USER + "/hard-delete$", _POST),
        ("students:portfolio_months", _STUDENT + r"/portfolio/(month|works/\d+/move)$", ("PATCH",)),
        ("program:video_delete", r"^/cabinet/admin/videos/\d+/delete$", _POST),
        ("guest_exam:participants", r"^/cabinet/staff/guest-exam/participants/\d+/delete$", _POST),
        ("feedback:dialogs", r"^/cabinet/superadmin/feedback/\d+/(delete|reopen|return-to-curator)$", _POST),
    )
)


def action_of(method: str, path: str) -> Action | None:
    """Действие, которому принадлежит запрос, или None."""
    for key, pattern, methods in _ACTION_RULES:
        if method in methods and pattern.match(path):
            return ACTIONS_BY_KEY[key]
    return None


def inherited_action_level(action: Action, section_level: str | None) -> str:
    """Уровень действия без своего правила: повторяет «Менять» раздела или закрыт."""
    if action.inherits and section_level == LEVEL_EDIT:
        return LEVEL_EDIT
    return LEVEL_NONE


def native_action_level(action: Action, role_name: str | None) -> str:
    """Что действие у роли сегодня, без единой строки в базе."""
    section = SECTIONS_BY_KEY.get(action.section) if action.section else None
    return inherited_action_level(action, native_level(section, role_name) if section else None)


def is_configurable_role(role_name: str | None) -> bool:
    return role_name in CONFIGURABLE_ROLES


def is_native(section: Section, role_name: str | None) -> bool:
    """Положен ли раздел роли по рангу — открыт ли он ей без правил."""
    return role_name in section.roles


def native_level(section: Section, role_name: str | None) -> str:
    """Уровень роли в разделе без правил — то, что она может сейчас.

    Модератор — наблюдатель (28.09.2026): положенные ему разделы («Ученики»,
    архив, статистика) он только смотрит, адреса держит белый список
    `rbac.py`. Остальным положенный раздел открыт целиком, внутри его делит
    ранг."""
    if not is_native(section, role_name):
        return LEVEL_NONE
    # В разделе без адресов на запись «Менять» не к чему: положенное — смотреть.
    if role_name == ROLE_MODERATOR or section.key in VIEW_ONLY_SECTIONS:
        return LEVEL_VIEW
    return LEVEL_EDIT


@dataclass(frozen=True)
class RequestAccess:
    """Что слой разделов решил про один запрос (`judge_request`)."""
    # Причина отказа 403 или None, если слой разделов запрос не держит.
    refusal: str | None = None
    # Раздел, из-за которого отказ, — для строки в логе.
    refused_section: str | None = None
    # Запрос пускает раздел, где уровень поднят выше положенного роли. Для
    # модератора это второй путь мимо белого списка `rbac.py`.
    raised: bool = False
    # Ранг на этот запрос: `Section.min_rank` поднятого раздела, 0 — без подъёма.
    rank: int = 0


def judge_request(
    method: str, path: str, query_params, levels: dict[str, str], role_name: str | None,
) -> RequestAccess:
    """Решение слоя разделов по запросу. `levels` — уровни сотрудника
    (`resolve_levels`); пусто — роль не настраивается, слой молчит.

    Адрес бывает у нескольких разделов сразу (карточка ученика — у «Учеников»
    и «Архива»): запрос пускает любой из них, где уровня хватает. Отказ —
    только когда не хватает во всех и каждый из них суперадмин ограничил:
    положенный роли или открытый сверх роли на просмотр. Раздел вне роли без
    правила держит ранг, как до переключателей."""
    if not levels:
        return RequestAccess()
    action = action_of(method, path)
    if action is not None:
        if levels.get(action.key) != LEVEL_EDIT:
            return RequestAccess(refusal=ACTION_CLOSED_DETAIL, refused_section=action.key)
        raised = native_action_level(action, role_name) != LEVEL_EDIT
        return RequestAccess(raised=raised, rank=action.min_rank if raised else 0)
    owners = section_owners(method, path, query_params)
    if not owners:
        return RequestAccess()
    needed = request_level(method)
    allowing = [key for key in owners if at_least(levels.get(key, LEVEL_NONE), needed)]
    if not allowing:
        restricted = all(
            is_native(SECTIONS_BY_KEY[key], role_name) or levels.get(key) != LEVEL_NONE
            for key in owners
        )
        if not restricted:
            return RequestAccess()
        view_only = any(levels.get(key) == LEVEL_VIEW for key in owners)
        return RequestAccess(
            refusal=SECTION_VIEW_ONLY_DETAIL if view_only else SECTION_CLOSED_DETAIL,
            refused_section=owners[0],
        )
    raised = [
        key for key in allowing
        if not at_least(native_level(SECTIONS_BY_KEY[key], role_name), levels[key])
    ]
    rank = 0
    # Балл подъём ранга не даёт: правило 30.09.2026 «балл ставит только ГП»
    # сильнее переключателя.
    if not is_score_request(method, path):
        rank = max(
            (SECTIONS_BY_KEY[key].min_rank for key in raised if SECTIONS_BY_KEY[key].elevates),
            default=0,
        )
    return RequestAccess(raised=bool(raised), rank=rank)


def can(user: dict, section_key: str, level: str = LEVEL_EDIT) -> bool:
    """Открыт ли сотруднику раздел на этом уровне — для кнопок в шаблонах,
    чтобы кнопка спрашивала то же, что сервер. Суперадмина не ограничивает
    ничто; у ученика уровней нет."""
    levels = user.get("section_levels") or {}
    if section_key not in levels:
        return (user.get("nav_rank") or user.get("role_rank") or 0) >= 5
    return at_least(levels[section_key], level)


def _rules_for(db: DBSession, *, user_id: int | None, role_id: int | None):
    conds = []
    if user_id is not None:
        conds.append(SectionAccessRule.user_id == user_id)
    if role_id is not None:
        conds.append(SectionAccessRule.role_id == role_id)
    if not conds:
        return []
    return db.query(SectionAccessRule).filter(or_(*conds)).all()


def resolve_levels(
    db: DBSession, *, user_id: int, role_id: int | None, role_name: str | None,
) -> dict[str, str]:
    """{раздел или действие: уровень} сотрудника по всему каталогу.

    Порядок: личное правило, затем правило роли, затем уровень по умолчанию
    (`native_level` у раздела, `inherited_action_level` от уже найденного
    уровня раздела у действия). Ключи, которых нет в каталоге (раздел
    переименовали или убрали), и незнакомые уровни пропускаются — старая
    строка в базе не должна ронять вход."""
    by_role: dict[str, str] = {}
    by_user: dict[str, str] = {}
    for row in _rules_for(db, user_id=user_id, role_id=role_id):
        if row.level not in LEVELS:
            continue
        if row.section_key not in SECTIONS_BY_KEY and row.section_key not in ACTIONS_BY_KEY:
            continue
        if row.user_id is not None:
            by_user[row.section_key] = row.level
        else:
            by_role[row.section_key] = row.level
    levels = {
        s.key: by_user.get(s.key, by_role.get(s.key, native_level(s, role_name)))
        for s in SECTIONS
    }
    for a in ACTIONS:
        levels[a.key] = _action_level(by_user.get(a.key, by_role.get(a.key)), a, levels)
    return levels


def _action_level(stored: str | None, action: Action, section_levels: dict[str, str]) -> str:
    """Уровень действия: своё правило (только «можно» или «нельзя») или
    уровень по умолчанию от раздела."""
    if stored == LEVEL_EDIT or stored == LEVEL_NONE:
        return stored
    return inherited_action_level(action, section_levels.get(action.section) if action.section else None)


def closed_sections(levels: dict[str, str], role_name: str | None) -> frozenset[str]:
    """Положенные роли разделы, закрытые правилом: пропадают из меню."""
    return frozenset(
        s.key for s in SECTIONS
        if is_native(s, role_name) and levels.get(s.key) == LEVEL_NONE
    )


def granted_sections(levels: dict[str, str], role_name: str | None) -> frozenset[str]:
    """Разделы вне роли, открытые правилом хотя бы на просмотр: появляются в
    меню, их спрашивает `has_grant`."""
    return frozenset(
        s.key for s in SECTIONS
        if not is_native(s, role_name) and levels.get(s.key, LEVEL_NONE) != LEVEL_NONE
    )


def has_grant(user: dict, section_key: str) -> bool:
    """Открыт ли раздел сотруднику сверх роли. Спрашивают проверки раздела с
    `elevates=False` рядом с проверкой ранга: `rank >= 4 or has_grant(...)`."""
    return section_key in (user.get("granted_sections") or ())


def closed_nav_keys(closed: frozenset[str] | None) -> frozenset[str]:
    """Пункты меню, которые прячутся вместе с закрытыми разделами."""
    if not closed:
        return frozenset()
    return frozenset(
        nav_key
        for key in closed if key in SECTIONS_BY_KEY
        for nav_key in SECTIONS_BY_KEY[key].nav_keys
    )


def granted_nav_keys(granted: frozenset[str] | None) -> tuple[str, ...]:
    """Пункты меню разделов, открытых сверх роли, в порядке `SECTIONS`."""
    if not granted:
        return ()
    return tuple(
        nav_key
        for s in SECTIONS if s.key in granted
        for nav_key in s.nav_keys
    )


# ── Настройка суперадмином ──────────────────────────────────────────────────
#
# Экран «Доступы» и блок «Доступ к разделам» карточки (шаг 3 плана
# `plans/2026-10-04-apparchi-тонкие-доступы.md`): в каждой ячейке выбор
# «Нет · Смотреть · Менять». Форма шлёт каждую ячейку явно; незнакомая или
# пропущенная ячейка не меняется — обрезанная форма не должна разом закрыть
# всё всем.

LEVEL_LABELS = {LEVEL_NONE: "нет", LEVEL_VIEW: "смотреть", LEVEL_EDIT: "менять"}
# Подписи на экране и в карточке: кнопки-сегменты и пункты списка.
LEVEL_TITLES = {LEVEL_NONE: "Нет", LEVEL_VIEW: "Смотреть", LEVEL_EDIT: "Менять"}
# У действия два состояния — галочка на экране, «Можно / Нельзя» в карточке.
ACTION_LABELS = {LEVEL_NONE: "нельзя", LEVEL_EDIT: "можно"}
ACTION_TITLES = {LEVEL_NONE: "Нельзя", LEVEL_EDIT: "Можно"}
ACTION_LEVELS = (LEVEL_NONE, LEVEL_EDIT)


def _label(key: str, level: str) -> str:
    return (ACTION_LABELS if key in ACTIONS_BY_KEY else LEVEL_LABELS)[level]

# Разделы, где нет ни одного адреса на запись: «Менять» им не к чему, на экране
# два уровня. Снимок тот же, что в миграции `639c04979ebf`; появится адрес на
# запись — покраснеет `tests/test_section_levels.py`.
VIEW_ONLY_SECTIONS = frozenset({"archive", "statistics", "mock_check", "lab3d"})

# Разделы, открывающие доступ к чужим аккаунтам: роль, логин и пароль, ссылка
# входа, вход «глазами». Поднять их выше положенного роли экран просит
# подтвердить (развилка 4 плана).
RISKY_SECTIONS = frozenset({"people"})


def levels_of(section: Section) -> tuple[str, ...]:
    """Уровни, которые экран предлагает в этом разделе."""
    return (LEVEL_NONE, LEVEL_VIEW) if section.key in VIEW_ONLY_SECTIONS else LEVELS


def is_risky(section: Section, role_name: str | None, level: str) -> bool:
    """Открывает ли уровень роли доступ к чужим аккаунтам сверх положенного."""
    return section.key in RISKY_SECTIONS and not at_least(native_level(section, role_name), level)


def _clean_level(section: Section, level: str | None) -> str | None:
    """Уровень из формы или None, если он незнакомый. «Менять» в разделе без
    записи — «Смотреть»: там нечего менять, а ячейка не должна врать."""
    if level not in LEVELS:
        return None
    if level not in levels_of(section):
        return LEVEL_VIEW
    return level


def _configurable_roles(db: DBSession) -> dict[str, Role]:
    rows = db.query(Role).filter(Role.name.in_(CONFIGURABLE_ROLES)).all()
    return {r.name: r for r in rows}


def role_levels(db: DBSession) -> dict[str, dict[str, str]]:
    """{роль: {раздел: уровень}} — все разделы у всех настраиваемых ролей."""
    roles = _configurable_roles(db)
    stored: dict[int, dict[str, str]] = {}
    if roles:
        rows = (
            db.query(SectionAccessRule)
            .filter(SectionAccessRule.role_id.in_([r.id for r in roles.values()]))
            .all()
        )
        for row in rows:
            if row.level in LEVELS:
                stored.setdefault(row.role_id, {})[row.section_key] = row.level
    result: dict[str, dict[str, str]] = {}
    for name in CONFIGURABLE_ROLES:
        role = roles.get(name)
        role_rows = stored.get(role.id, {}) if role else {}
        levels = {s.key: role_rows.get(s.key, native_level(s, name)) for s in SECTIONS}
        for a in ACTIONS:
            levels[a.key] = _action_level(role_rows.get(a.key), a, levels)
        result[name] = levels
    return result


def _audit(db: DBSession, *, action: str, actor_id: int, target_user_id: int | None, details: str) -> None:
    db.add(AuditLog(
        action=action,
        performed_by_id=actor_id,
        target_user_id=target_user_id,
        details=details[:1000],
    ))


def _put_rule(db: DBSession, *, key: str, level: str | None, actor_id: int,
              role_id: int | None = None, user_id: int | None = None) -> None:
    """Записать строку правила или убрать её (`level=None`)."""
    query = db.query(SectionAccessRule).filter(SectionAccessRule.section_key == key)
    query = query.filter(
        SectionAccessRule.role_id == role_id if role_id is not None
        else SectionAccessRule.user_id == user_id
    )
    row = query.first()
    if level is None:
        if row is not None:
            db.delete(row)
        return
    if row is None:
        db.add(SectionAccessRule(
            section_key=key, role_id=role_id, user_id=user_id, level=level, updated_by_id=actor_id,
        ))
    elif row.level != level:
        row.level = level
        row.updated_by_id = actor_id


def save_role_levels(db: DBSession, *, actor_id: int, desired: dict[str, dict[str, str]]) -> int:
    """Сохранить таблицу «разделы × роли» вместе с действиями. Возвращает
    число изменений.

    `desired` — {роль: {раздел или действие: уровень}}; незнакомые роли,
    ключи и уровни молча пропускаются, ключа, которого нет в `desired`,
    правка не касается.

    Строка в базе хранится, только пока уровень расходится с уровнем по
    умолчанию: у раздела — `native_level`, у действия — то, что даёт раздел
    после этого же сохранения. Вернули как по умолчанию — строка удаляется."""
    roles = _configurable_roles(db)
    current = role_levels(db)
    stored = {
        (row.role_id, row.section_key): row.level
        for row in db.query(SectionAccessRule).filter(SectionAccessRule.role_id.isnot(None)).all()
    }
    changes = 0
    for name in CONFIGURABLE_ROLES:
        role = roles.get(name)
        if role is None:
            continue
        wanted = desired.get(name, {})
        new_levels = dict(current[name])
        for section in SECTIONS:
            level_now = current[name][section.key]
            level = _clean_level(section, wanted.get(section.key))
            if level is None or level == level_now:
                continue
            new_levels[section.key] = level
            _put_rule(
                db, key=section.key, role_id=role.id, actor_id=actor_id,
                level=None if level == native_level(section, name) else level,
            )
            extra = "" if is_native(section, name) else " (сверх роли)"
            _audit(
                db, action="section_access_role", actor_id=actor_id, target_user_id=None,
                details=(
                    f"роль «{role.display_name}», раздел «{section.label}»{extra}: "
                    f"{LEVEL_LABELS[level_now]} → {LEVEL_LABELS[level]}"
                ),
            )
            changes += 1
        for action in ACTIONS:
            level_now = current[name][action.key]
            level = wanted.get(action.key)
            if level not in ACTION_LEVELS:
                # Ключа нет в форме — действие остаётся, как было: своё
                # правило не трогаем, а без него оно идёт за разделом.
                continue
            inherited = inherited_action_level(
                action, new_levels.get(action.section) if action.section else None,
            )
            target = None if level == inherited else level
            if stored.get((role.id, action.key)) != target:
                _put_rule(db, key=action.key, role_id=role.id, actor_id=actor_id, level=target)
            if level == level_now:
                continue
            _audit(
                db, action="section_access_role", actor_id=actor_id, target_user_id=None,
                details=(
                    f"роль «{role.display_name}», действие «{action.label}»: "
                    f"{ACTION_LABELS[level_now]} → {ACTION_LABELS[level]}"
                ),
            )
            changes += 1
    db.commit()
    return changes


def user_rules(db: DBSession, target: User) -> list[dict]:
    """Строки блока «Доступ к разделам» в карточке сотрудника — все разделы и
    действия.

    `state` — `role` (как по умолчанию) или личный уровень; `default` — что
    сотрудник получит без личного правила, карточка показывает его в пункте
    «Как у роли». Действие идёт сразу за своим разделом, с `is_action`."""
    role_name = target.role.name if target.role else None
    if not is_configurable_role(role_name):
        return []
    by_role: dict[str, str] = {}
    personal: dict[str, str] = {}
    for row in _rules_for(db, user_id=target.id, role_id=target.role_id):
        if row.level not in LEVELS:
            continue
        if row.user_id is not None:
            personal[row.section_key] = row.level
        else:
            by_role[row.section_key] = row.level
    effective_sections = {
        s.key: personal.get(s.key, by_role.get(s.key, native_level(s, role_name)))
        for s in SECTIONS
    }

    def action_rows(section_key: str | None) -> list[dict]:
        rows = []
        for a in ACTIONS:
            if a.section != section_key:
                continue
            default = _action_level(by_role.get(a.key), a, effective_sections)
            rows.append({
                "key": a.key,
                "label": a.label,
                "is_action": True,
                "native": native_action_level(a, role_name) == LEVEL_EDIT,
                "levels": ACTION_LEVELS,
                "risky": [LEVEL_EDIT] if a.risky and native_action_level(a, role_name) != LEVEL_EDIT else [],
                "default": default,
                "state": personal.get(a.key, USER_STATE_ROLE)
                if personal.get(a.key) in ACTION_LEVELS else USER_STATE_ROLE,
            })
        return rows

    result = []
    for s in SECTIONS:
        result.append({
            "key": s.key,
            "label": s.label,
            "is_action": False,
            "native": is_native(s, role_name),
            "levels": levels_of(s),
            "risky": [lv for lv in levels_of(s) if is_risky(s, role_name, lv)],
            "default": by_role.get(s.key, native_level(s, role_name)),
            "state": personal.get(s.key, USER_STATE_ROLE),
        })
        result.extend(action_rows(s.key))
    result.extend(action_rows(None))
    return result


def save_user_rules(db: DBSession, *, actor_id: int, target: User, desired: dict[str, str]) -> int:
    """Сохранить личные правила сотрудника. `desired` — {ключ: role|none|view|edit};
    ключ, которого в `desired` нет, и незнакомое значение не меняются. У
    действия — только role, none и edit."""
    role_name = target.role.name if target.role else None
    if not is_configurable_role(role_name):
        raise ValueError("Разделы настраиваются только куратору, модератору и Главному преподавателю")

    def label(key: str, state: str) -> str:
        return "как у роли" if state == USER_STATE_ROLE else _label(key, state)

    changes = 0
    for item in user_rules(db, target):
        key = item["key"]
        want = desired.get(key)
        if want != USER_STATE_ROLE:
            if item["is_action"]:
                want = want if want in ACTION_LEVELS else None
            else:
                want = _clean_level(SECTIONS_BY_KEY[key], want)
        if want is None or want == item["state"]:
            continue
        _put_rule(
            db, key=key, user_id=target.id, actor_id=actor_id,
            level=None if want == USER_STATE_ROLE else want,
        )
        if item["is_action"]:
            what = f"действие «{item['label']}»"
        else:
            what = f"раздел «{item['label']}»" + ("" if item["native"] else " (сверх роли)")
        _audit(
            db, action="section_access_user", actor_id=actor_id, target_user_id=target.id,
            details=f"{what}: {label(key, item['state'])} → {label(key, want)}",
        )
        changes += 1
    db.commit()
    return changes


def staff_with_personal_rules(db: DBSession) -> list[dict]:
    """Сотрудники с личными исключениями — список под таблицей «Доступы»."""
    rows = (
        db.query(SectionAccessRule, User)
        .join(User, SectionAccessRule.user_id == User.id)
        .filter(User.deleted_at.is_(None))
        .all()
    )
    by_user: dict[int, dict] = {}
    for rule, user in rows:
        item = SECTIONS_BY_KEY.get(rule.section_key) or ACTIONS_BY_KEY.get(rule.section_key)
        if item is None or rule.level not in LEVELS:
            continue
        entry = by_user.setdefault(user.id, {"user": user, "rules": []})
        entry["rules"].append({
            "label": item.label,
            "level_label": _label(rule.section_key, rule.level),
        })
    return sorted(
        by_user.values(),
        key=lambda e: ((e["user"].last_name or ""), (e["user"].first_name or e["user"].name or "")),
    )
