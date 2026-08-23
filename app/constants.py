"""Shared constants — single source of truth for the whole application."""

# ── Months ───────────────────────────────────────────────────────────────────

MONTHS = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]
MONTH_TO_NUM = {m: i + 1 for i, m in enumerate(MONTHS)}

# ── Tariffs ──────────────────────────────────────────────────────────────────

# Canonical form stored in the database — always UPPER.
TARIFFS = ["МАКСИМУМ", "УВЕРЕННЫЙ", "Я С ВАМИ"]

# Human-readable display form used in S3/Drive paths and UI labels.
TARIFF_DISPLAY = {
    "МАКСИМУМ": "Максимум",
    "УВЕРЕННЫЙ": "Уверенный",
    "Я С ВАМИ": "Я с вами",
}

# Short codes sent to n8n.
TARIFF_CODES = {
    "МАКСИМУМ": "01",
    "УВЕРЕННЫЙ": "02",
    "Я С ВАМИ": "03",
}

# Тарифы с обратной связью куратора: закрытие домашки/пробника ждёт кнопку
# «Принять работу»/«Закрыть цикл», а не факт загрузки финального фото.
# Решение владельца 23.08 — plans/2026-08-23-apparchi-week-month-gate-decisions.md.
TARIFFS_WITH_FEEDBACK = {"МАКСИМУМ", "УВЕРЕННЫЙ"}

# ── Mock exam ─────────────────────────────────────────────────────────────────

MOCK_SUBJECTS = ["Рисунок", "Композиция"]

# Тип экзаменационного задания. Метка-only: механика (цикл/локи/попытки/доступ)
# не зависит от kind — она ключуется по subject. См. ExamAssignment.kind.
#
# "guest" — билеты гостевого режима (Трек B). ВСЕГДА исключать этот kind из
# любого резолвера/списка/уведомления, адресованного настоящим ученикам —
# см. app/services/exam_cycle.py::get_active_tickets,
# app/services/exam_scheduler.py::_run_notification_check,
# app/api/cabinet_superadmin.py::exam_assignments_hub/_render_assignment_list.
ASSIGNMENT_KINDS = ("mock", "control", "guest")
ASSIGNMENT_KIND_LABELS = {"mock": "Пробник", "control": "Контрольная", "guest": "Гостевой"}

# ── Feature periods ───────────────────────────────────────────────────────────

FEATURE_PORTFOLIO_UPLOAD = "portfolio_upload"
FEATURE_MOCK_EXAM = "mock_exam"
FEATURE_RETAKE = "retake"

FEATURE_LABELS = {
    FEATURE_PORTFOLIO_UPLOAD: "Загрузка портфолио",
    FEATURE_MOCK_EXAM: "Пробные экзамены",
    FEATURE_RETAKE: "Отработки",
}

ENROLLMENT_YEARS = list(range(2020, 2031))  # 2020–2030

# ── Student tags (admin LK) ───────────────────────────────────────────────────

STUDY_MODES = ("offline", "online")
STUDY_MODE_LABELS = {"offline": "ОЧНО", "online": "ОНЛАЙН"}
EXAM_SUBJECT_HINTS = ("Р", "К", "Р + К")
COURSE_PERIODS = ["10-14 июня", "15-20 июня", "22-27 июня"]
LESSON_COUNTS = ["6", "8", "10"]

# Период "10-14 июня" обязателен для всех учеников — отображается всегда
# отмеченным в профиле и не может быть снят.
MANDATORY_COURSE_PERIOD = COURSE_PERIODS[0]

COHORT_TAGS = {"may", "june", "july", "august"}
COHORT_TAG_LABELS = {
    "may": "МАЙСКАЯ · М",
    "june": "ИЮНЬ · И",
    "july": "ИЮЛЬ · И",
    "august": "АВГУСТ · А",
}
