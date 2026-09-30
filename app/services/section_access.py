"""Переключатели доступа к разделам кабинета (владелец 30.09.2026).

Суперадмин закрывает сотрудникам разделы — всей роли сразу или одному
человеку. Устроено третьим слоем поверх уже существующих:

1. Ранг роли (`require_*` в `app/dependencies.py`) — потолок. Переключатель
   его не поднимает: личное «открыто» у куратора не пустит его в программы,
   куда ранг 2 не проходит и так.
2. Белый список модератора (`rbac.py::is_moderator_request_allowed`) — его
   потолок, тоже без изменений.
3. Здесь — сужение: закрытый раздел пропадает из меню, а его адреса отвечают
   403 с причиной `SECTION_CLOSED_DETAIL`. Проверка стоит в
   `get_current_user`, поэтому действует со следующего запроса и не требует
   правки роутов.

Исключение из «только сужает» — `Section.grantable`: раздел, который можно
открыть одному человеку сверх роли (сейчас архив куратору). Такие разделы
лежат в `user["granted_sections"]`, а код раздела спрашивает `has_grant`
рядом с проверкой ранга — без этой правки строка в `grantable` ничего не даст.

Настраиваются только сотрудники: куратор, модератор, Главный преподаватель.
Ученика держат срок доступа и гейты, суперадмина не закрывает ничто — иначе
он запер бы сам себя.

**Общие адреса не входят ни в один раздел.** Экран «Пробники» и диалог
обратной связи ставят балл и отправляют на доработку через адреса карточки
ученика (`/cabinet/students/{id}/works/{id}/score`, `.../revision`), видео и
задания встроены в чужие экраны. Закрой их префиксом — закрытие одного
раздела сломает кнопки в другом (тот же урок, что у
`PORTFOLIO_GATE_BLOCKED_EXACT` в `dependencies.py`). Поэтому у адреса бывает
несколько разделов-владельцев, и закрыт он, только когда закрыты все они:
карточку ученика открывают и «Ученики», и «Архив». Диалоги обратной связи,
переписка по домашкам и сдачам блоков, уведомления и главная не закрываются
никогда.

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

SECTION_CLOSED_DETAIL = "Раздел закрыт суперадмином"

ROLE_CURATOR = "куратор"
ROLE_MODERATOR = "модератор"
ROLE_HEAD = "админ"  # в интерфейсе — «Главный преподаватель»

# Порядок — порядок столбцов на странице «Доступы».
CONFIGURABLE_ROLES: tuple[str, ...] = (ROLE_CURATOR, ROLE_MODERATOR, ROLE_HEAD)

# Состояния личного правила сотрудника (select в карточке).
USER_STATE_ROLE = "role"
USER_STATE_OPEN = "open"
USER_STATE_CLOSED = "closed"
USER_STATES = (USER_STATE_ROLE, USER_STATE_OPEN, USER_STATE_CLOSED)


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    hint: str
    # У каких ролей раздел вообще есть по рангу. У остальных на странице
    # «Доступы» прочерк: закрывать нечего.
    roles: tuple[str, ...]
    # Пункты меню (`navigation.py`), которые пропадают вместе с разделом.
    nav_keys: tuple[str, ...] = ()
    # Роли, которым раздел можно открыть лично сверх ранга (владелец
    # 30.09.2026: «куратору дать доступ к Архиву учеников»). Роли целиком
    # такой раздел не открывается — только отдельному человеку в карточке.
    # Работает, только если код раздела спрашивает `has_grant`, а не ранг:
    # новый раздел сюда добавлять вместе с переделкой его проверок.
    grantable: tuple[str, ...] = ()


SECTIONS: tuple[Section, ...] = (
    Section(
        "students", "Ученики", "Список учеников и карточка ученика",
        (ROLE_CURATOR, ROLE_MODERATOR, ROLE_HEAD), ("students",),
    ),
    Section(
        "students_review", "Проверка по ученику", "Разбор всего, что сдал ученик, за один заход",
        (ROLE_CURATOR, ROLE_HEAD), ("students_review",),
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
        (ROLE_CURATOR, ROLE_HEAD), ("reports",),
    ),
    Section(
        "archive", "Архив учеников", "Прошлые потоки, только просмотр",
        (ROLE_MODERATOR, ROLE_HEAD), ("archive",),
        # Куратору с личным доступом — весь архив школы, только на чтение
        # (владелец 30.09.2026). Среди действующих — по-прежнему только свои.
        grantable=(ROLE_CURATOR,),
    ),
    Section(
        "statistics", "Статистика", "Статистика активности, выгрузки, статистика циклов",
        (ROLE_CURATOR, ROLE_MODERATOR, ROLE_HEAD), ("statistics", "activity"),
    ),
    Section(
        "guest_exam", "Гостевой пробник", "Ссылка, билеты и работы гостей",
        (ROLE_HEAD,),
    ),
    Section(
        "exams", "Билеты и периоды", "Задания пробников и периоды сдачи",
        (ROLE_HEAD,),
    ),
    Section(
        "lab3d", "3D Лаб", "3D-лаборатория",
        (ROLE_CURATOR, ROLE_HEAD), ("3dlab",),
    ),
    Section(
        "people", "Люди и доступы",
        "Пользователи, выдача входа, кураторы, теги, вход «глазами»",
        (ROLE_HEAD,),
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
    _rule("exams", _exact("/cabinet/upload-ticket-image")),
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


def blocked_section(method: str, path: str, query_params, closed: frozenset[str]) -> str | None:
    """Ключ раздела, из-за которого запрос закрыт, или None."""
    if not closed:
        return None
    owners = section_owners(method, path, query_params)
    if owners and all(key in closed for key in owners):
        return owners[0]
    return None


def is_configurable_role(role_name: str | None) -> bool:
    return role_name in CONFIGURABLE_ROLES


def _rules_for(db: DBSession, *, user_id: int | None, role_id: int | None):
    conds = []
    if user_id is not None:
        conds.append(SectionAccessRule.user_id == user_id)
    if role_id is not None:
        conds.append(SectionAccessRule.role_id == role_id)
    if not conds:
        return []
    return db.query(SectionAccessRule).filter(or_(*conds)).all()


def closed_sections(db: DBSession, *, user_id: int, role_id: int | None) -> frozenset[str]:
    """Закрытые сотруднику разделы: личное правило главнее правила роли.

    Ключи, которых нет в `SECTIONS` (раздел переименовали или убрали),
    пропускаются — старая строка в базе не должна ронять вход."""
    by_role: dict[str, bool] = {}
    by_user: dict[str, bool] = {}
    for row in _rules_for(db, user_id=user_id, role_id=role_id):
        if row.section_key not in SECTIONS_BY_KEY:
            continue
        if row.user_id is not None:
            by_user[row.section_key] = row.is_open
        else:
            by_role[row.section_key] = row.is_open
    return frozenset(
        key for key in SECTIONS_BY_KEY
        if not by_user.get(key, by_role.get(key, True))
    )


def granted_sections(db: DBSession, *, user_id: int, role_name: str | None) -> frozenset[str]:
    """Разделы, открытые сотруднику лично сверх ранга (`Section.grantable`)."""
    if not role_name:
        return frozenset()
    keys = [s.key for s in SECTIONS if role_name in s.grantable]
    if not keys:
        return frozenset()
    rows = (
        db.query(SectionAccessRule.section_key)
        .filter(
            SectionAccessRule.user_id == user_id,
            SectionAccessRule.section_key.in_(keys),
            SectionAccessRule.is_open == True,  # noqa: E712
        )
        .all()
    )
    return frozenset(r.section_key for r in rows)


def has_grant(user: dict, section_key: str) -> bool:
    """Открыт ли раздел сотруднику лично сверх ранга. Спрашивают проверки
    самого раздела рядом с проверкой ранга: `rank >= 4 or has_grant(...)`."""
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


# ── Настройка суперадмином ──────────────────────────────────────────────────

def _configurable_roles(db: DBSession) -> dict[str, Role]:
    rows = db.query(Role).filter(Role.name.in_(CONFIGURABLE_ROLES)).all()
    return {r.name: r for r in rows}


def role_matrix(db: DBSession) -> dict[str, dict[str, bool]]:
    """{роль: {раздел: открыт}} для страницы «Доступы». Только применимые пары."""
    roles = _configurable_roles(db)
    closed_by_role: dict[int, set[str]] = {}
    if roles:
        rows = (
            db.query(SectionAccessRule)
            .filter(SectionAccessRule.role_id.in_([r.id for r in roles.values()]))
            .all()
        )
        for row in rows:
            if not row.is_open:
                closed_by_role.setdefault(row.role_id, set()).add(row.section_key)
    matrix: dict[str, dict[str, bool]] = {}
    for name in CONFIGURABLE_ROLES:
        role = roles.get(name)
        closed = closed_by_role.get(role.id, set()) if role else set()
        matrix[name] = {
            s.key: s.key not in closed for s in SECTIONS if name in s.roles
        }
    return matrix


def _audit(db: DBSession, *, action: str, actor_id: int, target_user_id: int | None, details: str) -> None:
    db.add(AuditLog(
        action=action,
        performed_by_id=actor_id,
        target_user_id=target_user_id,
        details=details[:1000],
    ))


def save_role_matrix(db: DBSession, *, actor_id: int, desired: dict[str, dict[str, bool]]) -> int:
    """Сохранить таблицу «разделы × роли». Возвращает число изменений.

    `desired` — {роль: {раздел: открыт}}; неприменимые пары и незнакомые ключи
    молча пропускаются. Раздела, которого нет в `desired`, правка не касается:
    обрезанная или пустая форма не должна разом закрыть всё всем. Поэтому
    форма шлёт каждую ячейку явно — скрытое «0» перед галочкой «1»."""
    roles = _configurable_roles(db)
    current = role_matrix(db)
    changes = 0
    for name in CONFIGURABLE_ROLES:
        role = roles.get(name)
        if role is None:
            continue
        wanted_for_role = desired.get(name, {})
        for key, is_open_now in current[name].items():
            if key not in wanted_for_role:
                continue
            want_open = bool(wanted_for_role[key])
            if want_open == is_open_now:
                continue
            row = (
                db.query(SectionAccessRule)
                .filter(SectionAccessRule.role_id == role.id, SectionAccessRule.section_key == key)
                .first()
            )
            if want_open:
                if row is not None:
                    db.delete(row)
            else:
                if row is None:
                    db.add(SectionAccessRule(
                        section_key=key, role_id=role.id, is_open=False, updated_by_id=actor_id,
                    ))
                else:
                    row.is_open = False
                    row.updated_by_id = actor_id
            _audit(
                db, action="section_access_role", actor_id=actor_id, target_user_id=None,
                details=(
                    f"роль «{role.display_name}», раздел «{SECTIONS_BY_KEY[key].label}»: "
                    f"{'закрыт → открыт' if want_open else 'открыт → закрыт'}"
                ),
            )
            changes += 1
    db.commit()
    return changes


def user_rules(db: DBSession, target: User) -> list[dict]:
    """Строки блока «Доступ к разделам» в карточке сотрудника."""
    role_name = target.role.name if target.role else None
    if not is_configurable_role(role_name):
        return []
    role_closed = set()
    personal: dict[str, bool] = {}
    for row in _rules_for(db, user_id=target.id, role_id=target.role_id):
        if row.user_id is not None:
            personal[row.section_key] = row.is_open
        elif not row.is_open:
            role_closed.add(row.section_key)
    result = []
    for s in SECTIONS:
        grant_only = role_name not in s.roles and role_name in s.grantable
        if role_name not in s.roles and not grant_only:
            continue
        if s.key in personal:
            state = USER_STATE_OPEN if personal[s.key] else USER_STATE_CLOSED
        else:
            state = USER_STATE_ROLE
        if grant_only and state == USER_STATE_CLOSED:
            # «Закрыт» здесь то же, что «как у роли»: роли раздел не положен.
            state = USER_STATE_ROLE
        result.append({
            "key": s.key,
            "label": s.label,
            # Раздела нет у роли — «как у роли» значит «закрыт».
            "role_open": not grant_only and s.key not in role_closed,
            "state": state,
            "grant_only": grant_only,
        })
    return result


def save_user_rules(db: DBSession, *, actor_id: int, target: User, desired: dict[str, str]) -> int:
    """Сохранить личные правила сотрудника. `desired` — {раздел: role|open|closed};
    раздел, которого в `desired` нет, не меняется."""
    role_name = target.role.name if target.role else None
    if not is_configurable_role(role_name):
        raise ValueError("Разделы настраиваются только куратору, модератору и Главному преподавателю")
    changes = 0
    for item in user_rules(db, target):
        key = item["key"]
        want = desired.get(key)
        if item["grant_only"] and want == USER_STATE_CLOSED:
            want = USER_STATE_ROLE
        if want not in USER_STATES or want == item["state"]:
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
            row.is_open = want == USER_STATE_OPEN
            row.updated_by_id = actor_id
        labels = {
            USER_STATE_ROLE: "как у роли",
            USER_STATE_OPEN: "открыт",
            USER_STATE_CLOSED: "закрыт",
        }
        _audit(
            db, action="section_access_user", actor_id=actor_id, target_user_id=target.id,
            details=f"раздел «{item['label']}»: {labels[item['state']]} → {labels[want]}",
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
        entry = by_user.setdefault(user.id, {
            "user": user, "opened": [], "closed": [],
        })
        label = SECTIONS_BY_KEY[rule.section_key].label
        (entry["opened"] if rule.is_open else entry["closed"]).append(label)
    return sorted(
        by_user.values(),
        key=lambda e: ((e["user"].last_name or ""), (e["user"].first_name or e["user"].name or "")),
    )
