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
    NavItem(key="students", href="/cabinet/students", label="Ученики", icon="👥"),
    NavItem(key="reports", href="/cabinet/curator/reports", label="Отчёты", icon="🎬"),
    NavItem(key="statistics", href="/cabinet/students?tab=statistics", label="Статистика", icon="📈"),
)


STUDENT_NAV_ITEMS: tuple[StudentNavItem, ...] = (
    StudentNavItem(
        key="",
        desktop_href="/cabinet",
        mobile_href="/cabinet/student",
        desktop_label="Кабинет",
        mobile_label="Кабинет",
        aria_label="Кабинет",
        icon="profile",
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
        key="cycle",
        desktop_href="/cabinet/cycle",
        mobile_href="/cabinet/cycle",
        desktop_label="Цикл Пробника",
        mobile_label="Цикл",
        aria_label="Цикл Пробника",
        icon="cycle",
    ),
    StudentNavItem(
        key="mock",
        desktop_href="/upload/mock-exam",
        mobile_href="/upload/mock-exam",
        desktop_label="Пробник",
        mobile_label="Пробник",
        aria_label="Пробник",
        icon="mock",
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
)


def curator_nav_items() -> tuple[NavItem, ...]:
    return CURATOR_NAV_ITEMS


def student_nav_items() -> tuple[StudentNavItem, ...]:
    return STUDENT_NAV_ITEMS


def staff_nav_items(role_rank: int) -> tuple[StaffNavItem, ...]:
    return tuple(item for item in STAFF_NAV_ITEMS if item.is_visible_for(role_rank))
