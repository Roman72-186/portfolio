"""broadcasts — рассылки ученикам от преподавателя через бота (08.10.2026)

План — `plans/2026-10-07-apparchi-сообщения-лизы-я-с-вами.md`.

- `broadcasts` — сообщение (текст в разметке Telegram, одно вложение),
  проверка у преподавателя, состояние отправки;
- `broadcast_tariffs` / `broadcast_levels` / `broadcast_students` — кому, та же
  тройка, что у блока задания;
- `broadcast_recipients` — журнал по ученику: отправлено, не дошло и почему.

**Цепочка.** Встаёт после `904398797db7` (оплата, параллельная сессия,
закоммичена 08.10.2026 в `0e090ec`). Перед выкаткой — `alembic heads`: ровно
одна строка (AGENTS.md, правило 13).

Откат — снос пяти таблиц, журнал рассылок теряется.

Revision ID: 460e8057ef96
Revises: 904398797db7
Create Date: 2026-10-08 00:04:01.510901

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '460e8057ef96'
down_revision: Union[str, None] = '904398797db7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("media_kind", sa.String(20), nullable=True),
        sa.Column("media_s3_path", sa.String(500), nullable=True),
        sa.Column("media_s3_url", sa.String(500), nullable=True),
        sa.Column("media_filename", sa.String(200), nullable=True),
        sa.Column("media_content_type", sa.String(100), nullable=True),
        sa.Column("telegram_file_id", sa.String(300), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("preview_fingerprint", sa.String(64), nullable=True),
        sa.Column("preview_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("preview_token", sa.String(64), nullable=True),
        sa.Column("preview_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("sending_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('draft', 'sending', 'sent')", name="ck_broadcasts_status"),
        sa.CheckConstraint(
            "media_kind IS NULL OR media_kind IN ('photo', 'voice', 'video_note')",
            name="ck_broadcasts_media_kind",
        ),
    )
    op.create_index("ix_broadcasts_created_at", "broadcasts", ["created_at"])

    op.create_table(
        "broadcast_tariffs",
        sa.Column(
            "broadcast_id", sa.Integer(),
            sa.ForeignKey("broadcasts.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("tariff", sa.String(50), primary_key=True),
    )
    op.create_table(
        "broadcast_levels",
        sa.Column(
            "broadcast_id", sa.Integer(),
            sa.ForeignKey("broadcasts.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("level", sa.Integer(), primary_key=True),
        sa.CheckConstraint("level IN (1, 2)", name="ck_broadcast_levels_level"),
    )
    op.create_table(
        "broadcast_students",
        sa.Column(
            "broadcast_id", sa.Integer(),
            sa.ForeignKey("broadcasts.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
        ),
    )
    op.create_table(
        "broadcast_recipients",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "broadcast_id", sa.Integer(),
            sa.ForeignKey("broadcasts.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error", sa.String(300), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("broadcast_id", "user_id", name="uq_broadcast_recipients_user"),
    )
    op.create_index(
        "ix_broadcast_recipients_status", "broadcast_recipients", ["broadcast_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_broadcast_recipients_status", table_name="broadcast_recipients")
    op.drop_table("broadcast_recipients")
    op.drop_table("broadcast_students")
    op.drop_table("broadcast_levels")
    op.drop_table("broadcast_tariffs")
    op.drop_index("ix_broadcasts_created_at", table_name="broadcasts")
    op.drop_table("broadcasts")
