"""Добавить бессрочный режим подписки

Идентификатор ревизии: 20260408_0002
Пересматривает: 20260408_0001
Дата создания: 2026-04-08 19:35:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260408_0002"
down_revision = "20260408_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column(
            "is_lifetime",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.execute(
        """
        UPDATE subscriptions
        SET is_lifetime = TRUE,
            status = CASE
                WHEN status = 'expired' THEN 'active'
                ELSE status
            END
        WHERE user_id IN (
            SELECT id FROM users WHERE is_admin = TRUE
        )
        """
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "is_lifetime")
