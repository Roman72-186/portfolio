"""Индекс на exam_cycles.ticket_id — подборка соседей по баллу
(Портфолио → Пробные экзамены, владелец 11.09.2026).

Revision ID: c6623ea9c67f
Revises: 7c1d9e4f0a2b
Create Date: 2026-09-11
"""
from alembic import op


revision = "c6623ea9c67f"
down_revision = "7c1d9e4f0a2b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_exam_cycles_ticket_id", "exam_cycles", ["ticket_id"])


def downgrade() -> None:
    op.drop_index("ix_exam_cycles_ticket_id", table_name="exam_cycles")
