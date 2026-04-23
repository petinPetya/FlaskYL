"""Добавить поля верификации email у пользователя

Идентификатор ревизии: 20260423_0003
Пересматривает: 20260408_0002
Дата создания: 2026-04-23 16:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260423_0003"
down_revision = "20260408_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(), nullable=True))
    op.add_column(
        "users",
        sa.Column("email_verification_sent_at", sa.DateTime(), nullable=True),
    )
    op.execute(
        """
        UPDATE users
        SET email_verified_at = CURRENT_TIMESTAMP
        WHERE email_verified_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("users", "email_verification_sent_at")
    op.drop_column("users", "email_verified_at")
