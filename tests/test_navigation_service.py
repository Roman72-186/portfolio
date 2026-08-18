from app.services.navigation import curator_nav_items, staff_nav_items, student_nav_items


def test_curator_nav_items_keep_current_contract():
    items = curator_nav_items()

    assert [(item.key, item.href, item.label) for item in items] == [
        ("dashboard", "/cabinet/curator", "Кабинет"),
        ("students", "/cabinet/students", "Ученики"),
        ("cycles", "/cabinet/staff/cycles", "Цикл Пробника"),
        ("reports", "/cabinet/curator/reports", "Отчёты"),
        ("statistics", "/cabinet/students?tab=statistics", "Статистика"),
    ]


def test_student_nav_items_keep_current_contract():
    items = student_nav_items()

    assert [
        (
            item.key,
            item.desktop_href,
            item.mobile_href,
            item.desktop_label,
            item.mobile_label,
            item.aria_label,
            item.soon,
        )
        for item in items
    ] == [
        ("tracker", "/cabinet/tracker", "/cabinet/tracker", "Личный трекер", "Трекер", "Личный трекер", False),
        (
            "learning",
            "/cabinet/learning",
            "/cabinet/learning",
            "Актуальное образовательное пространство",
            "Обучение",
            "Актуальное образовательное пространство",
            False,
        ),
        ("3dlab", "/3dlab", "/3dlab", "3D Лаб", "3D Лаб", "3D Лаб", False),
        ("portfolio", "/cabinet/portfolio", "/cabinet/portfolio", "Портфолио", "Портфолио", "Портфолио", False),
        ("statistics", "#", "#", "Статистика", "Статистика", "Статистика (скоро)", True),
        ("personal", "/cabinet/personal", "/cabinet/personal", "Личная информация", "Личное", "Личная информация", False),
    ]


def test_staff_nav_items_keep_admin_contract():
    items = staff_nav_items(role_rank=4)

    assert [
        (
            item.key,
            item.href,
            item.sidebar_label,
            item.pill_label,
            item.aria_label,
            item.tooltip,
        )
        for item in items
    ] == [
        ("dashboard", "/cabinet", "Кабинет", "Кабинет", "Кабинет", "Кабинет"),
        ("students", "/cabinet/students", "Ученики", "Ученики", "Ученики", "Ученики"),
        ("cycles", "/cabinet/staff/cycles", "Цикл Пробника", "Цикл", "Цикл Пробника", "Цикл Пробника"),
        ("cases", "/cabinet/cases", "Кейсы", "Кейсы", "Кейсы", "Кейсы по пробникам"),
        ("videos", "/cabinet/admin/videos", "Видео", "Видео", "Управление видео", "Видеоуроки"),
        ("3dlab", "/3dlab", "3D Лаб", "3D Лаб", "3D Лаб", "3D Лаборатория"),
        (
            "reports",
            "/cabinet/curator/reports",
            "Видео-отчёты",
            "Отчёты",
            "Видео-отчёты",
            "Видео-отчёты кураторов",
        ),
        (
            "guest_exam",
            "/cabinet/staff/guest-exam",
            "Гостевой режим",
            "Гости",
            "Гостевой режим",
            "Гостевой режим — пробник для участников без регистрации",
        ),
    ]


def test_staff_nav_items_keep_rank_specific_visibility_contract():
    rank_3_items = staff_nav_items(role_rank=3)
    rank_4_items = staff_nav_items(role_rank=4)
    rank_5_items = staff_nav_items(role_rank=5)

    assert [item.key for item in rank_3_items] == [
        "dashboard",
        "students",
        "mock_check",
        "cycles",
        "3dlab",
    ]
    assert "mock_check" not in [item.key for item in rank_4_items]
    assert "mock_check" not in [item.key for item in rank_5_items]
    assert "cases" not in [item.key for item in rank_3_items]
    assert "cases" in [item.key for item in rank_4_items]
    assert "cases" in [item.key for item in rank_5_items]
    assert "videos" not in [item.key for item in rank_3_items]
    assert "videos" in [item.key for item in rank_4_items]
    assert "videos" in [item.key for item in rank_5_items]
    assert "reports" not in [item.key for item in rank_3_items]
    assert "reports" in [item.key for item in rank_4_items]
    assert "reports" in [item.key for item in rank_5_items]
    assert "guest_exam" not in [item.key for item in rank_3_items]
    assert "guest_exam" in [item.key for item in rank_4_items]
    assert "guest_exam" in [item.key for item in rank_5_items]
