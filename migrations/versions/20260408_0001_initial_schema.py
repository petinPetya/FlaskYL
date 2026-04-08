"""Initial schema

Revision ID: 20260408_0001
Revises:
Create Date: 2026-04-08 18:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260408_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tariffs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("days_valid", sa.Integer(), nullable=False),
        sa.Column("device_limit", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("traffic_limit_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("is_popular", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("tariff_id", sa.String(length=36), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "used_traffic_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("traffic_limit_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="active",
        ),
        sa.Column("config_code", sa.String(length=100), nullable=True),
        sa.Column("public_key", sa.String(length=128), nullable=True),
        sa.Column("private_key", sa.String(length=128), nullable=True),
        sa.Column("vpn_ip", sa.String(length=15), nullable=True),
        sa.Column("auto_renew", sa.Boolean(), nullable=True),
        sa.Column("last_renewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["tariff_id"], ["tariffs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("config_code"),
    )
    op.create_index(
        op.f("ix_subscriptions_status"),
        "subscriptions",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_subscriptions_user_id"),
        "subscriptions",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "devices",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("subscription_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "provisioning_state",
            sa.String(length=32),
            nullable=False,
            server_default="requested",
        ),
        sa.Column("vpn_uuid", sa.String(length=64), nullable=True),
        sa.Column("vpn_email", sa.String(length=255), nullable=True),
        sa.Column("vpn_link", sa.Text(), nullable=True),
        sa.Column("assigned_ip", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("provisioned_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["subscriptions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_devices_provisioning_state"),
        "devices",
        ["provisioning_state"],
        unique=False,
    )
    op.create_index(op.f("ix_devices_status"), "devices", ["status"], unique=False)
    op.create_index(
        op.f("ix_devices_subscription_id"),
        "devices",
        ["subscription_id"],
        unique=False,
    )
    op.create_table(
        "invoices",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("subscription_id", sa.String(length=36), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("payment_system", sa.String(length=32), nullable=True),
        sa.Column("payment_system_id", sa.String(length=255), nullable=True),
        sa.Column("payment_url", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["subscriptions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_invoices_payment_system_id"),
        "invoices",
        ["payment_system_id"],
        unique=False,
    )
    op.create_index(op.f("ix_invoices_status"), "invoices", ["status"], unique=False)
    op.create_index(op.f("ix_invoices_user_id"), "invoices", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_invoices_user_id"), table_name="invoices")
    op.drop_index(op.f("ix_invoices_status"), table_name="invoices")
    op.drop_index(op.f("ix_invoices_payment_system_id"), table_name="invoices")
    op.drop_table("invoices")
    op.drop_index(op.f("ix_devices_subscription_id"), table_name="devices")
    op.drop_index(op.f("ix_devices_status"), table_name="devices")
    op.drop_index(op.f("ix_devices_provisioning_state"), table_name="devices")
    op.drop_table("devices")
    op.drop_index(op.f("ix_subscriptions_user_id"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_status"), table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
    op.drop_table("tariffs")
