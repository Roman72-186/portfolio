from __future__ import annotations

from dataclasses import dataclass

# Единое название раздела программ у всех ролей (владелец 16.09.2026, созвон:
# «раздел с программами назвать одинаково у всех ролей — «Актуальное
# образовательное пространство»»). До этой правки текст расходился
# независимо в восьми местах — у ученика было три разных варианта
# («Обучение»/«Актуальное образовательное пространство»/«Образовательное
# пространство»), у персонала три других («Учебные программы»/«Программы»/
# «Циклы учебных программ»). Меняем только текст самого раздела — короткие
# подписи дочерних экранов (`pill_label`, `tooltip`, заголовки
# `cabinet_program_cycles.html` и т.п.) владелец просил не трогать: пункт
# меню персонала (`min_rank=4`) видимость не меняет, куратору и модератору
# его как не показывали, так и не показывают.
LEARNING_SPACE_LABEL = "Актуальное образовательное пространство"


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
    # Единый экран проверки по ученику (решение владельца 01.09.2026) — для
    # куратора этот пункт, а не STAFF_NAV_ITEMS: _curator_nav.html читает
    # отдельный список, не пересекающийся со staff-сайдбаром admin+.
    NavItem(key="students_review", href="/cabinet/staff/students-review", label="Проверка", icon="🗂️"),
    NavItem(key="reports", href="/cabinet/curator/reports", label="Отчёты", icon="🎬"),
    NavItem(key="statistics", href="/cabinet/students?tab=statistics", label="Статистика", icon="📈"),
    # Уведомления куратору (добавлено 12.09.2026) — сюда падают напоминания
    # о дне рождения ученика (exam_scheduler._run_birthday_check) и в
    # будущем любые другие Notification с user_id=куратор. У admin+
    # (STAFF_NAV_ITEMS) такого пункта пока нет — им сегодня ничего не адресуют.
    NavItem(key="notifications", href="/cabinet/staff/notifications", label="Уведомления", icon="🔔"),
)


STUDENT_NAV_ITEMS: tuple[StudentNavItem, ...] = (
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
        desktop_label=LEARNING_SPACE_LABEL,
        # Полный текст не влезает в мобильную кнопку — CSS-обрезка
        # (`.learning-back`/`base.css`), не второй текстовый вариант.
        mobile_label=LEARNING_SPACE_LABEL,
        aria_label=LEARNING_SPACE_LABEL,
        icon="learning",
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
    # Обратная связь — диалог с куратором по сданным работам (владелец
    # 09.09.2026). До 06.09 ученик попадал в него вкладкой недели; вкладки
    # снесли, а другого входа не осталось: с «Обучения», «Трекера» и
    # «Портфолио» ссылок на диалог нет, оставался только колокольчик, и то
    # лишь пока висит непрочитанное уведомление. Старую переписку ученик
    # открыть не мог вовсе.
    #
    # Ведёт на `/cabinet/feedback/`, а не сразу на `/cabinet/cycle`: первый —
    # это адрес самой обратной связи, он же разводит staff по их роутам
    # (`api/feedback.py::student_feedback_list`), ученика — на экран списка
    # циклов. Прямая ссылка на `/cabinet/cycle` из нижнего меню убрана
    # 05.07.2026 как «цикл пробника», и возвращать её под тем же именем не
    # нужно — см. `tests/test_navigation_contracts.py`.
    StudentNavItem(
        key="feedback",
        desktop_href="/cabinet/feedback/",
        mobile_href="/cabinet/feedback/",
        desktop_label="Обратная связь",
        mobile_label="Обратная связь",
        aria_label="Обратная связь",
        icon="cycle",
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
        # Единый экран проверки по ученику (решение владельца 01.09.2026,
        # plans/2026-09-01-apparchi-student-centric-review.md, этап 7).
        # min_rank=2: экран полный уже у куратора (право на балл Work/ExamCycle
        # расширено отдельно) — своих учеников он видит, чужих нет.
        # Пункт «Цикл Пробника» снесён 02.09.2026: диалог цикла (фото билета,
        # оценка, закрытие) уже открывается отсюда через строку экрана —
        # отдельный флатный список стал дублем (см. review_aggregate.py).
        key="students_review",
        href="/cabinet/staff/students-review",
        sidebar_label="Проверка по ученику",
        pill_label="По ученику",
        aria_label="Проверка по ученику",
        tooltip="Открыть ученика и разобрать всё, что он сдал, за один заход",
        icon="students",
        min_rank=2,
    ),
    StaffNavItem(
        # Точка А — входная оценка ученика по шести элементам сразу (Лиза
        # 14.09.2026, решение владельца 15.09.2026). min_rank=4: оценку
        # ставит только Главный преподаватель, куратору пункт не нужен — у
        # него и своего сайдбара (CURATOR_NAV_ITEMS) этой строки нет.
        # Отдельный пункт, а не строка внутри «Проверки по ученику»: точка А
        # собирается один раз на входе, и ГП нужен список «кого ещё не
        # разобрала» — подробнее в докстринге app/services/point_a.py.
        key="point_a",
        href="/cabinet/staff/point-a",
        sidebar_label="Оценка точки А",
        pill_label="Точка А",
        aria_label="Оценка точки А",
        tooltip="Пробники, контрольные и оба портфолио одного ученика на одном экране",
        icon="point_a",
        min_rank=4,
    ),
    StaffNavItem(
        key="program",
        # Вход сразу на циклы (10.09.2026): календарь убран из вкладок раздела
        # (`program_tabs.html`), но точку входа из бокового меню забыли
        # переключить вместе с ним — она вела на старый календарь.
        href="/cabinet/staff/program/cycles",
        sidebar_label=LEARNING_SPACE_LABEL,
        pill_label="Программы",
        aria_label=LEARNING_SPACE_LABEL,
        tooltip="Циклы учебных программ",
        icon="program",
        min_rank=4,
    ),
    # «Задачи», «Кейсы», «Дайджест», «Цели» и «Видео» здесь больше не пункты
    # меню. Задачи трекера и Кейсы скрыты совсем — страницы живут по прямым
    # ссылкам `/cabinet/staff/tracker` и `/cabinet/cases`; остальные три стали
    # вкладками раздела «Актуальное образовательное пространство»
    # (LEARNING_SPACE_LABEL) — `templates/partials/program_tabs.html`.
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
        min_rank=4,
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


def student_nav_items(access_expired: bool = False) -> tuple[StudentNavItem, ...]:
    """У ученика с истёкшим сроком доступа (`User.access_until`) в меню
    остаётся только «Личная информация» — остальные разделы ему всё равно
    отдадут 403 из `get_current_user`, и меню из живых на вид ссылок,
    отбрасывающих обратно, читалось бы как поломка платформы, а не как
    закрытый доступ."""
    if access_expired:
        return tuple(item for item in STUDENT_NAV_ITEMS if item.key == "personal")
    return STUDENT_NAV_ITEMS


def staff_nav_items(role_rank: int) -> tuple[StaffNavItem, ...]:
    return tuple(item for item in STAFF_NAV_ITEMS if item.is_visible_for(role_rank))
