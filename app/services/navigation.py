from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    key: str
    href: str
    label: str
    icon: str


@dataclass(frozen=True)
class StudentNavItem:
    key: str
    desktop_href: str
    mobile_href: str
    desktop_label: str
    mobile_label: str
    aria_label: str
    icon: str
    soon: bool = False


@dataclass(frozen=True)
class StaffNavItem:
    key: str
    href: str
    sidebar_label: str
    pill_label: str
    aria_label: str
    tooltip: str
    icon: str
    min_rank: int | None = None
    max_rank: int | None = None

    def is_visible_for(self, role_rank: int) -> bool:
        if self.min_rank is not None and role_rank < self.min_rank:
            return False
        if self.max_rank is not None and role_rank > self.max_rank:
            return False
        return True


CURATOR_NAV_ITEMS: tuple[NavItem, ...] = (
    NavItem(key="dashboard", href="/cabinet/curator", label="Кабинет", icon="🏠"),
    NavItem(key="students", href="/cabinet/students", label="Ученики", icon="👥"),
    NavItem(key="cycles", href="/cabinet/staff/cycles", label="Цикл Пробника", icon="🔁"),
    NavItem(key="reports", href="/cabinet/curator/reports", label="Отчёты", icon="🎬"),
    NavItem(key="statistics", href="/cabinet/students?tab=statistics", label="Статистика", icon="📈"),
)


STUDENT_NAV_ITEMS: tuple[StudentNavItem, ...] = (
    StudentNavItem(
        key="tracker",
        desktop_href="/cabinet/tracker",
        mobile_href="/cabinet/tracker",
        desktop_label="Личный трекер",
        mobile_label="Трекер",
        aria_label="Личный трекер",
        icon="tracker",
    ),
    StudentNavItem(
        key="learning",
        desktop_href="/cabinet/learning",
        mobile_href="/cabinet/learning",
        desktop_label="Актуальное образовательное пространство",
        mobile_label="Обучение",
        aria_label="Актуальное образовательное пространство",
        icon="learning",
    ),
    StudentNavItem(
        key="3dlab",
        desktop_href="/3dlab",
        mobile_href="/3dlab",
        desktop_label="3D Лаб",
        mobile_label="3D Лаб",
        aria_label="3D Лаб",
        icon="3dlab",
    ),
    StudentNavItem(
        key="portfolio",
        desktop_href="/cabinet/portfolio",
        mobile_href="/cabinet/portfolio",
        desktop_label="Портфолио",
        mobile_label="Портфолио",
        aria_label="Портфолио",
        icon="portfolio",
    ),
    StudentNavItem(
        key="statistics",
        desktop_href="#",
        mobile_href="#",
        desktop_label="Статистика",
        mobile_label="Статистика",
        aria_label="Статистика (скоро)",
        icon="statistics",
        soon=True,
    ),
    StudentNavItem(
        key="personal",
        desktop_href="/cabinet/personal",
        mobile_href="/cabinet/personal",
        desktop_label="Личная информация",
        mobile_label="Личное",
        aria_label="Личная информация",
        icon="personal",
    ),
)


STAFF_NAV_ITEMS: tuple[StaffNavItem, ...] = (
    StaffNavItem(
        key="dashboard",
        href="/cabinet",
        sidebar_label="Кабинет",
        pill_label="Кабинет",
        aria_label="Кабинет",
        tooltip="Кабинет",
        icon="profile",
    ),
    StaffNavItem(
        key="students",
        href="/cabinet/students",
        sidebar_label="Ученики",
        pill_label="Ученики",
        aria_label="Ученики",
        tooltip="Ученики",
        icon="students",
    ),
    StaffNavItem(
        key="mock_check",
        href="/cabinet/admin/mock-check",
        sidebar_label="Пробники",
        pill_label="Пробники",
        aria_label="Пробники",
        tooltip="Проверка пробников",
        icon="mock",
        max_rank=3,
    ),
    StaffNavItem(
        key="cycles",
        href="/cabinet/staff/cycles",
        sidebar_label="Цикл Пробника",
        pill_label="Цикл",
        aria_label="Цикл Пробника",
        tooltip="Цикл Пробника",
        icon="cycle",
    ),
    StaffNavItem(
        # Очередь проверки ответов (владелец 31.08.2026). min_rank=2, потому
        # что смотреть ответы должен и куратор, а в «Учебные программы» его не
        # пускают — там ранг 4. Своих учеников куратор видит, чужих нет.
        key="review",
        href="/cabinet/staff/review",
        sidebar_label="На проверку",
        pill_label="Проверка",
        aria_label="Ответы на проверку",
        tooltip="Ответы учеников, которые вы ещё не смотрели",
        icon="cycle",
        min_rank=2,
    ),
    StaffNavItem(
        key="program",
        href="/cabinet/staff/program",
        sidebar_label="Учебные программы",
        pill_label="Программы",
        aria_label="Учебные программы",
        tooltip="Календарь учебных программ",
        icon="program",
        min_rank=4,
    ),
    # «Задачи», «Кейсы», «Дайджест», «Цели» и «Видео» здесь больше не пункты
    # меню. Задачи трекера и Кейсы скрыты совсем — страницы живут по прямым
    # ссылкам `/cabinet/staff/tracker` и `/cabinet/cases`; остальные три стали
    # вкладками раздела «Учебные программы» —
    # `templates/partials/program_tabs.html`.
    StaffNavItem(
        key="3dlab",
        href="/3dlab",
        sidebar_label="3D Лаб",
        pill_label="3D Лаб",
        aria_label="3D Лаб",
        tooltip="3D Лаборатория",
        icon="3dlab",
    ),
    StaffNavItem(
        key="reports",
        href="/cabinet/curator/reports",
        sidebar_label="Видео-отчёты",
        pill_label="Отчёты",
        aria_label="Видео-отчёты",
        tooltip="Видео-отчёты кураторов",
        icon="reports",
        min_rank=4,
    ),
    StaffNavItem(
        key="archive",
        href="/cabinet/archive",
        sidebar_label="Архив учеников",
        pill_label="Архив",
        aria_label="Архив учеников",
        tooltip="Архив прошлых потоков: работы и переписки, только просмотр",
        icon="students",
        min_rank=5,
    ),
    StaffNavItem(
        key="guest_exam",
        href="/cabinet/staff/guest-exam",
        sidebar_label="Гостевой режим",
        pill_label="Гости",
        aria_label="Гостевой режим",
        tooltip="Гостевой режим — пробник для участников без регистрации",
        icon="mock",
        min_rank=4,
    ),
)


def curator_nav_items() -> tuple[NavItem, ...]:
    return CURATOR_NAV_ITEMS


def student_nav_items() -> tuple[StudentNavItem, ...]:
    return STUDENT_NAV_ITEMS


def staff_nav_items(role_rank: int) -> tuple[StaffNavItem, ...]:
    return tuple(item for item in STAFF_NAV_ITEMS if item.is_visible_for(role_rank))
