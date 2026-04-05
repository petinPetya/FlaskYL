import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lowlands_vpn import create_app
from lowlands_vpn.extensions import db
from lowlands_vpn.models import Device, Invoice, Subscription, Tariff, User


@pytest.fixture()
def app(tmp_path: Path):
    database_path = tmp_path / "test.db"
    app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test-secret-key",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path}",
        }
    )

    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, email: str, password: str = "strong-pass-123"):
    return client.post(
        "/register",
        data={
            "email": email,
            "password": password,
            "confirm_password": password,
        },
        follow_redirects=True,
    )


def login(client, email: str, password: str = "strong-pass-123"):
    return client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )


def logout(client):
    return client.post("/logout", data={}, follow_redirects=True)


def get_user_by_email(email: str) -> User | None:
    return db.session.scalar(db.select(User).where(User.email == email))


def get_tariff_by_name(name: str) -> Tariff:
    tariff = db.session.scalar(db.select(Tariff).where(Tariff.name == name))
    assert tariff is not None
    return tariff


def get_single_invoice() -> Invoice:
    return db.session.scalar(db.select(Invoice).limit(1))


def get_single_subscription() -> Subscription:
    return db.session.scalar(db.select(Subscription).limit(1))


def get_single_device() -> Device:
    return db.session.scalar(db.select(Device).limit(1))
