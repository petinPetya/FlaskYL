from pathlib import Path

from sqlalchemy import inspect, text

from lowlands_vpn.data import PLANS
from lowlands_vpn.extensions import db
from lowlands_vpn.models import Tariff

EXPECTED_SCHEMA = {
    "users": {
        "id",
        "email",
        "password_hash",
        "balance",
        "is_active",
        "is_admin",
        "created_at",
        "last_login_at",
        "updated_at",
    },
    "tariffs": {
        "id",
        "name",
        "description",
        "price_cents",
        "days_valid",
        "traffic_limit_bytes",
        "sort_order",
        "is_active",
        "is_popular",
        "created_at",
    },
    "subscriptions": {
        "id",
        "user_id",
        "tariff_id",
        "starts_at",
        "expires_at",
        "used_traffic_bytes",
        "traffic_limit_bytes",
        "status",
        "config_code",
        "public_key",
        "private_key",
        "vpn_ip",
        "auto_renew",
        "last_renewed_at",
        "created_at",
        "updated_at",
    },
    "invoices": {
        "id",
        "user_id",
        "subscription_id",
        "amount_cents",
        "status",
        "type",
        "payment_system",
        "payment_system_id",
        "payment_url",
        "description",
        "metadata_json",
        "created_at",
        "paid_at",
    },
}

LEGACY_TABLES = {"user"}


def init_database(instance_path):
    Path(instance_path).mkdir(parents=True, exist_ok=True)

    if schema_requires_reset():
        db.drop_all()
        drop_legacy_tables()

    db.create_all()
    seed_tariffs()


def schema_requires_reset():
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())

    if not existing_tables:
        return False

    if existing_tables.intersection(LEGACY_TABLES):
        return True

    for table_name, expected_columns in EXPECTED_SCHEMA.items():
        if table_name not in existing_tables:
            return True

        actual_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        if not expected_columns.issubset(actual_columns):
            return True

    return False


def drop_legacy_tables():
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())

    for table_name in sorted(existing_tables.intersection(LEGACY_TABLES)):
        db.session.execute(text(f'DROP TABLE IF EXISTS "{table_name}"'))

    db.session.commit()


def seed_tariffs():
    for sort_order, plan in enumerate(PLANS, start=1):
        tariff = Tariff.query.filter_by(name=plan["name"]).first()
        if tariff is None:
            tariff = Tariff(name=plan["name"])
            db.session.add(tariff)

        tariff.description = plan["description"]
        tariff.price_cents = plan["price_cents"]
        tariff.days_valid = plan["days_valid"]
        tariff.traffic_limit_bytes = plan["traffic_limit_bytes"]
        tariff.sort_order = sort_order
        tariff.is_active = True
        tariff.is_popular = plan.get("is_popular", False)

    db.session.commit()
