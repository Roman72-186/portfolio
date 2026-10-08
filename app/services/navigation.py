from __future__ import annotations

from dataclasses import dataclass

from app.services.section_access import closed_nav_keys, granted_nav_keys

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

# 3D-лаборатория закрыта ученикам на сентябрь 2026 (созвон 16.09.2026: «на
# сентябрь лабораторию для детей отключить, с октября будем открывать часть
# карточек»). Закрыто и в меню, и по прямой ссылке — страница, SSO-вход и
# файлы моделей проверяют один и тот же `can_open_3dlab`. Персонал и
# участники сообщества без роли ученика лабораторию видят как раньше.
# Открыть обратно — поставить True.
LAB3D_OPEN_FOR_STUDENTS = False


def can_open_3dlab(user: dict) -> bool:
    """Пускать ли пользователя в 3D-лабораторию: страница, SSO и ассеты."""
    role_rank = user.get("role_rank", 0)
    if role_rank == 1 and not LAB3D_OPEN_FOR_STUDENTS:
        return False
    return bool(user.get("is_group_member") or user.get("is_admin") or role_rank >= 1)


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
    NavItem(key="reports", href="/cabinet/curator/reports", label="Отчёты", icon="🎬"),
    NavItem(key="statistics", href="/cabinet/students?tab=statistics", label="Статистика", icon="📈"),
    # 3D-лаборатория (владелец 25.09.2026: «всем кураторам открыть доступ»).
    # Сервер куратора пускал и раньше (`can_open_3dlab`), но в его отдельном
    # меню пункта не было — зайти можно было только по прямой ссылке.
    NavItem(key="3dlab", href="/3dlab", label="3D Лаб", icon="🧊"),
    # Уведомления куратору (добавлено 12.09.2026) — любые Notification с
    # user_id=куратор. Напоминания о дне рождения ученика с 29.09.2026 уходят
    # не куратору, а ГП (exam_scheduler._run_birthday_check). У admin+
    # (STAFF_NAV_ITEMS) пункта нет: их уведомления открываются колокольчиком
    # из base.html, его «Все уведомления →» ведёт на тот же экран.
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
    # Архив (владелец 04.10.2026): все пройденные этапы и циклы. Полоса циклов
    # на экране обучения показывает только текущий этап, и со сменой этапа
    # прошлые видео пропадали из виду. Гейт портфолио «До» архив не
    # закрывает — как и саму ленту, сервер `/cabinet/learning/*` не блокирует.
    StudentNavItem(
        key="archive",
        desktop_href="/cabinet/learning/archive",
        mobile_href="/cabinet/learning/archive",
        desktop_label="Архив",
        mobile_label="Архив",
        aria_label="Архив пройденных циклов",
        icon="archive",
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
        # Очередь «кого проверять» (01.09.2026). Снят 05.10.2026, когда
        # проверка переехала во вкладку «Задания» карточки «Учеников»;
        # возвращён 06.10.2026 только ГП и суперадмину (владелец). Строка
        # экрана ведёт в ту же вкладку карточки. Куратору пункта нет: у него
        # тот же счётчик и фильтр в списке «Учеников».
        key="students_review",
        href="/cabinet/staff/students-review",
        sidebar_label="Проверка по ученику",
        pill_label="По ученику",
        aria_label="Проверка по ученику",
        tooltip="Кого проверять: ученики с непроверенными сдачами",
        icon="tracker",
        min_rank=4,
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
        # Рассылки ученикам через бота (владелец 07.10.2026): сообщение,
        # проверка у себя в Telegram, отправка, журнал.
        key="broadcasts",
        href="/cabinet/staff/broadcasts",
        sidebar_label="Рассылки",
        pill_label="Рассылки",
        aria_label="Рассылки",
        tooltip="Сообщения ученикам через бота: текст, фото, голосовое, кружок",
        icon="broadcasts",
        # Пока только суперадмин; ГП видит пункт, когда раздел ему открыли
        # в «Доступах» (`granted` в `staff_nav_items`).
        min_rank=5,
    ),
    StaffNavItem(
        key="program",
        # Вход на периоды (владелец 06.10.2026: «по дефолту открывать
        # Периоды»): вкладки идут от крупного к мелкому, Периоды → Этапы →
        # Циклы. До этого — циклы (10.09.2026, когда убрали календарь).
        href="/cabinet/staff/program/periods",
        sidebar_label=LEARNING_SPACE_LABEL,
        pill_label="Программы",
        aria_label=LEARNING_SPACE_LABEL,
        tooltip="Периоды, этапы и циклы учебных программ",
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
        icon="archive",
        min_rank=4,
    ),
    # «Гостевой режим» снят из меню (созвон 16.09.2026: «гостевой режим можно
    # скрыть, он уже не нужен»). Скрыт только пункт: экран
    # `/cabinet/staff/guest-exam` и гостевые ссылки работают, а данные гостей
    # сносить нельзя — AGENTS.md, инвариант 9 (перенос в точку А не решён).
    # Переключатели разделов для сотрудников (владелец 30.09.2026,
    # services/section_access.py) — только суперадмину.
    StaffNavItem(
        key="access",
        href="/cabinet/superadmin/access",
        sidebar_label="Доступы",
        pill_label="Доступы",
        aria_label="Доступ сотрудников к разделам",
        tooltip="Какие разделы открыты кураторам, модераторам и Главным преподавателям",
        icon="access",
        min_rank=5,
    ),
)


# Пункты, которые куратор видит, только если суперадмин открыл ему раздел
# сверх роли (`section_access.py`, владелец 30.09 и 03.10.2026). Ключ — пункт
# меню из `Section.nav_keys`; встают перед «Уведомлениями» в порядке `SECTIONS`.
CURATOR_GRANTED_NAV_ITEMS: dict[str, NavItem] = {
    "mock_check": NavItem(key="mock_check", href="/cabinet/admin/mock-check", label="Пробники", icon="⭐"),
    "point_a": NavItem(key="point_a", href="/cabinet/staff/point-a", label="Точка А", icon="🎯"),
    "program": NavItem(key="program", href="/cabinet/staff/program/periods", label="Программы", icon="📅"),
    "broadcasts": NavItem(key="broadcasts", href="/cabinet/staff/broadcasts", label="Рассылки", icon="✉️"),
    "archive": NavItem(key="archive", href="/cabinet/archive", label="Архив", icon="🗄️"),
    "guest_exam": NavItem(key="guest_exam", href="/cabinet/staff/guest-exam", label="Гостевой пробник", icon="🎟️"),
    "exams": NavItem(key="exams", href="/cabinet/exam-assignments", label="Билеты и периоды", icon="📝"),
    "people": NavItem(key="people", href="/cabinet/superadmin/users", label="Пользователи", icon="👤"),
}


def curator_nav_items(
    closed_sections: frozenset[str] | None = None,
    granted_sections: frozenset[str] | None = None,
) -> tuple[NavItem, ...]:
    hidden = closed_nav_keys(closed_sections)
    extra = [
        CURATOR_GRANTED_NAV_ITEMS[key]
        for key in granted_nav_keys(granted_sections)
        if key in CURATOR_GRANTED_NAV_ITEMS
    ]
    items: list[NavItem] = []
    for item in CURATOR_NAV_ITEMS:
        if item.key == "notifications":
            items.extend(extra)
            extra = []
        if item.key not in hidden:
            items.append(item)
    items.extend(extra)
    return tuple(items)


def student_nav_items(access_expired: bool = False) -> tuple[StudentNavItem, ...]:
    """У ученика с истёкшим сроком доступа (`User.access_until`) в меню
    остаётся только «Личная информация» — остальные разделы ему всё равно
    отдадут 403 из `get_current_user`, и меню из живых на вид ссылок,
    отбрасывающих обратно, читалось бы как поломка платформы, а не как
    закрытый доступ."""
    if access_expired:
        return tuple(item for item in STUDENT_NAV_ITEMS if item.key == "personal")
    if not LAB3D_OPEN_FOR_STUDENTS:
        return tuple(item for item in STUDENT_NAV_ITEMS if item.key != "3dlab")
    return STUDENT_NAV_ITEMS


# Меню модератора-наблюдателя (решение владельца 28.09.2026): только то, что
# ему открывает белый список `rbac.py::is_moderator_request_allowed`, плюс
# разделы, открытые ему сверх роли (`section_access.py`, 03.10.2026). Ранг у
# него как у ГП, поэтому по `min_rank` меню собралось бы целиком — из
# ссылок, отвечающих «Нет доступа».
MODERATOR_NAV_KEYS = ("students", "archive", "activity")

# «Статистика активности» — пункт только модератора: у ГП и суперадмина она
# открывается с дашборда, их меню эта правка не меняет.
MODERATOR_ONLY_NAV_ITEMS: tuple[StaffNavItem, ...] = (
    StaffNavItem(
        key="activity",
        href="/cabinet/superadmin/activity",
        sidebar_label="Статистика активности",
        pill_label="Статистика",
        aria_label="Статистика активности",
        tooltip="Активность учеников и сотрудников",
        icon="cases",
    ),
)

# Пункты разделов, у которых в меню ГП своего пункта нет: ГП заходит в них с
# дашборда или по ссылкам. Модератору их показывают, когда раздел открыт ему
# сверх роли — дашборда у него нет.
GRANTED_ONLY_STAFF_NAV_ITEMS: tuple[StaffNavItem, ...] = (
    StaffNavItem(
        key="guest_exam",
        href="/cabinet/staff/guest-exam",
        sidebar_label="Гостевой пробник",
        pill_label="Гости",
        aria_label="Гостевой пробник",
        tooltip="Ссылка, билеты и работы гостей",
        icon="mock",
    ),
    StaffNavItem(
        key="exams",
        href="/cabinet/exam-assignments",
        sidebar_label="Билеты и периоды",
        pill_label="Билеты",
        aria_label="Билеты и периоды",
        tooltip="Задания пробников и периоды сдачи",
        icon="program",
    ),
    StaffNavItem(
        key="people",
        href="/cabinet/superadmin/users",
        sidebar_label="Пользователи",
        pill_label="Люди",
        aria_label="Пользователи",
        tooltip="Аккаунты, роли и тарифы",
        icon="profile",
    ),
)


def staff_nav_items(
    role_rank: int,
    role_name: str | None = None,
    closed_sections: frozenset[str] | None = None,
    granted_sections: frozenset[str] | None = None,
) -> tuple[StaffNavItem, ...]:
    hidden = closed_nav_keys(closed_sections)
    if role_name == "модератор":
        by_key = {
            item.key: item
            for item in STAFF_NAV_ITEMS + MODERATOR_ONLY_NAV_ITEMS + GRANTED_ONLY_STAFF_NAV_ITEMS
        }
        keys = [key for key in MODERATOR_NAV_KEYS if key not in hidden]
        keys += [
            key for key in granted_nav_keys(granted_sections)
            if key in by_key and key not in keys
        ]
        return tuple(by_key[key] for key in keys)
    # Раздел, открытый сверх роли, показывает свой пункт и выше ранга — так
    # ГП видит «Рассылки», когда суперадмин открыл их в «Доступах». Остальные
    # разделы ГП положены по роли, так что это касается только сверх-ролевых.
    granted = set(granted_nav_keys(granted_sections))
    return tuple(
        item for item in STAFF_NAV_ITEMS
        if (item.is_visible_for(role_rank) or item.key in granted) and item.key not in hidden
    )
