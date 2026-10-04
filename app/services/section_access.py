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
    """{раздел: уровень} сотрудника по всем разделам каталога.

    Порядок: личное правило, затем правило роли, затем `native_level`. Ключи,
    которых нет в `SECTIONS` (раздел переименовали или убрали), и незнакомые
    уровни пропускаются — старая строка в базе не должна ронять вход."""
    by_role: dict[str, str] = {}
    by_user: dict[str, str] = {}
    for row in _rules_for(db, user_id=user_id, role_id=role_id):
        if row.section_key not in SECTIONS_BY_KEY or row.level not in LEVELS:
            continue
        if row.user_id is not None:
            by_user[row.section_key] = row.level
        else:
            by_role[row.section_key] = row.level
    return {
        s.key: by_user.get(s.key, by_role.get(s.key, native_level(s, role_name)))
        for s in SECTIONS
    }


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
        result[name] = {
            s.key: role_rows.get(s.key, native_level(s, name)) for s in SECTIONS
        }
    return result


def _audit(db: DBSession, *, action: str, actor_id: int, target_user_id: int | None, details: str) -> None:
    db.add(AuditLog(
        action=action,
        performed_by_id=actor_id,
        target_user_id=target_user_id,
        details=details[:1000],
    ))


def save_role_levels(db: DBSession, *, actor_id: int, desired: dict[str, dict[str, str]]) -> int:
    """Сохранить таблицу «разделы × роли». Возвращает число изменений.

    `desired` — {роль: {раздел: уровень}}; незнакомые роли, ключи и уровни
    молча пропускаются, раздела, которого нет в `desired`, правка не касается.

    Строка в базе хранится, только пока уровень расходится с `native_level`:
    вернули как положено роли — строка удаляется."""
    roles = _configurable_roles(db)
    current = role_levels(db)
    changes = 0
    for name in CONFIGURABLE_ROLES:
        role = roles.get(name)
        if role is None:
            continue
        wanted_for_role = desired.get(name, {})
        for key, level_now in current[name].items():
            if key not in wanted_for_role:
                continue
            section = SECTIONS_BY_KEY[key]
            level = _clean_level(section, wanted_for_role[key])
            if level is None or level == level_now:
                continue
            row = (
                db.query(SectionAccessRule)
                .filter(SectionAccessRule.role_id == role.id, SectionAccessRule.section_key == key)
                .first()
            )
            if level == native_level(section, name):
                if row is not None:
                    db.delete(row)
            elif row is None:
                db.add(SectionAccessRule(
                    section_key=key, role_id=role.id, level=level, updated_by_id=actor_id,
                ))
            else:
                row.level = level
                row.updated_by_id = actor_id
            extra = "" if is_native(section, name) else " (сверх роли)"
            _audit(
                db, action="section_access_role", actor_id=actor_id, target_user_id=None,
                details=(
                    f"роль «{role.display_name}», раздел «{section.label}»{extra}: "
                    f"{LEVEL_LABELS[level_now]} → {LEVEL_LABELS[level]}"
                ),
            )
            changes += 1
    db.commit()
    return changes


def user_rules(db: DBSession, target: User) -> list[dict]:
    """Строки блока «Доступ к разделам» в карточке сотрудника — все разделы.

    `state` — `role` (как у роли) или личный уровень; `role_level` — что
    сейчас у роли, карточка показывает его в пункте «Как у роли»."""
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
    result = []
    for s in SECTIONS:
        result.append({
            "key": s.key,
            "label": s.label,
            "native": is_native(s, role_name),
            "levels": levels_of(s),
            "risky": [lv for lv in levels_of(s) if is_risky(s, role_name, lv)],
            "role_level": by_role.get(s.key, native_level(s, role_name)),
            "state": personal.get(s.key, USER_STATE_ROLE),
        })
    return result


def save_user_rules(db: DBSession, *, actor_id: int, target: User, desired: dict[str, str]) -> int:
    """Сохранить личные правила сотрудника. `desired` — {раздел: role|none|view|edit};
    раздел, которого в `desired` нет, и незнакомое значение не меняются."""
    role_name = target.role.name if target.role else None
    if not is_configurable_role(role_name):
        raise ValueError("Разделы настраиваются только куратору, модератору и Главному преподавателю")

    def label(state: str) -> str:
        return "как у роли" if state == USER_STATE_ROLE else LEVEL_LABELS[state]

    changes = 0
    for item in user_rules(db, target):
        key = item["key"]
        want = desired.get(key)
        if want != USER_STATE_ROLE:
            want = _clean_level(SECTIONS_BY_KEY[key], want)
        if want is None or want == item["state"]:
            continue
        row = (
            db.query(SectionAccessRule)
            .filter(SectionAccessRule.user_id == target.id, SectionAccessRule.section_key == key)
            .first()
        )
        if want == USER_STATE_ROLE:
            if row is not None:
                db.delete(row)
        else:
            if row is None:
                row = SectionAccessRule(section_key=key, user_id=target.id)
                db.add(row)
            row.level = want
            row.updated_by_id = actor_id
        extra = "" if item["native"] else " (сверх роли)"
        _audit(
            db, action="section_access_user", actor_id=actor_id, target_user_id=target.id,
            details=f"раздел «{item['label']}»{extra}: {label(item['state'])} → {label(want)}",
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
        if rule.section_key not in SECTIONS_BY_KEY:
            continue
        if rule.level not in LEVELS:
            continue
        entry = by_user.setdefault(user.id, {"user": user, "rules": []})
        entry["rules"].append({
            "label": SECTIONS_BY_KEY[rule.section_key].label,
            "level": rule.level,
        })
    return sorted(
        by_user.values(),
        key=lambda e: ((e["user"].last_name or ""), (e["user"].first_name or e["user"].name or "")),
    )
