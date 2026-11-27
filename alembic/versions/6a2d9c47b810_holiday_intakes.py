"""Store named holiday intakes independently of track access."""

from alembic import op
import sqlalchemy as sa

revision = "6a2d9c47b810"
down_revision = "f84d92b13c50"
branch_labels = None
depends_on = None


def upgrade():
    intakes = op.create_table(
        "holiday_intakes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(100), nullable=False, unique=True),
    )
    for table in ("intake_links", "users"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("holiday_intake_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                f"fk_{table}_holiday_intake_id", "holiday_intakes", ["holiday_intake_id"], ["id"],
            )

    # Переносим подтверждённую разметку из фильтра, включая исторические ссылки.
    conn = op.get_bind()
    links = sa.table("intake_links", sa.column("id"), sa.column("program"), sa.column("holiday_intake_id"))
    users = sa.table("users", sa.column("id"), sa.column("program"), sa.column("holiday_intake_id"))
    for title, link_ids, user_id in (
        ("Октябрь 2026", (3, 4, 5, 6, 7), 293),
        ("Ноябрь 2026", (8,), 328),
        ("Старт 29 ноября", (9, 14, 15, 16), 350),
    ):
        intake_id = conn.execute(intakes.insert().values(title=title)).inserted_primary_key[0]
        conn.execute(links.update().where(
            links.c.id.in_(link_ids), links.c.program == "holiday",
        ).values(holiday_intake_id=intake_id))
        conn.execute(users.update().where(
            users.c.id == user_id, users.c.program == "holiday",
        ).values(holiday_intake_id=intake_id))


def downgrade():
    for table in ("users", "intake_links"):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_holiday_intake_id", type_="foreignkey")
            batch.drop_column("holiday_intake_id")
    op.drop_table("holiday_intakes")
