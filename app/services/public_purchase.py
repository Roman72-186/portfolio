"""Один договор допуска для покупки, анкеты и всех учебных маршрутов."""
from datetime import datetime
from urllib.parse import urlparse

from app.config import settings
from app.constants import TARIFFS_CURRENT
from app.models.payment import COHORT_FROM, KIND_MONTH, STATUS_PAID, Payment
from app.models.public_enrollment import PublicEnrollment
from app.models.user import User
from app.services import payments
from app.services.tz import _as_utc

PURCHASE_REQUIRED = "public_purchase_required"
AFTER_PAYMENT_TEXT = (
    "Остался последний шаг – заполни анкету годового курса. После этого появится "
    "ссылка на канал «Путь к сотке», где мы будем на связи во время обучения."
)


def invite_url() -> str:
    url = settings.public_purchase_invite_url.strip()
    parsed = urlparse(url)
    if (parsed.scheme == "https" and parsed.netloc in {"t.me", "telegram.me"}
            and parsed.path.strip("/")):
        return url
    return ""


def sales_ready() -> bool:
    return bool(
        settings.public_purchase_enabled and payments.payments_configured()
        and 1 <= settings.public_purchase_window_start <= settings.public_purchase_window_end <= 31
        and invite_url()
        and settings.telegram_login_client_id and settings.telegram_login_client_secret
        and settings.telegram_bot_token and settings.telegram_channel_id
    )


def catalog(db) -> list[dict]:
    result = []
    for tariff in TARIFFS_CURRENT:
        amount = payments.reference_price_kop(db, tariff, COHORT_FROM)
        if amount and amount > 0:
            result.append({"tariff": tariff, "amount_kop": amount})
    return result


def enrollment_for(db, user_id: int, *, lock=False):
    query = db.query(PublicEnrollment).filter(PublicEnrollment.user_id == user_id)
    if lock:
        query = query.populate_existing().with_for_update()
    return query.first()


def confirmed_payment(db, enrollment) -> Payment | None:
    if enrollment is None or enrollment.first_payment_id is None:
        return None
    # Не db.get: в identity map мог остаться оплаченный объект до ожидания
    # ответа Telegram, пока другой запрос уже оформил возврат.
    payment = db.query(Payment).filter(Payment.id == enrollment.first_payment_id).populate_existing().first()
    if (payment and payment.user_id == enrollment.user_id
            and payment.kind == KIND_MONTH and payment.status == STATUS_PAID):
        return payment
    return None


def allows_path(db, enrollment, path: str) -> bool:
    if path in {"/purchase", "/purchase/pay", "/purchase/check-channel", "/csrf", "/logout"}:
        return True
    return path == "/cabinet/profile" and confirmed_payment(db, enrollment) is not None


def checkout(db, user: User, base_url: str, now: datetime) -> Payment:
    # Один покупатель – один незавершённый заказ, в том числе из двух вкладок.
    enrollment = enrollment_for(db, user.id, lock=True)
    user = db.query(User).filter(User.id == user.id).populate_existing().with_for_update().one()
    if not enrollment or enrollment.activated_at or enrollment.first_payment_id:
        raise payments.PaymentUnavailable("Первая покупка уже оплачена. Обнови страницу.")
    if not sales_ready() or enrollment.selected_tariff not in {t["tariff"] for t in catalog(db)}:
        raise payments.PaymentUnavailable("Приём оплат пока закрыт. Напиши в службу заботы.")
    if not user.is_active or user.deleted_at is not None:
        raise payments.PaymentUnavailable("Аккаунт недоступен. Напиши в службу заботы.")
    user.pay_cohort = COHORT_FROM
    user.pay_next_tariff = enrollment.selected_tariff
    if not payments.has_window(user):
        user.pay_window_start = settings.public_purchase_window_start
        user.pay_window_end = settings.public_purchase_window_end
    return payments.get_or_create_payment(db, user, now, base_url, public_purchase=True)


def activate(db, user_id: int, now: datetime) -> bool:
    enrollment = enrollment_for(db, user_id, lock=True)
    user = db.query(User).filter(User.id == user_id).populate_existing().with_for_update().one()
    if not enrollment:
        return False
    if enrollment.activated_at:
        return True
    if (not confirmed_payment(db, enrollment) or not user.profile_completed
            or not user.is_active or user.deleted_at is not None or not user.is_group_member
            or user.paid_until is None or _as_utc(user.paid_until) <= _as_utc(now)):
        return False
    from app.models.audit_log import AuditLog
    from app.services.user_management import open_program_from_now, tag_annual_newcomer
    enrollment.activated_at = now
    open_program_from_now(user)
    tag_annual_newcomer(db, user)
    db.add(AuditLog(action="public_purchase_activated", performed_by_id=user.id,
                    target_user_id=user.id, details="Анкета и участие в канале подтверждены"))
    return True
