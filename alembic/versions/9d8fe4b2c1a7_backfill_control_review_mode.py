"""Freeze the available classification for controls submitted before the mode was stored.

Past tariffs and block deadlines cannot be reconstructed. Use the settings
available at upgrade time and the original completion marker where it exists.
"""

from datetime import datetime, time, timedelta, timezone

from alembic import op
import sqlalchemy as sa

revision = "9d8fe4b2c1a7"
down_revision = "7bc519ee2810"
branch_labels = None
depends_on = None

MOSCOW = timezone(timedelta(hours=3))


def _utc(value):
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _mode(submitted_at, completed_at, deadline):
    if deadline is None:
        return "full"
    first = _utc(submitted_at)
    if completed_at is not None:
        first = min(first, _utc(completed_at))
    deadline = _utc(deadline).astimezone(MOSCOW)
    if first <= deadline:
        return "full"
    next_evening = datetime.combine(deadline.date() + timedelta(days=1), time(19), tzinfo=MOSCOW)
    return "text" if first <= next_evening else "score"


def upgrade():
    date_time = sa.DateTime(timezone=True)
    blocks = sa.table("task_blocks", sa.column("id"), sa.column("block_type"), sa.column("submit_until", date_time))
    submissions = sa.table(
        "task_block_submissions", sa.column("id"), sa.column("block_id"), sa.column("user_id"),
        sa.column("submitted_at", date_time), sa.column("control_review_mode"),
    )
    states = sa.table(
        "task_block_states", sa.column("block_id"), sa.column("user_id"), sa.column("completed_at", date_time),
    )
    users = sa.table("users", sa.column("id"), sa.column("tariff"))
    overrides = sa.table(
        "task_block_tariff_deadlines", sa.column("block_id"), sa.column("tariff"),
        sa.column("submit_until", date_time),
    )
    deadline = sa.case(
        (overrides.c.block_id.is_not(None), overrides.c.submit_until),
        else_=blocks.c.submit_until,
    ).label("deadline")
    pending = submissions.c.control_review_mode.is_(None)
    query = (
        sa.select(submissions.c.id, submissions.c.submitted_at, states.c.completed_at, deadline)
        .select_from(submissions.join(blocks, submissions.c.block_id == blocks.c.id))
        .join(users, submissions.c.user_id == users.c.id)
        .outerjoin(states, sa.and_(
            states.c.block_id == submissions.c.block_id, states.c.user_id == submissions.c.user_id,
        ))
        .outerjoin(overrides, sa.and_(
            overrides.c.block_id == submissions.c.block_id,
            overrides.c.tariff == sa.func.upper(sa.func.trim(users.c.tariff)),
        ))
        .where(blocks.c.block_type == "timed", submissions.c.submitted_at.is_not(None), pending)
    )
    update = submissions.update().where(
        submissions.c.id == sa.bindparam("submission_id"), pending,
    ).values(control_review_mode=sa.bindparam("mode"))
    connection = op.get_bind()
    for batch in connection.execute(query).partitions(500):
        connection.execute(update, [
            {"submission_id": row.id, "mode": _mode(row.submitted_at, row.completed_at, row.deadline)}
            for row in batch
        ])


def downgrade():
    # Keep historical classifications when returning to the schema that uses them.
    pass
