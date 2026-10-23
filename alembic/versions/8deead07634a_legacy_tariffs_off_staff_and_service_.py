"""Старые тарифы «МАКСИМУМ» и «УВЕРЕННЫЙ» сняты с сотрудников и служебных (23.10.2026)

Владелец 23.10.2026: «старые тарифы, которые МАКСИМУМ и УВЕРЕННЫЙ… просто
скрыть, нигде не показывать, они не должны участвовать и прослеживаться».
Замер на проде в тот же день: в настройках заданий, циклов, событий,
рассылок и цен старых значений нет; у действующих учеников — только два
служебных; держат их 26 аккаунтов сотрудников (тариф им поставил прежний
дефолт при создании, на права не влияет) и архив прошлого потока.

Решения владельца: сотрудникам — снять тариф (пусто, как у созданных после
28.09.2026), всем 26 — и действующим, и удалённым; служебным ученикам
277 «служба заботы» и 286 «vlrk» — «УВЕРЕННЫЙ МАКСИМУМ»; архив не трогать.

Значения прода заморожены константами; условие по текущему тарифу делает
повторный запуск пустым, на пустой базе (тесты) миграция ничего не находит.
Откат возвращает прежние значения тем же спискам.

Revision ID: 8deead07634a
Revises: a7c3e9f1b2d4
Create Date: 2026-10-23 12:40:25.695751

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8deead07634a'
down_revision: Union[str, None] = 'a7c3e9f1b2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY = ("МАКСИМУМ", "УВЕРЕННЫЙ")
CONFIDENT_MAX = "УВЕРЕННЫЙ МАКСИМУМ"

# Сотрудники со старым тарифом на 23.10.2026 и что у них стояло.
STAFF_MAXIMUM = (3, 99, 106)
STAFF_CONFIDENT = (
    10, 12, 103, 105, 107, 108, 109, 131, 132, 133, 136, 139, 141, 150,
    160, 169, 170, 171, 172, 173, 174, 175, 278,
)
# Служебные ученические аккаунты («УВЕРЕННЫЙ» → «УВЕРЕННЫЙ МАКСИМУМ»).
SERVICE_STUDENTS = (277, 286)


def _update(sql: str, **params) -> None:
    stmt = sa.text(sql)
    for name, value in params.items():
        if isinstance(value, tuple):
            stmt = stmt.bindparams(sa.bindparam(name, expanding=True))
    op.get_bind().execute(stmt, params)


def upgrade() -> None:
    _update(
        "UPDATE users SET tariff = '' WHERE id IN :ids AND tariff IN :legacy",
        ids=STAFF_MAXIMUM + STAFF_CONFIDENT, legacy=LEGACY,
    )
    _update(
        "UPDATE users SET tariff = :new WHERE id IN :ids AND tariff = 'УВЕРЕННЫЙ'",
        ids=SERVICE_STUDENTS, new=CONFIDENT_MAX,
    )


def downgrade() -> None:
    _update(
        "UPDATE users SET tariff = 'МАКСИМУМ' WHERE id IN :ids AND tariff = ''",
        ids=STAFF_MAXIMUM,
    )
    _update(
        "UPDATE users SET tariff = 'УВЕРЕННЫЙ' WHERE id IN :ids AND tariff = ''",
        ids=STAFF_CONFIDENT,
    )
    _update(
        "UPDATE users SET tariff = 'УВЕРЕННЫЙ' WHERE id IN :ids AND tariff = :new",
        ids=SERVICE_STUDENTS, new=CONFIDENT_MAX,
    )
