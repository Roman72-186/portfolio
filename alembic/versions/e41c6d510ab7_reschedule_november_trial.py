"""Move the November trial a week later."""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "e41c6d510ab7"
down_revision = "d81f9a44226e"
branch_labels = depends_on = None

OLD_OPEN = datetime(2026, 11, 22, 5, tzinfo=timezone.utc)
OLD_END = datetime(2026, 11, 29, 18, tzinfo=timezone.utc)
NEW_OPEN = datetime(2026, 11, 29, 5, tzinfo=timezone.utc)
NEW_END = datetime(2026, 12, 6, 18, tzinfo=timezone.utc)


def _move(old_open, old_end, new_open, new_end):
    db = op.get_bind()
    params = dict(old_open=old_open, old_end=old_end, new_open=new_open, new_end=new_end)
    # Before payment the intake sets the deadline, and does not attach the tag.
    # Only use that deadline as a cohort marker if no other link shares it.
    db.execute(sa.text("""
        UPDATE users SET access_until = :new_end
        WHERE program = 'holiday' AND access_until = :old_end
          AND EXISTS (SELECT 1 FROM intake_links
                      WHERE slug = 'testpodgotovki' AND access_until = :old_end)
          AND (EXISTS (SELECT 1 FROM user_tags ut JOIN intake_links il ON il.tag_id = ut.tag_id
                       WHERE ut.user_id = users.id AND il.slug = 'testpodgotovki')
               OR NOT EXISTS (SELECT 1 FROM intake_links
                              WHERE slug <> 'testpodgotovki' AND access_until = :old_end))
    """), params)
    db.execute(sa.text("""
        UPDATE learning_topics SET opens_at = :new_open, ends_at = :new_end
        WHERE program = 'holiday' AND opens_at = :old_open AND ends_at = :old_end
          AND id IN (SELECT lt.topic_id FROM learning_topic_tags lt
                     JOIN intake_links il ON il.tag_id = lt.tag_id
                     WHERE il.slug = 'testpodgotovki')
    """), params)
    db.execute(sa.text("""
        UPDATE intake_links SET access_until = :new_end
        WHERE slug = 'testpodgotovki' AND access_until = :old_end
    """), params)


def upgrade():
    _move(OLD_OPEN, OLD_END, NEW_OPEN, NEW_END)


def downgrade():
    _move(NEW_OPEN, NEW_END, OLD_OPEN, OLD_END)
