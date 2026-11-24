"""Ноябрьский тест-драйв подготовки, 29 ноября – 6 декабря 2026."""
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from alembic import op

revision = "29f5f31e81b7"
down_revision = "5e22a6d2b991"
branch_labels = None
depends_on = None


def upgrade():
    db = op.get_bind()
    title = "Попробуй подготовку · 29 ноября"
    msk = timezone(timedelta(hours=3))
    params = dict(title=title, start=datetime(2026, 11, 29, 12, tzinfo=msk),
                  end=datetime(2026, 12, 6, 21, tzinfo=msk))
    params["tag"] = db.execute(sa.text("""
        INSERT INTO tags (name, created_at, is_hidden)
        VALUES (:title, CURRENT_TIMESTAMP, false) RETURNING id
    """), params).scalar_one()
    db.execute(sa.text("""
        INSERT INTO intake_links
            (slug, title, program, access_until, is_active, price_kop, tag_id, created_at)
        VALUES ('testpodgotovki-29', :title, 'holiday', :end, true, 345500, :tag, CURRENT_TIMESTAMP)
    """), params)
    parent = None
    for kind, name in (("period", title), ("stage", "Пробная неделя"),
                       ("week", "Подготовка к творческим экзаменам")):
        parent = db.execute(sa.text("""
            INSERT INTO learning_topics
                (kind, title, parent_id, program, opens_at, ends_at, sort_order,
                 assign_to_all, tariff_restricted, is_published, created_at, updated_at)
            VALUES (:kind, :name, :parent, 'holiday', :start, :end, 0,
                    false, false, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) RETURNING id
        """), dict(params, kind=kind, name=name, parent=parent)).scalar_one()
        db.execute(sa.text("""
            INSERT INTO learning_topic_tags (topic_id, tag_id) VALUES (:topic, :tag)
        """), dict(params, topic=parent))


def downgrade():
    # Сохраняем платежи и учебные работы. Повторный выпуск требует сверки данных.
    op.execute("UPDATE intake_links SET is_active=false WHERE slug='testpodgotovki-29'")
