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

# ── Mock exam ─────────────────────────────────────────────────────────────────

MOCK_SUBJECTS = ["Рисунок", "Композиция"]

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
EXAM_SUBJECT_HINTS = ("Р+К", "Р+Ч", "Р+К+Ч", "К+Ч")
COURSE_PERIODS = ["10-14 июня", "15-20 июня", "22-27 июня"]
LESSON_COUNTS = ["6", "8", "10"]
