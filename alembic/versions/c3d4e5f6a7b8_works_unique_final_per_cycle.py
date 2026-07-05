"""Partial unique index: no duplicate final attempt_number per (cycle_id, work_type)

Closes the race between two concurrent final-photo submissions in the same
Probnik/Otrabotka cycle (check-then-act in cycle_upload.py had no DB-level
guard) — two parallel requests could both compute the same next_attempt_number
before either commits. Scoped to (cycle_id, work_type, attempt_number) rather
than just (cycle_id, work_type): next_attempt_number()/tests/test_exam_cycle.py
intentionally allow several historical finals with different attempt_number in
one cycle. Verified against prod data before writing this migration: no
existing duplicate (cycle_id, work_type, attempt_number) rows with is_final=true.

Revision ID: c3d4e5f6a7b8
Revises: 375d357fbd05
Create Date: 2026-07-05
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = '375d357fbd05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'uq_works_cycle_final_attempt',
        'works',
        ['cycle_id', 'work_type', 'attempt_number'],
        unique=True,
        postgresql_where=text('is_final'),
    )


def downgrade() -> None:
    op.drop_index('uq_works_cycle_final_attempt', table_name='works')
