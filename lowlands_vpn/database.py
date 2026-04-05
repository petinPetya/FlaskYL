from pathlib import Path

from sqlalchemy import inspect, text

from lowlands_vpn.data import PLANS
from lowlands_vpn.extensions import db
from lowlands_vpn.models import Tariff

ADDITIVE_SCHEMA_PATCHES = {
    "tariffs": {
        "device_limit": "ALTER TABLE tariffs ADD COLUMN device_limit INTEGER DEFAULT 1 NOT NULL",
    },
    "devices": {
        "assigned_ip": "ALTER TABLE devices ADD COLUMN assigned_ip VARCHAR(64)",
        "last_error": "ALTER TABLE devices ADD COLUMN last_error VARCHAR(255)",
        "provisioned_at": "ALTER TABLE devices ADD COLUMN provisioned_at DATETIME",
        "revoked_at": "ALTER TABLE devices ADD COLUMN revoked_at DATETIME",
        "vpn_uuid": "ALTER TABLE devices ADD COLUMN vpn_uuid VARCHAR(64)",
        "vpn_email": "ALTER TABLE devices ADD COLUMN vpn_email VARCHAR(255)",
        "vpn_link": "ALTER TABLE devices ADD COLUMN vpn_link TEXT",
    },
}


def init_database(instance_path: str) -> None:
    Path(instance_path).mkdir(parents=True, exist_ok=True)
    db.create_all()
    apply_additive_schema_patches()
    seed_tariffs()


def apply_additive_schema_patches() -> None:
    inspector = inspect(db.engine)

    for table_name, patches in ADDITIVE_SCHEMA_PATCHES.items():
        existing_tables = set(inspector.get_table_names())
        if table_name not in existing_tables:
            continue

        existing_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        pending_columns = [
            column_name
            for column_name in patches
            if column_name not in existing_columns
        ]

        if not pending_columns:
            continue

        for column_name in pending_columns:
            db.session.execute(text(patches[column_name]))

        db.session.commit()
        inspector = inspect(db.engine)


def seed_tariffs() -> None:
    for sort_order, plan in enumerate(PLANS, start=1):
        tariff = Tariff.query.filter_by(name=plan["name"]).first()
        if tariff is None:
            tariff = Tariff(name=plan["name"])
            db.session.add(tariff)

        tariff.description = plan["description"]
        tariff.price_cents = plan["price_cents"]
        tariff.days_valid = plan["days_valid"]
        tariff.traffic_limit_bytes = plan["traffic_limit_bytes"]
        tariff.device_limit = plan["device_limit"]
        tariff.sort_order = sort_order
        tariff.is_active = True
        tariff.is_popular = plan.get("is_popular", False)

    db.session.commit()
