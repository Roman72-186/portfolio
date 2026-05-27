"""feedback_messages (диалог) + backfill из старых feedbacks/feedback_photos

Revision ID: d1e2f3a4b5c6
Revises: d0e1f2a3b4c5
Create Date: 2026-05-23
"""
from alembic import op
import sqlalchemy as sa


revision = "d1e2f3a4b5c6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feedback_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "feedback_id",
            sa.Integer(),
            sa.ForeignKey("feedbacks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sender_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("sender_role", sa.String(length=20), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("photo_s3_path", sa.String(length=500), nullable=True),
        sa.Column("photo_s3_url", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(text IS NOT NULL AND length(text) > 0) OR (photo_s3_url IS NOT NULL)",
            name="ck_feedback_messages_text_or_photo",
        ),
    )
    op.create_index(
        "ix_feedback_messages_feedback_created",
        "feedback_messages",
        ["feedback_id", "created_at"],
    )

    # ── Backfill: старые Feedback с 4 текстовыми полями → одно сообщение,
    #    каждое FeedbackPhoto → отдельное сообщение от того же куратора.
    #    Делаем через SQL, чтобы не зависеть от моделей.
    conn = op.get_bind()
    feedbacks = conn.execute(
        sa.text(
            "SELECT id, curator_id, greeting, strengths, weaknesses, recommendations, created_at "
            "FROM feedbacks ORDER BY id"
        )
    ).fetchall()

    for fb in feedbacks:
        fb_id, curator_id, greeting, strengths, weaknesses, recs, created_at = fb
        parts = []
        if greeting and greeting.strip():
            parts.append(greeting.strip())
        if strengths and strengths.strip():
            parts.append(f"**Сильные стороны:**\n{strengths.strip()}")
        if weaknesses and weaknesses.strip():
            parts.append(f"**Слабые стороны:**\n{weaknesses.strip()}")
        if recs and recs.strip():
            parts.append(f"**Рекомендации:**\n{recs.strip()}")
        combined_text = "\n\n".join(parts) if parts else None

        if combined_text:
            conn.execute(
                sa.text(
                    "INSERT INTO feedback_messages "
                    "(feedback_id, sender_id, sender_role, text, photo_s3_path, photo_s3_url, created_at) "
                    "VALUES (:fb_id, :sender_id, 'curator', :text, NULL, NULL, :created_at)"
                ),
                {"fb_id": fb_id, "sender_id": curator_id, "text": combined_text, "created_at": created_at},
            )

        # Все фото к этому feedback → отдельные сообщения от того же куратора
        photos = conn.execute(
            sa.text(
                "SELECT s3_path, s3_url, order_idx FROM feedback_photos "
                "WHERE feedback_id = :fb_id ORDER BY order_idx, id"
            ),
            {"fb_id": fb_id},
        ).fetchall()
        for s3_path, s3_url, _order_idx in photos:
            conn.execute(
                sa.text(
                    "INSERT INTO feedback_messages "
                    "(feedback_id, sender_id, sender_role, text, photo_s3_path, photo_s3_url, created_at) "
                    "VALUES (:fb_id, :sender_id, 'curator', NULL, :s3_path, :s3_url, :created_at)"
                ),
                {
                    "fb_id": fb_id,
                    "sender_id": curator_id,
                    "s3_path": s3_path,
                    "s3_url": s3_url,
                    "created_at": created_at,
                },
            )


def downgrade() -> None:
    op.drop_index("ix_feedback_messages_feedback_created", table_name="feedback_messages")
    op.drop_table("feedback_messages")
