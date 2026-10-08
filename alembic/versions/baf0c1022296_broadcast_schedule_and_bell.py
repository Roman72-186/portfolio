"""broadcast_schedule_and_bell — отложенная рассылка и копия в колокольчике (08.10.2026)

Ответы службы заботы на вопросы плана
`plans/2026-10-07-apparchi-сообщения-лизы-я-с-вами.md` (владелец 08.10.2026):
«нужно добавить запланированную рассылку, согласно часового пояса МСК и
часового пояса ученика» и «[в колокольчик] нужен тоже».

- `broadcasts`: статус `scheduled`, `schedule_mode` (`msk` / `local`),
  `scheduled_at` — выбранное время, `fire_at` — когда начать;
- `broadcast_recipients`: `due_at` — не раньше этого момента, `bell_at` —
  копия в колокольчике заведена;
- `notifications.broadcast_id` — уведомление показывает само сообщение.

Откат: запланированные рассылки становятся черновиками (статуса `scheduled`
прежняя схема не знает), поля расписания и колокольчика теряются. Уже
заведённые уведомления остаются, без ссылки на рассылку — простым текстом.

Revision ID: baf0c1022296
Revises: 460e8057ef96
Create Date: 2026-10-08 09:17:01.505852

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'baf0c1022296'
down_revision: Union[str, None] = '460e8057ef96'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("broadcasts") as batch:
        batch.add_column(sa.Column("schedule_mode", sa.String(10), nullable=True))
        batch.add_column(sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("fire_at", sa.DateTime(timezone=True), nullable=True))
        batch.drop_constraint("ck_broadcasts_status", type_="check")
        batch.create_check_constraint(
            "ck_broadcasts_status", "status IN ('draft', 'scheduled', 'sending', 'sent')",
        )
        batch.create_check_constraint(
            "ck_broadcasts_schedule_mode", "schedule_mode IS NULL OR schedule_mode IN ('msk', 'local')",
        )
    op.create_index("ix_broadcasts_status_fire_at", "broadcasts", ["status", "fire_at"])

    with op.batch_alter_table("broadcast_recipients") as batch:
        batch.add_column(sa.Column("due_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("bell_at", sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table("notifications") as batch:
        batch.add_column(sa.Column("broadcast_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_notifications_broadcast_id", "broadcasts", ["broadcast_id"], ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("notifications") as batch:
        batch.drop_constraint("fk_notifications_broadcast_id", type_="foreignkey")
        batch.drop_column("broadcast_id")

    with op.batch_alter_table("broadcast_recipients") as batch:
        batch.drop_column("bell_at")
        batch.drop_column("due_at")

    op.execute("UPDATE broadcasts SET status = 'draft' WHERE status = 'scheduled'")
    op.drop_index("ix_broadcasts_status_fire_at", table_name="broadcasts")
    with op.batch_alter_table("broadcasts") as batch:
        batch.drop_constraint("ck_broadcasts_schedule_mode", type_="check")
        batch.drop_constraint("ck_broadcasts_status", type_="check")
        batch.create_check_constraint(
            "ck_broadcasts_status", "status IN ('draft', 'sending', 'sent')",
        )
        batch.drop_column("fire_at")
        batch.drop_column("scheduled_at")
        batch.drop_column("schedule_mode")
