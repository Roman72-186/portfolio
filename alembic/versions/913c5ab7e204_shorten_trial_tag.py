"""Shorten the test-drive tag while keeping its existing assignments."""
from alembic import op
import sqlalchemy as sa

revision = "913c5ab7e204"
down_revision = "e41c6d510ab7"
branch_labels = depends_on = None


def _rename(before: str, after: str) -> None:
    op.get_bind().execute(sa.text("""
        UPDATE tags SET name = :after
        WHERE name = :before
          AND id = (SELECT tag_id FROM intake_links WHERE slug = 'testpodgotovki')
    """), {"before": before, "after": after})


def upgrade() -> None:
    _rename("Попробуй подготовку · ноябрь 2026", "Попробуй подготовку")


def downgrade() -> None:
    _rename("Попробуй подготовку", "Попробуй подготовку · ноябрь 2026")
